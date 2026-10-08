"""Build the watchlist: which job feeds to poll, and which DOL employer each one is.

1. feeds:   Greenhouse, Lever, Ashby, Workday and SmartRecruiters boards
            found in the SimplifyJobs listings (used only to discover slugs,
            never republished).
2. match:   link each feed's company to DOL employers (FEINs) with tiered
            rules. See ADR-001 in docs/decisions.md for why these rules.
3. probe:   call every feed once, drop dead ones, write companies.csv.
            For Workday this also finds the board's United States filter.

Match rules, strongest first. Every match records the rule that made it:

    alias      hand-written in data/aliases.csv, always wins
    exact      normalized names are equal, accepted automatically
               when they point at a single FEIN
    squashed   equal once spaces are removed, also tried on the slug
    prefix     the DOL name starts with the feed name as whole words

Anything short of an automatic accept goes to data/match_reviews.csv, where a
person marks each candidate accept or reject. Decisions are kept across
rebuilds, so nothing is reviewed twice.
"""

import asyncio
import csv
import json
import re
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import httpx

from filedfor.feeds import USER_AGENT, board_url, workday_jobs_url

LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/"
    ".github/scripts/listings.json"
)
LISTINGS = Path("data/raw/listings.json")
CLEAN = Path("data/lca/clean.parquet")
ALIASES = Path("data/aliases.csv")
REVIEWS = Path("data/match_reviews.csv")
MATCHES = Path("data/company_matches.csv")
COMPANIES = Path("data/companies.csv")
LABELS = Path("data/match_labels.csv")

# The board slug sits in a different place in each system's URLs
FEED_PATTERNS = {
    "greenhouse": re.compile(
        r"job-boards(\.eu)?\.greenhouse\.io/([\w-]+)/jobs|boards(\.eu)?\.greenhouse\.io/([\w-]+)/jobs"
    ),
    "lever": re.compile(r"jobs(\.eu)?\.lever\.co/([\w.-]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)"),
    "smartrecruiters": re.compile(
        r"(?:jobs|careers)\.smartrecruiters\.com/(?:[a-z]{2}(?:-[A-Z]{2})?/)?([\w-]+)/\d"
    ),
    # tenant.wdN.myworkdayjobs.com/[en-US/]site/... or wdN.myworkdaysite.com/recruiting/tenant/site
    "workday": re.compile(
        r"//([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)"
        r"|//(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([\w-]+)/([\w-]+)"
    ),
}
# Greenhouse paths that are not a company board
NOT_A_BOARD = {"embed", "agency"}

# A name used on fewer than this share of a FEIN's filings is more likely a
# mistyped FEIN than another name for the company ("Newton, Inc." under Meta)
MIN_NAME_SHARE = 0.10
# The prefix rule can return dozens of "Sierra ..." companies; keep the biggest
MAX_PREFIX_CANDIDATES = 5

LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "lp",
    "llp",
    "plc",
    "pllc",
    "pc",
    "pbc",
    "na",
    "us",
    "usa",
    "holding",
    "holdings",
    "group",
    "the",
}


def norm(name: str) -> str:
    """Company name reduced to the words that identify it.

    'Amazon.com Services, LLC' -> 'amazon com services'
    'Ernst & Young U.S. LLP'   -> 'ernst and young'
    """
    s = name.lower().replace("&", " and ")
    s = re.sub(r"['’]", "", s)  # "Jerry's" -> "jerrys"
    s = re.sub(
        r"\b(?:[a-z]\.){2,}", lambda m: m[0].replace(".", ""), s
    )  # "u.s." -> "us"
    words = re.sub(r"[^a-z0-9]+", " ", s).split()
    # Legal suffixes only come off the ends, so "US Bank" keeps its "us"
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


@dataclass
class Feed:
    system: str
    slug: str
    eu: bool
    names: Counter = field(default_factory=Counter)
    listings: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return self.system, self.slug

    @property
    def company(self) -> str:
        return self.names.most_common(1)[0][0]


def parse_feed_url(url: str) -> tuple[str, str, bool] | None:
    """(system, slug, is_eu) for a job URL on any supported system.
    A Workday slug is "tenant.wdN/site": the site name is case-sensitive."""
    for system, pattern in FEED_PATTERNS.items():
        m = pattern.search(url)
        if not m:
            continue
        if system == "greenhouse":
            eu, slug = (
                (m.group(1), m.group(2)) if m.group(2) else (m.group(3), m.group(4))
            )
        elif system == "lever":
            eu, slug = m.group(1), m.group(2)
        elif system == "workday":
            tenant, dc, site = m.group(1, 2, 3) if m.group(1) else m.group(5, 4, 6)
            return system, f"{tenant.lower()}.{dc}/{site}", False
        else:
            eu, slug = None, m.group(1)
        slug = urllib.request.unquote(slug).lower()
        if slug in NOT_A_BOARD:
            return None
        return system, slug, bool(eu)
    return None


def download_listings() -> None:
    LISTINGS.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(LISTINGS_URL, LISTINGS)


def extract_feeds(listings: list[dict]) -> list[Feed]:
    feeds: dict[tuple[str, str], Feed] = {}
    for item in listings:
        parsed = parse_feed_url(item["url"])
        if not parsed:
            continue
        system, slug, eu = parsed
        # Workday treats NVIDIAExternalCareerSite and nvidiaexternalcareersite
        # as one board; keep the first spelling seen
        key = (system, slug.lower())
        feed = feeds.setdefault(key, Feed(system, slug, eu))
        feed.names[item["company_name"].strip()] += 1
        feed.listings += 1
    return sorted(feeds.values(), key=lambda f: f.key)


@dataclass
class Employer:
    fein: str
    name: str
    tech_filings: int
    norm_names: set[str]


def load_employers(con: duckdb.DuckDBPyConnection) -> dict[str, Employer]:
    """Employers with tech filings, each with the normalized names it files under."""
    rows = con.sql(f"""
        select employer_fein, employer_name, count(*) as n
        from read_parquet('{CLEAN}')
        where is_tech
        group by all
        order by employer_fein, n desc, employer_name  -- stable tie-breaks
    """).fetchall()
    by_fein: dict[str, Counter] = defaultdict(Counter)
    raw_names: dict[str, Counter] = defaultdict(Counter)
    for fein, name, n in rows:
        by_fein[fein][norm(name)] += n
        raw_names[fein][name] += n
    employers = {}
    for fein, names in by_fein.items():
        total = sum(names.values())
        top = names.most_common(1)[0][0]
        kept = {
            n
            for n, c in names.items()
            if n and (n == top or c / total >= MIN_NAME_SHARE)
        }
        employers[fein] = Employer(
            fein, raw_names[fein].most_common(1)[0][0], total, kept
        )
    return employers


@dataclass
class Candidate:
    fein: str
    rule: str


def find_candidates(
    feeds: list[Feed],
    employers: dict[str, Employer],
    rejected: set[tuple[str, str, str]] = frozenset(),
) -> dict:
    """Candidate FEINs for each feed from the strongest rule that finds any.

    FEINs a reviewer already rejected for a feed don't count, so rejecting
    every candidate at one rule lets the next rule have a go.
    """
    exact, squashed, prefix = defaultdict(set), defaultdict(set), defaultdict(set)
    for e in employers.values():
        for n in e.norm_names:
            exact[n].add(e.fein)
            squashed[n.replace(" ", "")].add(e.fein)
            words = n.split()
            for i in range(1, len(words)):
                prefix[" ".join(words[:i])].add(e.fein)

    out: dict[tuple[str, str], list[Candidate]] = {}
    for feed in feeds:
        # Every name the feed is listed under: "Sigma" alone would miss
        # Sigma Computing, which the same board is also listed as
        names = {n for n in map(norm, feed.names) if n}
        if not names:
            continue
        # A Workday slug's tenant ("nvidia" in nvidia.wd5/...) is the name part
        slug_name = feed.slug.split(".")[0] if feed.system == "workday" else feed.slug
        squashed_names = {n.replace(" ", "") for n in names} | {
            re.sub(r"[^a-z0-9]", "", slug_name)
        }
        rules = [
            ("exact", set().union(*(exact.get(n, set()) for n in names))),
            (
                "squashed",
                set().union(*(squashed.get(n, set()) for n in squashed_names)),
            ),
            ("prefix", set().union(*(prefix.get(n, set()) for n in names))),
        ]
        for rule, feins in rules:
            ranked = sorted(feins, key=lambda f: (-employers[f].tech_filings, f))
            if rule == "prefix":
                ranked = ranked[:MAX_PREFIX_CANDIDATES]
            # Cap first, then drop rejections: rejecting the top five
            # "Quantum ..." firms must not bring up the next five
            ranked = [f for f in ranked if (*feed.key, f) not in rejected]
            if ranked:
                out[feed.key] = [Candidate(f, rule) for f in ranked]
                break
    return out


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)


REVIEW_FIELDS = [
    "system",
    "slug",
    "company",
    "rule",
    "fein",
    "dol_name",
    "tech_filings",
    "decision",
]
MATCH_FIELDS = ["system", "slug", "company", "fein", "dol_name", "tech_filings", "rule"]


def match(feeds: list[Feed], employers: dict[str, Employer]) -> Counter:
    """Write company_matches.csv and add new candidates to the review file."""
    aliases = defaultdict(list)
    for row in read_csv(ALIASES):
        if row["fein"] not in employers:
            raise ValueError(
                f"{ALIASES}: {row['fein']} has no tech filings ({row['slug']})"
            )
        aliases[(row["system"], row["slug"])].append(row["fein"])
    reviews = {(r["system"], r["slug"], r["fein"]): r for r in read_csv(REVIEWS)}
    for r in reviews.values():
        if r["decision"] not in ("", "accept", "reject"):
            raise ValueError(
                f"{REVIEWS}: decision must be accept or reject, got {r['decision']!r}"
            )
    rejected = {k for k, r in reviews.items() if r["decision"] == "reject"}
    candidates = find_candidates(feeds, employers, rejected)

    matches, stats = [], Counter()
    for feed in feeds:
        base = {"system": feed.system, "slug": feed.slug, "company": feed.company}
        if feed.key in aliases:
            accepted = [Candidate(f, "alias") for f in aliases[feed.key]]
        else:
            cands = candidates.get(feed.key, [])
            auto = len(cands) == 1 and cands[0].rule == "exact"
            accepted = []
            for c in cands:
                key = (feed.system, feed.slug, c.fein)
                if auto:
                    accepted.append(c)
                    continue
                if key not in reviews:
                    e = employers[c.fein]
                    reviews[key] = base | {
                        "rule": c.rule,
                        "fein": c.fein,
                        "dol_name": e.name,
                        "tech_filings": e.tech_filings,
                        "decision": "",
                    }
                if reviews[key]["decision"] == "accept":
                    accepted.append(c)
        if accepted:
            stats[accepted[0].rule] += 1
        elif feed.key not in aliases and any(
            not reviews[(feed.system, feed.slug, c.fein)]["decision"]
            for c in candidates.get(feed.key, [])
        ):
            stats["pending review"] += 1
        elif feed.key in candidates:
            stats["all rejected"] += 1
        else:
            stats["no match"] += 1
        for c in accepted:
            e = employers[c.fein]
            matches.append(
                base
                | {
                    "fein": c.fein,
                    "dol_name": e.name,
                    "tech_filings": e.tech_filings,
                    "rule": c.rule,
                }
            )

    # Decisions are kept forever; open questions only while they still apply
    current = {(k[0], k[1], c.fein) for k, cs in candidates.items() for c in cs}
    kept = [r for key, r in reviews.items() if r["decision"] or key in current]
    kept.sort(key=lambda r: (r["system"], r["slug"], -int(r["tech_filings"])))
    write_csv(MATCHES, matches, MATCH_FIELDS)
    write_csv(REVIEWS, kept, REVIEW_FIELDS)
    return stats


def count_jobs(system: str, body) -> int:
    if system == "lever":
        return len(body)
    if system == "smartrecruiters":
        return body["totalFound"]
    if system == "workday":
        return body["total"]
    return len(body["jobs"])


US_NAMES = {"united states", "united states of america", "usa", "us"}


def us_filter(facets: list[dict]) -> str:
    """'facet=value id' for a Workday board's United States filter, or ''.
    Boards name the facet differently (locationCountry, Location_Country,
    locationHierarchy1) and some nest it, so search by the value's label."""
    for f in facets:
        for v in f.get("values", []):
            if "facetParameter" in v:
                found = us_filter([v])
                if found:
                    return found
            elif (v.get("descriptor") or "").strip().lower() in US_NAMES:
                return f"{f['facetParameter']}={v['id']}"
    return ""


async def probe(
    feeds: list[Feed], concurrency: int = 8
) -> dict[tuple[str, str], tuple[str, int, str]]:
    """Call each feed once: ('ok', job count, US filter) or ('dead', HTTP status, '')."""
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": USER_AGENT}

    async def one(client: httpx.AsyncClient, feed: Feed):
        async with sem:
            for attempt in range(3):
                try:
                    if feed.system == "workday":
                        r = await client.post(
                            workday_jobs_url(feed.slug),
                            json={
                                "appliedFacets": {},
                                "limit": 1,
                                "offset": 0,
                                "searchText": "",
                            },
                        )
                    else:
                        r = await client.get(board_url(feed.system, feed.slug, feed.eu))
                    if r.status_code == 200:
                        body = r.json()
                        found = (
                            us_filter(body.get("facets", []))
                            if feed.system == "workday"
                            else ""
                        )
                        return feed.key, ("ok", count_jobs(feed.system, body), found)
                    if r.status_code < 500 and r.status_code != 429:
                        return feed.key, ("dead", r.status_code, "")
                except (httpx.TransportError, json.JSONDecodeError, KeyError):
                    pass
                await asyncio.sleep(2**attempt)
            return feed.key, ("error", 0, "")

    async with httpx.AsyncClient(
        timeout=20, headers=headers, follow_redirects=True
    ) as client:
        return dict(await asyncio.gather(*(one(client, f) for f in feeds)))


COMPANY_FIELDS = [
    "system",
    "slug",
    "eu",
    "company",
    "open_jobs",
    "feins",
    "tech_filings",
    "us_filter",
]


def write_companies(feeds: list[Feed], status: dict) -> Counter:
    by_feed = defaultdict(list)
    for m in read_csv(MATCHES):
        by_feed[(m["system"], m["slug"])].append(m)
    # A board that only timed out keeps its last known job count and filter;
    # only a real HTTP error (404, 410...) takes it off the watchlist
    before = {(r["system"], r["slug"]): r for r in read_csv(COMPANIES)}
    rows, stats = [], Counter()
    for feed in feeds:
        state, n, found = status[feed.key]
        stats[state] += 1
        if state == "error" and feed.key in before:
            n, found = (
                before[feed.key]["open_jobs"],
                before[feed.key].get("us_filter", ""),
            )
        elif state != "ok":
            continue
        ms = by_feed[feed.key]
        rows.append(
            {
                "system": feed.system,
                "slug": feed.slug,
                "eu": int(feed.eu),
                "company": feed.company,
                "open_jobs": n,
                "feins": ";".join(m["fein"] for m in ms),
                "tech_filings": sum(int(m["tech_filings"]) for m in ms),
                "us_filter": found,
            }
        )
    write_csv(COMPANIES, rows, COMPANY_FIELDS)
    return stats


def evaluate() -> None:
    """Score company_matches.csv against the hand-labelled sample.

    Precision: of the FEINs we attached, how many are right.
    Recall:    of the FEINs that should be attached, how many we found.
    Scored per (feed, FEIN) pair, so attaching one right and one wrong FEIN
    to the same company counts as one hit and one false match.
    """
    labels = read_csv(LABELS)
    predicted = defaultdict(dict)
    for m in read_csv(MATCHES):
        predicted[(m["system"], m["slug"])][m["fein"]] = m["rule"]
    tp, fp, fn = Counter(), Counter(), 0
    for row in labels:
        truth = {f for f in row["true_feins"].split(";") if f}
        pred = predicted[(row["system"], row["slug"])]
        for fein, rule in pred.items():
            (tp if fein in truth else fp)[rule] += 1
            if fein not in truth:
                print(f"  false match  {row['company']} -> {fein}")
        for fein in truth - pred.keys():
            fn += 1
            print(f"  missed       {row['company']} -> {fein}")
    hits, wrong = sum(tp.values()), sum(fp.values())
    with_filings = sum(bool(r["true_feins"]) for r in labels)
    print(f"sample              {len(labels):>6}   ({with_filings} with tech filings)")
    print(
        f"precision           {hits / max(hits + wrong, 1):>6.0%}   ({hits} of {hits + wrong})"
    )
    print(
        f"recall              {hits / max(hits + fn, 1):>6.0%}   ({hits} of {hits + fn})"
    )
    for rule in sorted(tp.keys() | fp.keys()):
        print(f"  {rule:<17} {tp[rule]:>3} right, {fp[rule]} wrong")


def main(probe_feeds: bool = True) -> None:
    if not LISTINGS.exists():
        download_listings()
    feeds = extract_feeds(json.loads(LISTINGS.read_text()))
    print(
        f"feeds               {len(feeds):>6,}   "
        + ", ".join(
            f"{s} {n}" for s, n in Counter(f.system for f in feeds).most_common()
        )
    )
    employers = load_employers(duckdb.connect())
    print(f"DOL tech employers  {len(employers):>6,}")
    for rule, n in match(feeds, employers).most_common():
        print(f"  {rule:<17} {n:>6,}")
    if probe_feeds:
        stats = write_companies(feeds, asyncio.run(probe(feeds)))
        print(
            "probe               "
            + ", ".join(f"{k} {v}" for k, v in stats.most_common())
        )


if __name__ == "__main__":
    import sys

    if "--eval" in sys.argv:
        evaluate()
    else:
        main(probe_feeds="--no-probe" not in sys.argv)
