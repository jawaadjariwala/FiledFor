"""Filing history per company for the company pages: tech LCA filings by
quarter, the job titles and worksite cities filed most, and the decision
date range.

Built locally from the row-level LCA data (data/lca/clean.parquet, which is
not in git) into data/company_profiles.json, which is committed. GitHub
Actions never sees the row-level data, so the pages read only the JSON, and
they build without these sections when it is missing.

    uv run python -m filedfor.profiles [path/to/clean.parquet]
"""

import csv
import json
import sys
from pathlib import Path

import duckdb

from filedfor.sponsors import CLEAN, ROLE_SOCS, WAGE_MAX, WAGE_MIN

COMPANIES = Path("data/companies.csv")
EXCLUDED = Path("data/excluded_boards.csv")  # boards that aren't employers
PROFILES = Path("data/company_profiles.json")
TOP_TITLES = 10
TOP_CITIES = 8
# A month with under this share of a typical month's filings, across all
# employers, makes its quarter look low for everyone; the pages say so
LOW_MONTH = 0.25

# DOL's fiscal year starts on October 1: October to December is Q1 of the
# next year's FY
FISCAL = """
    year(d) + (month(d) >= 10)::int  as fy,
    (month(d) + 2) % 12 // 3 + 1     as fq
"""


def excluded_boards(path: Path = EXCLUDED) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        return {(r["system"], r["slug"]) for r in csv.DictReader(f)}


def watchlist(
    companies: Path = COMPANIES, excluded: Path = EXCLUDED
) -> dict[str, list[str]]:
    """company -> its FEINs, for every company with tech filings (the same
    companies that get a page). Boards of one company are merged; boards
    that aren't employers are left out, as on the pages."""
    skip = excluded_boards(excluded)
    out: dict[str, set[str]] = {}
    with companies.open(newline="") as f:
        for r in csv.DictReader(f):
            if int(r["tech_filings"] or 0) > 0 and (r["system"], r["slug"]) not in skip:
                out.setdefault(r["company"], set()).update(
                    x for x in r["feins"].split(";") if x
                )
    return {name: sorted(feins) for name, feins in out.items()}


def quarters_between(first: tuple[int, int], last: tuple[int, int]):
    fy, q = first
    while (fy, q) <= last:
        yield fy, q
        fy, q = (fy, q + 1) if q < 4 else (fy + 1, 1)


def build(
    clean: Path = CLEAN, companies: Path = COMPANIES, excluded: Path = EXCLUDED
) -> dict:
    con = duckdb.connect()
    feins = watchlist(companies, excluded)
    con.execute("create table feins (company varchar, fein varchar)")
    con.executemany(
        "insert into feins values (?, ?)",
        [(name, f) for name, fs in feins.items() for f in fs],
    )
    # decision_date is a string in some builds of clean.parquet, a date in others
    con.execute(f"""
        create view lca as
        select *, try_cast(try_cast(decision_date as varchar) as timestamp)::date as d
        from read_parquet('{clean}')
    """)
    con.execute(f"""
        create table tech as
        select m.company, c.d, {FISCAL}, c.job_title, c.wage_yearly,
            c.worksite_city, c.worksite_state
        from lca c join feins m on c.employer_fein = m.fein
        where c.is_tech and c.d is not null
    """)

    # What the data covers, over every employer: a quarter inside the range
    # with no filings at all is a gap in the files, not a quiet quarter
    first, last = con.execute("select min(d), max(d) from lca").fetchone()
    present = {
        (fy, q)
        for fy, q in con.execute(
            f"select distinct {FISCAL} from (select d from lca where d is not null)"
        ).fetchall()
    }
    quarters = list(quarters_between(min(present), max(present)))
    missing = [q for q in quarters if q not in present]
    months = con.execute(
        "select strftime(d, '%Y-%m') m, count(*) n from lca "
        "where d is not null group by 1 order by 1"
    ).fetchall()
    typical = sorted(n for _, n in months)[len(months) // 2]
    low_months = [m for m, n in months if n < typical * LOW_MONTH]

    out: dict[str, dict] = {}
    for company, fy, fq, n in con.execute(
        "select company, fy, fq, count(*) from tech group by all"
    ).fetchall():
        p = out.setdefault(company, {"quarters": [0] * len(quarters)})
        p["quarters"][quarters.index((fy, fq))] = n
    for p in out.values():
        for q in missing:
            p["quarters"][quarters.index(q)] = None

    # Titles and cities are grouped ignoring case and spacing, and shown in
    # their most common spelling (on a tie, not in capitals)
    titles = con.execute(f"""
        with t as (
            select company, upper(trim(job_title)) as k, trim(job_title) as job_title, wage_yearly
            from tech where job_title is not null and trim(job_title) <> ''
        ), spelled as (
            select company, k, first(job_title order by n desc, job_title = upper(job_title), job_title) as title
            from (select company, k, job_title, count(*) n from t group by all)
            group by all
        ), counted as (
            select company, k, count(*) as n,
                median(wage_yearly) filter (
                    where wage_yearly between {WAGE_MIN} and {WAGE_MAX}
                ) as wage
            from t group by all
        )
        select company, title, n, wage from (
            select *, row_number() over (
                partition by company order by n desc, k
            ) as rank
            from counted join spelled using (company, k)
        ) where rank <= {TOP_TITLES}
        order by company, rank
    """).fetchall()
    for company, title, n, wage in titles:
        out[company].setdefault("titles", []).append(
            [title.strip(), n, int(round(wage, -3)) if wage else None]
        )

    cities = con.execute(f"""
        with t as (
            select company, upper(trim(worksite_city)) as k,
                upper(trim(worksite_state)) as state, trim(worksite_city) as city
            from tech where worksite_city is not null and trim(worksite_city) <> ''
        ), spelled as (
            select company, k, state, first(city order by n desc, city = upper(city), city) as city
            from (select company, k, state, city, count(*) n from t group by all)
            group by all
        ), counted as (
            select company, k, state, count(*) as n from t group by all
        )
        select company, city, state, n from (
            select *, row_number() over (
                partition by company order by n desc, k, state
            ) as rank
            from counted join spelled using (company, k, state)
        ) where rank <= {TOP_CITIES}
        order by company, rank
    """).fetchall()
    for company, city, state, n in cities:
        out[company].setdefault("cities", []).append([nice_city(city), state, n])

    for company, lo, hi in con.execute(
        "select company, min(d), max(d) from tech group by 1"
    ).fetchall():
        out[company]["first"], out[company]["last"] = lo.isoformat(), hi.isoformat()

    # Exact medians over every filing of the company, across all its FEINs:
    # a median of per-FEIN medians is not the company's median. Fields use
    # the same occupation codes and filters as data/role_evidence.parquet
    in_range = f"wage_yearly between {WAGE_MIN} and {WAGE_MAX}"
    for company, wage in con.execute(
        f"select company, median(wage_yearly) filter (where {in_range}) "
        "from tech group by 1"
    ).fetchall():
        out[company]["wage"] = round(wage) if wage else None
    roles = " union all ".join(
        f"select '{role}' as role, unnest({socs}) as soc_code"
        for role, socs in ROLE_SOCS.items()
    )
    for company, role, wage in con.execute(f"""
        with roles as ({roles})
        select m.company, r.role, median(c.wage_yearly) filter (where {in_range})
        from lca c join feins m on c.employer_fein = m.fein join roles r using (soc_code)
        group by all order by 1, 2
    """).fetchall():
        if wage and company in out:
            out[company].setdefault("field_wages", {})[role] = round(wage)

    return {
        "meta": {
            "source": "U.S. Department of Labor, LCA disclosure data",
            "first": first.isoformat(),
            "last": last.isoformat(),
            "quarters": [list(q) for q in quarters],
            "missing": [list(q) for q in missing],
            "low_months": low_months,
        },
        "companies": dict(sorted(out.items())),
    }


def nice_city(city: str) -> str:
    """'SAN FRANCISCO' -> 'San Francisco'. Mixed-case spellings are kept
    as filed ('McLean')."""
    return city.title() if city.isupper() or city.islower() else city


def write(profiles: dict, path: Path = PROFILES) -> None:
    """One company per line, so a rebuild reads as a git diff of the
    companies whose filings changed."""
    dump = lambda x: json.dumps(
        x, separators=(",", ":"), ensure_ascii=False, sort_keys=True
    )
    rows = ",\n".join(
        f"{dump(name)}:{dump(p)}" for name, p in sorted(profiles["companies"].items())
    )
    path.write_text(
        f'{{"meta":{dump(profiles["meta"])},\n"companies":{{\n{rows}\n}}}}\n'
    )


def load(path: Path | None = None) -> dict:
    """The committed profiles, or empty ones when the file is missing or
    unreadable, so pages still build without their history sections."""
    try:
        data = json.loads((path or PROFILES).read_text())
        return {"meta": data["meta"], "companies": data["companies"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {"meta": {}, "companies": {}}


def main() -> None:
    clean = Path(sys.argv[1]) if len(sys.argv) > 1 else CLEAN
    profiles = build(clean)
    write(profiles)
    m = profiles["meta"]
    print(
        f"{len(profiles['companies']):,} companies, decisions {m['first']} to {m['last']}"
    )
    print(f"quarters: {', '.join(f'FY{fy} Q{q}' for fy, q in m['quarters'])}")
    print(f"missing quarters: {m['missing'] or 'none'}; low months: {m['low_months']}")
    print(f"wrote {PROFILES} ({PROFILES.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
