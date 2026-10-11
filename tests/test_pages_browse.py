"""Field, city and ranking pages, and the filing numbers behind them."""

import json
import re
from datetime import UTC, datetime

import duckdb
import pytest

from filedfor import browse_stats, pages, pages_browse
from filedfor.classify import FIELDS, METROS
from filedfor.pages import Context

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
SHEET = (
    "system,slug,eu,company,open_jobs,feins,tech_filings,us_filter\n"
    "greenhouse,acme,0,Acme <Labs>,10,11-1111111,90,\n"
    "lever,acme2,0,Acme <Labs>,3,11-1111111,90,\n"
    "ashby,brand,0,Acme Brand,1,11-1111111;22-2222222,5,\n"
    "ashby,beta,0,Beta,2,33-3333333,40,\n"
)


# --- browse_stats --------------------------------------------------------------


def lca(tmp_path, rows):
    """A tiny clean.parquet with the columns browse_stats reads."""
    con = duckdb.connect()
    con.execute(
        "create table t (case_number varchar, decision_date varchar, employer_fein varchar,"
        " job_title varchar, soc_code varchar, wage_level varchar, wage_yearly double,"
        " worksite_city varchar, worksite_state varchar, is_new_hire boolean)"
    )
    con.executemany(
        "insert into t values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(f"I-{i}", *r) for i, r in enumerate(rows)],
    )
    path = tmp_path / "clean.parquet"
    con.execute(f"copy t to '{path}' (format parquet)")
    return path


def test_field_comes_from_the_code_or_the_title_when_the_code_is_shared():
    socs = browse_stats.soc_fields()
    assert browse_stats.field_for("15-1252.00", "Software Engineer II", socs) == "swe"
    assert (
        browse_stats.field_for("15-1252.00", "Machine Learning Engineer", socs) == "ai"
    )
    assert (
        browse_stats.field_for("15-1252.00", "Member of Technical Staff", socs) == "swe"
    )
    # A title naming a field the code doesn't cover falls back to the default
    assert browse_stats.field_for("15-1252.00", "Product Designer", socs) == "swe"
    assert browse_stats.field_for("15-2051.00", "Data Scientist", socs) == "data"
    assert browse_stats.field_for("15-1212.00", "Analyst", socs) == "security"
    assert browse_stats.field_for("29-1141.00", "Nurse", socs) is None


def test_metro_needs_a_city_and_a_state_that_belong_to_it():
    assert browse_stats.metro_for("Seattle", "WA") == "Seattle"
    assert browse_stats.metro_for("JERSEY CITY", "NJ") == "New York"
    assert browse_stats.metro_for("McLean", "VA") == "Washington DC"
    assert browse_stats.metro_for("Berkeley Heights", "NJ") is None
    assert browse_stats.metro_for("Brooklyn", "OH") is None
    assert browse_stats.metro_for("Omaha", "NE") is None
    assert browse_stats.metro_for(None, "WA") is None


def test_a_shared_fein_belongs_to_the_company_with_more_filings(tmp_path):
    sheet = tmp_path / "companies.csv"
    sheet.write_text(SHEET)
    assert browse_stats.fein_owners(sheet) == {
        "11-1111111": "Acme <Labs>",
        "22-2222222": "Acme Brand",
        "33-3333333": "Beta",
    }


def test_stats_on_a_tiny_fixture(tmp_path):
    sheet = tmp_path / "companies.csv"
    sheet.write_text(SHEET)
    a, b, other = "11-1111111", "33-3333333", "99-9999999"
    rows = [
        # decision, fein, title, soc, level, wage, city, state, new hire
        (
            "2024-10-01 00:00:00",
            a,
            "Software Engineer",
            "15-1252.00",
            "I",
            100_000,
            "Seattle",
            "WA",
            True,
        ),
        (
            "2025-01-05 00:00:00",
            a,
            "Software Engineer",
            "15-1252.00",
            "II",
            120_000,
            "Redmond",
            "WA",
            False,
        ),
        (
            "2025-03-01 00:00:00",
            a,
            "ML Engineer",
            "15-1252.00",
            "III",
            200_000,
            "Seattle",
            "WA",
            True,
        ),
        (
            "2025-06-01 00:00:00",
            b,
            "Software Engineer",
            "15-1252.00",
            "IV",
            300_000,
            "Omaha",
            "NE",
            False,
        ),
        (
            "2026-06-30 00:00:00",
            other,
            "Software Developer",
            "15-1252.00",
            "Unknown",
            5,
            "Seattle",
            "WA",
            False,
        ),
        (
            "2025-02-01 00:00:00",
            other,
            "Nurse",
            "29-1141.00",
            "I",
            80_000,
            "Seattle",
            "WA",
            True,
        ),
    ]
    stats = browse_stats.compute(lca(tmp_path, rows), sheet)
    assert stats["period"] == {"from": "2024-10-01", "to": "2026-06-30"}
    assert stats["all"]["filings"] == 5  # the nurse is not tech
    swe = stats["fields"]["swe"]
    assert swe["filings"] == 4 and swe["employers"] == 3 and swe["companies"] == 2
    assert (swe["new_hire"], swe["level_12"], swe["level_34"]) == (1, 2, 1)
    assert swe["median_wage"] == 120_000  # the $5 wage is out of range
    assert swe["top"][0] == ["Acme <Labs>", 2, 1, 2, 0, 110_000]
    assert stats["fields"]["ai"]["top"] == [["Acme <Labs>", 1, 1, 0, 1, 200_000]]
    seattle = stats["metros"]["Seattle"]
    assert seattle["all"]["filings"] == 4
    assert seattle["fields"]["swe"]["filings"] == 3
    assert [r[0] for r in seattle["fields"]["swe"]["top"]] == ["Acme <Labs>"]
    assert "Omaha" not in json.dumps(stats)
    assert stats["placed_share"] == 0.8


def test_entry_list_uses_the_strictest_minimum_that_leaves_enough():
    big = [[f"C{i}", 120, 0, 60 + i, 60 - i, None] for i in range(12)]
    least, rows = browse_stats._entry(big)
    assert least == 100 and rows[0][0] == "C11" and len(rows) == 12
    small = [[f"C{i}", 15, 0, i, 15 - i, None] for i in range(10)]
    assert browse_stats._entry(small)[0] == 10


# --- pages ---------------------------------------------------------------------


STATS = {
    "period": {"from": "2024-10-01", "to": "2026-06-30"},
    "all": {
        "filings": 1000,
        "new_hire": 300,
        "level_12": 500,
        "level_34": 400,
        "median_wage": 150_000,
        "employers": 50,
        "companies": 2,
        "top": [
            ["Acme <Labs>", 600, 200, 300, 250, 160_000],
            ["Beta", 100, 10, 90, 10, 90_000],
        ],
        "entry": [["Beta", 100, 10, 90, 10, 90_000]],
        "entry_min": 50,
    },
    "fields": {
        "swe": {
            "filings": 800,
            "new_hire": 200,
            "level_12": 400,
            "level_34": 300,
            "median_wage": 140_000,
            "employers": 40,
            "companies": 1,
            "top": [["Acme <Labs>", 500, 150, 250, 200, 150_000]],
            "entry": [],
            "entry_min": 10,
        }
    },
    "metros": {
        "Seattle": {
            "all": {
                "filings": 100,
                "new_hire": 30,
                "level_12": 50,
                "level_34": 40,
                "median_wage": 155_000,
                "employers": 5,
                "companies": 1,
                "top": [["Acme <Labs>", 90, 30, 45, 40, 160_000]],
            },
            "fields": {
                "swe": {
                    "filings": 90,
                    "new_hire": 30,
                    "level_12": 45,
                    "level_34": 40,
                    "median_wage": 160_000,
                    "employers": 4,
                    "companies": 1,
                    "top": [["Acme <Labs>", 90, 30, 45, 40, 160_000]],
                },
                # Filings here, but none by a tracked company
                "design": {
                    "filings": 3,
                    "new_hire": 1,
                    "level_12": 2,
                    "level_34": 1,
                    "median_wage": 90_000,
                    "employers": 2,
                    "companies": 0,
                    "top": [],
                },
            },
        }
    },
}

EVIDENCE = {
    "filings": 500,
    "new_hire_filings": 150,
    "level_1": 100,
    "level_2": 150,
    "level_3": 150,
    "level_4": 50,
    "median_wage": 150_000,
    "last_decision": "2026-06-30",
}


def job(
    title, level="entry", role="swe", metros=("Seattle",), company="Acme <Labs>", **kw
):
    return {
        "system": "greenhouse",
        "slug": "acme",
        "job_id": title,
        "company": company,
        "title": title,
        "url": "https://example.com/" + title,
        "location": "Seattle, WA",
        "is_remote": False,
        "role": role,
        "level": level,
        "min_years": None,
        "posted_at": "2026-10-10T00:00:00+00:00",
        "first_seen_at": "2026-10-10T00:00:00+00:00",
        "salary_min": None,
        "salary_max": None,
        "states": ["WA"],
        "metros": list(metros),
    } | kw


@pytest.fixture
def setup(tmp_path, monkeypatch):
    sheet = tmp_path / "companies.csv"
    sheet.write_text(SHEET)
    monkeypatch.setattr(pages, "WATCHLIST", sheet)
    stats = tmp_path / "browse_stats.json"
    stats.write_text(json.dumps(STATS))
    monkeypatch.setattr(pages_browse, "STATS", stats)
    return stats


def context(jobs):
    companies = {
        "Acme <Labs>": {"tech_filings": 90, "evidence": {"swe": EVIDENCE}},
        "Beta": {"tech_filings": 40, "evidence": {}},  # nothing for software
    }
    return Context(jobs=jobs, companies=companies, now=NOW, updated=NOW.isoformat())


def by_path(ctx):
    return {p.path: p for p in pages_browse.pages(ctx)}


def text(p):
    return pages.render(p, "2026-10-11 12:00")


def test_the_page_set_is_the_same_with_or_without_jobs(setup):
    empty = by_path(context([]))
    full = by_path(
        context([job("Software Engineer"), job("SWE Intern", level="intern")])
    )
    assert set(empty) == set(full)
    n = len(FIELDS)
    assert len(empty) == 1 + 1 + n * 3 + n * len(METROS) + len(METROS) + 1 + n
    for path in (
        "jobs/",
        "jobs/new-grad/",
        "jobs/software-engineering/",
        "jobs/software-engineering/new-grad/",
        "jobs/cybersecurity/internships/",
        "jobs/ai-ml/bay-area/",
        "jobs/data/raleigh-durham/",
        "jobs/in/washington-dc/",
        "rankings/",
        "rankings/product-management/",
    ):
        assert path in empty
    assert {p.group for p in empty.values()} == {"browse", "rankings"}
    assert all(p.css == ["pages-browse.css"] for p in empty.values())


def test_titles_and_descriptions_fit(setup):
    for p in pages_browse.pages(context([job("Software Engineer")])):
        assert len(p.title) <= 60, p.title
        assert len(p.description) <= 160, p.description


def test_field_city_page_lists_jobs_sponsors_and_board_link(setup):
    ctx = context(
        [
            job("Software Engineer"),
            job("Senior Engineer", level="senior"),
            job("Elsewhere", metros=("Austin",)),
            job("Not filed", company="Beta"),  # no software filings: the board hides it
        ]
    )
    out = text(by_path(ctx)["jobs/software-engineering/seattle/"])
    assert "Software Engineer</a>" in out and "Senior Engineer</a>" in out
    assert "Elsewhere" not in out and "Not filed" not in out
    assert "2 software engineering jobs in Seattle" in out
    assert "90 certified LCAs" in out and "October 2024 to June 2026" in out
    board = pages.app_link(
        f="swe", l="intern,entry,early,mid,senior", age="30", loc="m:Seattle"
    )
    assert f'href="{pages.esc(board)}"' in out
    assert 'href="/companies/acme-labs/"' in out  # sponsors link to company pages
    assert '"@type":"ItemList"' in out and "JobPosting" not in out
    assert "Bellevue" in out  # which places count as Seattle


def test_new_grad_links_use_the_boards_default_levels(setup):
    b = pages_browse.Browse(context([]), STATS)
    assert b.board("swe", levels=pages_browse.NEW_GRAD) == "/?f=swe&age=30"
    assert b.board("ai", levels=("intern",)) == "/?f=ai&l=intern&age=30"


def test_levels_follow_the_board(setup):
    unclear = job("Engineer", level="unclear")
    assert pages_browse.bucket(unclear) == "early"
    assert pages_browse.bucket(unclear | {"min_years": 3}) == "mid"
    assert pages_browse.bucket(unclear | {"min_years": 5}) == "senior"
    ctx = context([unclear, job("Staff", level="senior")])
    out = text(by_path(ctx)["jobs/software-engineering/new-grad/"])
    assert "Engineer</a>" in out and "Staff</a>" not in out


def test_empty_pages_say_so_and_keep_sponsors_and_links(setup):
    pages_ = by_path(context([]))
    out = text(pages_["jobs/software-engineering/seattle/"])
    assert "No software engineering jobs at H-1B sponsors in Seattle" in out
    assert "Open the job board with these filters" in out
    assert "Acme &lt;Labs&gt;" in out  # sponsors still shown
    assert 'href="/jobs/software-engineering/austin/"' in out
    # Filings in the city, none by a tracked company: say so, show national sponsors
    design = text(pages_["jobs/design/seattle/"])
    assert "None of the companies FiledFor tracks filed design LCAs" in design
    # No filings at all for a city and field
    assert "No certified LCAs for AI/ML roles" in text(pages_["jobs/ai-ml/miami/"])


def test_every_value_is_escaped(setup):
    nasty = job('<script>alert("x")</script>', company="Acme <Labs>")
    for p in pages_browse.pages(context([nasty])):
        # JSON-LD carries the raw text, and the layer stops it closing its tag
        out = re.sub(r'<script type="application/ld\+json">.*?</script>', "", text(p))
        assert "<script>alert" not in out
        assert 'alert(\\"x\\")</script>' not in text(p)
        assert "Acme <Labs>" not in out


def test_rankings_show_filings_shares_and_open_jobs(setup):
    out = text(by_path(context([job("Software Engineer")]))["rankings/"])
    assert "Top 2 of 2 companies" in out
    assert "55%" in out and "90%" in out  # level I-II: 300 of 550, 90 of 100
    assert "33%" in out and "$160K" in out  # new hires: 200 of 600
    assert "at least 50" in out
    assert "not a visa approval or a hire" in out


def test_pages_build_without_the_stats_file(setup, monkeypatch, tmp_path):
    monkeypatch.setattr(pages_browse, "STATS", tmp_path / "missing.json")
    ps = by_path(context([job("Software Engineer")]))
    assert len(ps) == len(by_path(context([])))
    out = text(ps["jobs/software-engineering/seattle/"])
    assert "Software Engineer</a>" in out
    assert "Acme &lt;Labs&gt;" in out  # sponsors from the board's own evidence
    assert "500" in text(ps["rankings/software-engineering/"])


def test_no_em_dashes_anywhere(setup):
    for p in pages_browse.pages(context([job("Software Engineer")])):
        assert "\u2014" not in text(p)
