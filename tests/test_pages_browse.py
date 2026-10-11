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
    "ashby,slack,0,Slack,4,11-1111111,5,\n"  # files under Acme's FEIN
)


# --- browse_stats --------------------------------------------------------------


def lca(tmp_path, rows):
    """A tiny clean.parquet with the columns browse_stats reads. Each row is
    'decision|fein|employer|title|soc|level|wage|city|state|new hire'."""
    con = duckdb.connect()
    con.execute(
        "create table t (case_number varchar, decision_date varchar, employer_fein varchar,"
        " employer_name varchar, job_title varchar, soc_code varchar, wage_level varchar,"
        " wage_yearly double, worksite_city varchar, worksite_state varchar,"
        " is_new_hire boolean)"
    )
    parsed = []
    for i, r in enumerate(rows):
        d, fein, name, title, soc, level, wage, city, state, new = r.split("|")
        parsed.append(
            (
                f"I-{i}",
                d,
                fein,
                name,
                title,
                soc,
                level,
                float(wage),
                city,
                state,
                new == "y",
            )
        )
    con.executemany("insert into t values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", parsed)
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


def test_readable_names_only_change_case():
    assert browse_stats.readable("AMAZON.COM SERVICES LLC") == "Amazon.com Services LLC"
    assert browse_stats.readable("KPMG  LLP") == "KPMG LLP"
    assert (
        browse_stats.readable("WAL-MART ASSOCIATES, INC.")
        == "Wal-Mart Associates, Inc."
    )
    assert browse_stats.readable("Google LLC") == "Google LLC"  # mixed case: as filed


@pytest.mark.parametrize(
    "filed, shown",
    [
        ("TECH MAHINDRA (AMERICAS), INC", "Tech Mahindra (Americas), Inc"),
        ("UT-BATTELLE, LLC (OAK RIDGE)", "Ut-Battelle, LLC (Oak Ridge)"),
        ("FCA US LLC", "FCA US LLC"),
        ("GE ENERGY", "GE Energy"),
        ("UL SERVICES GROUP LLC", "UL Services Group LLC"),
        ("ASM AMERICA INC.", "ASM America Inc."),
        (
            "PRICEWATERHOUSECOOPERS ADVISORY SERVICES LLC",
            "PricewaterhouseCoopers Advisory Services LLC",
        ),
        ("MONGODB, INC.", "MongoDB, Inc."),
        ("BURNS & MCDONNELL ENGINEERING CO", "Burns & McDonnell Engineering Co"),
        ("DIRECTV, LLC", "DIRECTV, LLC"),
        (
            "TRUSTEES OF THE UNIVERSITY OF PENNSYLVANIA",
            "Trustees of the University of Pennsylvania",
        ),
        ("THE HOME DEPOT", "The Home Depot"),
        ("ROYAL CARIBBEAN CRUISES LTD.", "Royal Caribbean Cruises LTD."),
        ("ACME PVT LTD", "Acme PVT LTD"),
        ("DECOVITA-USA INC", "Decovita-USA Inc"),
        ("IBM CORPORATION", "IBM Corporation"),
        ("AT&T SERVICES, INC.", "AT&T Services, Inc."),
        ("SAP AMERICA, INC.", "SAP America, Inc."),
        ("KPMG LLP", "KPMG LLP"),
        ("ERNST & YOUNG U.S. LLP", "Ernst & Young U.S. LLP"),
        ("EY LLP", "EY LLP"),
        ("3M COMPANY", "3M Company"),
        (
            "INTERNATIONAL BUSINESS MACHINES CORP",
            "International Business Machines Corp",
        ),
    ],
)
def test_all_capital_names_get_readable_case(filed, shown):
    assert browse_stats.readable(filed) == shown


def test_a_name_filed_in_mixed_case_is_used_as_filed():
    pick = browse_stats.pick_name
    # The same name in mixed case, even if less common, wins over converting
    royal = [("ROYAL CARIBBEAN CRUISES LTD.", 9), ("Royal Caribbean Cruises Ltd.", 2)]
    assert pick(royal) == "Royal Caribbean Cruises Ltd."
    # A different name that happens to be mixed case does not
    infosys = [("INFOSYS LIMITED", 50), ("Infosys Nova Holdings LLC", 3)]
    assert pick(infosys) == "Infosys Limited"
    # Characters lost in the export are skipped
    assert pick([("ACME INC", 5), ("Acme Inc\u00bf", 9)]) == "Acme Inc"


def test_stats_on_a_tiny_fixture(tmp_path):
    sheet = tmp_path / "companies.csv"
    sheet.write_text(SHEET)
    rows = [
        "2024-10-01 00:00:00|11-1111111|ACME LABS INC|Software Engineer|15-1252.00|I|100000|Seattle|WA|y",
        "2025-01-05 00:00:00|11-1111111|ACME LABS INC|Software Engineer|15-1252.00|II|120000|Redmond|WA|n",
        "2025-03-01 00:00:00|11-1111111|ACME LABS INC|ML Engineer|15-1252.00|III|200000|Seattle|WA|y",
        "2025-06-01 00:00:00|33-3333333|Beta Corp|Software Engineer|15-1252.00|IV|300000|Omaha|NE|n",
        "2026-06-30 00:00:00|99-9999999|BIGCO INC|Software Developer|15-1252.00|Unknown|5|Seattle|WA|n",
        "2026-01-02 00:00:00|99-9999999|BIGCO INC|Software Developer|15-1252.00|I|90000|Seattle|WA|n",
        "2026-01-03 00:00:00|99-9999999|Bigco, Inc.|Software Developer|15-1252.00|II|95000|Austin|TX|y",
        "2025-02-01 00:00:00|99-9999999|BIGCO INC|Nurse|29-1141.00|I|80000|Seattle|WA|y",
    ]
    stats = browse_stats.compute(lca(tmp_path, rows), sheet)
    assert stats["period"] == {"from": "2024-10-01", "to": "2026-06-30"}
    assert stats["all"]["filings"] == 7  # the nurse is not tech
    swe = stats["fields"]["swe"]
    assert swe["filings"] == 6 and swe["employers"] == 3 and swe["companies"] == 2
    assert swe["ranked"] == 3
    assert (swe["new_hire"], swe["level_12"], swe["level_34"]) == (2, 4, 1)
    # Every employer is ranked; untracked ones by their most common name,
    # as filed in mixed case when it ever was
    assert swe["top"][0] == ["Bigco, Inc.", 3, 1, 2, 0, 92_500, 0]
    assert swe["top"][1] == ["Acme <Labs>", 2, 1, 2, 0, 110_000, 1]
    assert stats["fields"]["ai"]["top"] == [["Acme <Labs>", 1, 1, 0, 1, 200_000, 1]]
    seattle = stats["metros"]["Seattle"]
    assert seattle["all"]["filings"] == 5
    assert seattle["fields"]["swe"]["filings"] == 4
    assert [r[0] for r in seattle["fields"]["swe"]["top"]] == [
        "Acme <Labs>",
        "Bigco, Inc.",
    ]
    assert "Omaha" not in json.dumps(stats)
    assert stats["placed_share"] == round(6 / 7, 4)


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
            ["Bigco <Inc>", 900, 100, 400, 400, 170_000, 0],
            ["Acme <Labs>", 600, 200, 300, 250, 160_000, 1],
            ["Beta", 100, 10, 90, 10, 90_000, 1],
        ],
        "entry": [["Beta", 100, 10, 90, 10, 90_000, 1]],
        "entry_min": 50,
        "ranked": 40,
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
            "top": [["Acme <Labs>", 500, 150, 250, 200, 150_000, 1]],
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
                "top": [["Acme <Labs>", 90, 30, 45, 40, 160_000, 1]],
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
                    "top": [["Acme <Labs>", 90, 30, 45, 40, 160_000, 1]],
                },
                # Too few filings, and only by an employer FiledFor doesn't track
                "design": {
                    "filings": 3,
                    "new_hire": 1,
                    "level_12": 2,
                    "level_34": 1,
                    "median_wage": 90_000,
                    "employers": 2,
                    "companies": 0,
                    "top": [["Bigco <Inc>", 3, 1, 2, 1, 90_000, 0]],
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
        "Slack": {"tech_filings": 90, "evidence": {"swe": EVIDENCE}},
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
    assert (
        "90 LCAs for software engineering roles at Seattle-area worksites were certified "
        "between October 2024 and June 2026"
    ) in out
    assert "including some the employer later withdrew" in out
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
    # An employer FiledFor doesn't track: plain text, no open-jobs count
    design = text(pages_["jobs/design/seattle/"])
    assert "Bigco &lt;Inc&gt;</td>" in design and "not tracked" in design
    # No filings at all for a city and field
    assert (
        "No LCAs for AI/ML roles at Miami-area worksites were certified between"
        in text(pages_["jobs/ai-ml/miami/"])
    )


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
    assert "Top 3 of 40 employers" in out
    assert "appears once for each" in out
    assert "<td>Bigco &lt;Inc&gt;</td>" in out  # untracked: no link
    assert 'href="/companies/acme-labs/"' in out
    assert "55%" in out and "90%" in out  # level I-II: 300 of 550, 90 of 100
    assert "33%" in out and "$160K" in out  # new hires: 200 of 600
    assert "at least 50" in out
    assert "not a visa approval or a hire" in out


def test_thin_city_pages_are_noindex(setup):
    ps = by_path(context([job("Designer", role="design", company="Acme <Labs>")]))
    # 3 filings, no open design jobs (Acme has no design filings, so it's hidden)
    assert ps["jobs/design/seattle/"].noindex
    assert not ps["jobs/software-engineering/seattle/"].noindex  # 90 filings
    with_job = by_path(context([job("Engineer", metros=("Miami",))]))
    assert not with_job["jobs/software-engineering/miami/"].noindex  # an open job
    assert with_job["jobs/ai-ml/miami/"].noindex  # nothing at all
    assert not any(p.noindex for p in ps.values() if p.path.count("/") != 3)
    assert '<meta name="robots" content="noindex' in text(ps["jobs/design/seattle/"])


def test_pages_build_without_the_stats_file(setup, monkeypatch, tmp_path):
    monkeypatch.setattr(pages_browse, "STATS", tmp_path / "missing.json")
    ps = by_path(context([job("Software Engineer")]))
    assert len(ps) == len(by_path(context([])))
    out = text(ps["jobs/software-engineering/seattle/"])
    assert "Software Engineer</a>" in out
    assert "Acme &lt;Labs&gt;" in out  # sponsors from the board's own evidence
    ranking = text(ps["rankings/software-engineering/"])
    assert "500" in ranking and "Every employer" not in ranking
    # Slack files under Acme's FEIN: its filings are not counted a second time
    assert "Slack" not in ranking


def test_no_em_dashes_anywhere(setup):
    for p in pages_browse.pages(context([job("Software Engineer")])):
        assert "\u2014" not in text(p)
