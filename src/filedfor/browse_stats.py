"""Filing numbers for the field, city and ranking pages (pages_browse.py).

Run locally after sponsors.py has built data/lca/clean.parquet (row-level,
so it stays out of git). It writes data/browse_stats.json, which is
committed, so the scheduled publish never needs the raw data.

    uv run python -m filedfor.browse_stats [path/to/clean.parquet]

Each certified tech LCA is put in at most one field. Its occupation code
decides when only one field lists that code in sponsors.ROLE_SOCS. Some
codes are shared (Software Developers is filed for software, ML and data
roles alike); then the job title decides, read with classify.role, and a
title that names none of the code's fields falls back to SHARED_DEFAULT.
So these counts are a subset of the per-company evidence on the job board,
which counts a shared code toward every field that lists it.

The worksite goes to a metro when classify.places finds one of the board's
metro cities in "City, ST" and the state belongs to that metro (so Berkeley
Heights, NJ is not the Bay Area). Employers are named by data/companies.csv:
only companies FiledFor tracks appear in the top lists; totals count every
employer.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from filedfor.classify import FIELDS, METROS, places, role
from filedfor.sponsors import CLEAN, ROLE_SOCS, WAGE_MAX, WAGE_MIN

OUT = Path("data/browse_stats.json")
COMPANIES = Path("data/companies.csv")
# Where a shared occupation code goes when the title doesn't settle it
SHARED_DEFAULT = {
    "15-1252.00": "swe",  # Software Developers
    "15-1299.08": "swe",  # Computer Systems Engineers/Architects
    "15-2051.00": "data",  # Data Scientists
    "15-1243.00": "data",  # Database Architects
    "15-1242.00": "it",  # Database Administrators
}
# Metros whose cities span more than their home state
METRO_STATES = {"New York": {"NY", "NJ"}, "Washington DC": {"DC", "VA", "MD"}}
TOP = 15  # employers kept per field and city
TOP_NATIONAL = 50  # employers kept per field nationally, for the rankings
# Filings an employer needs to be ranked on entry-level share, strictest first
ENTRY_MINS = (100, 50, 20, 10)
ENTRY_ENOUGH = 10


def soc_fields() -> dict[str, list[str]]:
    """Occupation code -> the fields that list it, in FIELDS order."""
    out: dict[str, list[str]] = defaultdict(list)
    for f in FIELDS:
        for soc in ROLE_SOCS[f]:
            out[soc].append(f)
    missing = [s for s, fs in out.items() if len(fs) > 1 and s not in SHARED_DEFAULT]
    if missing:
        raise ValueError(f"shared occupation codes without a default: {missing}")
    return dict(out)


def field_for(soc: str, title: str | None, socs: dict[str, list[str]]) -> str | None:
    fields = socs.get(soc)
    if not fields:
        return None
    if len(fields) == 1:
        return fields[0]
    by_title = role(title or "")
    return by_title if by_title in fields else SHARED_DEFAULT[soc]


def metro_for(city: str | None, state: str | None) -> str | None:
    if not city or not state:
        return None
    _, metros = places(f"{city}, {state}")
    for m in metros:
        if state in METRO_STATES.get(m, {METROS[m][0]}):
            return m
    return None


def fein_owners(companies: Path = COMPANIES) -> dict[str, str]:
    """FEIN -> FiledFor company name. A FEIN listed under two companies
    (a parent and a brand, say) goes to the one with more tech filings, so
    no filing is counted twice in a ranking."""
    cands: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    with companies.open(newline="") as f:
        for r in csv.DictReader(f):
            key = (
                -int(r["tech_filings"] or 0),
                -int(r["open_jobs"] or 0),
                r["company"],
            )
            for fein in filter(None, r["feins"].split(";")):
                cands[fein].append(key)
    return {fein: min(keys)[2] for fein, keys in cands.items()}


AGG = f"""
    count(*)                                                 as filings,
    count(*) filter (where is_new_hire)                      as new_hire,
    count(*) filter (where wage_level in ('I', 'II'))        as level_12,
    count(*) filter (where wage_level in ('III', 'IV'))      as level_34,
    median(wage_yearly) filter (
        where wage_yearly between {WAGE_MIN} and {WAGE_MAX}
    )                                                        as median_wage
"""
COLS = ("filings", "new_hire", "level_12", "level_34", "median_wage")


def _cell(row: tuple) -> dict:
    out = dict(zip(COLS, row, strict=True))
    out["median_wage"] = round(out["median_wage"]) if out["median_wage"] else None
    return out


def compute(clean: Path = CLEAN, companies: Path = COMPANIES) -> dict:
    con = duckdb.connect()
    socs = soc_fields()
    src = f"read_parquet('{clean}')"
    period = con.sql(
        f"select min(decision_date::timestamp::date), max(decision_date::timestamp::date) from {src}"
    ).fetchone()

    shared = [s for s, fs in socs.items() if len(fs) > 1]
    titles = con.sql(
        f"select distinct soc_code, job_title from {src} "
        f"where soc_code in (select unnest(?::varchar[]))",
        params=[shared],
    ).fetchall()
    con.execute(
        "create table title_field (soc_code varchar, job_title varchar, field varchar)"
    )
    con.executemany(
        "insert into title_field values (?, ?, ?)",
        [(s, t, field_for(s, t, socs)) for s, t in titles],
    )
    con.execute("create table soc_field (soc_code varchar, field varchar)")
    con.executemany(
        "insert into soc_field values (?, ?)",
        [(s, fs[0]) for s, fs in socs.items() if len(fs) == 1],
    )
    sites = con.sql(
        f"select distinct worksite_city, worksite_state from {src}"
    ).fetchall()
    con.execute("create table site_metro (city varchar, state varchar, metro varchar)")
    con.executemany(
        "insert into site_metro values (?, ?, ?)",
        [(c, s, m) for c, s in sites if (m := metro_for(c, s))],
    )
    con.execute("create table owner (fein varchar, company varchar)")
    con.executemany(
        "insert into owner values (?, ?)", list(fein_owners(companies).items())
    )
    con.execute(f"""
        create table f as
        select coalesce(sf.field, tf.field) as field, sm.metro, o.company,
               c.employer_fein, c.wage_level, c.wage_yearly, c.is_new_hire
        from {src} c
        left join soc_field sf on sf.soc_code = c.soc_code
        left join title_field tf
            on tf.soc_code = c.soc_code and tf.job_title is not distinct from c.job_title
        left join site_metro sm
            on sm.city = c.worksite_city and sm.state = c.worksite_state
        left join owner o on o.fein = c.employer_fein
        where coalesce(sf.field, tf.field) is not null
    """)

    def cells(keys: list[str]) -> dict[tuple, dict]:
        rows = con.sql(
            f"select {_lead(keys)} {AGG}, count(distinct employer_fein) as employers "
            f"from f {_group(keys)}"
        ).fetchall()
        n = len(keys)
        return {r[:n]: _cell(r[n : n + len(COLS)]) | {"employers": r[-1]} for r in rows}

    def ranked(keys: list[str]) -> dict[tuple, list]:
        """Tracked employers within each group, most filings first, as
        compact rows: [company, filings, new_hire, level_12, level_34,
        median_wage]."""
        by = [*keys, "company"]
        rows = con.sql(
            f"select {_lead(by)} {AGG} from f where company is not null {_group(by)}"
        ).fetchall()
        out: dict[tuple, list] = defaultdict(list)
        n = len(keys)
        for r in rows:
            c = _cell(r[n + 1 :])
            out[r[:n]].append([r[n], *(c[k] for k in COLS)])
        for v in out.values():
            v.sort(key=lambda x: (-x[1], x[0]))
        return out

    in_metro, all_rows = con.sql(
        "select count(*) filter (where metro is not null), count(*) from f"
    ).fetchone()
    total = cells([]).get((), _cell((0, 0, 0, 0, None)) | {"employers": 0})
    by_field, by_metro = cells(["field"]), cells(["metro"])
    by_field_metro = cells(["field", "metro"])
    rank_all, rank_field = ranked([]), ranked(["field"])
    rank_metro, rank_field_metro = ranked(["metro"]), ranked(["field", "metro"])

    def national(cell: dict, rows: list) -> dict:
        least, entry = _entry(rows)
        return cell | {
            "companies": len(rows),
            "top": rows[:TOP_NATIONAL],
            "entry": entry,
            "entry_min": least,
        }

    def local(cell: dict, rows: list) -> dict:
        return cell | {"companies": len(rows), "top": rows[:TOP]}

    return {
        "built": datetime.now(UTC).date().isoformat(),
        "period": {"from": str(period[0]), "to": str(period[1])},
        "placed_share": round(in_metro / all_rows, 4) if all_rows else 0,
        "all": national(total, rank_all.get((), [])),
        "fields": {
            f: national(by_field[(f,)], rank_field.get((f,), []))
            for f in FIELDS
            if (f,) in by_field
        },
        "metros": {
            m: {
                "all": local(by_metro[(m,)], rank_metro.get((m,), [])),
                "fields": {
                    f: local(by_field_metro[(f, m)], rank_field_metro.get((f, m), []))
                    for f in FIELDS
                    if (f, m) in by_field_metro
                },
            }
            for m in METROS
            if (m,) in by_metro
        },
    }


def _lead(keys: list[str]) -> str:
    return "".join(f"{k}, " for k in keys)


def _group(keys: list[str]) -> str:
    return f"group by {', '.join(keys)}" if keys else ""


def _entry(rows: list[list]) -> tuple[int, list[list]]:
    """Employers with the highest level I-II share (among filings with a
    known level), and the filing minimum used: the strictest of ENTRY_MINS
    that still leaves ENTRY_ENOUGH employers, so small fields get a list too."""

    def share(r: list) -> float:
        return r[3] / (r[3] + r[4])

    for least in ENTRY_MINS:
        ok = [r for r in rows if r[1] >= least and r[3] + r[4]]
        if len(ok) >= ENTRY_ENOUGH:
            break
    ok.sort(key=lambda r: (-share(r), -r[1], r[0]))
    return least, ok[:TOP]


def write(stats: dict, out: Path = OUT) -> int:
    text = json.dumps(stats, separators=(",", ":"), sort_keys=True)
    out.write_text(text + "\n")
    return len(text)


def report(stats: dict) -> None:
    total = stats["all"]["filings"]
    print(f"period              {stats['period']['from']} to {stats['period']['to']}")
    print(f"tech LCAs in fields {total:>10,}")
    for f, c in stats["fields"].items():
        print(f"  {f:<17} {c['filings']:>10,}")
    print(f"placed in a metro   {stats['placed_share']:>10.1%}")


def main() -> None:
    clean = Path(sys.argv[1]) if len(sys.argv) > 1 else CLEAN
    stats = compute(clean)
    size = write(stats)
    report(stats)
    print(f"wrote {OUT} ({size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
