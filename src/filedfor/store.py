"""Postgres: the poller's memory between runs (ADR-002).

All reads happen at the start of a run and all writes in one transaction at
the end, so the database is awake for seconds. The site never reads from here;
it reads the jobs.json the poller publishes.

Neon's free plan allows 5 GB of data sent out a month. A run therefore never
reads the whole jobs table: it keeps a copy between runs (Mirror, in the
Actions cache) and reads only rows changed since, and it reads descriptions
only for jobs it hasn't seen.
"""

import gzip
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
-- Workday boards are mostly read in part; this is the last read to the end (ADR-003)
alter table boards add column if not exists last_full_at timestamptz;
-- Rules v6: pay range and work arrangement read from each posting
alter table jobs add column if not exists salary_min int;
alter table jobs add column if not exists salary_max int;
alter table jobs add column if not exists arrangement text;
alter table jobs add column if not exists description_html text;
alter table jobs add column if not exists changed_at timestamptz not null default now();
create index if not exists jobs_changed_at on jobs (changed_at);

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
    "salary_min",
    "salary_max",
    "arrangement",
    "description_html",
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


def known_boards(conn: psycopg.Connection) -> set[Board]:
    """Boards fetched successfully before."""
    return {
        (r["system"], r["slug"])
        for r in conn.execute(
            "select system, slug from boards where first_ok_at is not null"
        )
    }


def workday_last_full(conn: psycopg.Connection) -> dict[str, datetime]:
    """slug -> when each Workday board was last read to the end."""
    return {
        r["slug"]: r["last_full_at"]
        for r in conn.execute(
            "select slug, last_full_at from boards "
            "where system = 'workday' and last_full_at is not null"
        )
    }


def save_run(
    conn: psycopg.Connection,
    new_jobs: Iterable[dict],
    closed: Iterable[Key],
    ok_boards: Iterable[Board],
    failed_boards: dict[Board, str],
    stats: dict,
    now: datetime,
    full_boards: Iterable[Board] = (),
) -> None:
    """Everything a run learned, in one transaction. `full_boards`: Workday
    boards read to the end this run."""
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
                # A run that couldn't fetch the description keeps the stored one
                f"{c} = coalesce(excluded.{c}, jobs.{c})"
                if c == "description_html"
                else f"{c} = excluded.{c}"
                for c in JOB_COLUMNS
                if c not in ("system", "slug", "job_id", "first_seen_at", "alerted_at")
            )
            with conn.cursor() as cur:
                cur.executemany(
                    f"insert into jobs ({cols}) values ({marks}) "
                    f"on conflict (system, slug, job_id) do update set {updates}, "
                    "closed_at = null, changed_at = now(), "  # a job that came back is open again
                    "alerted_at = coalesce(jobs.alerted_at, excluded.alerted_at)",
                    rows,
                )
        closed = list(closed)
        if closed:
            with conn.cursor() as cur:
                cur.executemany(
                    # Closed jobs aren't shown, so their descriptions go
                    "update jobs set closed_at = %s, description_html = null, changed_at = now() "
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
            cur.executemany(
                "update boards set last_full_at = %s where system = %s and slug = %s",
                [(now, *b) for b in full_boards],
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
            f"select {', '.join(MIRROR_COLUMNS)} from jobs "
            "where alerted_at is null and closed_at is null "
            "order by first_seen_at, system, slug, job_id"
        )
    )


def mark_alerted(conn: psycopg.Connection, keys: Iterable[Key], now: datetime) -> None:
    keys = list(keys)
    if not keys:
        return
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "update jobs set alerted_at = %s, changed_at = now() "
            "where system = %s and slug = %s and job_id = %s",
            [(now, *k) for k in keys],
        )


def record_alerts(conn: psycopg.Connection, sent: int) -> None:
    conn.execute(
        "update runs set alerts_sent = %s where id = (select max(id) from runs)",
        (sent,),
    )
    conn.commit()


MIRROR = Path(".cache/jobs.json.gz")
MIRROR_COLUMNS = [c for c in JOB_COLUMNS if c != "description_html"] + [
    "closed_at",
    "changed_at",
]
TIMES = ("posted_at", "first_seen_at", "alerted_at", "closed_at", "changed_at")
LISTED_DAYS = 30
SEP = "\x1f"


def _key(r: dict) -> Key:
    return r["system"], r["slug"], r["job_id"]


class Mirror:
    """The jobs table without descriptions, kept between runs. sync() reads
    only rows changed since the last sync; if the copy's row count then
    disagrees with the database's, it reads the whole table again, so the
    copy can't drift."""

    def __init__(self, path: Path = MIRROR) -> None:
        self.path = path
        self.rows: dict[Key, dict] = {}
        self.mark: datetime | None = None
        try:
            data = json.loads(gzip.decompress(path.read_bytes()))
            for r in data["rows"]:
                for c in TIMES:
                    if r.get(c):
                        r[c] = datetime.fromisoformat(r[c])
                self.rows[_key(r)] = r
            self.mark = datetime.fromisoformat(data["mark"])
        except (OSError, ValueError, KeyError, TypeError):
            self.rows, self.mark = {}, None

    def sync(self, conn: psycopg.Connection) -> int:
        """Bring the copy up to date. Returns how many rows were read."""
        cols = ", ".join(MIRROR_COLUMNS)
        if self.mark is None:
            self.rows = {}
            fresh = conn.execute(f"select {cols} from jobs").fetchall()
        else:
            # Only one run writes at a time, so a small margin is enough to
            # cover a write committed while the last sync was reading
            fresh = conn.execute(
                f"select {cols} from jobs where changed_at > %s",
                (self.mark - timedelta(minutes=2),),
            ).fetchall()
        for r in fresh:
            self.rows[_key(r)] = dict(r)
        # The database's clock when this read began (now() is the start of
        # the transaction): the next sync reads rows changed after it
        check = conn.execute("select count(*) as n, now() as t from jobs").fetchone()
        if self.mark is not None and check["n"] != len(self.rows):
            self.mark = None
            return len(fresh) + self.sync(conn)
        self.mark = check["t"]
        return len(fresh)

    def forget(self, keys: Iterable[Key]) -> None:
        for k in keys:
            self.rows.pop(k, None)

    def save(self) -> None:
        def plain(r: dict) -> dict:
            return {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in r.items()
            }

        self.path.parent.mkdir(exist_ok=True)
        data = {
            "mark": self.mark.isoformat() if self.mark else None,
            "rows": [plain(r) for r in self.rows.values()],
        }
        self.path.write_bytes(gzip.compress(json.dumps(data).encode()))

    def state(self) -> tuple[set[Key], set[Key]]:
        """(open jobs, closed jobs)."""
        open_keys, closed_keys = set(), set()
        for k, r in self.rows.items():
            (closed_keys if r["closed_at"] else open_keys).add(k)
        return open_keys, closed_keys

    def stale(self, version: int) -> dict[Key, str]:
        """Open jobs classified by older rules, with their titles: re-read
        once, or closed if the title no longer names a role FiledFor covers."""
        return {
            k: r["title"]
            for k, r in self.rows.items()
            if not r["closed_at"] and (r["classifier_version"] or 0) < version
        }

    def open_count(self) -> int:
        return sum(1 for r in self.rows.values() if not r["closed_at"])

    def listable(self, now: datetime) -> list[dict]:
        """What the site can list: open, in the US or unknown, not blocked,
        posted in the last 30 days."""
        since = now - timedelta(days=LISTED_DAYS)
        return [
            r
            for r in self.rows.values()
            if not r["closed_at"]
            and r["is_us"] is not False
            and not (r["no_sponsorship"] or r["citizens_only"] or r["clearance"])
            and (r["posted_at"] or r["first_seen_at"]) > since
        ]


def open_jobs(conn: psycopg.Connection, mirror: Mirror) -> list[dict]:
    """Jobs the site can list, newest first, each with checked_at: the last
    time its board was read successfully. Descriptions are read separately."""
    checked = {
        (r["system"], r["slug"]): r["last_ok_at"]
        for r in conn.execute("select system, slug, last_ok_at from boards")
    }
    jobs = [
        dict(r, checked_at=checked.get(_key(r)[:2])) for r in mirror.listable(utcnow())
    ]
    oldest = datetime.min.replace(tzinfo=UTC)
    jobs.sort(key=lambda j: (j["system"], j["slug"], j["job_id"]))
    jobs.sort(key=lambda j: j["posted_at"] or oldest, reverse=True)
    return jobs


def descriptions(conn: psycopg.Connection, keys: list[Key]) -> dict[Key, str]:
    """The stored description of each of these jobs that has one."""
    out: dict[Key, str] = {}
    for i in range(0, len(keys), 500):
        batch = keys[i : i + 500]
        rows = conn.execute(
            "select j.system, j.slug, j.job_id, j.description_html from jobs j "
            "join unnest(%s::text[], %s::text[], %s::text[]) as k(system, slug, job_id) "
            "using (system, slug, job_id) where j.description_html is not null",
            ([k[0] for k in batch], [k[1] for k in batch], [k[2] for k in batch]),
        )
        out |= {
            (r["system"], r["slug"], r["job_id"]): r["description_html"] for r in rows
        }
    return out


def prune(conn: psycopg.Connection, now: datetime, days: int = 90) -> list[Key]:
    """Forget jobs closed more than `days` ago. Returns the deleted keys."""
    rows = conn.execute(
        "delete from jobs where closed_at < %s returning system, slug, job_id",
        (now - timedelta(days=days),),
    ).fetchall()
    conn.commit()
    return [(r["system"], r["slug"], r["job_id"]) for r in rows]


def utcnow() -> datetime:
    return datetime.now(UTC)
