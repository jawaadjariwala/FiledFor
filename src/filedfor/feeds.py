"""Fetch job boards and turn each system's response into the same Posting.

Greenhouse, Lever, Ashby, Workday and SmartRecruiters all publish open jobs
through public JSON APIs. Each has its own field names, dates and location formats; the parse_*
functions are the only place that knows about them.

Lever and Ashby include full descriptions in the board list. The others don't,
so fetch_description gets one job at a time, only for new candidates (see
ADR-002).

Workday and SmartRecruiters boards are paged. Both list newest first, so they
are read only as deep as a run needs (see ADR-003).
"""

import asyncio
import html
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

USER_AGENT = (
    "FiledFor (open-source job board; https://github.com/jawaadjariwala/FiledFor)"
)

# Greenhouse serves EU boards from its main API; Lever EU boards need the EU host
API = {
    ("greenhouse", False): "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    ("greenhouse", True): "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    ("lever", False): "https://api.lever.co/v0/postings/{slug}?mode=json",
    ("lever", True): "https://api.eu.lever.co/v0/postings/{slug}?mode=json",
    ("ashby", False): "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    # US jobs only, filtered by SmartRecruiters itself; newest first
    (
        "smartrecruiters",
        False,
    ): "https://api.smartrecruiters.com/v1/companies/{slug}/postings?country=us&limit=100",
}
GREENHOUSE_JOB = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}"

WORKDAY_PAGE = 20  # the most Workday returns per request
WORKDAY_MAX_OFFSET = 2000  # Workday stops paging here, however many jobs there are


@dataclass
class Posting:
    system: str
    slug: str
    job_id: str
    title: str
    url: str
    location: str | None
    country: str | None  # structured country when the system gives one
    workplace: str | bool | None  # Lever "remote"/"onsite"/..., Ashby isRemote
    posted_at: datetime | None
    description: str | None  # plain text; None until fetched (Greenhouse)
    # Yearly pay range from a structured field (Lever); otherwise read from the
    # description later
    salary: tuple[int, int] | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.system, self.slug, self.job_id


def board_url(system: str, slug: str, eu: bool = False) -> str:
    return API[(system, eu and system in ("greenhouse", "lever"))].format(
        slug=urllib.parse.quote(slug, safe="")
    )


def careers_url(system: str, slug: str, eu: bool = False) -> str:
    """The board's public careers page, for people (not the API)."""
    if system == "workday":
        host, site = workday_parts(slug)
        return f"https://{host}/{site}"
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{slug}",
        "lever": f"https://jobs{'.eu' if eu else ''}.lever.co/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/{slug}",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}",
    }[system]


def html_to_text(s: str | None) -> str:
    """Greenhouse double-escapes its HTML; unescape, drop tags, keep line breaks."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<\s*(br|/p|/li|/div|/h\d)\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).astimezone(UTC)
    except ValueError:
        return None


def parse_greenhouse(slug: str, body: dict) -> list[Posting]:
    return [
        Posting(
            system="greenhouse",
            slug=slug,
            job_id=str(j["id"]),
            title=(j.get("title") or "").strip(),
            url=j.get("absolute_url") or "",
            location=(j.get("location") or {}).get("name"),
            country=None,
            workplace=None,
            posted_at=_iso(j.get("first_published") or j.get("updated_at")),
            description=html_to_text(j["content"]) if j.get("content") else None,
        )
        for j in body.get("jobs", [])
    ]


def _lever_salary(r: dict | None) -> tuple[int, int] | None:
    """Lever's structured pay range, made yearly. US dollars only."""
    if not r or r.get("currency") not in (None, "USD") or not r.get("min"):
        return None
    factor = 2080 if "hour" in (r.get("interval") or "") else 1
    low, high = r["min"] * factor, (r.get("max") or r["min"]) * factor
    return (round(low), round(high)) if 20_000 <= low <= high <= 1_000_000 else None


def parse_lever(slug: str, body: list) -> list[Posting]:
    out = []
    for j in body:
        cats = j.get("categories") or {}
        lists = " ".join(
            f"{x.get('text', '')}\n{html_to_text(x.get('content'))}"
            for x in j.get("lists") or []
        )
        created = j.get("createdAt")
        out.append(
            Posting(
                system="lever",
                slug=slug,
                job_id=j["id"],
                title=(j.get("text") or "").strip(),
                url=j.get("hostedUrl") or "",
                location=cats.get("location"),
                country=j.get("country"),
                workplace=j.get("workplaceType"),
                salary=_lever_salary(j.get("salaryRange")),
                posted_at=datetime.fromtimestamp(created / 1000, UTC)
                if created
                else None,
                description="\n".join(
                    filter(
                        None,
                        [j.get("descriptionPlain"), lists, j.get("additionalPlain")],
                    )
                ),
            )
        )
    return out


def parse_ashby(slug: str, body: dict) -> list[Posting]:
    out = []
    for j in body.get("jobs", []):
        if j.get("isListed") is False:
            continue
        addr = (j.get("address") or {}).get("postalAddress") or {}
        out.append(
            Posting(
                system="ashby",
                slug=slug,
                job_id=j["id"],
                title=(j.get("title") or "").strip(),
                url=j.get("jobUrl") or "",
                location=j.get("location"),
                country=addr.get("addressCountry"),
                workplace=j.get("workplaceType") or j.get("isRemote"),
                posted_at=_iso(j.get("publishedAt")),
                description=j.get("descriptionPlain")
                or html_to_text(j.get("descriptionHtml")),
            )
        )
    return out


def workday_parts(slug: str) -> tuple[str, str]:
    """'nvidia.wd5/NVIDIAExternalCareerSite' -> (host, site)."""
    host, site = slug.split("/", 1)
    return f"{host}.myworkdayjobs.com", site


def workday_jobs_url(slug: str) -> str:
    host, site = workday_parts(slug)
    return f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}/jobs"


OLD = 10**6  # stands for "30+ days ago"


def workday_days(posted_on: str | None) -> int | None:
    """Days since posting from Workday's text. None when it doesn't say;
    OLD for "30+ Days Ago", which FiledFor never publishes."""
    s = (posted_on or "").lower()
    if "30+" in s:
        return OLD
    if "today" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\s+days?", s)
    return int(m.group(1)) if m else None


def parse_workday(
    slug: str, body: dict, now: datetime, us_only: bool = False
) -> list[Posting]:
    """One page of a Workday board. Postings over 30 days old are dropped.
    `us_only`: the request was filtered to the US, so every job is."""
    host, site = workday_parts(slug)
    out = []
    for j in body.get("jobPostings", []):
        path = j.get("externalPath") or ""
        days = workday_days(j.get("postedOn"))
        if not path or days == OLD:
            continue
        out.append(
            Posting(
                system="workday",
                slug=slug,
                job_id=path.rsplit("/", 1)[-1],
                title=(j.get("title") or "").strip(),
                url=f"https://{host}/{site}{path}",
                location=j.get("locationsText"),
                country="US" if us_only else None,
                workplace=None,
                # Day precision. A job first seen "Today" is at most one run old
                posted_at=now - timedelta(days=days) if days is not None else None,
                description=None,
            )
        )
    return out


def workday_detail_url(p: Posting) -> str:
    """The posting's page URL with the API prefix the careers site itself uses."""
    host = workday_parts(p.slug)[0]
    tenant = host.split(".")[0]
    return p.url.replace(f"https://{host}/", f"https://{host}/wday/cxs/{tenant}/", 1)


def parse_smartrecruiters(slug: str, body: dict) -> list[Posting]:
    out = []
    for j in body.get("content", []):
        loc = j.get("location") or {}
        out.append(
            Posting(
                system="smartrecruiters",
                slug=slug,
                job_id=str(j["id"]),
                title=(j.get("name") or "").strip(),
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                location=loc.get("fullLocation"),
                country=loc.get("country"),
                workplace=loc.get("remote"),
                posted_at=_iso(j.get("releasedDate")),
                description=None,
            )
        )
    return out


SMARTRECRUITERS_JOB = (
    "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}"
)


PARSERS = {"greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby}


@dataclass
class BoardResult:
    system: str
    slug: str
    ok: bool
    postings: list[Posting]
    error: str | None = None
    # False when only the newest pages were read, so a job missing from
    # `postings` may still be open further down (Workday quick reads)
    complete: bool = True


def retry_wait(r: httpx.Response | None, attempt: int) -> float:
    """Seconds before the next try. A 429 means slow down: honour Retry-After
    (capped) or back off harder than for a timeout."""
    if r is not None and r.status_code == 429:
        after = r.headers.get("Retry-After", "")
        return min(float(after), 30) if after.isdigit() else 5 * 2**attempt
    return 2**attempt


async def get_json(client: httpx.AsyncClient, url: str, tries: int = 4, body=None):
    """GET (or POST `body`) with retries on timeouts, 429 and 5xx. Other
    statuses fail at once."""
    last = "no attempt"
    for attempt in range(tries):
        r = None
        try:
            r = await (client.get(url) if body is None else client.post(url, json=body))
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                break
        except (httpx.TransportError, ValueError) as e:
            last = type(e).__name__
        if attempt < tries - 1:
            await asyncio.sleep(retry_wait(r, attempt))
    raise RuntimeError(last)


async def fetch_board(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, system: str, slug: str, eu: bool
) -> BoardResult:
    async with sem:
        try:
            body = await get_json(client, board_url(system, slug, eu))
            return BoardResult(system, slug, True, PARSERS[system](slug, body))
        except Exception as e:  # noqa: BLE001 (one broken board must never stop the run)
            return BoardResult(system, slug, False, [], str(e) or type(e).__name__)


async def fetch_workday(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    slug: str,
    us_filter: str,
    full: bool,
    now: datetime | None = None,
) -> BoardResult:
    """Read a Workday board newest first.

    full:  page until a page holds only jobs over 30 days old (or Workday's
           paging limit), so every recent job is seen and closed ones can be
           detected.
    quick: page while a page still has jobs posted today or yesterday. Cheap,
           finds new jobs, but can't tell what closed.
    us_filter: "facet=value id" for the board's country filter, if it has one.
    """
    now = now or datetime.now(UTC)
    url = workday_jobs_url(slug)
    facets = {}
    if us_filter:
        name, value = us_filter.split("=", 1)
        facets = {name: [value]}
    postings: list[Posting] = []
    offset = 0
    async with sem:
        try:
            while True:
                body = await get_json(
                    client,
                    url,
                    body={
                        "appliedFacets": facets,
                        "limit": WORKDAY_PAGE,
                        "offset": offset,
                        "searchText": "",
                    },
                )
                raw = body.get("jobPostings", [])
                page = parse_workday(slug, body, now, us_only=bool(facets))
                postings += page
                offset += WORKDAY_PAGE
                days = [workday_days(j.get("postedOn")) for j in raw]
                if full:
                    done = bool(raw) and all(d == OLD for d in days)
                else:
                    done = not any(d is not None and d <= 1 for d in days)
                if (
                    done
                    or not raw
                    or offset >= min(body.get("total") or 0, WORKDAY_MAX_OFFSET)
                ):
                    break
        except Exception as e:  # noqa: BLE001 (one broken board must never stop the run)
            return BoardResult("workday", slug, False, [], str(e) or type(e).__name__)
    return BoardResult("workday", slug, True, postings, complete=full)


async def fetch_smartrecruiters(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    slug: str,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=30),
) -> BoardResult:
    """Page newest first until a page reaches jobs older than `max_age`.
    Everything newer has then been seen, so the read counts as complete."""
    now = now or datetime.now(UTC)
    postings: list[Posting] = []
    offset = 0
    async with sem:
        try:
            while True:
                body = await get_json(
                    client, board_url("smartrecruiters", slug) + f"&offset={offset}"
                )
                page = parse_smartrecruiters(slug, body)
                postings += [
                    p for p in page if p.posted_at and now - p.posted_at <= max_age
                ]
                offset += len(page)
                oldest = min((p.posted_at for p in page if p.posted_at), default=None)
                if (
                    not page
                    or offset >= (body.get("totalFound") or 0)
                    or (oldest and now - oldest > max_age)
                ):
                    break
        except Exception as e:  # noqa: BLE001 (one broken board must never stop the run)
            return BoardResult(
                "smartrecruiters", slug, False, [], str(e) or type(e).__name__
            )
    return BoardResult("smartrecruiters", slug, True, postings)


async def fetch_description(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, p: Posting
) -> None:
    """Fill in a Greenhouse, Workday or SmartRecruiters description. Leaves it
    None if the fetch fails. Workday also gives the job's country here."""
    if p.description is not None or p.system not in (
        "greenhouse",
        "workday",
        "smartrecruiters",
    ):
        return
    async with sem:
        try:
            if p.system == "greenhouse":
                body = await get_json(
                    client, GREENHOUSE_JOB.format(slug=p.slug, id=p.job_id)
                )
                p.description = html_to_text(body.get("content"))
                return
            if p.system == "smartrecruiters":
                body = await get_json(
                    client, SMARTRECRUITERS_JOB.format(slug=p.slug, id=p.job_id)
                )
                sections = ((body.get("jobAd") or {}).get("sections") or {}).values()
                p.description = "\n".join(
                    html_to_text((x or {}).get("text")) for x in sections
                )
                return
            info = (await get_json(client, workday_detail_url(p))).get(
                "jobPostingInfo"
            ) or {}
            p.description = html_to_text(info.get("jobDescription"))
            country = (info.get("jobRequisitionLocation") or {}).get("country") or {}
            p.country = (
                p.country or country.get("alpha2Code") or country.get("descriptor")
            )
        except RuntimeError:
            pass


def client(timeout: float = 20) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
