"""Convert DOL LCA disclosure workbooks to Parquet.

The Office of Foreign Labor Certification publishes every H-1B Labor Condition
Application as quarterly Excel files with 98 columns. We keep only the columns
FiledFor needs. Contact names, phone numbers, emails, attorney and preparer
details are dropped on read and never stored.

Every column is read as a string. Types are applied in a later step, so a
malformed value in one file can't silently turn into a null here.
"""

from pathlib import Path

import fastexcel
import polars as pl

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/lca")

KEEP_COLUMNS = [
    "CASE_NUMBER",
    "CASE_STATUS",
    "RECEIVED_DATE",
    "DECISION_DATE",
    "VISA_CLASS",
    "JOB_TITLE",
    "SOC_CODE",
    "SOC_TITLE",
    "FULL_TIME_POSITION",
    "BEGIN_DATE",
    "TOTAL_WORKER_POSITIONS",
    "NEW_EMPLOYMENT",
    "CONTINUED_EMPLOYMENT",
    "CHANGE_EMPLOYER",
    "EMPLOYER_NAME",
    "TRADE_NAME_DBA",
    "EMPLOYER_CITY",
    "EMPLOYER_STATE",
    "EMPLOYER_FEIN",
    "NAICS_CODE",
    "SECONDARY_ENTITY",
    "SECONDARY_ENTITY_BUSINESS_NAME",
    "WORKSITE_CITY",
    "WORKSITE_STATE",
    "WAGE_RATE_OF_PAY_FROM",
    "WAGE_RATE_OF_PAY_TO",
    "WAGE_UNIT_OF_PAY",
    "PREVAILING_WAGE",
    "PW_UNIT_OF_PAY",
    "PW_WAGE_LEVEL",
    "H_1B_DEPENDENT",
]


def _normalize(name: str) -> str:
    # DOL renames columns between quarters, e.g. H_1B_DEPENDENT vs H-1B_DEPENDENT
    return name.strip().upper().replace("-", "_").replace(" ", "_")


def convert(xlsx: Path, out_dir: Path = OUT_DIR) -> Path:
    """Read one workbook's first sheet, keep KEEP_COLUMNS, write Parquet."""
    reader = fastexcel.read_excel(xlsx)
    header = reader.load_sheet(0, n_rows=0).available_columns()
    actual = {_normalize(c.name): c.name for c in header}

    missing = [c for c in KEEP_COLUMNS if c not in actual]
    if missing:
        raise ValueError(f"{xlsx.name} is missing columns: {missing}")

    wanted = [actual[c] for c in KEEP_COLUMNS]
    sheet = reader.load_sheet(0, use_columns=wanted, dtypes="string")
    df = (
        sheet.to_polars()
        .rename({actual[c]: c for c in KEEP_COLUMNS})
        .with_columns(pl.lit(xlsx.name).alias("SOURCE_FILE"))
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{xlsx.stem}.parquet"
    df.write_parquet(out, compression="zstd")
    return out


def main() -> None:
    files = sorted(RAW_DIR.glob("LCA_Disclosure_Data_*.xlsx"))
    if not files:
        raise SystemExit(f"No LCA workbooks in {RAW_DIR}")
    for xlsx in files:
        out = convert(xlsx)
        rows = pl.scan_parquet(out).select(pl.len()).collect().item()
        size_mb = out.stat().st_size / 1e6
        print(f"{xlsx.name}: {rows:,} rows -> {out.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
