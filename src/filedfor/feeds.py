"""Fetch job boards and turn each system's response into the same Posting.

Greenhouse, Lever and Ashby all publish open jobs through public JSON APIs.
Each has its own field names, dates and location formats; the parse_* functions
are the only place that knows about them.

Lever and Ashby include full descriptions in the board list. Greenhouse
doesn't, so fetch_description gets one job at a time, only for new candidates
(see ADR-002).
"""

import asyncio
import html
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

USER_AGENT = "FiledFor (open-source job board; filedfor.com)"

# Greenhouse serves EU boards from its main API; Lever EU boards need the EU host
API = {
    ("greenhouse", False): "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    ("greenhouse", True): "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    ("lever", False): "https://api.lever.co/v0/postings/{slug}?mode=json",
    ("lever", True): "https://api.eu.lever.co/v0/postings/{slug}?mode=json",
    ("ashby", False): "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}
GREENHOUSE_JOB = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}"


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

    @property
    def key(self) -> tuple[str, str, str]:
        return self.system, self.slug, self.job_id


def board_url(system: str, slug: str, eu: bool = False) -> str:
    return API[(system, eu and system != "ashby")].format(
        slug=urllib.parse.quote(slug, safe="")
    )


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
                workplace=j.get("isRemote"),
                posted_at=_iso(j.get("publishedAt")),
                description=j.get("descriptionPlain")
                or html_to_text(j.get("descriptionHtml")),
            )
        )
    return out


PARSERS = {"greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby}


@dataclass
class BoardResult:
    system: str
    slug: str
    ok: bool
    postings: list[Posting]
    error: str | None = None


async def get_json(client: httpx.AsyncClient, url: str, tries: int = 3):
    """GET with retries on timeouts, 429 and 5xx. Other statuses fail at once."""
    last = "no attempt"
    for attempt in range(tries):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                break
        except (httpx.TransportError, ValueError) as e:
            last = type(e).__name__
        await asyncio.sleep(2**attempt)
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


async def fetch_description(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, p: Posting
) -> None:
    """Fill in a Greenhouse description. Leaves it None if the fetch fails."""
    if p.description is not None or p.system != "greenhouse":
        return
    async with sem:
        try:
            body = await get_json(
                client, GREENHOUSE_JOB.format(slug=p.slug, id=p.job_id)
            )
            p.description = html_to_text(body.get("content"))
        except RuntimeError:
            pass


def client(timeout: float = 20) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
