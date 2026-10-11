"""Job pages: the description, the record and Google's job markup."""

import json
from datetime import UTC, datetime

from filedfor import pages, pages_job
from filedfor.pages import Context

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
JOB = {
    "system": "greenhouse",
    "slug": "acme",
    "job_id": "1",
    "company": "Acme <Labs>",
    "title": "Data Engineer & <Analyst>",
    "url": "https://example.com/job/1",
    "location": "Austin, TX",
    "is_remote": False,
    "role": "data",
    "level": "entry",
    "min_years": 1,
    "posted_at": "2026-10-10T12:00:00+00:00",
    "first_seen_at": "2026-10-10T12:00:00+00:00",
    "salary_min": 100000,
    "salary_max": 120000,
    "arrangement": "hybrid",
    "checked_at": "2026-10-11T11:30:00+00:00",
    "states": ["TX"],
    "metros": ["Austin"],
}
EV = {
    "filings": 120,
    "new_hire_filings": 30,
    "level_1": 10,
    "level_2": 20,
    "level_3": 20,
    "level_4": 10,
    "median_wage": 130000,
}


def ctx(description=None, evidence=EV):
    return Context(
        jobs=[JOB],
        companies={"Acme <Labs>": {"evidence": {"data": evidence}}},
        now=NOW,
        updated=NOW.isoformat(),
        descriptions={pages.job_key(JOB): description} if description else {},
    )


def test_page_with_description_carries_job_posting_markup():
    (page,) = pages_job.pages(ctx("<p>Build pipelines.</p>"))
    assert page.path.startswith("job/acme-labs-data-engineer-analyst-")
    html = pages.render(page, "now")
    assert "<p>Build pipelines.</p>" in html
    assert (
        "Data Engineer &amp; &lt;Analyst&gt;" in html
        and "<Analyst>" not in html.split("<script")[0]
    )
    ld = page.jsonld[0]
    assert (
        ld["@type"] == "JobPosting" and ld["baseSalary"]["value"]["minValue"] == 100000
    )
    assert ld["jobLocation"][0]["address"]["addressRegion"] == "TX"
    assert not page.noindex
    assert '"@type":"JobPosting"' in html


def test_page_without_description_links_out_and_stays_out_of_search():
    (page,) = pages_job.pages(ctx())
    assert page.noindex and page.jsonld == []
    assert "Read it and apply" in page.body


def test_record_with_no_filings_says_so():
    (page,) = pages_job.pages(ctx(evidence=None))
    assert "No H-1B filings found" in page.body


def test_remote_jobs_are_marked_telecommute():
    remote = {**JOB, "arrangement": "remote", "states": []}
    ld = pages_job.posting_ld(
        Context(jobs=[remote], companies={}, now=NOW, updated=""), remote, "<p>x</p>"
    )
    assert ld["jobLocationType"] == "TELECOMMUTE" and "jobLocation" not in ld
    json.dumps(ld)  # serialisable
