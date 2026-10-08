# FiledFor roadmap

A free, open-source job board for international students: new-grad tech jobs
found within about 30 minutes of posting, each one shown with **evidence of
whether that company has sponsored that kind of role at that level**, from
Department of Labor filings.

## Why this and not the existing ones

Checked on 2026-09-27:

| Existing | What it does | Gap |
|---|---|---|
| wluky/intl-newgrad-jobs (1 star) | Weekly list, hand-curated sponsor companies | Weekly, company-level only, Staff/Manager roles leak in |
| LXP86050/job-radar, calvinlee326/h1b-job-scraper | Daily scrapers / email | Daily, little traction |
| Migrate Mate, H1BVisaJobs.com, Simplify visa page | Commercial boards using LCA data | Company-level "sponsors H-1B" tags |

What none of them do:

1. **Role-level evidence.** Not "Stripe sponsors", but "Stripe filed N
   certified LCAs for software developer roles in the last 2 fiscal years,
   X% at wage level I, median wage $Y."
2. **Lottery odds context.** The H-1B lottery now weights selection by wage
   level, so the wage levels a company files new grads at matter. Show the
   level distribution for each company.
3. **Freshness.** 30-minute polling and push alerts, versus daily or weekly.
4. **Strict entry-level filtering**, tested against a labelled set of titles.

## Architecture

```
 DOL LCA files ──> sponsors (Parquet/DuckDB) ──┐
                                               ├─> watchlist ─> poller ─> filter ─> enrich ─> jobs.json
 SimplifyJobs slugs ──> feed discovery ────────┘   (Greenhouse, Lever, Ashby)       (evidence)    │
                                                                                                   ├─> static site + company pages
                                                                                                   ├─> ntfy alerts per lane
                                                                                                   └─> RSS
```

- **Language:** Python only (uv, pydantic, httpx, DuckDB, Jinja, pytest, ruff).
  No Node, so the whole project stays in one language.
- **Hosting:** GitHub Actions (30-minute schedule) and GitHub Pages, on
  filedfor.com. About $12 a year for the domain, everything else $0.
- **Alerts:** ntfy.sh public topics per lane (swe, data, ai, all) and RSS.

## Verified on 2026-09-27

- Greenhouse (Stripe, 701 jobs, `first_published`), Lever (Palantir, 321,
  `createdAt`), Ashby (Ramp, 158, `publishedAt`) feeds all respond
- SimplifyJobs `listings.json`: 19,730 listings, about 1,200 unique feed slugs
  (559 Greenhouse, 425 Ashby, 219 Lever). The repo has no license, so it's
  used only to discover company slugs, never republished, and credited.
- DOL LCA disclosure data is published through FY2026 Q3
- **FY2025 files are quarterly, FY2026 Q3 is cumulative** (decisions 2025-10-01 to 2026-06-30).
  Together they cover 2024-10-01 to 2026-06-30 with no gaps (checked on decision dates)
- **Schema drift:** the same column is `H_1B_DEPENDENT` in one quarter and `H-1B_DEPENDENT`
  in another, and column counts differ (97 vs 98). Columns are matched on normalized names
- **10,409 cases appear twice**, every one as Certified then later Certified - Withdrawn.
  Rule: keep the latest decision per CASE_NUMBER
- Only the ~31 needed columns are stored. Contact, attorney and preparer details are dropped on read

## Verified on 2026-09-29 (watchlist)

- Simplify's `company_url` is a Simplify page, not the company's domain, so matching is on names
- 44 Greenhouse URLs are `embed?token=` links with no slug. They're skipped
- Greenhouse serves EU boards from its main API. There is no `boards-api.eu` host
- A FEIN's name list includes one-off names from mistyped FEINs ("Newton, Inc." under Meta).
  Names used on under 10% of a FEIN's filings are ignored for matching
- DuckDB `group by` output order isn't fixed, and neither is Python set order. Both needed
  explicit tie-breaks before the match table came out the same on every run
- Many unmatched boards are correct misses: defense firms that hire US persons only (Anduril,
  Saronic) and UK employers with no US filings. Legal-name cases (SpaceX, Airtable as Formagrid,
  Lucid as Lucid USA with 300 filings) live in `data/aliases.csv`

## Known gaps

- **Oracle, iCIMS and other systems aren't covered.** Workday and SmartRecruiters were added in ADR-003; together
  they were about half of Simplify's listings.
- Simplify's sponsorship flag is mostly empty (97 of 19,730), so the DOL
  data carries the evidence.
- LCA filings show history, not a promise for any single role. The site says
  so on every page, and is not legal advice.

## Plan

**Schedule:** Phase 1 by October 4, public launch around October 10, 2026.

Two phases. Phase 1 works end to end for a single user within 4 days. Phase 2
makes it public.

### Phase 1: working for one person

**Day 1: the sponsor data**
- [x] Confirm the current wage-weighted lottery rule: from FY2027, Level I = 1 entry up to Level IV = 4 entries (DHS final rule, Dec 2025)
- [x] Download LCA files (FY2025 Q1 to Q4, FY2026 Q3), convert to Parquet (675 MB -> 33 MB, 1,034,048 rows)
- [x] Delete the Excel files once the clean layer is built
- [x] Explore: employer, SOC code, job title, wage level, wage, state, status
- [x] `sponsors` (per employer, keyed on FEIN) and `sponsor_roles` (per employer x full SOC code):
      filings, new hires, wage-level mix, median yearly wage. 977,292 certified H-1B cases, 37,713 employers with tech filings
- [x] Data quality checks printed on every build: row counts per stage, collapsed duplicates, out-of-range wages

**Day 2: the watchlist**
- [x] Extract feed slugs from SimplifyJobs listings (1,201 boards: 556 Greenhouse, 424 Ashby, 221 Lever)
- [x] Entity resolution: DOL employer names to feed companies. Tiered rules (alias, exact,
      squashed, prefix) plus a review queue, not fuzzy scores. Why: ADR-001 in `docs/decisions.md`
- [x] Measure match quality on a hand-labelled sample of 50: precision 100% (19 of 19),
      recall 95% (19 of 20). Small sample, so treat both as rough
- [x] Probe every feed, drop dead ones, write `companies.csv` (1,148 live, 53 dead; 549 live boards
      matched to a sponsor, with 39,132 open jobs before any entry-level filtering)

**Day 3: the poller**
- [x] pydantic `Job` model, one adapter per feed system
- [x] Concurrent polite fetching, retries, timeouts, per-feed error isolation
- [x] Entry-level classifier, tested against about 200 hand-labelled titles
- [x] Role to occupation mapping, attaching evidence to each job
- [x] Jobs and seen store in **Postgres** (free tier, e.g. Neon or Supabase), tests on saved responses

**Day 4: running on its own**
- [x] GitHub Actions schedule, state kept in Postgres between runs
- [x] Discord alerts filtered by lane and location (ntfy dropped: public topics can be spammed)
- [x] Health report: which feeds failed, how stale the data is
- [ ] Run for 24 hours and fix what breaks

### Phase 2: public

**Day 5: the site**
- [x] Static site generated from `jobs.json`: filter by lane, location, remote, posted-within.
      Only jobs posted in the last 30 days are published (older ones are mostly evergreen)
- [x] Every job shows its evidence badge (link to per-company numbers comes with Day 6 pages)
- [x] Methodology page: data sources, matching, limits, disclaimer

**Day 6: company pages**
- [ ] **Real lottery level per filing:** compare each offered wage to the OEWS wage levels for its SOC code and
      worksite area (DOL FLC Data Center tables). The FY2027 lottery weights by the highest level the offered
      wage reaches, not by PW_WAGE_LEVEL.
- [ ] One generated page per sponsor ("Does X sponsor H-1B for new grads?"):
      filings over time, wage levels, top titles, open roles
- [ ] SEO basics: titles, descriptions, sitemap

**Day 7: alerts, metrics, polish**
- [x] RSS feed per lane (public push alerts move to the web push app)
- [ ] Privacy-friendly visitor counter (to report real usage numbers)
- [ ] Measure time from posting to alert, median, shown on the site
- [x] README: architecture diagram, data pipeline, design decisions, known gaps

**Day 8: launch**
- [ ] Data story: "Which companies file new grads at wage levels that win the
      lottery?" with 3 or 4 charts, as the launch post
- [ ] Public launch
- [ ] Watch feedback and fix fast for the first 48 hours

### Stretch
- [ ] Model classifier for the cases rules can't decide (level unclear from the title, no years
      stated): rules filter first, the model judges only those, low confidence stays out of alerts.
      Measure on a fresh labelled sample against rules alone
- [x] Workday and SmartRecruiters support (ADR-003)
- [ ] Email digest
- [ ] Cap-exempt employers (universities, hospitals) as their own lane
