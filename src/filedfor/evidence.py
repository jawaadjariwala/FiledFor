"""Sponsorship evidence for a job: what its company has filed for this role type.

data/role_evidence.parquet (built by sponsors.py) has one row per employer
FEIN and role type. A company can own several FEINs (see ADR-001), so the
numbers are summed across them. A median can't be summed: for a company
with several FEINs the exact median per role type comes from
data/company_profiles.json (built from the row-level data by profiles.py),
and without it no median is shown rather than an average of medians.
"""

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from filedfor.classify import FIELDS

ROLE_EVIDENCE = Path("data/role_evidence.parquet")
COMPANIES = Path("data/companies.csv")
PROFILES = Path("data/company_profiles.json")


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
        self,
        role_evidence: Path = ROLE_EVIDENCE,
        companies: Path = COMPANIES,
        profiles: Path = PROFILES,
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
        self._company: dict[tuple[str, str], str] = {}
        with companies.open(newline="") as f:
            for row in csv.DictReader(f):
                feins = [x for x in row["feins"].split(";") if x]
                self._feins[(row["system"], row["slug"])] = feins
                self._company[(row["system"], row["slug"])] = row["company"]
        try:
            companies_ = json.loads(profiles.read_text())["companies"]
            self._wages = {n: p.get("field_wages", {}) for n, p in companies_.items()}
        except (OSError, ValueError, KeyError, TypeError):
            self._wages = {}

    def feins(self, system: str, slug: str) -> list[str]:
        return self._feins.get((system, slug), [])

    def lookup(self, system: str, slug: str, role: str) -> Evidence | None:
        """None when the company has no filings for this role type."""
        return self.for_feins(
            self.feins(system, slug), role, self._company.get((system, slug))
        )

    def company(self, boards: list[tuple[str, str]]) -> dict[str, dict | None]:
        """Evidence for every role type, across all of a company's boards
        (NVIDIA has several Workday sites under the same FEINs)."""
        feins = sorted({f for b in boards for f in self.feins(*b)})
        name = next((self._company[b] for b in boards if b in self._company), None)
        return {
            role: (ev.as_dict() if (ev := self.for_feins(feins, role, name)) else None)
            for role in FIELDS
        }

    def for_feins(
        self, feins: list[str], role: str, company: str | None = None
    ) -> Evidence | None:
        rows = [
            self._by_fein_role[(f, role)]
            for f in feins
            if (f, role) in self._by_fein_role
        ]
        if not rows:
            return None
        total = sum(r["filings"] for r in rows)
        if len(rows) == 1:
            wage = rows[0]["median_wage"] and round(rows[0]["median_wage"])
        else:
            wage = self._wages.get(company or "", {}).get(role)
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
