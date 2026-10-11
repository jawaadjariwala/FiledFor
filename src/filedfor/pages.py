"""Static HTML pages for search engines: one per sponsor, per field and city,
rankings. Each run renders them from the same data as the site, so they are
never staler than the job list.

Page modules (pages_company, pages_browse) each expose `pages(ctx)` returning
a list of Page. This module owns the shared layout, the sitemap index and the
writing; the modules own only their content.

    uv run python -m filedfor.pages      rebuild pages from public/*.json,
                                         without the database (for development)
"""

import hashlib
import html
import importlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape

PUBLIC = Path("public")
SITE = Path("site")
SITE_URL = os.environ.get("SITE_URL", "https://filedfor.com/")
MODULES = ("filedfor.pages_company", "filedfor.pages_browse")
FIELD_NAMES = {
    "swe": "Software",
    "ai": "AI/ML",
    "data": "Data",
    "hardware": "Hardware & Embedded",
    "it": "IT & Cloud",
    "security": "Security",
    "product": "Product",
    "design": "Design",
}

esc = html.escape


@dataclass
class Context:
    """What every page module gets. jobs are the listed jobs exactly as the
    site receives them (dates as ISO strings, states and metros parsed);
    companies is companies.json."""

    jobs: list[dict]
    companies: dict[str, dict]
    now: datetime
    updated: str  # when the job data was last refreshed, ISO

    def jobs_at(self, company: str) -> list[dict]:
        if not hasattr(self, "_by_company"):
            self._by_company: dict[str, list[dict]] = {}
            for j in self.jobs:
                self._by_company.setdefault(j["company"], []).append(j)
        return self._by_company.get(company, [])


@dataclass
class Page:
    path: str  # where it is served, relative to the site root: "companies/stripe/"
    title: str  # the <title>; keep under about 60 characters
    description: str  # meta description; about 150 characters
    body: str  # HTML inside <main>; escape every value with esc()
    crumbs: list[tuple[str, str]] = field(
        default_factory=list
    )  # (name, path) after Home
    jsonld: list[dict] = field(default_factory=list)  # schema.org objects
    css: list[str] = field(default_factory=list)  # extra stylesheets in site/
    group: str = "pages"  # which sitemap file lists it: sitemap-<group>.xml
    lastmod: str | None = None  # ISO date; defaults to the build date


def slug(name: str) -> str:
    """'Procter & Gamble' -> 'procter-gamble'. Stable, so URLs never change."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "company"


def company_path(name: str) -> str:
    return f"companies/{slug(name)}/"


def app_link(**params: str) -> str:
    """A link into the job board with filters, e.g. app_link(company='Stripe')."""
    return "/" + (f"?{urlencode(params)}" if params else "")


def job_key(j: dict) -> str:
    return f"{j['system']}|{j['slug']}|{j['job_id']}"


def asset(name: str) -> str:
    """Root-relative URL of a file in site/, versioned by its content."""
    p = SITE / name
    v = hashlib.sha1(p.read_bytes()).hexdigest()[:8] if p.exists() else "0"
    return f"/{name}?v={v}"


def money(n: float | None) -> str:
    return f"${round(n / 1000)}K" if n else ""


def ago(iso: str | None, now: datetime) -> str:
    if not iso:
        return ""
    days = (now - datetime.fromisoformat(iso)).days
    return "today" if days < 1 else "1 day ago" if days == 1 else f"{days} days ago"


def job_list(jobs: list[dict], now: datetime, limit: int = 25) -> str:
    """Newest first. Titles open the job in FiledFor; company links go to the
    company page."""
    jobs = sorted(
        jobs, key=lambda j: j.get("posted_at") or j["first_seen_at"], reverse=True
    )
    items = []
    for j in jobs[:limit]:
        bits = [esc(j.get("location") or "Location not listed")]
        if j.get("salary_min"):
            bits.append(f"{money(j['salary_min'])}–{money(j['salary_max'])}")
        bits.append(f"posted {ago(j.get('posted_at') or j['first_seen_at'], now)}")
        items.append(
            '<li class="pjob">'
            f'<a class="pjob-title" href="{esc(app_link(job=job_key(j)))}">{esc(j["title"])}</a>'
            f'<span class="pjob-meta"><a href="/{company_path(j["company"])}">{esc(j["company"])}</a>'
            f" · {' · '.join(bits)}</span></li>"
        )
    return f'<ul class="pjobs">{"".join(items)}</ul>' if items else ""


HEADER = """<header class="topbar"><div class="topbar-inner">
<a class="brand" href="/" aria-label="FiledFor home"><svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8"/><path d="M11 8h11v4h-7v3h6v4h-6v5h-4z"/></svg><span>FiledFor</span></a>
<nav class="topnav" aria-label="Main"><a class="navlink" href="/">Jobs</a><a class="navlink" href="/companies/">Companies</a>
<button type="button" class="navlink icon-only" id="theme" aria-label="Switch between light and dark" title="Switch between light and dark"><svg viewBox="0 0 24 24" aria-hidden="true" class="moon"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg><svg viewBox="0 0 24 24" aria-hidden="true" class="sun"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/></svg></button>
</nav></div></header>"""

FOOTER = """<footer class="footer wrap"><span>FiledFor · open data, no sign-up. Filing data from the U.S. Department of Labor.</span>
<a href="/companies/">All companies</a><a href="https://github.com/jawaadjariwala/FiledFor">GitHub</a></footer>"""


def render(page: Page, built: str) -> str:
    url = SITE_URL + page.path
    trail = [("Home", "")] + page.crumbs
    crumbs = " / ".join(
        f'<a href="/{p}">{esc(n)}</a>'
        if i < len(trail) - 1
        else f"<span>{esc(n)}</span>"
        for i, (n, p) in enumerate(trail)
    )
    ld = page.jsonld + [
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": i + 1,
                    "name": n,
                    "item": SITE_URL + p,
                }
                for i, (n, p) in enumerate(trail)
            ],
        }
    ]
    css = "".join(
        f'<link rel="stylesheet" href="{asset(c)}">'
        for c in ["style.css", "pages.css", *page.css]
    )
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(x, separators=(",", ":")).replace("</", "<\\/")}</script>'
        for x in ld
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(page.title)}</title>
<meta name="description" content="{esc(page.description)}">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="website"><meta property="og:site_name" content="FiledFor">
<meta property="og:title" content="{esc(page.title)}"><meta property="og:description" content="{esc(page.description)}">
<meta property="og:url" content="{esc(url)}"><meta property="og:image" content="{SITE_URL}og.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#1f7a52">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%231f7a52'/%3E%3Cpath d='M11 8h11v4h-7v3h6v4h-6v5h-4z' fill='white'/%3E%3C/svg%3E"><link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<script>try{{const t=localStorage.getItem("ff.theme");if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
{css}{scripts}
</head><body class="static">
{HEADER}
<main class="wrap page" id="main"><nav class="crumbs" aria-label="Breadcrumb">{crumbs}</nav>
{page.body}
<p class="page-updated">Jobs updated {esc(built)} UTC. FiledFor checks every company's job board every 30 minutes.</p>
</main>
{FOOTER}
<script src="{asset("pages.js")}" defer></script>
<script data-goatcounter="https://jawaadjariwala.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>
</body></html>
"""


def build(ctx: Context) -> list[Page]:
    out: list[Page] = []
    for name in MODULES:
        out += importlib.import_module(name).pages(ctx)
    paths = [p.path for p in out]
    dupes = {p for p in paths if paths.count(p) > 1}
    if dupes:
        raise ValueError(f"two pages share a path: {sorted(dupes)[:5]}")
    return out


def sitemaps(pages: list[Page], now: datetime) -> dict[str, str]:
    """sitemap.xml becomes an index of one sitemap per group (each under
    the 50,000-URL limit), plus sitemap-main.xml for the job board itself."""
    today = now.date().isoformat()
    groups: dict[str, list[str]] = {
        "main": [f"<url><loc>{escape(SITE_URL)}</loc><lastmod>{today}</lastmod></url>"]
    }
    for p in pages:
        groups.setdefault(p.group, []).append(
            f"<url><loc>{escape(SITE_URL + p.path)}</loc><lastmod>{p.lastmod or today}</lastmod></url>"
        )
    head = '<?xml version="1.0" encoding="UTF-8"?>\n'
    ns = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
    files = {
        f"sitemap-{g}.xml": f"{head}<urlset {ns}>{''.join(urls)}</urlset>\n"
        for g, urls in groups.items()
    }
    files["sitemap.xml"] = (
        f"{head}<sitemapindex {ns}>"
        + "".join(
            f"<sitemap><loc>{SITE_URL}{name}</loc><lastmod>{today}</lastmod></sitemap>"
            for name in files
        )
        + "</sitemapindex>\n"
    )
    return files


def write_all(ctx: Context, out: Path = PUBLIC) -> int:
    """Render every page into out/ and write the sitemaps. Returns the page count."""
    pages = build(ctx)
    built = ctx.updated[:16].replace("T", " ")
    for p in pages:
        target = out / p.path / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(p, built))
    for name, text in sitemaps(pages, ctx.now).items():
        (out / name).write_text(text)
    return len(pages)


def from_public(data: Path = PUBLIC) -> Context:
    """The context rebuilt from published JSON, so pages can be developed
    without the database."""
    recent = json.loads((data / "jobs-recent.json").read_text())
    older = json.loads((data / "jobs-older.json").read_text())
    return Context(
        jobs=recent["jobs"] + older["jobs"],
        companies=json.loads((data / "companies.json").read_text()),
        now=datetime.now(UTC),
        updated=recent["updated"],
    )


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else PUBLIC
    n = write_all(from_public(src))
    print(f"wrote {n} pages to {PUBLIC}/")
