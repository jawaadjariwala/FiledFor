"""One page per sponsor company, and the A to Z index at /companies/.

Every company in ctx.directory gets a page, open jobs or not. What a page
shows comes from four places, and each part is left out when its data is
missing:

- tech filing counts (new employment, wage levels) from
  data/sponsors.parquet, summed over the company's FEINs
- medians from data/company_profiles.json, exact over all the company's
  filings; without it, only for companies with one FEIN, where the stored
  per-FEIN median is exact
- filings by field from companies.json, or the evidence index for
  companies with no job listed today
- filing history (quarters, titles, cities) from data/company_profiles.json,
  built locally by profiles.py
- logo, website, PERM notice and careers pages from the watchlist files
"""

import bisect
import csv
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb

from filedfor import feeds, profiles, publish
from filedfor import pages as layer
from filedfor.evidence import ROLE_EVIDENCE, EvidenceIndex
from filedfor.pages import FIELD_NAMES, Context, Page, esc
from filedfor.sponsors import SPONSORS

EXCLUDED = Path("data/excluded_boards.csv")
SIMILAR = 8
# Below this many filings, shares and medians say little: only counts are shown
SMALL = 10
JOBS_SHOWN = 10
# Similar sponsors: candidates within NEAR places by volume, and how much a
# different mix of fields counts against one (against log filing ratio)
NEAR, MIX_WEIGHT = 60, 3
MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]  # fmt: skip
NEW_EMPLOYMENT = (
    "New employment means a filing for a new H-1B job, often a person's first H-1B "
    "(this can include someone already working at the company on student work "
    "authorization), as opposed to an extension or a transfer from another employer."
)
# Why a month had few filings across every employer, where it is known
LOW_MONTH_REASONS = {"2025-10": "while the federal government was shut down"}


@dataclass
class Totals:
    """Tech filings across a company's FEINs."""

    filings: int = 0
    new_hire: int = 0
    levels: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    wage: int | None = None


def num(n: int) -> str:
    return f"{n:,}"


def count(n: int, word: str) -> str:
    """count(1, "filing") -> '1 filing'; count(3, "filing") -> '3 filings'."""
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def pct(part: int, whole: int) -> int | None:
    return round(part * 100 / whole) if whole else None


def low_share(ev: dict) -> int | None:
    """Share of filings at wage level I or II, of those with a known level.
    Same rule as the job board."""
    known = ev["level_1"] + ev["level_2"] + ev["level_3"] + ev["level_4"]
    return pct(ev["level_1"] + ev["level_2"], known)


def month(iso: str, day: bool = False) -> str:
    d = date.fromisoformat(iso)
    return (
        f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
        if day
        else f"{MONTHS[d.month - 1]} {d.year}"
    )


def quarter_months(fy: int, q: int) -> str:
    """DOL fiscal quarters: FY2026 Q1 is October to December 2025."""
    first = (9 + 3 * (q - 1)) % 12
    year = fy - 1 if q == 1 else fy
    return f"{MONTHS[first]} to {MONTHS[first + 2]} {year}"


class Sources:
    """Everything the pages read besides ctx, loaded once per build."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.boards: dict[str, list[dict]] = {}
        excluded = profiles.excluded_boards(EXCLUDED)
        with layer.WATCHLIST.open(newline="") as f:
            for r in csv.DictReader(f):
                if (
                    r["company"] in ctx.directory
                    and (r["system"], r["slug"]) not in excluded
                ):
                    self.boards.setdefault(r["company"], []).append(r)
        self.profiles = profiles.load()
        self.domains = self._domains()
        self.notices = publish.notices()
        self._index: EvidenceIndex | bool | None = None
        self.sponsors, self.period = self._sponsors()
        meta = self.profiles["meta"]
        if meta.get("first") and meta.get("last"):
            self.period = (meta["first"], meta["last"])
        self._totals = {n: self._total(n) for n in ctx.directory}
        # The headline count per company: from the same FEINs as every other
        # total, so shares can't pass 100%. The watchlist's count otherwise
        self.counts = {
            n: t.filings if (t := self._totals[n]) else ctx.directory[n]
            for n in ctx.directory
        }

    def _domains(self) -> dict[str, str]:
        if not publish.DOMAINS.exists():
            return {}
        with publish.DOMAINS.open(newline="") as f:
            return {r["company"]: r["domain"] for r in csv.DictReader(f) if r["domain"]}

    def _sponsors(self) -> tuple[dict[str, tuple], tuple[str, str] | None]:
        """FEIN -> tech totals, and the decision dates the data covers."""
        if not SPONSORS.exists():
            return {}, None
        rows = duckdb.sql(f"""
            select employer_fein, tech_filings, tech_new_hire_filings, tech_level_1,
                tech_level_2, tech_level_3, tech_level_4, tech_median_wage
            from read_parquet('{SPONSORS}') where tech_filings > 0
        """).fetchall()
        first, last = duckdb.sql(
            f"select min(first_decision), max(last_decision) from read_parquet('{SPONSORS}')"
        ).fetchone()
        period = (str(first)[:10], str(last)[:10]) if first and last else None
        return {r[0]: r[1:] for r in rows}, period

    def feins(self, name: str) -> list[str]:
        return sorted(
            {f for b in self.boards.get(name, []) for f in b["feins"].split(";") if f}
        )

    def totals(self, name: str) -> Totals | None:
        return self._totals.get(name)

    def _total(self, name: str) -> Totals | None:
        rows = [self.sponsors[f] for f in self.feins(name) if f in self.sponsors]
        if not rows:
            return None
        if self.has_medians(name):
            wage = self.profiles["companies"][name]["wage"]
        elif len(self.feins(name)) == 1 and rows[0][6]:
            wage = round(rows[0][6])
        else:
            wage = None  # a median can't be built from per-FEIN medians
        return Totals(
            filings=sum(r[0] for r in rows),
            new_hire=sum(r[1] for r in rows),
            levels=[sum(r[i] for r in rows) for i in range(2, 6)],
            wage=wage,
        )

    def has_medians(self, name: str) -> bool:
        """True when the profile holds exact medians for the company."""
        return "wage" in (self.profiles["companies"].get(name) or {})

    def field_wage(self, name: str, field: str, ev: dict) -> int | None:
        if self.has_medians(name):
            return (self.profiles["companies"][name].get("field_wages") or {}).get(
                field
            )
        return ev["median_wage"] if len(self.feins(name)) == 1 else None

    def field_wages_known(self, name: str) -> bool:
        return self.has_medians(name) or len(self.feins(name)) == 1

    def evidence(self, name: str) -> dict[str, dict | None]:
        info = self.ctx.companies.get(name)
        if info and "evidence" in info:
            return info["evidence"] or {}
        if self._index is None:
            try:
                self._index = EvidenceIndex(ROLE_EVIDENCE, layer.WATCHLIST)
            except (duckdb.Error, OSError):
                self._index = False  # missing: don't try again for every company
        if not self._index:
            return {}
        return self._index.company(
            [(b["system"], b["slug"]) for b in self.boards.get(name, [])]
        )

    def careers(self, name: str) -> list[str]:
        info = self.ctx.companies.get(name)
        if info and info.get("careers"):
            return info["careers"]
        return [
            feeds.careers_url(b["system"], b["slug"], b["eu"] == "1")
            for b in self.boards.get(name, [])
        ]

    def logo(self, name: str) -> str | None:
        info = self.ctx.companies.get(name) or {}
        if info.get("logo"):
            return info["logo"]
        d = self.domains.get(name)
        return (
            f"logos/{d}.png"
            if d and (layer.SITE / "logos" / f"{d}.png").exists()
            else None
        )

    def notice(self, name: str) -> dict | None:
        info = self.ctx.companies.get(name) or {}
        return info.get("notice") or self.notices.get(name)


def period_text(src: Sources) -> str:
    if not src.period:
        return "in the Department of Labor data FiledFor uses"
    return f"from {month(src.period[0])} to {month(src.period[1])}"


# Page sections. Each returns "" when it has nothing to show.


def summary(name: str, filings: int, t: Totals | None, jobs: int, src: Sources) -> str:
    text = (
        f"{esc(name)} filed {num(filings)} H-1B Labor Condition Applications (LCAs) "
        f"for tech roles that the Department of Labor certified, {period_text(src)}."
    )
    bits = []
    if filings < SMALL:
        t = None  # too few for a share or a median
    if t and (share := pct(t.new_hire, filings)) is not None:
        bits.append(f"{share}% were for new H-1B employment")
    if t and t.wage:
        bits.append(f"the median wage on them was {layer.money(t.wage)} a year")
    if bits:
        sentence = " and ".join(bits)
        text += f" {sentence[0].upper()}{sentence[1:]}."
    text += (
        f" FiledFor lists {num(jobs)} open tech job{'s' if jobs != 1 else ''} there right now."
        if jobs
        else " FiledFor has no open tech jobs listed there right now."
    )
    return f'<p class="lede">{text}</p>'


def tiles(filings: int, t: Totals | None, src: Sources) -> str:
    when = (
        f"{month(src.period[0])} to {month(src.period[1])}"
        if src.period
        else "all years on file"
    )
    out = [(num(filings), f"tech LCA filings, {when}")]
    small = ""
    if filings < SMALL:
        t = None
        small = (
            f'<p class="cc-fine">With fewer than {SMALL} filings, the sample is too small '
            "for shares or a median wage to mean much, so only the count is shown.</p>"
        )
    if t:
        if (share := pct(t.new_hire, filings)) is not None:
            out.append(
                (
                    f"{share}%",
                    f"for new H-1B employment ({count(t.new_hire, 'filing')})",
                )
            )
        if (low := pct(t.levels[0] + t.levels[1], sum(t.levels))) is not None:
            out.append((f"{low}%", "at wage level I or II, the lower two of four"))
        if t.wage:
            out.append((layer.money(t.wage), "median yearly wage on these filings"))
    return (
        '<div class="stats">'
        + "".join(
            f'<div class="stat"><b>{esc(v)}</b><span>{esc(k)}</span></div>'
            for v, k in out
        )
        + "</div>"
        + small
    )


def notice_box(n: dict | None) -> str:
    if not n:
        return ""
    src = n.get("source") or ""
    link = (
        f' <a href="{esc(src)}" rel="noopener">Source</a>'
        if src.startswith(("https://", "http://"))
        else ""
    )
    return (
        f'<div class="notice-box" role="note"><strong>{esc(n.get("notice") or "Notice")}</strong>'
        f" ({esc(n.get('date') or 'date not given')}). {esc(n.get('detail') or '')}{link}</div>"
    )


def open_jobs(ctx: Context, name: str, careers: list[str]) -> str:
    jobs = ctx.jobs_at(name)
    head = f"<h2>Open tech jobs at {esc(name)}</h2>"
    careers_links = (
        '<ul class="links">'
        + "".join(
            f'<li><a href="{esc(u)}" rel="noopener">{esc(host(u))}</a></li>'
            for u in careers
        )
        + "</ul>"
        if careers
        else ""
    )
    if not jobs:
        return (
            head
            + f"<p>No open tech jobs at {esc(name)} are listed on FiledFor right now. "
            "FiledFor checks its job board every 30 minutes, so new ones show up here soon after they are posted."
            + (
                " You can also look at its careers pages directly:</p>" + careers_links
                if careers
                else "</p>"
            )
        )
    n = len(jobs)
    shown = min(n, JOBS_SHOWN)
    return (
        head
        + (
            f"<p>{num(n)} open tech jobs that don't say they rule out visa sponsorship"
            if n > 1
            else "<p>1 open tech job that doesn't say it rules out visa sponsorship"
        )
        + (f". The {shown} newest are below.</p>" if n > shown else ".</p>")
        + layer.job_list(ctx, jobs, limit=JOBS_SHOWN, show_company=False)
        + f'<p class="cc-actions"><a class="cta" href="{esc(layer.app_link(company=name))}">'
        + (f"See all {num(n)} on the job board" if n > 1 else "See it on the job board")
        + "</a></p>"
    )


def host(url: str) -> str:
    """'https://jobs.lever.co/acme' -> 'jobs.lever.co/acme', for link text."""
    return url.split("://", 1)[-1].rstrip("/")


def by_field(name: str, ev: dict[str, dict | None], src: Sources) -> str:
    rows = sorted(
        ((f, e) for f, e in ev.items() if e and f in FIELD_NAMES),
        key=lambda x: -x[1]["filings"],
    )
    if not rows:
        return ""
    wages = src.field_wages_known(name)
    cells = []
    for f, e in rows:
        enough = e["filings"] >= SMALL
        share = low_share(e) if enough else None
        wage = src.field_wage(name, f, e) if enough else None
        cells.append(
            f'<tr><td>{esc(FIELD_NAMES[f])}</td><td class="num">{num(e["filings"])}</td>'
            f'<td class="num">{num(e["new_hire_filings"])}</td>'
            f'<td class="num">{"n/a" if share is None else f"{share}%"}</td>'
            + (f'<td class="num">{layer.money(wage) or "n/a"}</td>' if wages else "")
            + "</tr>"
        )
    notes = (
        "One filing can count in more than one field: Software Developer filings count toward "
        "software, AI/ML and data, because those roles are often filed under that job code. "
        "Level I-II is the share of filings at the lower two of four prevailing wage levels."
    )
    if any(e["filings"] < SMALL for _, e in rows):
        notes += f" n/a means fewer than {SMALL} filings in that field, too few for a share or a median."
    return (
        "<h2>Filings by field</h2>"
        f'<div class="table-scroll"><table class="ptable"><caption class="sr-only">{esc(name)} tech LCA filings by field</caption>'
        '<thead><tr><th scope="col">Field</th><th scope="col" class="num">Filings</th>'
        '<th scope="col" class="num">New employment</th><th scope="col" class="num">Level I-II</th>'
        + ('<th scope="col" class="num">Median wage</th>' if wages else "")
        + f"</tr></thead><tbody>{''.join(cells)}</tbody></table></div>"
        f'<p class="cc-fine">{esc(notes)}</p>'
    )


def quarters(name: str, p: dict | None, meta: dict) -> str:
    """A column per DOL fiscal quarter, values written on the columns, so
    the chart reads as text too. A quarter missing from the data is drawn
    as an empty slot, never bridged."""
    qs = meta.get("quarters") or []
    values = (p or {}).get("quarters") or []
    if not qs or len(values) != len(qs):
        return ""
    top = max((v for v in values if v), default=0)
    if not top:
        return ""
    cols = []
    for (fy, q), v in zip(qs, values, strict=True):
        label = f'<span class="qlabel">FY{fy % 100:02d} Q{q}<small>{esc(quarter_months(fy, q))}</small></span>'
        if v is None:
            cols.append(
                f'<li class="qgap">{label}<span class="qval">no data</span><span class="qbar"></span></li>'
            )
        else:
            h = 0 if not v else max(2, round(v * 100 / top))
            cols.append(
                f'<li>{label}<span class="qval">{num(v)}</span>'
                f'<span class="qbar" style="--h:{h}" title="{esc(f"FY{fy} Q{q}: {count(v, 'filing')}")}"></span></li>'
            )
    notes = []
    if meta.get("missing"):
        gaps = ", ".join(f"FY{fy} Q{q}" for fy, q in meta["missing"])
        notes.append(
            f"The Department of Labor files for {gaps} are not in this data, so those quarters are left blank."
        )
    for m in meta.get("low_months") or []:
        y, mo = int(m[:4]), int(m[5:7])
        fy, q = (y + 1, 1) if mo >= 10 else (y, (mo + 2) // 3 + 1)
        why = LOW_MONTH_REASONS.get(m)
        notes.append(
            f"Very few filings were decided in {MONTH_NAMES[mo - 1]} {y} across all employers"
            + (f", {why}" if why else "")
            + f", so FY{fy} Q{q} looks low for every company."
        )
    return (
        "<h2>Filings by quarter</h2>"
        f'<p class="cc-fine">Tech LCA filings by the quarter they were decided. Quarters are federal fiscal quarters: '
        f"FY{qs[0][0] % 100:02d} Q1 runs from October to December {qs[0][0] - 1}.</p>"
        f'<ol class="qchart" aria-label="{esc(name)} tech LCA filings by quarter">{"".join(cols)}</ol>'
        + "".join(f'<p class="cc-fine">{esc(n)}</p>' for n in notes)
    )


def titles_and_cities(name: str, p: dict | None) -> str:
    if not p:
        return ""
    parts = []
    if p.get("titles"):
        rows = "".join(
            f'<tr><td>{esc(t)}</td><td class="num">{num(n)}</td><td class="num">{layer.money(w) or "n/a"}</td></tr>'
            for t, n, w in p["titles"]
        )
        parts.append(
            '<section><h2>Job titles filed most</h2><div class="table-scroll"><table class="ptable">'
            f'<caption class="sr-only">Job titles on {esc(name)} tech LCA filings</caption>'
            '<thead><tr><th scope="col">Job title as filed</th><th scope="col" class="num">Filings</th>'
            f'<th scope="col" class="num">Median wage</th></tr></thead><tbody>{rows}</tbody></table></div></section>'
        )
    if p.get("cities"):
        rows = "".join(
            f'<tr><td>{esc(c)}{", " + esc(s) if s else ""}</td><td class="num">{num(n)}</td></tr>'
            for c, s, n in p["cities"]
        )
        parts.append(
            '<section><h2>Worksite cities</h2><div class="table-scroll"><table class="ptable">'
            f'<caption class="sr-only">Worksite cities on {esc(name)} tech LCA filings</caption>'
            '<thead><tr><th scope="col">City</th><th scope="col" class="num">Filings</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div></section>"
        )
    if not parts:
        return ""
    span = ""
    if p.get("first") and p.get("last"):
        span = (
            f'<p class="cc-fine">From the same tech filings, decided {esc(month(p["first"], day=True))} '
            f"to {esc(month(p['last'], day=True))}. Wages are yearly medians as filed.</p>"
        )
    return f'<div class="cc-pair">{"".join(parts)}</div>{span}'


@dataclass
class Mix:
    """A company's filings by field as a unit vector, for finding similar
    sponsors."""

    vector: dict[str, float]


def field_mix(ev: dict[str, dict | None]) -> Mix:
    counts = {f: e["filings"] for f, e in ev.items() if e}
    norm = math.sqrt(sum(n * n for n in counts.values())) or 1
    return Mix({f: n / norm for f, n in counts.items()})


def peers(counts: dict[str, int], mixes: dict[str, Mix]) -> dict[str, list[str]]:
    """name -> up to SIMILAR companies closest in filing volume and in mix of
    fields. Field counts overlap (one Software Developer filing counts in
    three fields), so a single "main field" says little; the whole mix is
    compared instead. Only the nearest by volume are scored."""
    pool = sorted((math.log(counts[n]), n) for n, m in mixes.items() if m.vector)
    out: dict[str, list[str]] = {}
    for size, n in pool:
        i = bisect.bisect_left(pool, (size, n))
        scored = []
        for o_size, o in pool[max(0, i - NEAR) : i + NEAR + 1]:
            if o != n:
                cos = sum(
                    v * mixes[o].vector.get(f, 0) for f, v in mixes[n].vector.items()
                )
                scored.append((abs(o_size - size) + MIX_WEIGHT * (1 - cos), o))
        out[n] = [o for _, o in sorted(scored)[:SIMILAR]]
    return out


def similar(ctx: Context, others: list[str], counts: dict[str, int]) -> str:
    if not others:
        return ""
    links = "".join(
        f'<li><a href="{esc(ctx.company_url(o))}">{esc(o)} <span class="cc-n">{num(counts[o])}</span></a></li>'
        for o in sorted(others, key=lambda o: (-counts[o], o))
    )
    return (
        "<h2>Similar sponsors</h2>"
        "<p>Companies with a similar number of tech filings, spread across similar fields. "
        "Each shows its tech LCA filings.</p>"
        f'<ul class="links">{links}</ul>'
    )


def meaning(src: Sources) -> str:
    return (
        "<h2>What these numbers mean</h2>"
        '<div class="note"><p>An LCA (Labor Condition Application) is a filing an employer makes with the '
        "Department of Labor before hiring or keeping an H-1B worker in a role, at a stated wage. "
        "It is not a visa approval or a hire: one LCA can cover a role that is never filled, "
        "and the visa itself is decided later by USCIS. Certified means the Department of Labor accepted the filing.</p>"
        "<p>Counts here are certified H-1B LCAs for tech roles (computer occupations, plus hardware engineers and "
        "IT managers), including ones the employer withdrew after certification. "
        + NEW_EMPLOYMENT
        + " "
        "Wage levels I to IV are the prevailing wage levels for the role and city, from entry level (I) to the most "
        f"experienced (IV).</p><p>Source: U.S. Department of Labor LCA disclosure data, decisions {period_text(src)}, "
        'matched to companies by employer tax ID (FEIN). <a href="https://www.dol.gov/agencies/eta/foreign-labor/performance" '
        'rel="noopener">DOL performance data</a>.</p></div>'
    )


def title_for(name: str) -> str:
    for t in (
        f"{name} H-1B Sponsorship and Tech Jobs | FiledFor",
        f"{name} H-1B Filings and Jobs | FiledFor",
        f"{name} H-1B Filings | FiledFor",
    ):
        if len(t) <= 60:
            return t
    return f"{name} H-1B Filings"


def description_for(
    name: str, filings: int, t: Totals | None, jobs: int, src: Sources, history: bool
) -> str:
    when = (
        f" from {month(src.period[0])} to {month(src.period[1])}" if src.period else ""
    )
    d = f"{name} filed {num(filings)} tech H-1B LCAs{when}"
    if t and t.wage and filings >= SMALL:
        d += f", median wage {layer.money(t.wage)}"
    d += "." + (
        f" See {num(jobs)} open tech jobs" if jobs else " See its careers pages"
    )
    d += ", job titles and cities filed." if history else "."
    return d if len(d) <= 160 else d[:157].rsplit(" ", 1)[0] + "..."


def company_page(ctx: Context, name: str, src: Sources, others: list[str]) -> Page:
    path = ctx.company_url(name).lstrip("/")
    filings = src.counts[name]
    t = src.totals(name)
    ev = src.evidence(name)
    p = src.profiles["companies"].get(name)
    jobs = len(ctx.jobs_at(name))
    logo = src.logo(name)
    head = (
        '<div class="cc-head">'
        + (
            f'<img class="cc-logo" src="/{esc(logo)}" alt="" width="48" height="48">'
            if logo
            else ""
        )
        + f"<h1>{esc(name)}: H-1B filings and open tech jobs</h1></div>"
    )
    body = (
        head
        + summary(name, filings, t, jobs, src)
        + notice_box(src.notice(name))
        + tiles(filings, t, src)
        + open_jobs(ctx, name, src.careers(name))
        + by_field(name, ev, src)
        + quarters(name, p, src.profiles["meta"])
        + titles_and_cities(name, p)
        + similar(ctx, others, src.counts)
        + meaning(src)
    )
    org = {"@context": "https://schema.org", "@type": "Organization", "name": name}
    if d := src.domains.get(name):
        org["url"] = f"https://{d}/"
    if logo:
        org["logo"] = layer.SITE_URL + logo
    return Page(
        nav="companies",
        path=path,
        title=title_for(name),
        description=description_for(
            name,
            filings,
            t,
            jobs,
            src,
            bool(p and (p.get("titles") or p.get("cities"))),
        ),
        body=body,
        crumbs=[("Companies", "companies/"), (name, path)],
        jsonld=[org],
        css=["pages-company.css"],
        group="companies",
    )


def index_page(ctx: Context, src: Sources) -> Page:
    names = sorted(ctx.directory, key=lambda n: (n.casefold(), n))
    groups: dict[str, list[str]] = {}
    for n in names:
        first = layer.slug(n)[0].upper()
        groups.setdefault(first if first.isalpha() else "0-9", []).append(n)
    letters = sorted(groups, key=lambda k: (k != "0-9", k))
    jump = "".join(f'<a href="#letter-{esc(k.lower())}">{esc(k)}</a>' for k in letters)
    sections = []
    for k in letters:
        items = []
        for n in groups[k]:
            jobs = len(ctx.jobs_at(n))
            meta = count(src.counts[n], "filing") + (
                f" · {count(jobs, 'open job')}" if jobs else ""
            )
            items.append(
                f'<li><a href="{esc(ctx.company_url(n))}">{esc(n)}</a><span>{meta}</span></li>'
            )
        sections.append(
            f'<section id="letter-{esc(k.lower())}"><h2>{esc(k)}</h2><ul class="cc-index">{"".join(items)}</ul></section>'
        )
    total = len(names)
    with_jobs = sum(1 for n in names if ctx.jobs_at(n))
    body = (
        "<h1>Companies with H-1B filings for tech roles</h1>"
        f'<p class="lede">{num(total)} companies FiledFor watches have filed H-1B Labor Condition Applications (LCAs) '
        f"for tech roles {period_text(src)}, and {num(with_jobs)} of them have open tech jobs listed right now. "
        "Each page shows what the company filed: how many, for which fields and job titles, at what wages and where.</p>"
        '<p class="cc-fine">Numbers are tech LCA filings certified by the Department of Labor. '
        "An LCA is a step before an H-1B petition, not a visa approval or a hire.</p>"
        # Shown by pages.js, which filters the list below as you type
        '<div class="cc-find" hidden><label class="search">'
        '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>'
        '<span class="visually-hidden">Search companies</span>'
        f'<input type="search" id="cc-q" placeholder="Search {num(total)} companies, like ‘NVIDIA’ or ‘Salesforce’" autocomplete="off" spellcheck="false">'
        '</label><p class="cc-count" aria-live="polite"></p></div>'
        f'<nav class="cc-jump" aria-label="Jump to letter">{jump}</nav>'
        + "".join(sections)
    )
    return Page(
        nav="companies",
        path="companies/",
        title="Companies with H-1B Filings for Tech Roles | FiledFor",
        description=(
            f"{num(total)} companies with H-1B LCA filings for tech roles, A to Z, with filing counts "
            f"and open jobs. {num(with_jobs)} are hiring now."
        ),
        body=body,
        crumbs=[("Companies", "companies/")],
        css=["pages-company.css"],
        group="companies",
    )


def pages(ctx: Context) -> list[Page]:
    src = Sources(ctx)
    similar_to = peers(
        src.counts, {n: field_mix(src.evidence(n)) for n in ctx.directory}
    )
    return [index_page(ctx, src)] + [
        company_page(ctx, n, src, similar_to.get(n, [])) for n in sorted(ctx.directory)
    ]
