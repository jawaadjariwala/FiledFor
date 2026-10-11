"""A page per listed job: the full description, the company's sponsor record
for that kind of role, more jobs at the company and similar jobs elsewhere.

Pages with a description carry schema.org JobPosting data, so Google's job
search can show them. The Apply button always goes to the employer's own
posting; FiledFor never takes applications.
"""

import json

from filedfor.pages import (
    FIELD_NAMES,
    SITE_URL,
    Context,
    Page,
    ago,
    company_link,
    esc,
    job_key,
    job_path,
    money,
)

CSS = ["pages-job.css"]
SETUP = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}
MORE = 5


def bucket(j: dict) -> str:
    """The board's level buckets: a title without a level is placed by the
    years the description asks for."""
    if j["level"] != "unclear":
        return j["level"]
    y = j.get("min_years")
    return "early" if y is None or y <= 2 else ("senior" if y >= 5 else "mid")


LEVEL_NAMES = {
    "intern": "Internship",
    "entry": "New grad",
    "early": "Not stated in the title",
    "mid": "Mid level",
    "senior": "Senior",
}


def setup_of(j: dict) -> str | None:
    return j.get("arrangement") or ("remote" if j.get("is_remote") else None)


def pay(j: dict) -> str | None:
    lo, hi = j.get("salary_min"), j.get("salary_max")
    if not lo:
        return None
    return money(lo) if lo == hi else f"{money(lo)} to {money(hi)}"


def when(j: dict) -> str:
    return j.get("posted_at") or j["first_seen_at"]


def fact(label: str, value: str | None) -> str:
    return f"<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>" if value else ""


def logo(ctx: Context, name: str, cls: str = "xl") -> str:
    src = ctx.companies.get(name, {}).get("logo")
    letters = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    img = (
        f'<img src="/{esc(src)}" alt="" width="64" height="64">'
        if src
        else esc(letters)
    )
    return f'<span class="logo {cls}" aria-hidden="true">{img}</span>'


def level_bar(ev: dict) -> str:
    levels = [ev.get(f"level_{i}") or 0 for i in range(1, 5)]
    total = sum(levels)
    if not total:
        return ""
    label = ", ".join(
        f"level {n} {c}" for n, c in zip(("I", "II", "III", "IV"), levels, strict=True)
    )
    segs = "".join(
        f'<span class="seg l{i + 1}" style="flex:{c}"></span>'
        for i, c in enumerate(levels)
        if c
    )
    key = "".join(
        f'<span><i class="sw l{i + 1}"></i>{n}</span>'
        for i, n in enumerate(("I", "II", "III", "IV"))
    )
    return f'<div class="bar" role="img" aria-label="Filings by wage level: {esc(label)}">{segs}</div><div class="key">{key}<span>Wage levels, I is the entry end</span></div>'


def record(ctx: Context, j: dict) -> str:
    c = ctx.companies.get(j["company"], {})
    field = FIELD_NAMES[j["role"]]
    ev = (c.get("evidence") or {}).get(j["role"])
    page = ctx.company_url(j["company"])
    more = (
        f'<a href="{page}">{esc(j["company"])}\'s full sponsor page</a>' if page else ""
    )
    if not ev:
        return (
            '<section class="record none" aria-labelledby="rec"><div class="record-head">'
            f'<h2 id="rec">Sponsor record</h2><span>{esc(j["company"])}, {esc(field.lower())} roles</span></div>'
            '<div class="record-grid"><p>No H-1B filings found for this kind of role under the names we matched. '
            "The company may file under a different legal name, so this isn't proof it doesn't sponsor.</p></div>"
            f'<div class="record-foot">{more}</div></section>'
        )
    levels = sum(ev.get(f"level_{i}") or 0 for i in range(1, 5))
    share = (
        round(((ev.get("level_1") or 0) + (ev.get("level_2") or 0)) * 100 / levels)
        if levels
        else None
    )
    cells = [
        ("Certified filings", f"{ev['filings']:,}"),
        ("New H-1B employment", f"{ev['new_hire_filings']:,}"),
        ("At entry wage levels", f"{share}%" if share is not None else None),
        ("Median wage filed", money(ev.get("median_wage")) or None),
    ]
    grid = "".join(
        f"<div><dt>{esc(a)}</dt><dd>{esc(b)}</dd></div>" for a, b in cells if b
    )
    return (
        '<section class="record" aria-labelledby="rec"><div class="record-head">'
        f'<h2 id="rec">Sponsor record</h2><span>{esc(j["company"])}, {esc(field.lower())} roles, Oct 2024 to Jun 2026</span></div>'
        f'<dl class="record-grid">{grid}</dl><div class="record-foot">{level_bar(ev)}'
        "<p>Filings are applications the employer made with the Department of Labor before hiring "
        "or keeping someone on H-1B in this kind of role. They show what the company did before, "
        f"not a promise for this job.</p>{more}</div></section>"
    )


def mini(ctx: Context, jobs: list[dict], with_company: bool) -> str:
    items = []
    for x in jobs:
        sub = [x["company"]] if with_company else []
        sub += [
            x.get("location") or "Location not listed",
            f"posted {ago(when(x), ctx.now)}",
        ]
        items.append(
            f'<li><a href="/{job_path(x)}"><strong>{esc(x["title"])}</strong>'
            f"<span>{esc(', '.join(sub))}</span></a></li>"
        )
    return f'<ul class="mini-jobs">{"".join(items)}</ul>'


def posting_ld(ctx: Context, j: dict, description: str) -> dict:
    """schema.org JobPosting, with the fields Google's job search needs."""
    c = ctx.companies.get(j["company"], {})
    org = {"@type": "Organization", "name": j["company"]}
    if c.get("logo"):
        org["logo"] = SITE_URL + c["logo"]
    ld = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": j["title"],
        "description": description,
        "datePosted": when(j)[:10],
        "hiringOrganization": org,
        "directApply": False,
        "url": SITE_URL + job_path(j),
        "identifier": {
            "@type": "PropertyValue",
            "name": j["company"],
            "value": j["job_id"],
        },
    }
    if setup_of(j) == "remote":
        ld["jobLocationType"] = "TELECOMMUTE"
        ld["applicantLocationRequirements"] = {"@type": "Country", "name": "US"}
    places = [
        {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressRegion": s,
                "addressCountry": "US",
            },
        }
        for s in j.get("states") or []
    ]
    if places or setup_of(j) != "remote":
        ld["jobLocation"] = places or {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressCountry": "US"},
        }
    if j.get("salary_min"):
        ld["baseSalary"] = {
            "@type": "MonetaryAmount",
            "currency": "USD",
            "value": {
                "@type": "QuantitativeValue",
                "minValue": j["salary_min"],
                "maxValue": j["salary_max"],
                "unitText": "YEAR",
            },
        }
    return ld


def job_page(ctx: Context, j: dict, similar: list[dict]) -> Page:
    k = job_key(j)
    company = j["company"]
    field = FIELD_NAMES[j["role"]]
    description = ctx.descriptions.get(k)
    c = ctx.companies.get(company, {})
    notice = c.get("notice")
    others = sorted(
        (x for x in ctx.jobs_at(company) if job_key(x) != k), key=when, reverse=True
    )[:MORE]
    years = j.get("min_years")
    facts = "".join(
        [
            fact("Pay", f"{pay(j)} a year" if pay(j) else "Not in the posting"),
            fact("Work setup", SETUP.get(setup_of(j) or "", "Not stated")),
            fact("Level", LEVEL_NAMES[bucket(j)]),
            fact(
                "Experience asked",
                None
                if years is None
                else ("None" if years == 0 else f"{years}+ years"),
            ),
            fact("Field", field),
            fact("Posted", ago(when(j), ctx.now)),
            fact(
                "Last checked",
                f"{ago(j['checked_at'], ctx.now)}, still open"
                if j.get("checked_at")
                else None,
            ),
        ]
    )
    snap = json.dumps(
        {
            "t": j["title"],
            "c": company,
            "u": j["url"],
            "l": j.get("location") or "",
            "p": when(j),
        }
    )
    actions = (
        f'<div class="jobhead-actions" data-k="{esc(k)}" data-snap="{esc(snap)}">'
        f'<a class="btn primary" href="{esc(j["url"])}" target="_blank" rel="noopener" data-goatcounter-click="apply-jobpage">Apply</a>'
        '<button type="button" class="btn" data-act="save" aria-pressed="false">Save</button>'
        '<button type="button" class="btn" data-act="applied" aria-pressed="false">Mark applied</button></div>'
    )
    notice_html = (
        f'<div class="notice-box" role="note"><strong>{esc(notice["notice"])}</strong> ({esc(notice["date"])}). '
        f'{esc(notice["detail"])} <a href="{esc(notice["source"])}" target="_blank" rel="noopener">Source</a></div>'
        if notice and notice.get("source", "").startswith("http")
        else ""
    )
    if description:
        article = (
            f'<article class="jd" aria-label="Job description">{description}'
            f'<p class="jd-source">From the posting on {esc(company)}\'s job site. '
            f'<a href="{esc(j["url"])}" target="_blank" rel="noopener">Read the original</a> before you apply.</p></article>'
        )
    else:
        article = (
            '<article class="jd jd-missing" aria-label="Job description">'
            f"<p>The full description is on {esc(company)}'s job site.</p>"
            f'<a class="btn primary" href="{esc(j["url"])}" target="_blank" rel="noopener">Read it and apply</a></article>'
        )
    where = j.get("location") or "Location not listed"
    body = f"""<main id="main">
<div class="jobtop"><div class="jobtop-inner">
<a class="back" href="/">Back to jobs</a>
<div class="jobhead">{logo(ctx, company)}
<div class="jobhead-text"><h1>{esc(j["title"])}</h1><p>{company_link(ctx, company)}, {esc(where)}</p></div>
{actions}</div>
<dl class="facts">{facts}</dl>
</div></div>
<div class="jobbody">
<div class="jobbody-main">{article}</div>
<aside class="jobbody-side">{notice_html}{record(ctx, j)}
{f'<section class="side-box"><h2>More at {esc(company)}</h2>{mini(ctx, others, False)}</section>' if others else ""}
{f'<section class="side-box"><h2>Similar {esc(field.lower())} jobs</h2>{mini(ctx, similar, True)}</section>' if similar else ""}
</aside></div>
</main>"""
    title = f"{j['title']} at {company}"
    pieces = [f"{company} is hiring: {j['title']}, {where}."]
    if pay(j):
        pieces.append(f"Pay {pay(j)} a year.")
    ev = (c.get("evidence") or {}).get(j["role"])
    if ev:
        pieces.append(
            f"{company} filed {ev['filings']:,} H-1B applications for {field.lower()} roles."
        )
    return Page(
        path=job_path(j),
        title=title if len(title) <= 70 else title[:67].rstrip() + "...",
        description=" ".join(pieces)[:160],
        body=body,
        jsonld=[posting_ld(ctx, j, description)] if description else [],
        css=CSS,
        group="jobs",
        lastmod=(j.get("checked_at") or when(j))[:10],
        nav="jobs",
        bare=True,
        noindex=not description,  # a page that only links out is too thin for search
    )


def pages(ctx: Context) -> list[Page]:
    # Similar jobs: same field and level, other companies, newest first
    groups: dict[tuple[str, str], list[dict]] = {}
    for j in sorted(ctx.jobs, key=when, reverse=True):
        groups.setdefault((j["role"], bucket(j)), []).append(j)
    out = []
    for j in ctx.jobs:
        pool = groups[(j["role"], bucket(j))]
        similar = [x for x in pool[: MORE * 4] if x["company"] != j["company"]][:MORE]
        out.append(job_page(ctx, j, similar))
    return out
