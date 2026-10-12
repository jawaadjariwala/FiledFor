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
    "greenhouse,acme,0,Acme,1,11-1;22-2,50,\n"
    "lever,acme2,0,Acme,1,11-1,40,\n"  # second board, same company
    "ashby,beta,0,Beta Corp,1,33-3,12,\n"
    'ashby,evil,0,<Evil & "Co">,1,44-4,1,\n'
    "ashby,nofilings,0,No Filings,1,,0,\n"
    # Boards of one company with different FEINs: the watchlist keeps the
    # larger count (8), the pages count both (16)
    "greenhouse,split,0,Split,1,55-5,8,\n"
    "lever,split,0,Split,1,66-6,8,\n"
    "ashby,junk,0,Split,1,77-7,8,\n"  # excluded: not an employer board
)


def write_clean(path, rows):
    """rows: (fein, decision date, is_tech, soc code, title, wage, city, state)."""
    values = ", ".join(
        f"('{f}', date '{d}', {t}, '{soc}', '{title}', {w if w else 'null'}, '{c}', '{s}')"
        for f, d, t, soc, title, w, c, s in rows
    )
    duckdb.sql(
        f"copy (select * from (values {values}) v(employer_fein, decision_date, is_tech, "
        f"soc_code, job_title, wage_yearly, worksite_city, worksite_state)) "
        f"to '{path}' (format parquet)"
    )


@pytest.fixture
def sheet(tmp_path):
    p = tmp_path / "companies.csv"
    p.write_text(WATCHLIST)
    return p


@pytest.fixture
def excluded(tmp_path):
    p = tmp_path / "excluded.csv"
    p.write_text("system,slug,reason\nashby,junk,Not an employer\n")
    return p


# profiles.py

SWE, DS = "15-1252.00", "15-2051.00"


def test_profiles_count_quarters_titles_cities_and_medians(tmp_path, sheet, excluded):
    clean = tmp_path / "clean.parquet"
    write_clean(
        clean,
        [
            # FY2025 Q1 (October to December 2024)
            (
                "11-1",
                "2024-10-03",
                True,
                SWE,
                "Software Engineer",
                150000,
                "SEATTLE",
                "WA",
            ),
            (
                "22-2",
                "2024-12-20",
                True,
                SWE,
                "SOFTWARE ENGINEER",
                170000,
                "Seattle",
                "WA",
            ),
            (
                "11-1",
                "2024-11-02",
                True,
                SWE,
                "Software Engineer ",
                5000,
                "Seattle",
                "WA",
            ),
            # FY2025 Q2 has no filings from anyone: a gap in the files
            # FY2025 Q3 (April to June 2025)
            ("11-1", "2025-05-01", True, DS, "Data Scientist", 120000, "Austin", "TX"),
            (
                "11-1",
                "2025-05-02",
                False,
                "13-2011.00",
                "Accountant",
                80000,
                "Austin",
                "TX",
            ),
            ("33-3", "2025-06-30", True, SWE, "Engineer", None, "McLean", "VA"),
            ("99-9", "2025-04-15", True, SWE, "Not on the watchlist", 1, "X", "CA"),
            ("77-7", "2025-04-15", True, SWE, "Excluded board", 1, "X", "CA"),
        ],
    )
    out = profiles.build(clean, sheet, excluded)
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
    # Exact medians over all of the company's filings, across FEINs
    assert acme["wage"] == 150000
    assert acme["field_wages"] == {"swe": 160000, "ai": 150000, "data": 150000}
    beta = out["companies"]["Beta Corp"]
    assert beta["titles"] == [["Engineer", 1, None]]
    assert beta["cities"] == [["McLean", "VA", 1]]
    assert beta["wage"] is None and "field_wages" not in beta
    assert "No Filings" not in out["companies"]
    assert EVIL not in out["companies"]  # no filings in the data
    assert "Split" not in out["companies"]  # the excluded board's FEIN is ignored


def test_profiles_written_one_company_per_line(tmp_path):
    data = {
        "meta": {"first": "2024-10-01"},
        "companies": {"B": {"wage": 1}, "A": {"quarters": [1, None]}},
    }
    path = tmp_path / "p.json"
    profiles.write(data, path)
    lines = path.read_text().splitlines()
    assert lines[2].startswith('"A":') and lines[3].startswith('"B":')
    assert profiles.load(path) == data


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
def site(tmp_path, sheet, excluded, monkeypatch):
    """Sources on disk; tests remove what they need gone."""
    sponsors = tmp_path / "sponsors.parquet"
    duckdb.sql(f"""
        copy (select * from (values
            ('11-1', 40, 20, 10, 10, 10, 10, 150000.0, '2024-10-01', '2026-06-30'),
            ('22-2', 10, 10, 10, 0, 0, 0, 100000.0, '2024-10-01', '2026-06-30'),
            ('33-3', 12, 0, 0, 0, 12, 0, 98000.0, '2025-01-01', '2025-02-01'),
            ('55-5', 8, 8, 8, 0, 0, 0, 90000.0, '2025-01-01', '2025-02-01'),
            ('66-6', 8, 8, 8, 0, 0, 0, 90000.0, '2025-01-01', '2025-02-01'),
            ('77-7', 500, 0, 0, 0, 0, 0, 90000.0, '2025-01-01', '2025-02-01')
        ) v(employer_fein, tech_filings, tech_new_hire_filings, tech_level_1,
            tech_level_2, tech_level_3, tech_level_4, tech_median_wage,
            first_decision, last_decision)) to '{sponsors}' (format parquet)
    """)
    evidence = tmp_path / "role_evidence.parquet"
    duckdb.sql(f"""
        copy (select * from (values
            ('33-3', 'swe', 12, 0, 0, 0, 12, 0, 99000.0, date '2025-02-01')
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
                        "quarters": [40, None, 10],
                        "titles": [["<b>Engineer</b>", 50, 150000]],
                        "cities": [["Seattle", "WA", 50]],
                        "first": "2024-10-03",
                        "last": "2025-05-01",
                        "wage": 141000,
                        "field_wages": {"swe": 133000},
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
    monkeypatch.setattr(pages_company, "EXCLUDED", excluded)
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
    "tech_filings": 50,
    "evidence": {
        "swe": {
            "filings": 12,
            "new_hire_filings": 3,
            "level_1": 3,
            "level_2": 3,
            "level_3": 6,
            "level_4": 0,
            "median_wage": 140000,  # per-FEIN medians averaged: not shown
            "last_decision": "2026-06-30",
        },
        "data": {
            "filings": 4,
            "new_hire_filings": 1,
            "level_1": 4,
            "level_2": 0,
            "level_3": 0,
            "level_4": 0,
            "median_wage": 90000,
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
        "companies/split/",
    }
    index = out["companies/"]
    assert index.group == "companies" and index.crumbs == [("Companies", "companies/")]
    assert '<a href="#letter-a">A</a>' in index.body
    # The search box ships hidden and pages.js shows it, so no-JS visitors keep the plain list
    assert '<div class="cc-find" hidden>' in index.body and 'id="cc-q"' in index.body
    assert index.body.index('class="cc-find"') < index.body.index('class="cc-jump"')
    assert "&lt;Evil &amp; &quot;Co&quot;&gt;" in index.body
    assert "1 filing<" in index.body and "1 filings" not in index.body
    assert "16 filings<" in index.body  # the same count as Split's page


def test_company_page_with_jobs_profile_and_evidence(site):
    page = build([job("Acme", 1), job("Acme", 2)], {"Acme": ACME_INFO})[
        "companies/acme/"
    ]
    b = page.body
    assert page.title == "Acme H-1B Sponsorship and Tech Jobs | FiledFor"
    assert page.description == (
        "Acme filed 50 tech H-1B LCAs from Oct 2024 to Jun 2025, median wage $141K. "
        "See 2 open tech jobs, job titles and cities filed."
    )
    assert page.crumbs == [("Companies", "companies/"), ("Acme", "companies/acme/")]
    assert page.css == ["pages-company.css"] and page.group == "companies"
    assert "<h1>Acme: H-1B filings and open tech jobs</h1>" in b
    # Tiles: both FEINs summed, level share over known levels, exact median
    assert "<b>50</b>" in b
    assert "60%</b><span>for new H-1B employment (30 filings)" in b
    assert "60%</b><span>at wage level I or II" in b
    assert "$141K</b>" in b and "the median wage on them was $141K" in b
    assert "Engineer &lt;1&gt;" in b and "See all 2 on the job board" in b
    assert 'href="/?company=Acme"' in b
    # Field medians from the profile, never the averaged per-FEIN one; a
    # field under 10 filings shows no share or median
    assert (
        '<td>Software</td><td class="num">12</td><td class="num">3</td>'
        '<td class="num">50%</td><td class="num">$133K</td>'
    ) in b
    assert "$140K" not in b
    assert (
        '<td>Data</td><td class="num">4</td><td class="num">1</td><td class="num">n/a</td>'
        in b
    )
    assert "fewer than 10 filings in that field" in b
    assert 'class="qgap"' in b and "no data" in b  # the gap is drawn as a gap
    assert "&lt;b&gt;Engineer&lt;/b&gt;" in b and "<b>Engineer</b>" not in b
    assert "Seattle, WA" in b
    assert page.jsonld[0] == {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Acme",
        "url": "https://acme.com/",
    }
    assert chr(0x2014) not in b + page.title + page.description  # no em dashes


def test_without_a_profile_medians_show_only_for_one_fein(site, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES", site / "gone.json")
    out = build((), {"Acme": ACME_INFO})
    acme = out["companies/acme/"]
    # Two FEINs: a median can't be built from theirs, so none is shown
    assert "median" not in acme.description
    assert "Median wage" not in acme.body and "median yearly wage" not in acme.body
    assert "$" not in acme.body.split("Filings by field")[1].split("</table>")[0]
    # One FEIN: the stored median is exact
    beta = out["companies/beta-corp/"].body
    assert "$98K</b><span>median yearly wage" in beta
    assert '<td class="num">$99K</td>' in beta


def test_boards_with_different_feins_count_every_fein_once(site):
    b = build()["companies/split/"].body
    assert "Split filed 16 H-1B Labor Condition Applications" in b
    assert "100%</b><span>for new H-1B employment (16 filings)" in b
    assert "500" not in b  # the excluded board's FEIN


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
    # No profile: no history sections, no titles or cities promised, and the
    # JSON-LD has no invented url
    assert "Filings by quarter" not in b and "Job titles filed most" not in b
    assert "titles" not in page.description
    assert "url" not in page.jsonld[0]


def test_small_samples_show_only_the_count(site):
    b = build()["companies/evil-co/"].body
    assert "<b>1</b>" in b and "too small for shares or a median wage" in b
    assert "%" not in b.split('class="stats"')[1].split("</div></div>")[0]
    assert "median wage on them" not in b


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
    assert "Acme filed 50 H-1B Labor Condition Applications" in b  # the watchlist count
    assert "in the Department of Labor data FiledFor uses" in b
    assert "new H-1B employment" not in b.split("What these numbers mean")[0]
    assert "Filings by field" not in b
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
