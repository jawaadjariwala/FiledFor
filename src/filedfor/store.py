"""Postgres: the poller's memory between runs (ADR-002).

All reads happen at the start of a run and all writes in one transaction at
the end, so the database is awake for seconds. The site never reads from here;
it reads the jobs.json the poller publishes.
"""

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from filedfor.diff import Board, Key

SCHEMA = """
create table if not exists boards (
    system               text not null,
    slug                 text not null,
    first_ok_at          timestamptz,
    last_ok_at           timestamptz,
    last_error           text,
    consecutive_failures int not null default 0,
    primary key (system, slug)
);

create table if not exists jobs (
    system             text not null,
    slug               text not null,
    job_id             text not null,
    company            text not null,
    title              text not null,
    url                text not null,
    location           text,
    is_us              boolean,
    is_remote          boolean not null,
    role               text not null,
    level              text not null,
    min_years          int,
    no_sponsorship     boolean,
    citizens_only      boolean,
    clearance          boolean,
    evidence           jsonb,
    posted_at          timestamptz,
    first_seen_at      timestamptz not null,
    closed_at          timestamptz,
    alerted_at         timestamptz,
    classifier_version int not null,
    primary key (system, slug, job_id)
);
create index if not exists jobs_open on jobs (system, slug) where closed_at is null;
create index if not exists jobs_pending on jobs (first_seen_at)
    where alerted_at is null and closed_at is null;

create table if not exists runs (
    id            bigserial primary key,
    started_at    timestamptz not null,
    finished_at   timestamptz not null,
    boards_ok     int not null,
    boards_failed int not null,
    postings      int not null,
    candidates    int not null,
    new_jobs      int not null,
    closed_jobs   int not null,
    alerts_sent   int not null default 0
);
"""

JOB_COLUMNS = [
    "system",
    "slug",
    "job_id",
    "company",
    "title",
    "url",
    "location",
    "is_us",
    "is_remote",
    "role",
    "level",
    "min_years",
    "no_sponsorship",
    "citizens_only",
    "clearance",
    "evidence",
    "posted_at",
    "first_seen_at",
    "alerted_at",
    "classifier_version",
]


def connect(url: str | None = None) -> psycopg.Connection:
    # Neon's pooler hands each transaction to any server connection, so
    # server-side prepared statements can't be relied on
    return psycopg.connect(
        url or os.environ["DATABASE_URL"], prepare_threshold=None, row_factory=dict_row
    )


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA)
    conn.commit()


def load_state(conn: psycopg.Connection) -> tuple[set[Key], set[Key], set[Board]]:
    """(open jobs, closed jobs, boards fetched successfully before)."""
    open_keys, closed_keys = set(), set()
    for r in conn.execute("select system, slug, job_id, closed_at from jobs"):
        (closed_keys if r["closed_at"] else open_keys).add(
            (r["system"], r["slug"], r["job_id"])
        )
    known = {
        (r["system"], r["slug"])
        for r in conn.execute(
            "select system, slug from boards where first_ok_at is not null"
        )
    }
    return open_keys, closed_keys, known


def save_run(
    conn: psycopg.Connection,
    new_jobs: Iterable[dict],
    closed: Iterable[Key],
    ok_boards: Iterable[Board],
    failed_boards: dict[Board, str],
    stats: dict,
    now: datetime,
) -> None:
    """Everything a run learned, in one transaction."""
    with conn.transaction():
        rows = []
        for j in new_jobs:
            j = dict(j)
            j["evidence"] = json.dumps(j["evidence"]) if j.get("evidence") else None
            rows.append([j.get(c) for c in JOB_COLUMNS])
        if rows:
            cols = ", ".join(JOB_COLUMNS)
            marks = ", ".join(["%s"] * len(JOB_COLUMNS))
            updates = ", ".join(
                f"{c} = excluded.{c}"
                for c in JOB_COLUMNS
                if c not in ("system", "slug", "job_id", "first_seen_at", "alerted_at")
            )
            with conn.cursor() as cur:
                cur.executemany(
                    f"insert into jobs ({cols}) values ({marks}) "
                    f"on conflict (system, slug, job_id) do update set {updates}, "
                    "closed_at = null, "  # a job that came back is open again
                    "alerted_at = coalesce(jobs.alerted_at, excluded.alerted_at)",
                    rows,
                )
        closed = list(closed)
        if closed:
            with conn.cursor() as cur:
                cur.executemany(
                    "update jobs set closed_at = %s "
                    "where system = %s and slug = %s and job_id = %s",
                    [(now, *k) for k in closed],
                )
        with conn.cursor() as cur:
            cur.executemany(
                "insert into boards (system, slug, first_ok_at, last_ok_at) "
                "values (%s, %s, %s, %s) on conflict (system, slug) do update set "
                "last_ok_at = excluded.last_ok_at, last_error = null, consecutive_failures = 0, "
                "first_ok_at = coalesce(boards.first_ok_at, excluded.first_ok_at)",
                [(*b, now, now) for b in ok_boards],
            )
            cur.executemany(
                "insert into boards (system, slug, last_error, consecutive_failures) "
                "values (%s, %s, %s, 1) on conflict (system, slug) do update set "
                "last_error = excluded.last_error, "
                "consecutive_failures = boards.consecutive_failures + 1",
                [(*b, err[:200]) for b, err in failed_boards.items()],
            )
        conn.execute(
            "insert into runs (started_at, finished_at, boards_ok, boards_failed, postings, "
            "candidates, new_jobs, closed_jobs) values (%(started_at)s, %(finished_at)s, "
            "%(boards_ok)s, %(boards_failed)s, %(postings)s, %(candidates)s, %(new_jobs)s, "
            "%(closed_jobs)s)",
            stats,
        )


def pending_alerts(conn: psycopg.Connection) -> list[dict]:
    """Open jobs not yet alerted, oldest first. Filters are applied by the caller."""
    return list(
        conn.execute(
            "select * from jobs where alerted_at is null and closed_at is null "
            "order by first_seen_at, system, slug, job_id"
        )
    )


def mark_alerted(conn: psycopg.Connection, keys: Iterable[Key], now: datetime) -> None:
    keys = list(keys)
    if not keys:
        return
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "update jobs set alerted_at = %s where system = %s and slug = %s and job_id = %s",
            [(now, *k) for k in keys],
        )


def record_alerts(conn: psycopg.Connection, sent: int) -> None:
    conn.execute(
        "update runs set alerts_sent = %s where id = (select max(id) from runs)",
        (sent,),
    )
    conn.commit()


def open_jobs(conn: psycopg.Connection) -> list[dict]:
    return list(
        conn.execute(
            "select * from jobs where closed_at is null "
            "order by posted_at desc nulls last, system, slug, job_id"
        )
    )


def prune(conn: psycopg.Connection, now: datetime, days: int = 90) -> int:
    """Forget jobs closed more than `days` ago. Returns how many were deleted."""
    cur = conn.execute(
        "delete from jobs where closed_at < %s", (now - timedelta(days=days),)
    )
    conn.commit()
    return cur.rowcount


def utcnow() -> datetime:
    return datetime.now(UTC)
