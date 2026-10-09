"""Sponsorship evidence for a job: what its company has filed for this role type.

data/role_evidence.parquet (built by sponsors.py) has one row per employer
FEIN and role type. A company can own several FEINs (see ADR-001), so the
numbers are summed across them. Wages are medians per FEIN, combined as a
filing-weighted average, which is exact for companies with one FEIN.
"""

import csv
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

ROLE_EVIDENCE = Path("data/role_evidence.parquet")
COMPANIES = Path("data/companies.csv")


@dataclass(frozen=True)
class Evidence:
    filings: int
    new_hire_filings: int
    level_1: int
    level_2: int
    level_3: int
    level_4: int
    median_wage: int | None
    last_decision: str | None

    def as_dict(self) -> dict:
        return asdict(self)


class EvidenceIndex:
    def __init__(
        self, role_evidence: Path = ROLE_EVIDENCE, companies: Path = COMPANIES
    ) -> None:
        rows = duckdb.sql(f"select * from read_parquet('{role_evidence}')").fetchall()
        cols = [
            "fein",
            "role",
            "filings",
            "new_hire_filings",
            "level_1",
            "level_2",
            "level_3",
            "level_4",
            "median_wage",
            "last_decision",
        ]
        self._by_fein_role = {
            (r[0], r[1]): dict(zip(cols, r, strict=True)) for r in rows
        }
        self._feins: dict[tuple[str, str], list[str]] = {}
        with companies.open(newline="") as f:
            for row in csv.DictReader(f):
                feins = [x for x in row["feins"].split(";") if x]
                self._feins[(row["system"], row["slug"])] = feins

    def feins(self, system: str, slug: str) -> list[str]:
        return self._feins.get((system, slug), [])

    def lookup(self, system: str, slug: str, role: str) -> Evidence | None:
        """None when the company has no filings for this role type."""
        return self.for_feins(self.feins(system, slug), role)

    def company(self, boards: list[tuple[str, str]]) -> dict[str, dict | None]:
        """Evidence for every role type, across all of a company's boards
        (NVIDIA has several Workday sites under the same FEINs)."""
        feins = sorted({f for b in boards for f in self.feins(*b)})
        return {
            role: (ev.as_dict() if (ev := self.for_feins(feins, role)) else None)
            for role in ("swe", "ai", "data")
        }

    def for_feins(self, feins: list[str], role: str) -> Evidence | None:
        rows = [
            self._by_fein_role[(f, role)]
            for f in feins
            if (f, role) in self._by_fein_role
        ]
        if not rows:
            return None
        total = sum(r["filings"] for r in rows)
        wages = [(r["median_wage"], r["filings"]) for r in rows if r["median_wage"]]
        wage = (
            round(sum(w * n for w, n in wages) / sum(n for _, n in wages))
            if wages
            else None
        )
        last = max(
            (r["last_decision"] for r in rows if r["last_decision"]), default=None
        )
        return Evidence(
            filings=total,
            new_hire_filings=sum(r["new_hire_filings"] for r in rows),
            level_1=sum(r["level_1"] for r in rows),
            level_2=sum(r["level_2"] for r in rows),
            level_3=sum(r["level_3"] for r in rows),
            level_4=sum(r["level_4"] for r in rows),
            median_wage=wage,
            last_decision=str(last) if last else None,
        )
