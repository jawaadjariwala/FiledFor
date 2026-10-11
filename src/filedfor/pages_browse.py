"""Field, city and ranking pages: the same fixed set on every build, whether
or not jobs are open today, so their URLs stay put for search engines.

    jobs/                                  hub
    jobs/new-grad/                         new grad, every field
    jobs/<field>/                          one field, every level
    jobs/<field>/new-grad/                 the board's default new grad view
    jobs/<field>/internships/
    jobs/<field>/<metro>/                  one field in one metro
    jobs/in/<metro>/                       every field in one metro
    rankings/                              top sponsors across tech
    rankings/<field>/                      top sponsors in one field

Open jobs come from the context (the last 30 days, at companies with filings
for the job's field, as the board shows by default). Filing numbers come
from data/browse_stats.json (browse_stats.py); without it the pages fall back
to the per-company evidence in companies.json and still build.
"""

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from filedfor import pages as layer
from filedfor.browse_stats import fein_owners
from filedfor.classify import FIELDS, METROS
from filedfor.pages import (
    SITE_URL,
    Context,
    Page,
    app_link,
    company_link,
    esc,
    job_key,
    job_list,
    money,
    slug,
)

STATS = Path("data/browse_stats.json")
CSS = ["pages-browse.css"]
FIELD_SLUGS = {
    "swe": "software-engineering",
    "ai": "ai-ml",
    "data": "data",
    "hardware": "hardware-embedded",
    "it": "it-cloud",
    "security": "cybersecurity",
    "product": "product-management",
    "design": "design",
}
NAMES = {
    "swe": "Software Engineering",
    "ai": "AI/ML",
    "data": "Data",
    "hardware": "Hardware & Embedded",
    "it": "IT & Cloud",
    "security": "Cybersecurity",
    "product": "Product Management",
    "design": "Design",
}
# The field inside a sentence
PHRASES = {
    "swe": "software engineering",
    "ai": "AI/ML",
    "data": "data",
    "hardware": "hardware and embedded",
    "it": "IT and cloud",
    "security": "cybersecurity",
    "product": "product management",
    "design": "design",
}
# What each field covers, in the words a searcher uses
COVERS = {
    "swe": "software engineers and developers, backend, frontend, full stack, mobile, platform and site reliability",
    "ai": "machine learning engineers, AI and research scientists, and applied scientists",
    "data": "data scientists, data engineers, analytics engineers and data analysts",
    "hardware": "hardware, electrical, embedded, firmware, ASIC and FPGA engineers",
    "it": "cloud, DevOps, network and systems engineers, IT and database administrators",
    "security": "security engineers, security analysts and application security",
    "product": "product managers and technical program managers",
    "design": "product, UX and UI designers and UX researchers",
}
LEVELS = ("intern", "entry", "early", "mid", "senior")  # every level the board has
NEW_GRAD = ("entry", "early")  # the board's default view
AGE = "30"  # days of jobs the pages and their board links cover
JOB_CAP = 30
SPONSOR_CAP = 15
RANK_CAP = 50
ENTRY_MIN = 50  # without browse_stats.json: filings needed for the entry list
THIN = 50  # a field and city page with fewer filings and no open jobs is noindex
H1B = '<span class="nowrap">H-1B</span>'  # headings never break "H-1B" at the hyphen
ALIASES = {"nyc", "new york city", "research triangle", "washington, d.c"}


# --- small helpers ------------------------------------------------------------


def load_stats(path: Path | None = None) -> dict:
    """browse_stats.json, or {} when it is missing or unreadable."""
    path = path or STATS
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def bucket(j: dict) -> str:
    """The board's level for a job (app.js bucket())."""
    if j["level"] != "unclear":
        return j["level"]
    years = j.get("min_years")
    if years is None or years <= 2:
        return "early"
    return "senior" if years >= 5 else "mid"


def num(n: int | None) -> str:
    return f"{n:,}" if n is not None else "n/a"


def pct(part: int | None, whole: int | None) -> str:
    return f"{round(100 * part / whole)}%" if part is not None and whole else "n/a"


def share_of(part: int, whole: int) -> str:
    """'8%', or 'less than 1%' rather than a misleading 0%."""
    if whole and 0 < part / whole < 0.005:
        return "less than 1%"
    return pct(part, whole)


def in_metro(m: str) -> str:
    return "the Bay Area" if m == "Bay Area" else m


def metro_path(m: str) -> str:
    return slug(m)


def cities(m: str) -> list[str]:
    """The place names the board counts as this metro (classify.METROS)."""
    out = []
    for raw in METROS[m][1].split("|"):
        name = re.sub(r"[\\?]", "", raw)
        if name in ALIASES:
            continue
        town, _, state = name.partition(", ")
        town = " ".join(w[:1].upper() + w[1:] for w in town.split())
        out.append(f"{town}, {state.upper()}" if state else town)
    if m == "Washington DC":
        out.insert(0, "Washington")
    return out


def month(iso: str) -> str:
    y, m = iso[:7].split("-")
    names = "January February March April May June July August September October November December"
    return f"{names.split()[int(m) - 1]} {y}"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{num(n)} {one if n == 1 else (many or one + 's')}"


def links(items: list[tuple[str, str, int | None]]) -> str:
    """Pills: (label, root-relative path, optional count)."""
    lis = "".join(
        f'<li><a href="{esc(href)}">{esc(label)}'
        + (f' <span class="n">{num(n)}</span>' if n is not None else "")
        + "</a></li>"
        for label, href, n in items
    )
    return f'<ul class="links">{lis}</ul>'


def stat_tiles(tiles: list[tuple[str, str]]) -> str:
    return (
        '<div class="stats">'
        + "".join(
            f'<div class="stat"><b>{esc(v)}</b><span>{esc(k)}</span></div>'
            for k, v in tiles
        )
        + "</div>"
    )


def item_list(name: str, items: list[tuple[str, str | None]]) -> dict:
    """schema.org ItemList of (name, absolute URL or None)."""
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": name,
        "numberOfItems": len(items),
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": n}
            | ({"url": u} if u else {})
            for i, (n, u) in enumerate(items)
        ],
    }


def filed_by_another() -> set[str]:
    """Companies whose FEINs all belong to another company (Slack files as
    Salesforce), so a list built from companies.json counts those filings
    once. Empty when the watchlist can't be read."""
    try:
        owners = fein_owners(layer.WATCHLIST)
        feins: dict[str, set[str]] = defaultdict(set)
        with layer.WATCHLIST.open(newline="") as f:
            for r in csv.DictReader(f):
                feins[r["company"]] |= set(filter(None, r["feins"].split(";")))
    except (OSError, KeyError, ValueError):
        return set()
    return {c for c, fs in feins.items() if fs and all(owners[x] != c for x in fs)}


def tracked(row: list) -> bool:
    """Stats rows end in 1 for a company FiledFor tracks, 0 for any other
    employer; rows without the flag come from the board's own evidence."""
    return len(row) < 7 or bool(row[6])


def clip(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 3].rsplit(" ", 1)[0] + "..."


# --- the pages ----------------------------------------------------------------


class Browse:
    def __init__(self, ctx: Context, stats: dict) -> None:
        self.ctx = ctx
        self.stats = stats
        p = stats.get("period") or {}
        self.period = (
            f"{month(p['from'])} to {month(p['to'])}"
            if p.get("from") and p.get("to")
            else None
        )
        # "Oct 2024 to Jun 2026", for meta descriptions
        self.short = (
            f"{month(p['from'])[:3]} {p['from'][:4]} to {month(p['to'])[:3]} {p['to'][:4]}"
            if self.period
            else None
        )
        ev = {n: c.get("evidence") or {} for n, c in ctx.companies.items()}
        # As the board shows by default: only companies with filings for the field
        self.jobs = [
            j | {"_bucket": bucket(j)}
            for j in ctx.jobs
            if ev.get(j["company"], {}).get(j["role"])
        ]
        self._by_field: dict[str, list[dict]] = {}
        for j in self.jobs:
            self._by_field.setdefault(j["role"], []).append(j)
        self._select: dict[tuple, list[dict]] = {}

    # --- data -----------------------------------------------------------------

    def select(
        self, field: str | None = None, metro: str | None = None, levels=LEVELS
    ) -> list[dict]:
        """Open jobs in a field, metro and levels (None means any). Cached:
        every page counts its siblings' jobs for its links."""
        key = (field, metro, tuple(levels))
        if key not in self._select:
            pool = self.jobs if field is None else self._by_field.get(field, [])
            self._select[key] = [
                j
                for j in pool
                if (metro is None or metro in j["metros"]) and j["_bucket"] in levels
            ]
        return self._select[key]

    def board(
        self, field: str | None = None, metro: str | None = None, levels=LEVELS
    ) -> str:
        params = {}
        if field:
            params["f"] = field
        if tuple(levels) != NEW_GRAD:
            params["l"] = ",".join(levels)
        params["age"] = AGE
        if metro:
            params["loc"] = f"m:{metro}"
        return app_link(**params)

    def cell(self, field: str | None = None, metro: str | None = None) -> dict | None:
        s = self.stats
        if not s:
            return None
        if metro:
            m = s.get("metros", {}).get(metro) or {}
            return (m.get("fields", {}).get(field) if field else m.get("all")) or None
        return (s.get("fields", {}).get(field) if field else s.get("all")) or None

    def evidence_rows(self, field: str | None) -> list[list]:
        """Without browse_stats.json: companies with open jobs, from their
        filing record on the board, in the same row shape."""
        rows = []
        if not hasattr(self, "_another"):
            self._another = filed_by_another()
        for name, c in self.ctx.companies.items():
            if name in self._another:
                continue
            if field is None:
                if c.get("tech_filings"):
                    rows.append([name, c["tech_filings"], None, None, None, None, 1])
                continue
            e = (c.get("evidence") or {}).get(field)
            if e:
                rows.append(
                    [
                        name,
                        e["filings"],
                        e["new_hire_filings"],
                        e["level_1"] + e["level_2"],
                        e["level_3"] + e["level_4"],
                        e["median_wage"],
                        1,
                    ]
                )
        rows.sort(key=lambda r: (-r[1], r[0]))
        return rows

    def top(self, field: str | None, metro: str | None = None) -> list[list]:
        c = self.cell(field, metro)
        if c is not None:
            return c.get("top", [])
        if metro:  # no city numbers without the stats file
            here = {j["company"] for j in self.select(field, metro)}
            return [r for r in self.evidence_rows(field) if r[0] in here]
        return self.evidence_rows(field)

    def entry(self, field: str | None) -> tuple[int, list[list]]:
        c = self.cell(field)
        if c is not None:
            return c.get("entry_min", ENTRY_MIN), c.get("entry", [])
        rows = [
            r
            for r in self.evidence_rows(field)
            if r[1] >= ENTRY_MIN and r[3] is not None and r[3] + r[4]
        ]
        rows.sort(key=lambda r: (-r[3] / (r[3] + r[4]), -r[1], r[0]))
        return ENTRY_MIN, rows[:SPONSOR_CAP]

    # --- shared blocks --------------------------------------------------------

    def name_cell(self, r: list) -> str:
        """Tracked employers link to their company page; others are plain text."""
        return company_link(self.ctx, r[0]) if tracked(r) else esc(r[0])

    def open_cell(self, r: list, open_now: Counter) -> str:
        if not tracked(r):
            return '<span class="bp-untracked">not tracked</span>'
        return num(open_now.get(r[0], 0))

    def counted(self, cell: dict | None) -> str:
        """' (30,983 filed Oct 2024 to Jun 2026)' for descriptions."""
        if not cell or not cell.get("filings"):
            return ""
        return f" ({num(cell['filings'])} certified {self.short})" if self.short else ""

    def when(self) -> str:
        """' certified between October 2024 and June 2026'."""
        return (
            f" certified between {self.period.replace(' to ', ' and ')}"
            if self.period
            else ""
        )

    def filing_sentence(self, cell: dict | None, what: str, where: str) -> str:
        """'Employers filed 12,345 certified LCAs for X roles at Y worksites
        from A to B; 31% were for new H-1B employment and 58% at wage levels I and II.'"""
        if not cell or not cell.get("filings"):
            if self.stats:
                return f"No LCAs for {what} roles at {where} worksites were{self.when() or ' certified'}."
            return ""
        known = cell["level_12"] + cell["level_34"]
        head = (
            f"{plural(cell['filings'], 'LCA')} for {what} roles at {where} worksites"
            f" were{self.when() or ' certified'}"
        )
        tail = []
        if cell.get("new_hire") is not None:
            tail.append(
                f"{pct(cell['new_hire'], cell['filings'])} were for new H-1B employment"
            )
        if known:
            tail.append(
                f"{pct(cell['level_12'], known)} were at wage levels I and II, the entry end of the scale"
            )
        text = head + ("; " + " and ".join(tail) if tail else "") + "."
        if cell.get("median_wage"):
            text += f" The median wage on those filings was {money(cell['median_wage'])} a year."
        return text

    def tiles(self, jobs: list[dict], cell: dict | None, new_grad: bool = True) -> str:
        t = [("open jobs, last 30 days", num(len(jobs)))]
        if new_grad:
            t.append(
                ("of them new grad", num(sum(j["_bucket"] in NEW_GRAD for j in jobs)))
            )
        if cell and cell.get("filings"):
            known = cell["level_12"] + cell["level_34"]
            label = f"LCAs certified {self.period}" if self.period else "LCA filings"
            t += [
                (label, num(cell["filings"])),
                ("for new H-1B employment", pct(cell.get("new_hire"), cell["filings"])),
                ("at wage levels I and II", pct(cell["level_12"], known)),
            ]
            if cell.get("median_wage"):
                t.append(("median wage on filings", money(cell["median_wage"])))
        return stat_tiles(t)

    def jobs_block(
        self, jobs: list[dict], board: str, empty: str, noun: str = "jobs"
    ) -> str:
        cta = (
            f'<p class="bp-cta"><a class="cta" href="{esc(board)}">See all {plural(len(jobs), noun[:-1], noun)} on the job board</a></p>'
            if jobs
            else f'<p class="bp-cta"><a class="cta" href="{esc(board)}">Open the job board with these filters</a></p>'
        )
        if not jobs:
            return f'<p class="note bp-empty">{esc(empty)}</p>{cta}'
        more = (
            f'<p class="bp-more">Showing the newest {JOB_CAP} of {num(len(jobs))}.</p>'
            if len(jobs) > JOB_CAP
            else ""
        )
        return job_list(self.ctx, jobs, JOB_CAP) + more + cta

    def sponsor_table(
        self, rows: list[list], jobs: list[dict], caption: str, open_label: str
    ) -> str:
        if not rows:
            return '<p class="note">No filings on record here.</p>'
        open_now = Counter(j["company"] for j in jobs)
        body = "".join(
            "<tr>"
            f"<td>{self.name_cell(r)}</td>"
            f'<td class="num">{num(r[1])}</td>'
            f'<td class="num">{pct(r[3], (r[3] or 0) + (r[4] or 0)) if r[3] is not None else "n/a"}</td>'
            f'<td class="num">{self.open_cell(r, open_now)}</td>'
            "</tr>"
            for r in rows[:SPONSOR_CAP]
        )
        return (
            (f'<p class="bp-sub">{esc(caption)}.</p>' if caption else "")
            + '<div class="table-scroll"><table class="ptable bp-sponsors">'
            '<thead><tr><th scope="col">Company</th><th scope="col" class="num">LCA filings</th>'
            f'<th scope="col" class="num">Level I-II</th><th scope="col" class="num">{esc(open_label)}</th></tr></thead>'
            f"<tbody>{body}</tbody></table></div>"
        )

    def about(self) -> str:
        period = self.when()
        return (
            '<h2>About these numbers</h2><p class="note">'
            "An LCA (Labor Condition Application) is a filing an employer makes with the U.S. "
            "Department of Labor before hiring or keeping an H-1B worker in a role at a stated wage. "
            "It is not a visa approval or a hire. "
            f"Counts here are LCAs{period}, by worksite, including some the employer later "
            "withdrew. Each filing counts once, in one field: "
            "by its occupation code, or by the job title when the code covers several fields "
            "(Software Developers is used for software, ML and data roles alike). The job board and "
            "company pages count a shared code toward every field it covers, so their numbers can be higher. "
            "Sponsor lists include every employer that filed; the ones whose job boards FiledFor checks "
            "link to their company page and show their open jobs."
            "</p>"
        )

    def jobs_ld(self, name: str, jobs: list[dict]) -> list[dict]:
        if not jobs:
            return []
        newest = sorted(
            jobs, key=lambda j: j.get("posted_at") or j["first_seen_at"], reverse=True
        )
        return [
            item_list(
                name,
                [
                    (
                        f"{j['title']} at {j['company']}",
                        SITE_URL + app_link(job=job_key(j))[1:],
                    )
                    for j in newest[:JOB_CAP]
                ],
            )
        ]

    def company_ld(self, name: str, rows: list[list]) -> dict:
        return item_list(
            name,
            [
                (
                    r[0],
                    (SITE_URL + u[1:])
                    if tracked(r) and (u := self.ctx.company_url(r[0]))
                    else None,
                )
                for r in rows
            ],
        )

    def page(
        self,
        path: str,
        title: str,
        description: str,
        body: str,
        crumbs,
        jsonld=(),
        group="browse",
        noindex=False,
    ) -> Page:
        return Page(
            path=path,
            title=title,
            description=clip(description),
            body=body,
            crumbs=list(crumbs),
            jsonld=list(jsonld),
            css=CSS,
            group=group,
            noindex=noindex,
            nav="rankings" if group == "rankings" else "jobs",
        )

    # --- page kinds -----------------------------------------------------------

    def hub(self) -> Page:
        jobs = self.jobs
        cell = self.cell()
        rows = "".join(
            "<tr>"
            f'<td><a href="/jobs/{FIELD_SLUGS[f]}/">{esc(NAMES[f])}</a></td>'
            f'<td class="num">{num(len(self.select(f)))}</td>'
            f'<td class="num">{num(len(self.select(f, levels=NEW_GRAD)))}</td>'
            f'<td class="num">{num((self.cell(f) or {}).get("filings")) if self.stats else "n/a"}</td>'
            "</tr>"
            for f in FIELDS
        )
        table = (
            '<div class="table-scroll"><table class="ptable bp-fields">'
            '<thead><tr><th scope="col">Field</th><th scope="col" class="num">Open jobs</th>'
            '<th scope="col" class="num">New grad</th><th scope="col" class="num">LCA filings</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )
        metros = links(
            [
                (m, f"/jobs/in/{metro_path(m)}/", len(self.select(metro=m)))
                for m in METROS
            ]
        )
        lede = (
            f"FiledFor lists {plural(len(jobs), 'tech job')} posted in the last 30 days at companies "
            "that have filed H-1B paperwork for that kind of role. Browse them by field, by city, or "
            "start with new grad roles. " + self.filing_sentence(cell, "tech", "U.S.")
        )
        body = (
            f"<h1>Tech jobs at {H1B} sponsors, by field and city</h1>"
            f'<p class="lede">{esc(lede)}</p>'
            + self.tiles(jobs, cell)
            + '<p class="bp-cta"><a class="cta" href="/jobs/new-grad/">New grad jobs</a> '
            '<a class="bp-alt" href="/rankings/">Top sponsors by field</a></p>'
            + "<h2>By field</h2>"
            + table
            + "<h2>By city</h2>"
            + metros
            + "<h2>New grad and internships</h2>"
            + links(
                [
                    (
                        "All fields, new grad",
                        "/jobs/new-grad/",
                        len(self.select(levels=NEW_GRAD)),
                    )
                ]
                + [
                    (
                        f"{NAMES[f]}, new grad",
                        f"/jobs/{FIELD_SLUGS[f]}/new-grad/",
                        len(self.select(f, levels=NEW_GRAD)),
                    )
                    for f in FIELDS
                ]
                + [
                    (
                        f"{NAMES[f]} internships",
                        f"/jobs/{FIELD_SLUGS[f]}/internships/",
                        len(self.select(f, levels=("intern",))),
                    )
                    for f in FIELDS
                ]
            )
            + "<h2>Rankings</h2>"
            + links(
                [("All tech", "/rankings/", None)]
                + [(NAMES[f], f"/rankings/{FIELD_SLUGS[f]}/", None) for f in FIELDS]
            )
            + self.about()
        )
        return self.page(
            "jobs/",
            "Tech Jobs at H-1B Sponsors by Field and City",
            f"{num(len(jobs))} open tech jobs at H-1B sponsors, by field and city, and the "
            f"companies with the most LCA filings{self.counted(cell)}.",
            body,
            [("Jobs", "jobs/")],
        )

    def field(self, f: str) -> Page:
        jobs, cell = self.select(f), self.cell(f)
        name, phrase = NAMES[f], PHRASES[f]
        lede = (
            f"{plural(len(jobs), phrase + ' job')} posted in the last 30 days at companies that have filed "
            f"LCAs for {phrase} roles. This covers {COVERS[f]}. "
            if jobs
            else f"No {phrase} jobs at H-1B sponsors were posted in the last 30 days. This field covers {COVERS[f]}. "
        ) + self.filing_sentence(cell, phrase, "U.S.")
        rows = self.top(f)
        by_city = [
            (m, f"/jobs/{FIELD_SLUGS[f]}/{metro_path(m)}/", len(self.select(f, m)))
            for m in METROS
        ]
        body = (
            f"<h1>{esc(name)} jobs at {H1B} sponsors</h1>"
            f'<p class="lede">{esc(lede)}</p>'
            + self.tiles(jobs, cell)
            + "<h2>Latest jobs</h2>"
            + self.jobs_block(
                jobs,
                self.board(f),
                f"No {phrase} jobs at companies with filings were posted in the last 30 days. "
                "The board updates every 30 minutes, and the sponsors below are the ones to watch.",
            )
            + f"<h2>Top sponsors for {esc(phrase)}</h2>"
            + self.sponsor_table(
                rows,
                jobs,
                f"Employers with the most {phrase} LCA filings{self.when()}",
                "Open jobs",
            )
            + f'<p class="bp-more"><a href="/rankings/{FIELD_SLUGS[f]}/">Full ranking, with new-hire share and wages</a></p>'
            + f"<h2>{esc(name)} jobs by city</h2>"
            + links(by_city)
            + "<h2>More</h2>"
            + links(
                [
                    (
                        f"{name}, new grad",
                        f"/jobs/{FIELD_SLUGS[f]}/new-grad/",
                        len(self.select(f, levels=NEW_GRAD)),
                    ),
                    (
                        f"{name} internships",
                        f"/jobs/{FIELD_SLUGS[f]}/internships/",
                        len(self.select(f, levels=("intern",))),
                    ),
                ]
                + [
                    (NAMES[o], f"/jobs/{FIELD_SLUGS[o]}/", len(self.select(o)))
                    for o in FIELDS
                    if o != f
                ]
            )
            + self.about()
        )
        desc = (
            f"{num(len(jobs))} open {phrase} jobs at H-1B sponsors, and the top sponsors "
            f"by LCA filings{self.counted(cell)}."
        )
        return self.page(
            f"jobs/{FIELD_SLUGS[f]}/",
            f"{name} Jobs at H-1B Sponsors",
            desc,
            body,
            [("Jobs", "jobs/"), (name, f"jobs/{FIELD_SLUGS[f]}/")],
            [self.company_ld(f"Top {name} H-1B sponsors", rows[:SPONSOR_CAP])]
            + self.jobs_ld(f"{name} jobs", jobs),
        )

    def new_grad(self, f: str | None) -> Page:
        jobs, cell = self.select(f, levels=NEW_GRAD), self.cell(f)
        name = NAMES[f] if f else "Tech"
        phrase = PHRASES[f] if f else "tech"
        least, rows = self.entry(f)
        known = (cell["level_12"] + cell["level_34"]) if cell else 0
        lede = (
            f"{plural(len(jobs), 'new grad ' + phrase + ' job')} posted in the last 30 days at companies "
            f"that have filed LCAs for {'that kind of' if f is None else phrase} role. "
            if jobs
            else f"No new grad {phrase} jobs at H-1B sponsors were posted in the last 30 days. "
        ) + (
            "New grad here means what the board's default view shows: titles that say new grad, junior "
            "or entry level, and titles with no level that ask for 2 years of experience or less. "
        )
        if cell and known:
            lede += (
                f"Of the {num(cell['filings'])} {phrase} LCAs{self.when()}, "
                f"{pct(cell['level_12'], known)} were at wage levels I and II, where most new grads are filed."
            )
        path = f"jobs/{FIELD_SLUGS[f]}/new-grad/" if f else "jobs/new-grad/"
        crumbs = [("Jobs", "jobs/")] + (
            [(NAMES[f], f"jobs/{FIELD_SLUGS[f]}/"), ("New grad", path)]
            if f
            else [("New grad", path)]
        )
        others = (
            [
                (
                    "All fields, new grad",
                    "/jobs/new-grad/",
                    len(self.select(levels=NEW_GRAD)),
                )
            ]
            if f
            else []
        ) + [
            (
                f"{NAMES[o]}, new grad",
                f"/jobs/{FIELD_SLUGS[o]}/new-grad/",
                len(self.select(o, levels=NEW_GRAD)),
            )
            for o in FIELDS
            if o != f
        ]
        if f:
            others = [
                (f"All {name} jobs", f"/jobs/{FIELD_SLUGS[f]}/", len(self.select(f))),
                (
                    f"{name} internships",
                    f"/jobs/{FIELD_SLUGS[f]}/internships/",
                    len(self.select(f, levels=("intern",))),
                ),
            ] + others
        by_city = links(
            [
                (
                    m,
                    f"/jobs/{FIELD_SLUGS[f]}/{metro_path(m)}/"
                    if f
                    else f"/jobs/in/{metro_path(m)}/",
                    n,
                )
                for m in METROS
                if (n := len(self.select(f, m, NEW_GRAD)))
            ]
        )
        body = (
            f"<h1>New grad {esc(phrase if f else 'tech')} jobs at {H1B} sponsors</h1>"
            f'<p class="lede">{esc(lede)}</p>'
            + self.tiles(jobs, cell, new_grad=False)
            + "<h2>Latest new grad jobs</h2>"
            + self.jobs_block(
                jobs,
                self.board(f, levels=NEW_GRAD),
                f"No new grad {phrase} jobs at companies with filings were posted in the last 30 days. "
                "Hiring for new grads is seasonal; the companies below file the most at entry-level wages.",
            )
            + "<h2>Most entry-level friendly sponsors</h2>"
            + f'<p class="bp-sub">Employers with at least {num(least)} {esc(phrase)} LCA filings{esc(self.when())}, '
            "ranked by the share filed at wage levels I and II.</p>"
            + self.sponsor_table(
                rows,
                jobs,
                "",
                "Open new grad jobs",
            )
            + ("<h2>New grad jobs by city</h2>" + by_city if "<li>" in by_city else "")
            + "<h2>More</h2>"
            + links(
                others
                + (
                    [(f"{name} sponsor ranking", f"/rankings/{FIELD_SLUGS[f]}/", None)]
                    if f
                    else [("Sponsor rankings", "/rankings/", None)]
                )
            )
            + self.about()
        )
        title = f"New Grad {name} Jobs at H-1B Sponsors"
        desc = (
            f"{num(len(jobs))} new grad {phrase} jobs at H-1B sponsors from the last 30 days, "
            "and the sponsors that file most often at entry-level wage levels."
        )
        return self.page(
            path,
            title,
            desc,
            body,
            crumbs,
            [self.company_ld(f"Entry-level friendly {name} H-1B sponsors", rows)]
            + self.jobs_ld(f"New grad {name} jobs", jobs),
        )

    def internships(self, f: str) -> Page:
        jobs = self.select(f, levels=("intern",))
        name, phrase = NAMES[f], PHRASES[f]
        lede = (
            f"{plural(len(jobs), phrase + ' internship')} posted in the last 30 days at companies that have "
            f"filed LCAs for full-time {phrase} roles. "
            if jobs
            else f"No {phrase} internships at H-1B sponsors were posted in the last 30 days. "
        ) + (
            "Internships themselves are usually done on F-1 CPT or OPT, not H-1B. A company's filings show "
            "it has gone on to file for full-time people in this field, which is the step that matters after."
        )
        open_now = Counter(j["company"] for j in jobs)
        rows = []
        for company, n in sorted(open_now.items(), key=lambda kv: (-kv[1], kv[0]))[
            :SPONSOR_CAP
        ]:
            e = (self.ctx.companies.get(company, {}).get("evidence") or {}).get(f) or {}
            rows.append(
                "<tr>"
                f"<td>{company_link(self.ctx, company)}</td>"
                f'<td class="num">{num(n)}</td>'
                f'<td class="num">{num(e.get("filings"))}</td>'
                f'<td class="num">{num(e.get("new_hire_filings"))}</td>'
                "</tr>"
            )
        table = (
            f'<p class="bp-sub">{esc(name)} LCA filings{esc(self.when())}, as the job board counts them.</p>'
            '<div class="table-scroll"><table class="ptable bp-sponsors">'
            '<thead><tr><th scope="col">Company</th><th scope="col" class="num">Open internships</th>'
            '<th scope="col" class="num">LCA filings</th><th scope="col" class="num">New employment</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>"
            if rows
            else '<p class="note">No company has an open internship in this field right now.</p>'
        )
        body = (
            f"<h1>{esc(name)} internships at {H1B} sponsors</h1>"
            f'<p class="lede">{esc(lede)}</p>'
            + stat_tiles(
                [
                    ("open internships, last 30 days", num(len(jobs))),
                    ("companies hiring interns", num(len(open_now))),
                    (
                        "open new grad jobs in the field",
                        num(len(self.select(f, levels=NEW_GRAD))),
                    ),
                ]
            )
            + "<h2>Latest internships</h2>"
            + self.jobs_block(
                jobs,
                self.board(f, levels=("intern",)),
                f"No {phrase} internships at companies with filings were posted in the last 30 days. "
                "Most internship postings open from late summer to winter.",
                "internships",
            )
            + "<h2>Companies hiring interns now</h2>"
            + table
            + "<h2>More</h2>"
            + links(
                [
                    (
                        f"{name}, new grad",
                        f"/jobs/{FIELD_SLUGS[f]}/new-grad/",
                        len(self.select(f, levels=NEW_GRAD)),
                    ),
                    (
                        f"All {name} jobs",
                        f"/jobs/{FIELD_SLUGS[f]}/",
                        len(self.select(f)),
                    ),
                    (f"{name} sponsor ranking", f"/rankings/{FIELD_SLUGS[f]}/", None),
                ]
                + [
                    (
                        f"{NAMES[o]} internships",
                        f"/jobs/{FIELD_SLUGS[o]}/internships/",
                        len(self.select(o, levels=("intern",))),
                    )
                    for o in FIELDS
                    if o != f
                ]
            )
            + self.about()
        )
        return self.page(
            f"jobs/{FIELD_SLUGS[f]}/internships/",
            f"{name} Internships at H-1B Sponsors",
            f"{num(len(jobs))} {phrase} internships from the last 30 days at {num(len(open_now))} "
            f"companies that have filed H-1B LCAs for full-time {phrase} roles.",
            body,
            [
                ("Jobs", "jobs/"),
                (name, f"jobs/{FIELD_SLUGS[f]}/"),
                ("Internships", f"jobs/{FIELD_SLUGS[f]}/internships/"),
            ],
            self.jobs_ld(f"{name} internships", jobs),
        )

    def field_metro(self, f: str, m: str) -> Page:
        jobs, cell = self.select(f, m), self.cell(f, m)
        national = self.cell(f)
        name, phrase, where = NAMES[f], PHRASES[f], in_metro(m)
        ng = len([j for j in jobs if j["_bucket"] in NEW_GRAD])
        lede = (
            f"{plural(len(jobs), phrase + ' job')} in {where} posted in the last 30 days at companies that "
            f"have filed LCAs for {phrase} roles, {num(ng)} of them new grad. "
            if jobs
            else f"No {phrase} jobs at H-1B sponsors in {where} were posted in the last 30 days. "
        ) + self.filing_sentence(cell, phrase, f"{where}-area")
        if cell and national and national.get("filings"):
            lede += f" That is {share_of(cell['filings'], national['filings'])} of all {phrase} filings in the U.S."
        rows = self.top(f, m)
        caption = (
            f"Employers with the most {phrase} LCA filings at {where}-area worksites{self.when()}"
            if cell
            else f"Companies with open jobs here, by {phrase} filings nationwide"
        )
        sponsors = self.sponsor_table(rows, jobs, caption, "Open jobs here")
        body = (
            f"<h1>{esc(name)} jobs at {H1B} sponsors in {esc(where)}</h1>"
            f'<p class="lede">{esc(lede)}</p>'
            + self.tiles(jobs, cell)
            + "<h2>Latest jobs</h2>"
            + self.jobs_block(
                jobs,
                self.board(f, m),
                f"No {phrase} jobs at companies with filings in {where} were posted in the last 30 days. "
                f"The employers below filed the most {phrase} LCAs here, so they are the ones to watch.",
            )
            + (
                f'<p class="bp-more"><a href="{esc(self.board(f, m, NEW_GRAD))}">New grad {esc(phrase)} jobs in {esc(where)} on the board ({num(ng)})</a></p>'
                if jobs
                else ""
            )
            + f"<h2>Top {esc(phrase)} sponsors in {esc(where)}</h2>"
            + sponsors
            + f'<p class="bp-sub">{esc(where)} here means {esc(", ".join(cities(m)))}, the same places the job board\'s {esc(m)} filter uses.</p>'
            + f"<h2>{esc(name)} jobs in other cities</h2>"
            + links(
                [
                    (
                        o,
                        f"/jobs/{FIELD_SLUGS[f]}/{metro_path(o)}/",
                        len(self.select(f, o)),
                    )
                    for o in METROS
                    if o != m
                ]
            )
            + f"<h2>Other fields in {esc(where)}</h2>"
            + links(
                [
                    (
                        f"All tech in {where}",
                        f"/jobs/in/{metro_path(m)}/",
                        len(self.select(metro=m)),
                    )
                ]
                + [
                    (
                        NAMES[o],
                        f"/jobs/{FIELD_SLUGS[o]}/{metro_path(m)}/",
                        len(self.select(o, m)),
                    )
                    for o in FIELDS
                    if o != f
                ]
            )
            + "<h2>More</h2>"
            + links(
                [
                    (
                        f"All {name} jobs",
                        f"/jobs/{FIELD_SLUGS[f]}/",
                        len(self.select(f)),
                    ),
                    (
                        f"{name}, new grad",
                        f"/jobs/{FIELD_SLUGS[f]}/new-grad/",
                        len(self.select(f, levels=NEW_GRAD)),
                    ),
                    (f"{name} sponsor ranking", f"/rankings/{FIELD_SLUGS[f]}/", None),
                ]
            )
            + self.about()
        )
        desc = (
            f"{num(len(jobs))} open {phrase} jobs in {where} at H-1B sponsors, and the top "
            f"local sponsors by LCA filings{self.counted(cell)}."
        )
        return self.page(
            f"jobs/{FIELD_SLUGS[f]}/{metro_path(m)}/",
            f"{name} Jobs at H-1B Sponsors in {where}",
            desc,
            body,
            [
                ("Jobs", "jobs/"),
                (name, f"jobs/{FIELD_SLUGS[f]}/"),
                (m, f"jobs/{FIELD_SLUGS[f]}/{metro_path(m)}/"),
            ],
            (
                [
                    self.company_ld(
                        f"Top {name} H-1B sponsors in {where}", rows[:SPONSOR_CAP]
                    )
                ]
                if rows
                else []
            )
            + self.jobs_ld(f"{name} jobs in {where}", jobs),
            noindex=self.thin(jobs, cell),
        )

    def thin(self, jobs: list[dict], cell: dict | None) -> bool:
        """Too little here for search: no open jobs and few filings. Without
        the stats file nothing is judged thin."""
        return bool(self.stats) and not jobs and (cell or {}).get("filings", 0) < THIN

    def metro(self, m: str) -> Page:
        jobs, cell = self.select(metro=m), self.cell(metro=m)
        where = in_metro(m)
        lede = (
            f"{plural(len(jobs), 'tech job')} in {where} posted in the last 30 days at companies that have "
            "filed LCAs for that kind of role. "
            if jobs
            else f"No tech jobs at H-1B sponsors in {where} were posted in the last 30 days. "
        ) + self.filing_sentence(cell, "tech", f"{where}-area")
        if cell and self.stats.get("all", {}).get("filings"):
            lede += f" That is {share_of(cell['filings'], self.stats['all']['filings'])} of all tech filings in the U.S."
        rows = self.top(None, m)
        fields = ""
        for f in FIELDS:
            c = self.cell(f, m) or {}
            known = (c.get("level_12") or 0) + (c.get("level_34") or 0)
            fields += (
                "<tr>"
                f'<td><a href="/jobs/{FIELD_SLUGS[f]}/{metro_path(m)}/">{esc(NAMES[f])}</a></td>'
                f'<td class="num">{num(len(self.select(f, m)))}</td>'
                f'<td class="num">{num(c.get("filings"))}</td>'
                f'<td class="num">{pct(c.get("level_12"), known)}</td>'
                "</tr>"
            )
        body = (
            f"<h1>Tech jobs at {H1B} sponsors in {esc(where)}</h1>"
            f'<p class="lede">{esc(lede)}</p>'
            + self.tiles(jobs, cell)
            + "<h2>By field</h2>"
            + '<div class="table-scroll"><table class="ptable bp-fields">'
            + '<thead><tr><th scope="col">Field</th><th scope="col" class="num">Open jobs</th>'
            + '<th scope="col" class="num">LCA filings</th><th scope="col" class="num">Level I-II</th></tr></thead>'
            + f"<tbody>{fields}</tbody></table></div>"
            + "<h2>Latest jobs</h2>"
            + self.jobs_block(
                jobs,
                self.board(metro=m),
                f"No tech jobs at companies with filings in {where} were posted in the last 30 days.",
            )
            + f"<h2>Top sponsors in {esc(where)}</h2>"
            + self.sponsor_table(
                rows,
                jobs,
                f"Employers with the most tech LCA filings at {where}-area worksites{self.when()}"
                if cell
                else "Companies with open jobs here, by tech filings nationwide",
                "Open jobs here",
            )
            + f'<p class="bp-sub">{esc(where)} here means {esc(", ".join(cities(m)))}, the same places the job board\'s {esc(m)} filter uses.</p>'
            + "<h2>Other cities</h2>"
            + links(
                [
                    (o, f"/jobs/in/{metro_path(o)}/", len(self.select(metro=o)))
                    for o in METROS
                    if o != m
                ]
            )
            + self.about()
        )
        desc = (
            f"{num(len(jobs))} open tech jobs in {where} at H-1B sponsors, by field, and the "
            f"top local sponsors by LCA filings{self.counted(cell)}."
        )
        return self.page(
            f"jobs/in/{metro_path(m)}/",
            f"Tech Jobs at H-1B Sponsors in {where}",
            desc,
            body,
            [("Jobs", "jobs/"), (m, f"jobs/in/{metro_path(m)}/")],
            (
                [self.company_ld(f"Top H-1B sponsors in {where}", rows[:SPONSOR_CAP])]
                if rows
                else []
            )
            + self.jobs_ld(f"Tech jobs in {where}", jobs),
        )

    def ranking_table(
        self, rows: list[list], open_now: Counter, lead: str = "filings"
    ) -> str:
        if not rows:
            return '<p class="note">No filing numbers for this ranking yet.</p>'
        cols = [
            ("filings", "LCA filings", lambda r: num(r[1])),
            (
                "new",
                "New employment",
                lambda r: pct(r[2], r[1]) if r[2] is not None else "n/a",
            ),
            (
                "level",
                "Level I-II",
                lambda r: pct(r[3], r[3] + r[4]) if r[3] is not None else "n/a",
            ),
            ("wage", "Median wage", lambda r: money(r[5]) or "n/a"),
            ("open", "Open jobs", lambda r: self.open_cell(r, open_now)),
        ]
        if lead == "level":
            cols.insert(0, cols.pop(2))
        head = "".join(
            f'<th scope="col" class="num">{esc(label)}</th>' for _, label, _ in cols
        )
        body = "".join(
            "<tr>"
            f'<td class="rank">{i + 1}</td><td>{self.name_cell(r)}</td>'
            + "".join(
                f'<td class="num" data-label="{esc(label)}">{fn(r)}</td>'
                for _, label, fn in cols
            )
            + "</tr>"
            for i, r in enumerate(rows)
        )
        return (
            '<div class="table-scroll"><table class="ptable bp-rank">'
            f'<thead><tr><th scope="col" class="rank">#</th><th scope="col">Company</th>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>"
        )

    def ranking(self, f: str | None) -> Page:
        cell = self.cell(f)
        name = NAMES[f] if f else "Tech"
        phrase = PHRASES[f] if f else "tech"
        rows = self.top(f)[:RANK_CAP]
        least, entry = self.entry(f)
        open_now = Counter(j["company"] for j in self.select(f))
        ranked = (cell or {}).get("ranked") or len(self.top(f))
        lede = (
            (
                f"Every employer, ranked by LCAs for {phrase} roles{self.when()}. "
                if cell
                else f"Companies with jobs on FiledFor now, ranked by the {phrase} LCA filings on their record. "
            )
            + "An LCA is the filing an employer makes with the Department of Labor before an H-1B worker "
            "starts or continues in a role; it is not a visa approval or a hire. "
        )
        if cell and cell.get("filings"):
            lede += (
                f"Across all {num(ranked)} employers, {num(cell['filings'])} {phrase} LCAs were certified; "
                f"{pct(cell['new_hire'], cell['filings'])} were for new H-1B employment and "
                f"{pct(cell['level_12'], cell['level_12'] + cell['level_34'])} at wage levels I and II. "
            )
        lede += "Companies whose job boards FiledFor checks link to their page and show their open jobs."
        path = f"rankings/{FIELD_SLUGS[f]}/" if f else "rankings/"
        crumbs = [("Rankings", "rankings/")] + ([(name, path)] if f else [])
        jobs_links = (
            [
                (f"{name} jobs", f"/jobs/{FIELD_SLUGS[f]}/", len(self.select(f))),
                (
                    f"{name}, new grad",
                    f"/jobs/{FIELD_SLUGS[f]}/new-grad/",
                    len(self.select(f, levels=NEW_GRAD)),
                ),
            ]
            if f
            else [
                ("All jobs by field and city", "/jobs/", None),
                ("New grad jobs", "/jobs/new-grad/", len(self.select(levels=NEW_GRAD))),
            ]
        )
        others = ([("All tech", "/rankings/", None)] if f else []) + [
            (NAMES[o], f"/rankings/{FIELD_SLUGS[o]}/", None) for o in FIELDS if o != f
        ]
        body = (
            f"<h1>Top {H1B} sponsors for {esc(phrase if f else 'tech jobs')}</h1>"
            f'<p class="lede">{esc(lede)}</p>'
            + (
                '<p class="bp-sub">Ranked by the employer named on the filing. A company that files '
                "under several legal entities, like Amazon, appears once for each.</p>"
                if cell
                else ""
            )
            + (
                stat_tiles(
                    [
                        (
                            f"LCAs certified {self.period}"
                            if self.period
                            else "LCA filings",
                            num(cell["filings"]),
                        ),
                        ("employers that filed", num(ranked)),
                        (
                            "for new H-1B employment",
                            pct(cell["new_hire"], cell["filings"]),
                        ),
                        ("median wage on filings", money(cell["median_wage"]) or "n/a"),
                        ("open jobs now", num(sum(open_now.values()))),
                    ]
                )
                if cell
                else ""
            )
            + f"<h2>Most {esc(phrase)} LCA filings</h2>"
            + f'<p class="bp-sub">Top {num(min(len(rows), RANK_CAP))} of {num(ranked)} employers with {esc(phrase)} filings. '
            "New employment: share of filings for a new H-1B job, often a first H-1B, rather than an extension or transfer. Level I-II: share at the two "
            "entry-level wage levels, of filings with a level. Open jobs: posted in the last 30 days.</p>"
            + self.ranking_table(rows, open_now)
            + "<h2>Most entry-level friendly</h2>"
            + f'<p class="bp-sub">Employers with at least {num(least)} {esc(phrase)} LCA filings, ranked by the share filed '
            "at wage levels I and II, where most new grads are filed. A high share means the company often "
            "files for people early in their careers.</p>"
            + self.ranking_table(entry, open_now, lead="level")
            + "<h2>Jobs</h2>"
            + links(jobs_links)
            + "<h2>Other rankings</h2>"
            + links(others)
            + self.about()
        )
        title = (
            f"Top H-1B Sponsors for {name}, by LCA Filings"
            if f
            else "Top H-1B Sponsors for Tech Jobs, by LCA Filings"
        )
        top3 = ", ".join(r[0] for r in rows[:3])
        desc = (
            f"Top {phrase} H-1B sponsors by LCA filings"
            + (f" ({self.short})" if self.short else "")
            + (f": {top3} and more" if top3 else "")
            + ", with new-hire and entry-level wage shares."
        )
        return self.page(
            path,
            title,
            desc,
            body,
            crumbs,
            [self.company_ld(f"Top H-1B sponsors for {phrase}", rows)] if rows else [],
            group="rankings",
        )


def pages(ctx: Context) -> list[Page]:
    b = Browse(ctx, load_stats())
    out = [b.hub(), b.new_grad(None)]
    for f in FIELDS:
        out += [b.field(f), b.new_grad(f), b.internships(f)]
        out += [b.field_metro(f, m) for m in METROS]
    out += [b.metro(m) for m in METROS]
    out += [b.ranking(None)] + [b.ranking(f) for f in FIELDS]
    return out
