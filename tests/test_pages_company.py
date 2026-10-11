"""Company pages and their filing history (profiles.py), on tiny fixtures."""

import json
from datetime import UTC, datetime

import duckdb
import pytest

from filedfor import pages, pages_company, profiles, publish
from filedfor.pages import Context

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
EVIL = '<Evil & "Co">'
WATCHLIST = (
    "system,slug,eu,company,open_jobs,feins,tech_filings,us_filter\n"
    "greenhouse,acme,0,Acme,1,11-1;22-2,5,\n"
    "lever,acme2,0,Acme,1,11-1,4,\n"  # second board, same company
    "ashby,beta,0,Beta Corp,1,33-3,2,\n"
    'ashby,evil,0,<Evil & "Co">,1,44-4,1,\n'
    "ashby,nofilings,0,No Filings,1,,0,\n"
)


def write_clean(path, rows):
    """rows: (fein, decision date, is_tech, title, wage, city, state)."""
    values = ", ".join(
        f"('{f}', date '{d}', {t}, '{title}', {w if w else 'null'}, '{c}', '{s}')"
        for f, d, t, title, w, c, s in rows
    )
    duckdb.sql(
        f"copy (select * from (values {values}) v(employer_fein, decision_date, is_tech, "
        f"job_title, wage_yearly, worksite_city, worksite_state)) to '{path}' (format parquet)"
    )


@pytest.fixture
def sheet(tmp_path):
    p = tmp_path / "companies.csv"
    p.write_text(WATCHLIST)
    return p


# profiles.py


def test_profiles_count_quarters_titles_and_cities(tmp_path, sheet):
    clean = tmp_path / "clean.parquet"
    write_clean(
        clean,
        [
            # FY2025 Q1 (October to December 2024)
            ("11-1", "2024-10-03", True, "Software Engineer", 150000, "SEATTLE", "WA"),
            ("22-2", "2024-12-20", True, "SOFTWARE ENGINEER", 170000, "Seattle", "WA"),
            ("11-1", "2024-11-02", True, "Software Engineer ", 5000, "Seattle", "WA"),
            # FY2025 Q2 has no filings from anyone: a gap in the files
            # FY2025 Q3 (April to June 2025)
            ("11-1", "2025-05-01", True, "Data Scientist", 120000, "Austin", "TX"),
            ("11-1", "2025-05-02", False, "Accountant", 80000, "Austin", "TX"),
            ("33-3", "2025-06-30", True, "Engineer", None, "McLean", "VA"),
            ("99-9", "2025-04-15", True, "Not on the watchlist", 1, "X", "CA"),
        ],
    )
    out = profiles.build(clean, sheet)
    meta, acme = out["meta"], out["companies"]["Acme"]
    assert (meta["first"], meta["last"]) == ("2024-10-03", "2025-06-30")
    assert meta["quarters"] == [[2025, 1], [2025, 2], [2025, 3]]
    assert meta["missing"] == [[2025, 2]]
    # Both FEINs counted, non-tech left out, the gap is null and not zero
    assert acme["quarters"] == [3, None, 1]
    # Titles grouped ignoring case and spaces, the most common spelling
    # shown, and the out-of-range wage left out of the median
    assert acme["titles"][0] == ["Software Engineer", 3, 160000]
    assert acme["cities"][0] == ["Seattle", "WA", 3]
    assert (acme["first"], acme["last"]) == ("2024-10-03", "2025-05-01")
    beta = out["companies"]["Beta Corp"]
    assert beta["titles"] == [["Engineer", 1, None]]
    assert beta["cities"] == [["McLean", "VA", 1]]
    assert "No Filings" not in out["companies"]
    assert EVIL not in out["companies"]  # no filings in the data


def test_profiles_load_survives_a_missing_or_broken_file(tmp_path):
    assert profiles.load(tmp_path / "nope.json") == {"meta": {}, "companies": {}}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert profiles.load(bad) == {"meta": {}, "companies": {}}


def test_quarter_months_follow_the_federal_fiscal_year():
    assert pages_company.quarter_months(2026, 1) == "Oct to Dec 2025"
    assert pages_company.quarter_months(2026, 2) == "Jan to Mar 2026"
    assert pages_company.quarter_months(2025, 4) == "Jul to Sep 2025"


# Pages


@pytest.fixture
def site(tmp_path, sheet, monkeypatch):
    """Sources on disk for three companies; tests remove what they need gone."""
    sponsors = tmp_path / "sponsors.parquet"
    duckdb.sql(f"""
        copy (select * from (values
            ('11-1', 4, 2, 1, 1, 1, 1, 150000.0, '2024-10-01', '2026-06-30'),
            ('22-2', 1, 1, 1, 0, 0, 0, 100000.0, '2024-10-01', '2026-06-30'),
            ('33-3', 2, 0, 0, 0, 2, 0, null, '2025-01-01', '2025-02-01')
        ) v(employer_fein, tech_filings, tech_new_hire_filings, tech_level_1,
            tech_level_2, tech_level_3, tech_level_4, tech_median_wage,
            first_decision, last_decision)) to '{sponsors}' (format parquet)
    """)
    evidence = tmp_path / "role_evidence.parquet"
    duckdb.sql(f"""
        copy (select * from (values
            ('33-3', 'swe', 2, 0, 0, 0, 2, 0, null, date '2025-02-01')
        ) v(employer_fein, role, filings, new_hire_filings, level_1, level_2,
            level_3, level_4, median_wage, last_decision)) to '{evidence}' (format parquet)
    """)
    prof = tmp_path / "profiles.json"
    prof.write_text(
        json.dumps(
            {
                "meta": {
                    "first": "2024-10-01",
                    "last": "2025-06-30",
                    "quarters": [[2025, 1], [2025, 2], [2025, 3]],
                    "missing": [[2025, 2]],
                    "low_months": [],
                },
                "companies": {
                    "Acme": {
                        "quarters": [4, None, 1],
                        "titles": [["<b>Engineer</b>", 5, 150000]],
                        "cities": [["Seattle", "WA", 5]],
                        "first": "2024-10-03",
                        "last": "2025-05-01",
                    }
                },
            }
        )
    )
    domains = tmp_path / "domains.csv"
    domains.write_text("company,domain\nAcme,acme.com\n")
    notices = tmp_path / "notices.csv"
    notices.write_text(
        "company,notice,detail,date,source\n"
        'Beta Corp,PERM filings suspended,"DOL stopped <processing> them.",2026-10-08,https://example.org/n?a=1&b=2\n'
    )
    monkeypatch.setattr(pages, "WATCHLIST", sheet)
    monkeypatch.setattr(pages_company, "SPONSORS", sponsors)
    monkeypatch.setattr(pages_company, "ROLE_EVIDENCE", evidence)
    monkeypatch.setattr(pages_company, "EXCLUDED", tmp_path / "none.csv")
    monkeypatch.setattr(profiles, "PROFILES", prof)
    monkeypatch.setattr(publish, "DOMAINS", domains)
    monkeypatch.setattr(publish, "NOTICES", notices)
    return tmp_path


def job(company, n):
    return {
        "system": "greenhouse",
        "slug": "acme",
        "job_id": str(n),
        "company": company,
        "title": f"Engineer <{n}>",
        "location": "Seattle, WA",
        "posted_at": "2026-10-10T00:00:00+00:00",
        "first_seen_at": "2026-10-10T00:00:00+00:00",
    }


def build(jobs=(), companies=None):
    ctx = Context(
        jobs=list(jobs),
        companies=companies or {},
        now=NOW,
        updated=NOW.isoformat(),
    )
    return {p.path: p for p in pages_company.pages(ctx)}


ACME_INFO = {
    "tech_filings": 5,
    "evidence": {
        "swe": {
            "filings": 5,
            "new_hire_filings": 3,
            "level_1": 1,
            "level_2": 1,
            "level_3": 2,
            "level_4": 0,
            "median_wage": 140000,
            "last_decision": "2026-06-30",
        },
        "ai": None,
    },
    "careers": ["https://job-boards.greenhouse.io/acme"],
}


def test_every_company_with_filings_gets_a_page_and_the_index(site):
    out = build()
    assert set(out) == {
        "companies/",
        "companies/acme/",
        "companies/beta-corp/",
        "companies/evil-co/",
    }
    index = out["companies/"]
    assert index.group == "companies" and index.crumbs == [("Companies", "companies/")]
    assert '<a href="#letter-a">A</a>' in index.body
    assert "&lt;Evil &amp; &quot;Co&quot;&gt;" in index.body
    assert "1 filing<" in index.body and "1 filings" not in index.body


def test_company_page_with_jobs_profile_and_evidence(site):
    page = build([job("Acme", 1), job("Acme", 2)], {"Acme": ACME_INFO})[
        "companies/acme/"
    ]
    b = page.body
    assert page.title == "Acme H-1B Sponsorship and Tech Jobs | FiledFor"
    assert len(page.description) <= 160 and "5 tech H-1B LCAs" in page.description
    assert page.crumbs == [("Companies", "companies/"), ("Acme", "companies/acme/")]
    assert page.css == ["pages-company.css"] and page.group == "companies"
    assert "<h1>Acme: H-1B filings and open tech jobs</h1>" in b
    # Tiles: both FEINs summed, level share over known levels, weighted wage
    assert "60%</b><span>for new hires (3 filings)" in b
    assert "60%</b><span>at wage level I or II" in b
    assert "$140K</b>" in b  # (150K x 4 + 100K x 1) / 5
    assert "Engineer &lt;1&gt;" in b and "See all 2 on the job board" in b
    assert 'href="/?company=Acme"' in b
    # Filings by field from companies.json; history from the profile
    assert (
        '<td>Software</td><td class="num">5</td><td class="num">3</td><td class="num">50%</td>'
        in b
    )
    assert 'class="qgap"' in b and "no data" in b  # the gap is drawn as a gap
    assert "&lt;b&gt;Engineer&lt;/b&gt;" in b and "<b>Engineer</b>" not in b
    assert "Seattle, WA" in b
    org = page.jsonld[0]
    assert org == {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Acme",
        "url": "https://acme.com/",
    }
    assert "—" not in b + page.title + page.description  # no em dashes


def test_company_without_open_jobs_links_its_careers_page_and_notice(site):
    page = build()["companies/beta-corp/"]
    b = page.body
    assert "No open tech jobs at Beta Corp are listed" in b
    assert 'href="https://jobs.ashbyhq.com/beta"' in b
    assert "See all" not in b
    # Notice text, date and source exactly as stored, escaped
    assert "<strong>PERM filings suspended</strong> (2026-10-08)" in b
    assert "DOL stopped &lt;processing&gt; them." in b
    assert 'href="https://example.org/n?a=1&amp;b=2"' in b
    # Evidence from the index, since Beta Corp is not in companies.json
    assert "<td>Software</td>" in b
    # No profile: no history sections, and the JSON-LD has no invented url
    assert "Filings by quarter" not in b and "Job titles filed most" not in b
    assert "url" not in page.jsonld[0]


def test_hostile_names_are_escaped_everywhere(site):
    page = build([job(EVIL, 1)])["companies/evil-co/"]
    html = pages.render(page, "2026-10-11 12:00")
    assert EVIL not in html
    assert "&lt;Evil &amp; &quot;Co&quot;&gt;: H-1B filings" in page.body
    assert page.jsonld[0]["name"] == EVIL  # JSON-LD carries it as data


def test_pages_build_with_no_data_files_at_all(site, monkeypatch):
    monkeypatch.setattr(pages_company, "SPONSORS", site / "gone.parquet")
    monkeypatch.setattr(pages_company, "ROLE_EVIDENCE", site / "gone2.parquet")
    monkeypatch.setattr(profiles, "PROFILES", site / "gone.json")
    monkeypatch.setattr(publish, "DOMAINS", site / "gone.csv")
    monkeypatch.setattr(publish, "NOTICES", site / "gone2.csv")
    out = build()
    b = out["companies/acme/"].body
    assert "Acme filed 5 H-1B Labor Condition Applications" in b
    assert "in the Department of Labor data FiledFor uses" in b
    assert "for new hires" not in b and "Filings by field" not in b
    assert "Filings by quarter" not in b and "Similar sponsors" not in b
    assert "What these numbers mean" in b


def test_similar_sponsors_link_other_company_pages(site):
    b = build((), {"Acme": ACME_INFO})["companies/acme/"].body
    assert "Similar sponsors" in b
    assert 'href="/companies/beta-corp/"' in b
    assert 'href="/companies/acme/"' not in b.split("Similar sponsors")[1]


def test_title_stays_short_for_long_names():
    t = pages_company.title_for("International Business Machines Corporation")
    assert len(t) <= 60 and t.startswith("International Business Machines")
