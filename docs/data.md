# Data sources and how they're cleaned

What FiledFor reads, what it keeps, and the quirks found along the way. Design
decisions are in [decisions.md](decisions.md).

## DOL H-1B filings (sponsor evidence)

**Source:** the Labor Condition Application (LCA) disclosure files from the
U.S. Department of Labor's [performance data page](https://www.dol.gov/agencies/eta/foreign-labor/performance).
An LCA is the first step of every H-1B petition, so a certified LCA is a
public record that an employer intended to hire someone on an H-1B for that
role.

**Coverage:** FY2025 Q1 to Q4 plus FY2026 Q3, which together cover decisions
from 2024-10-01 to 2026-06-30 with no gaps. FY2025 files are quarterly;
FY2026 Q3 is cumulative from the start of the fiscal year. 1,034,048 rows in,
977,292 certified H-1B cases out.

**Pipeline:** `lca.py` converts the Excel files to Parquet, keeping only the
columns FiledFor uses. `sponsors.py` builds three tables with DuckDB:

| Table | One row per | Used for |
|---|---|---|
| `data/sponsors.parquet` | employer (FEIN) | filings, tech filings, wage-level mix, median tech wage |
| `data/sponsor_roles.parquet` | employer and occupation (SOC code) | detailed role evidence |
| `data/role_evidence.parquet` | employer and field (software, AI/ML, data) | the line shown next to each job |

**Rules:**

- **What counts:** H-1B cases decided as Certified, including ones the
  employer withdrew after certification. Denied cases and ones withdrawn
  before a decision are left out.
- **Tech filings:** occupation codes starting `15-` (computer and mathematical
  occupations).
- **Fields:** each FiledFor field maps to a set of occupation codes
  (`ROLE_SOCS` in `sponsors.py`). Software Developers (15-1252) count toward all
  three, because data and ML engineers are often filed under it.
- **New hires:** filings with new employment, as opposed to extensions,
  amendments or transfers.
- **Wages:** converted to yearly (hourly x 2,080, monthly x 12, and so on).
  Yearly wages outside $20,000 to $1,000,000 are treated as data-entry errors
  and left out of medians.
- **Wage level** is the prevailing wage level on the filing (I to IV). The
  FY2027 lottery weights selection by the highest level the *offered* wage
  reaches, which can be higher.

**Quirks found:**

- **Schema drift:** the same column is `H_1B_DEPENDENT` in one quarter and
  `H-1B_DEPENDENT` in another, and column counts differ (97 vs 98). Columns
  are matched on normalized names.
- **Duplicate cases:** 10,409 cases appear twice, each as Certified and later
  as Certified - Withdrawn. The latest decision per case number wins.
- **Privacy:** contact names, phone numbers, emails, attorney and preparer
  details are dropped on read and never stored.

## Company job boards (the jobs)

**Discovery:** board slugs come from the public
[SimplifyJobs](https://github.com/SimplifyJobs/New-Grad-Positions) listings
file. That repo has no license, so it's used only to find which companies
hire through which system. Its listings are never republished.

**Systems:** Greenhouse, Lever, Ashby, Workday and SmartRecruiters, all read
through the same public JSON endpoints their careers pages use. No logins and
no HTML scraping. How Workday's paging is handled: [ADR-003](decisions.md#adr-003-workday-and-smartrecruiters).

**Quirks found:**

- Simplify's `company_url` points to a Simplify page, not the company's site,
  so companies are matched to employers by name
  ([ADR-001](decisions.md#adr-001-matching-job-feed-companies-to-dol-employers)).
- 44 Greenhouse URLs are `embed?token=` links with no board slug. They're
  skipped.
- Greenhouse serves EU boards from its main API; Lever EU boards need
  `api.eu.lever.co`.
- A FEIN's names include one-off names from mistyped FEINs ("Newton, Inc."
  under Meta). Names used on under 10% of a FEIN's filings are ignored for
  matching.
- Many unmatched boards are correct misses: defense firms that hire US persons
  only, and non-US employers with no US filings. Legal-name cases (SpaceX as
  Space Exploration Technologies, Airtable as Formagrid) live in
  `data/aliases.csv`.
- Workday dates are text ("Posted 3 Days Ago"), so Workday posting times have
  day precision.
- Simplify's own sponsorship flag is almost always empty (97 of 19,730
  listings), so the DOL data carries the evidence.

## Hand-made files

| File | What it is |
|---|---|
| `data/aliases.csv` | Companies that file under a different legal name |
| `data/match_reviews.csv` | Accept or reject decision for every match the rules couldn't make on their own |
| `data/match_labels.csv` | 50 random boards labelled by hand, to score matching |
| `data/labels/titles.csv`, `data/labels/descriptions.csv` | Hand-labelled titles and description snippets, to score the classifier (`python -m filedfor.evaluate`) |
| `data/excluded_boards.csv` | Gig marketplaces that aren't employers |
| `data/domains.csv` | Company websites, for logos. Each logo was checked by eye; a company without one gets a lettered badge |
