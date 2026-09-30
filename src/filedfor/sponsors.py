"""Build the sponsor tables from the raw LCA Parquet files.

Three steps, all in DuckDB SQL:

1. clean:   one row per case (latest decision wins), H-1B only, typed columns,
            wages converted to yearly. Row-level, so it stays out of git.
2. sponsors: one row per employer (keyed on FEIN, the federal tax ID) with
            filing counts, wage-level mix and median wage for tech roles.
3. sponsor_roles: one row per employer and occupation, the role-level
            evidence shown next to each job.

"Sponsorship evidence" means an LCA that was certified, including ones the
employer withdrew after certification. Denied applications and ones withdrawn
before a decision are excluded.
"""

from pathlib import Path

import duckdb

RAW_GLOB = "data/lca/LCA_Disclosure_Data_*.parquet"
CLEAN = Path("data/lca/clean.parquet")
SPONSORS = Path("data/sponsors.parquet")
SPONSOR_ROLES = Path("data/sponsor_roles.parquet")

# Hours and pay periods per year, to put every wage on the same footing
YEARLY_FACTOR = """
    case WAGE_UNIT_OF_PAY
        when 'Year' then 1 when 'Month' then 12 when 'Bi-Weekly' then 26
        when 'Week' then 52 when 'Hour' then 2080
    end
"""

CLEAN_SQL = f"""
with ranked as (
    select *,
        try_cast(DECISION_DATE as timestamp)::date as decision_date,
        row_number() over (
            partition by CASE_NUMBER
            order by try_cast(DECISION_DATE as timestamp) desc
        ) as rn
    from '{RAW_GLOB}'
)
select
    CASE_NUMBER                                         as case_number,
    CASE_STATUS                                         as case_status,
    decision_date,
    try_cast(BEGIN_DATE as timestamp)::date             as begin_date,
    EMPLOYER_FEIN                                       as employer_fein,
    trim(EMPLOYER_NAME)                                 as employer_name,
    EMPLOYER_STATE                                      as employer_state,
    NAICS_CODE                                          as naics_code,
    JOB_TITLE                                           as job_title,
    trim(SOC_CODE)                                      as soc_code,
    SOC_TITLE                                           as soc_title,
    left(SOC_CODE, 3) = '15-'                           as is_tech,
    coalesce(nullif(PW_WAGE_LEVEL, 'N/A'), 'Unknown')   as wage_level,
    try_cast(WAGE_RATE_OF_PAY_FROM as double) * {YEARLY_FACTOR} as wage_yearly,
    WORKSITE_CITY                                       as worksite_city,
    WORKSITE_STATE                                      as worksite_state,
    try_cast(NEW_EMPLOYMENT as integer) > 0             as is_new_hire,
    FULL_TIME_POSITION = 'Y'                            as is_full_time,
    H_1B_DEPENDENT = 'Yes'                              as h1b_dependent,
    SECONDARY_ENTITY = 'Yes'                            as placed_at_client
from ranked
where rn = 1
  and VISA_CLASS = 'H-1B'
  and CASE_STATUS in ('Certified', 'Certified - Withdrawn')
"""

# Annual wages outside this range are data-entry errors, not real offers
WAGE_MIN, WAGE_MAX = 20_000, 1_000_000

SPONSORS_SQL = f"""
select
    employer_fein,
    mode(employer_name)                                        as employer_name,
    count(*)                                                   as filings,
    count(*) filter (where is_tech)                            as tech_filings,
    count(*) filter (where is_tech and is_new_hire)            as tech_new_hire_filings,
    count(*) filter (where is_tech and wage_level = 'I')       as tech_level_1,
    count(*) filter (where is_tech and wage_level = 'II')      as tech_level_2,
    count(*) filter (where is_tech and wage_level = 'III')     as tech_level_3,
    count(*) filter (where is_tech and wage_level = 'IV')      as tech_level_4,
    count(*) filter (where is_tech and wage_level = 'Unknown') as tech_level_unknown,
    median(wage_yearly) filter (
        where is_tech and wage_yearly between {WAGE_MIN} and {WAGE_MAX}
    )                                                          as tech_median_wage,
    avg(h1b_dependent::int)                                    as h1b_dependent_share,
    avg(placed_at_client::int)                                 as placed_at_client_share,
    list(distinct employer_name)                               as name_variants,
    min(decision_date)                                         as first_decision,
    max(decision_date)                                         as last_decision
from read_parquet('{CLEAN}')
group by employer_fein
"""

SPONSOR_ROLES_SQL = f"""
select
    employer_fein,
    soc_code,
    mode(soc_title)                                  as soc_title,
    count(*)                                         as filings,
    count(*) filter (where is_new_hire)              as new_hire_filings,
    count(*) filter (where wage_level = 'I')         as level_1,
    count(*) filter (where wage_level = 'II')        as level_2,
    count(*) filter (where wage_level = 'III')       as level_3,
    count(*) filter (where wage_level = 'IV')        as level_4,
    count(*) filter (where wage_level = 'Unknown')   as level_unknown,
    median(wage_yearly) filter (
        where wage_yearly between {WAGE_MIN} and {WAGE_MAX}
    )                                                as median_wage
from read_parquet('{CLEAN}')
where is_tech
group by employer_fein, soc_code
"""


def build(con: duckdb.DuckDBPyConnection | None = None) -> None:
    con = con or duckdb.connect()
    con.sql(f"copy ({CLEAN_SQL}) to '{CLEAN}' (format parquet, compression zstd)")
    con.sql(f"copy ({SPONSORS_SQL}) to '{SPONSORS}' (format parquet, compression zstd)")
    con.sql(
        f"copy ({SPONSOR_ROLES_SQL}) to '{SPONSOR_ROLES}' (format parquet, compression zstd)"
    )


def report(con: duckdb.DuckDBPyConnection | None = None) -> None:
    """Data-quality numbers for every stage, so a bad rule shows up as a bad count."""
    con = con or duckdb.connect()
    one = lambda q: con.sql(q).fetchone()[0]
    raw = one(f"select count(*) from '{RAW_GLOB}'")
    cases = one(f"select count(distinct CASE_NUMBER) from '{RAW_GLOB}'")
    clean = one(f"select count(*) from '{CLEAN}'")
    tech = one(f"select count(*) from '{CLEAN}' where is_tech")
    bad_wage = one(
        f"select count(*) from '{CLEAN}' "
        f"where wage_yearly is null or wage_yearly not between {WAGE_MIN} and {WAGE_MAX}"
    )
    employers = one(f"select count(*) from '{SPONSORS}'")
    tech_employers = one(f"select count(*) from '{SPONSORS}' where tech_filings > 0")
    print(f"raw rows            {raw:>10,}")
    print(
        f"distinct cases      {cases:>10,}   ({raw - cases:,} repeated filings collapsed)"
    )
    print(
        f"clean H-1B evidence {clean:>10,}   (certified, incl. withdrawn after certification)"
    )
    print(f"  tech (SOC 15-)    {tech:>10,}")
    print(f"  wage out of range {bad_wage:>10,}   (excluded from medians only)")
    print(f"employers           {employers:>10,}")
    print(f"  with tech filings {tech_employers:>10,}")


def main() -> None:
    con = duckdb.connect()
    build(con)
    report(con)


if __name__ == "__main__":
    main()
