"""Big H-1B tech filers whose jobs FiledFor can't read, for record-only pages.

Most of the largest sponsors (Amazon, Google, Microsoft...) run their own
careers sites, so they never reach the watchlist and had no company page.
Their filings are in the same Department of Labor data, so they get a page
with the filing record and no job list.

An employer qualifies when none of its FEINs is on the watchlist and its
group has at least MIN_FILINGS tech filings. FEINs are grouped by name with
legal suffixes removed; data/filer_names.csv names the big ones properly and
groups their entities (Amazon files under five). Groups whose name matches a
watchlist company are left out, so a brand never gets two pages.

Built locally from data/sponsors.parquet into data/filers.csv, which is
committed so the site build can read it:

    uv run python -m filedfor.filers
"""

import csv
import re
from pathlib import Path

import duckdb

from filedfor.sponsors import SPONSORS

COMPANIES = Path("data/companies.csv")
NAMES = Path("data/filer_names.csv")
FILERS = Path("data/filers.csv")
MIN_FILINGS = 50

SUFFIX = re.compile(
    r"\b(INC|INCORPORATED|LLC|L L C|LLP|LP|L P|CORP|CORPORATION|CO|COMPANY|LTD|"
    r"LIMITED|NA|N A|PLLC|PC|PLC|THE|DBA)\b"
)
LEGAL = re.compile(
    r"(,?\s+(inc|incorporated|llc|l\.l\.c|llp|l\.p|lp|corp|corporation|co|company|"
    r"ltd|limited|n\.a|plc|pllc|p\.c)\.?)+$",
    re.IGNORECASE,
)
SMALL_WORDS = {"AND", "OF", "THE", "FOR", "IN", "AT", "ON", "DE"}


def key(name: str) -> str:
    """'Persistent Systems, Inc.' and 'PERSISTENT SYSTEMS LIMITED' -> 'PERSISTENT SYSTEMS'."""
    k = re.sub(r"[^A-Z0-9 ]+", " ", name.upper().replace("&", " AND "))
    return re.sub(r"\s+", " ", SUFFIX.sub(" ", k)).strip()


def display(name: str) -> str:
    """A readable name: no legal suffix, and not in capitals.
    'WAL-MART ASSOCIATES, INC.' -> 'Wal-Mart Associates'."""
    n = name.strip()
    if m := re.search(
        r"\bd/?b/?a\.?\s+(.+)$", n, re.IGNORECASE
    ):  # "X d/b/a Y" trades as Y
        n = m.group(1)
    n = re.sub(r"\s+", " ", n)
    # "The Options Clearing Corporation" needs its last word; others don't
    n = n if n.upper().startswith("THE ") else LEGAL.sub("", n).strip(" ,.&")
    n = re.sub(
        r"(,?\s+(inc|llc|llp|ltd|l\.p|n\.a)\.?)+$", "", n, flags=re.IGNORECASE
    ).strip(" ,.&")
    if n != n.upper():
        return n
    words = []
    for i, w in enumerate(n.split()):
        if i and w in SMALL_WORDS:
            words.append(w.lower())
        elif (len(w) <= 3 and w.isalpha()) or any(c.isdigit() for c in w):
            words.append(w)  # IBM, HCL, NTT
        else:
            words.append(re.sub(r"[A-Z]+", lambda m: m.group(0).capitalize(), w))
    return " ".join(words)


def named() -> list[tuple[str, re.Pattern]]:
    with NAMES.open(newline="") as f:
        return [(r["company"], re.compile(r["pattern"])) for r in csv.DictReader(f)]


def watchlist() -> tuple[set[str], set[str], set[str]]:
    """FEINs, name keys and names of every company already on the watchlist."""
    feins: set[str] = set()
    keys: set[str] = set()
    names: set[str] = set()
    with COMPANIES.open(newline="") as f:
        for r in csv.DictReader(f):
            feins.update(x for x in r["feins"].split(";") if x)
            keys.add(key(r["company"]))
            names.add(r["company"].casefold())
    return feins, keys, names


def build(min_filings: int = MIN_FILINGS) -> list[dict]:
    on_list, wkeys, wnames = watchlist()
    rules = named()
    rows = duckdb.sql(f"""
        select employer_fein, employer_name, tech_filings
        from read_parquet('{SPONSORS}') where tech_filings > 0
    """).fetchall()
    groups: dict[str, dict] = {}
    for fein, name, n in rows:
        if fein in on_list:
            continue
        upper = name.upper()
        brand = next((c for c, p in rules if p.search(upper)), None)
        k = f"named:{brand}" if brand else key(name)
        g = groups.setdefault(k, {"brand": brand, "feins": [], "names": []})
        g["feins"].append(fein)
        g["names"].append((n, name))

    out: dict[str, dict] = {}
    for k, g in groups.items():
        total = sum(n for n, _ in g["names"])
        if total < min_filings:
            continue
        if not g["brand"] and any(k == w or k.startswith(w + " ") for w in wkeys):
            continue  # another entity of a company that already has a page
        name = g["brand"] or display(max(g["names"])[1])
        if name.casefold() in wnames:
            continue
        o = out.setdefault(
            name.casefold(), {"company": name, "feins": set(), "tech_filings": 0}
        )
        o["feins"].update(g["feins"])
        o["tech_filings"] += total
    return sorted(
        (
            {
                "company": o["company"],
                "feins": ";".join(sorted(o["feins"])),
                "tech_filings": o["tech_filings"],
            }
            for o in out.values()
        ),
        key=lambda r: (-r["tech_filings"], r["company"]),
    )


def write(rows: list[dict], path: Path = FILERS) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "feins", "tech_filings"])
        w.writeheader()
        w.writerows(rows)


def load(path: Path = FILERS) -> dict[str, dict]:
    """company -> {"feins": [...], "tech_filings": n}; empty without the file."""
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {
            r["company"]: {
                "feins": [x for x in r["feins"].split(";") if x],
                "tech_filings": int(r["tech_filings"]),
            }
            for r in csv.DictReader(f)
        }


def main() -> None:
    rows = build()
    write(rows)
    total = sum(r["tech_filings"] for r in rows)
    print(f"{len(rows)} record-only employers, {total:,} tech filings -> {FILERS}")


if __name__ == "__main__":
    main()
