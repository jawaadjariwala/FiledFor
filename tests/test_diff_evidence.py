from datetime import UTC, datetime, timedelta

import duckdb

from filedfor.diff import diff, should_alert
from filedfor.evidence import EvidenceIndex

A = ("lever", "acme")
B = ("ashby", "beta")


def k(board, n):
    return (*board, str(n))


def test_new_and_closed():
    ch = diff(
        open_in_db={k(A, 1), k(A, 2)},
        candidates_now={k(A, 2), k(A, 3)},
        ok_boards={A},
        known_boards={A},
    )
    assert ch.new == {k(A, 3)} and ch.closed == {k(A, 1)} and not ch.silent


def test_failed_board_never_closes_jobs():
    ch = diff(
        open_in_db={k(A, 1)}, candidates_now=set(), ok_boards=set(), known_boards={A}
    )
    assert not ch.closed


def test_first_sight_of_a_board_is_silent():
    ch = diff(set(), {k(B, 1), k(A, 9)}, ok_boards={A, B}, known_boards={A})
    assert ch.new == {k(B, 1), k(A, 9)} and ch.silent == {k(B, 1)}


def test_reopened_job_is_silent():
    ch = diff(set(), {k(A, 1)}, {A}, {A}, closed_in_db={k(A, 1)})
    assert ch.silent == {k(A, 1)}


def test_alert_window():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert should_alert(now - timedelta(hours=5), now)
    assert not should_alert(now - timedelta(days=4), now)
    assert should_alert(None, now)


def test_evidence_sums_across_feins(tmp_path):
    ev = tmp_path / "ev.parquet"
    duckdb.sql("""
        select * from (values
            ('11-1', 'swe', 10, 4, 3, 5, 2, 0, 100000.0, date '2026-01-01'),
            ('22-2', 'swe', 30, 6, 1, 9, 15, 5, 200000.0, date '2026-03-01'),
            ('11-1', 'ai',   2, 1, 1, 1, 0, 0, 150000.0, date '2025-12-01')
        ) t(employer_fein, role, filings, new_hire_filings, level_1, level_2,
            level_3, level_4, median_wage, last_decision)
    """).write_parquet(str(ev))
    comp = tmp_path / "companies.csv"
    comp.write_text(
        "system,slug,eu,company,open_jobs,feins,tech_filings\n"
        "lever,acme,0,Acme,5,11-1;22-2,40\nlever,none,0,None,1,,0\n"
    )
    idx = EvidenceIndex(ev, comp)
    e = idx.lookup("lever", "acme", "swe")
    assert (e.filings, e.new_hire_filings, e.level_1) == (40, 10, 4)
    assert e.median_wage == 175000  # (100k x 10 + 200k x 30) / 40
    assert e.last_decision == "2026-03-01"
    assert idx.lookup("lever", "acme", "data") is None
    assert idx.lookup("lever", "none", "swe") is None


def test_personal_filter_skips_research_only_at_frontier_labs():
    from filedfor.poll import PERSONAL

    base = {
        "role": "ai",
        "level": "entry",
        "min_years": None,
        "is_us": True,
        "no_sponsorship": False,
        "citizens_only": False,
        "clearance": False,
    }
    exa = base | {"slug": "exa", "title": "Research Engineer, Content Understanding"}
    lab = base | {"slug": "anthropic", "title": "Research Engineer, Interpretability"}
    applied = base | {"slug": "anthropic", "title": "AI Engineer, New Grad"}
    assert (
        PERSONAL.matches(exa)
        and not PERSONAL.matches(lab)
        and PERSONAL.matches(applied)
    )
