"""Score the classifiers against the hand-labelled sets in data/labels.

    uv run python -m filedfor.evaluate

Three title sets:
- titles.csv (rules v2/v3, ADR-002): three fields and three levels. Drawn in
  buckets (entry-ish, plain, senior-ish) at fixed counts, so scores are also
  weighted back to each bucket's share of all 33,235 titles. Scored on its
  original terms: fields it didn't cover count as none, mid and senior count
  as experienced.
- titles_v5.csv and titles_v5_test.csv (rules v5, ADR-004): eight fields and
  five levels. The test set was labelled before the rules ran on it.
Blank flags count as "n".
"""

import csv
import re
from collections import Counter
from pathlib import Path

from filedfor import classify

LABELS = Path("data/labels")
# Size of each bucket in the 2026-09-30 snapshot the sample was drawn from
BUCKET_SIZE = {"entry-ish": 2618, "plain": 16924, "senior-ish": 13693}
ROLE_CODE = {"a": "ai", "s": "swe", "d": "data", "n": None}
LEVEL_CODE = {"e": "entry", "x": "experienced", "i": "intern", "?": "unclear"}
FIELDS = ["swe", "ai", "data", "hardware", "it", "security", "product", "design"]


def _v3_role(title: str) -> str | None:
    """Rules v5 seen through the v3 labels, which only knew three fields."""
    r = classify.role(title)
    return r if r in ("ai", "swe", "data") else None


def _v3_level(title: str) -> str:
    lv = classify.level(title)
    return "experienced" if lv in ("mid", "senior") else lv


def _read(name: str) -> list[dict]:
    with (LABELS / name).open(newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def _pr(pairs: list[tuple[bool, bool, float]]) -> tuple[float, float, int, int]:
    """Weighted precision and recall from (predicted, actual, weight)."""
    tp = sum(w for p, a, w in pairs if p and a)
    fp = sum(w for p, a, w in pairs if p and not a)
    fn = sum(w for p, a, w in pairs if a and not p)
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    return (
        precision,
        recall,
        sum(1 for p, _, _ in pairs if p),
        sum(1 for _, a, _ in pairs if a),
    )


def _line(name: str, raw, weighted) -> str:
    p, r, npred, nact = raw
    wp, wr, _, _ = weighted
    return (
        f"  {name:<22} precision {p:5.0%} (weighted {wp:5.0%})   "
        f"recall {r:5.0%} (weighted {wr:5.0%})   [{npred} predicted, {nact} labelled]"
    )


def _excluded() -> set[tuple[str, str]]:
    with Path("data/excluded_boards.csv").open(newline="") as f:
        return {(r["system"], r["slug"]) for r in csv.DictReader(f)}


def titles() -> list[str]:
    """Rows from boards FiledFor no longer fetches (gig marketplaces) are
    left out: the product never sees those titles."""
    excluded = _excluded()
    all_rows = _read("titles.csv")
    rows = [r for r in all_rows if tuple(r["id"].split(":")[:2]) not in excluded]
    counts = Counter(r["bucket"] for r in rows)
    weight = {b: BUCKET_SIZE[b] / counts[b] for b in counts}
    misses = []
    print(
        f"Titles: {len(rows)} labelled "
        f"({len(all_rows) - len(rows)} from excluded gig marketplaces left out)"
    )
    role_ok = 0
    for code, name in (("a", "ai"), ("s", "swe"), ("d", "data")):
        raw, wtd = [], []
        for r in rows:
            p, a = _v3_role(r["title"]) == name, r["role"] == code
            raw.append((p, a, 1.0))
            wtd.append((p, a, weight[r["bucket"]]))
        print(_line(f"role {name}", _pr(raw), _pr(wtd)))
    tech_raw, tech_wtd = [], []
    for r in rows:
        pred, want = _v3_role(r["title"]), ROLE_CODE[r["role"]]
        role_ok += pred == want
        tech_raw.append((pred is not None, want is not None, 1.0))
        tech_wtd.append((pred is not None, want is not None, weight[r["bucket"]]))
        if pred != want:
            misses.append(f"role   {r['title'][:60]!r}: rules {pred}, you {want}")
    print(_line("any tech role", _pr(tech_raw), _pr(tech_wtd)))
    print(f"  role exact match       {role_ok}/{len(rows)}")

    level_ok = 0
    for code, name in (("e", "entry"), ("i", "intern"), ("x", "experienced")):
        raw, wtd = [], []
        for r in rows:
            p, a = _v3_level(r["title"]) == name, r["level"] == code
            raw.append((p, a, 1.0))
            wtd.append((p, a, weight[r["bucket"]]))
        print(_line(f"level {name}", _pr(raw), _pr(wtd)))
    for r in rows:
        pred, want = _v3_level(r["title"]), LEVEL_CODE[r["level"]]
        level_ok += pred == want
        if pred != want:
            misses.append(f"level  {r['title'][:60]!r}: rules {pred}, you {want}")
    print(f"  level exact match      {level_ok}/{len(rows)}")
    return misses


def titles_v5(name: str) -> list[str]:
    """Eight fields, five levels. Labels: role is a field or blank (none)."""
    rows = _read(name)
    misses = []
    print(f"\nTitles v5, {name}: {len(rows)} labelled")
    for field in FIELDS:
        pairs = [
            (classify.role(r["title"]) == field, r["role"] == field, 1.0) for r in rows
        ]
        print(_line(f"role {field}", _pr(pairs), _pr(pairs)))
    tech = [(classify.role(r["title"]) is not None, bool(r["role"]), 1.0) for r in rows]
    print(_line("any tech role", _pr(tech), _pr(tech)))
    exact = 0
    for r in rows:
        pred, want = classify.role(r["title"]), r["role"] or None
        exact += pred == want
        if pred != want:
            misses.append(f"role   {r['title'][:60]!r}: rules {pred}, you {want}")
    print(f"  role exact match       {exact}/{len(rows)}")
    tech_rows = [r for r in rows if r["role"]]
    ok = 0
    for r in tech_rows:
        pred = classify.level(r["title"])
        ok += pred == r["level"]
        if pred != r["level"]:
            misses.append(f"level  {r['title'][:60]!r}: rules {pred}, you {r['level']}")
    print(f"  level exact match      {ok}/{len(tech_rows)}   (tech titles)")
    return misses


def descriptions() -> list[str]:
    rows = _read("descriptions.csv")
    misses = []
    print(f"\nDescriptions: {len(rows)} labelled")
    texts = [r["snippet"].replace(" | ", "\n") for r in rows]
    for col in ("no_sponsorship", "citizens_only", "clearance"):
        pairs = []
        for r, text in zip(rows, texts, strict=True):
            p = getattr(classify.flags(text, r["title"], r["company"]), col)
            a = r[col].lower() == "y"
            pairs.append((p, a, 1.0))
            if p != a:
                misses.append(f"{col:<14} {r['title'][:50]!r}: rules {p}, you {a}")
        print(_line(col, _pr(pairs), _pr(pairs)))
    exact = band = 0
    for r, text in zip(rows, texts, strict=True):
        pred = classify.min_years(text)
        want = (
            int(re.sub(r"\D", "", r["min_years"]))
            if re.search(r"\d", r["min_years"])
            else None
        )
        exact += pred == want
        # What the product uses: is it new-grad friendly (2 or fewer, or unstated)?
        band += (pred is None or pred <= 2) == (want is None or want <= 2)
        if pred != want:
            misses.append(
                f"min_years      {r['title'][:50]!r}: rules {pred}, you {want}"
            )
    print(f"  min_years exact        {exact}/{len(rows)}")
    print(f"  min_years <=2 vs >2    {band}/{len(rows)}   (the split alerts use)")
    return misses


def main() -> None:
    misses = (
        titles()
        + titles_v5("titles_v5.csv")
        + titles_v5("titles_v5_test.csv")
        + descriptions()
    )
    print(f"\nDisagreements ({len(misses)}):")
    for m in misses:
        print("  " + m)


if __name__ == "__main__":
    main()
