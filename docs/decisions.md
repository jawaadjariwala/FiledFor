# Design decisions

Each decision is recorded here with the options that were considered and why
one won, so the reasoning survives after the code changes.

---

## ADR-001: Matching job-feed companies to DOL employers

**Status:** Accepted
**Date:** 2026-09-29

### Context

Every job needs sponsorship evidence, which means linking the company behind a
job feed to the employer on the DOL filings. The two sides don't share any key.

- **Feed side:** 1,201 Greenhouse, Lever and Ashby boards, found in the
  SimplifyJobs listings. Each has a slug (`andurilindustries`) and a display
  name (`Anduril`). There's no company domain, because `company_url` points to
  a Simplify page.
- **DOL side:** 37,713 employers with tech filings, keyed on FEIN (federal tax
  ID), with legal names like `Anduril Industries, Inc.` Names come in varying
  case, contain typos (`Sierra Cnsulting Inc`), and some companies file under
  several FEINs (Amazon files under 4).

A first pass on 2026-09-29 measured how far simple rules get:

| Rule | Feeds matched | Notes |
|---|---|---|
| Exact match after normalizing (lowercase, strip punctuation and legal suffixes) | 433 (36%) | 14 of these hit more than one FEIN |
| Slug or name with spaces removed | +26 | `toshibaglobalcommercesolutions`, `elevenlabs`, `figureai` |
| DOL name starts with the feed name | 169 candidates | The top candidate is often wrong |
| Nothing | ~573 | Includes legal-name aliases and many startups that never sponsored |

The prefix rule shows the core risk. `Archer` ranks Archer Daniels Midland
(125 filings) above Archer Aviation. `Planet` ranks Planet AI above Planet
Labs. `Sierra` ranks a consulting firm first. Some companies can't be matched by
any string rule: SpaceX files as Space Exploration Technologies Corp and
Airtable as Formagrid Inc.

**The two kinds of error cost different amounts.** A wrong match shows a
student a sponsorship record that belongs to a different company, which is
worse than showing nothing. A missed match shows "no filings found". That's
only acceptable if the site says "no match found" rather than "doesn't
sponsor".

### Decision

A **tiered, deterministic matcher with a human review queue and a
hand-maintained alias file.** Every match records the rule that produced it.

1. **Aliases** (`data/aliases.csv`, hand-written): feed company to FEIN(s),
   for legal-name cases like SpaceX and Airtable. These always win.
2. **Exact:** the normalized feed name equals a normalized DOL name.
   Accepted automatically.
3. **Squashed:** the slug or name with spaces removed equals a DOL name with
   spaces removed. Sent to review.
4. **Prefix:** the DOL name starts with the feed name as whole words. Sent to
   review, with candidates ranked by tech filings.
5. **Unmatched:** shown on the site as "no DOL match found".

Review decisions (accept or reject each candidate) are saved in
`data/match_reviews.csv`, so a rebuild never asks the same question twice. The
output is `data/company_matches.csv`, with one row per feed and FEIN pair, so
one company can own several FEINs.

Quality is measured on a random sample of 50 feeds, labelled by hand
independently of the matcher's output. The measures are precision (of
accepted matches, how many are right) and recall (of feeds that do have DOL
filings, how many were found).

### Options considered

**A. Exact normalized match only**

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | $0 |
| Precision | High, but short generic names like "Close" can still collide |
| Recall | 36% of feeds, and it misses easy cases like Anduril |

Simple and explainable, but it leaves too much evidence on the table.

**B. Fuzzy matching (rapidfuzz scores with a threshold)**

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Cost | $0 |
| Precision | Poor on short names. A score can't tell Archer Aviation from Archer Daniels Midland |
| Recall | Higher, and it catches typos |

A similarity score gives no reason for its answer, and the threshold would be
tuned on the same data it's judged on. The measured misses so far are legal-name
aliases, not typos, so fuzzy matching would fix the wrong problem. Rejected
for now. **Revisit if** the labelled sample shows typo-driven misses.

**C. LLM-assisted matching**

| Dimension | Assessment |
|---|---|
| Complexity | Medium (prompting, parsing, caching) |
| Cost | Small, but not zero, on every rebuild |
| Precision | Good on well-known legal names, but can invent confident wrong ones |
| Recall | Best on aliases like SpaceX |

The one thing it's better at, knowing legal names, only matters for a few dozen
companies. That's cheaper to capture once in the alias file than to pay for and
re-check on every run. It also makes the pipeline non-deterministic. Rejected
as part of the pipeline, but fine as a one-off helper to suggest aliases, since
each suggestion is reviewed before it's written.

**D. Hand-curated only**

| Dimension | Assessment |
|---|---|
| Complexity | Low code, high effort |
| Cost | Hours per 1,000 companies, repeated as the feed list grows |
| Precision | Highest |
| Recall | Limited by time |

Doesn't scale past launch. Its best part, human judgment on the hard cases, is
kept in the review queue and the alias file.

### Trade-off analysis

The chosen design uses cheap, predictable rules for the easy majority and
spends human time only where a rule can be fooled: prefix candidates, squashed
matches and aliases. Recording the rule on each match means every badge on the
site can be traced to why it's there. It also lets the evaluation report
precision separately for each rule, so any weak rule shows up in the numbers.

### Consequences

- **Easier:** explaining and auditing any match, rebuilding without
  re-reviewing, and adding a company by hand.
- **Harder:** new feeds that land in the prefix rule need a review pass. The
  queue should stay small after the first pass.
- **Revisit:** fuzzy matching if the sample shows typo misses, and domain-based
  matching if a source of company domains turns up.
- **Not solved here:** subsidiaries that file under a parent's FEIN, and
  parents that file under a subsidiary's FEIN. Handled case by case in the
  alias file.

### Result (2026-09-29)

| Rule | Feeds matched |
|---|---|
| exact | 429 |
| prefix (reviewed) | 108 |
| squashed (reviewed) | 27 |
| alias | 11 |
| no match | 626 |

On a random sample of 50 feeds (seed 2026), labelled from a separate broad
search of DOL names, precision was 100% (19 of 19 FEINs attached were right)
and recall was 95% (19 of 20). The one miss, Underdog Fantasy filing as
Underdog Sports Holdings, was left unfixed so the score isn't tuned to the
sample. With 20 positives these numbers are rough, so the next check should
be a larger sample.

Two changes came out of building it:

- A feed is matched on **every** name it's listed under plus its slug. Using
  only the most common name ("Sigma") missed Sigma Computing, with 52 filings.
- If a reviewer rejects every candidate at one rule, the next rule gets a
  turn. The five-candidate cap on the prefix rule is applied before
  rejections are removed, so generic names don't page through endless
  "Quantum ..." firms.

### Action items

1. [x] `watchlist.py`: slug extraction, normalization, match rules, review
       queue, `company_matches.csv`
2. [x] First review pass on the squashed and prefix candidates
3. [x] Alias file for well-known legal-name mismatches
4. [x] Label 50 random feeds and report precision and recall for each rule
5. [x] Probe every feed, drop dead ones, write `companies.csv`
6. [ ] Second opinion on the review decisions and labels, which were made in
       one pass by the same reviewer

---

## ADR-002: The poller

**Status:** Accepted
**Date:** 2026-09-30

### Context

The poller turns the watchlist into alerts: every 30 minutes it fetches every
open job from 1,148 boards, keeps the ones a new grad could apply to, attaches
sponsorship evidence, and notifies people about jobs they haven't seen. It has
to cost $0 and survive boards that fail.

Measured on 2026-09-30, one full pass:

| System | Boards | Open jobs | Downloaded | Per job |
|---|---|---|---|---|
| Greenhouse | 532 | 44,269 | 37 MB | 0.8 KB (no descriptions in the list) |
| Lever | 206 | 16,439 | 251 MB | 15.3 KB (descriptions always included) |
| Ashby | 410 | 14,313 | 201 MB | 14.0 KB (descriptions always included) |
| **Total** | **1,148** | **75,021** | **489 MB** | **27 seconds at 8 concurrent** |

Of those 75,025 jobs, 26,280 have tech titles and 14,771 of those aren't
clearly senior. 1,342 say entry-level or intern outright. 99.97% have a
posting date.

### Requirements

- **Functional:** fetch, classify (role type, level, US or remote, warning
  flags), attach evidence, detect new and closed jobs, alert each new match
  once, publish the data the site reads, report feed health.
- **Freshness:** alert within about 30 minutes of a job appearing on its board.
- **Cost:** $0 beyond the domain. GitHub Actions, Neon free tier (0.5 GB,
  scales to zero), GitHub Pages.
- **Reliability:** one bad board never blocks the rest. A failed fetch must
  never close that board's jobs.
- **Privacy:** no job descriptions and no personal data stored.

### Design

```
 companies.csv ──┐   GitHub Actions, every 30 min (python -m filedfor.poll)
 sponsor_roles ──┤
                 v
   1. fetch      all boards, 8 at a time, retries, per-board isolation
   2. classify   role type, level, location, remote          (title + fields)
   3. keep       candidates: tech role, not clearly senior, US or remote
   4. diff       vs open candidates in Postgres -> new / still open / closed
   5. enrich     new only: fetch Greenhouse description, extract years and
                 warning flags ("no sponsorship", "citizens only", clearance),
                 attach evidence (role type -> occupation codes -> FEIN rows)
   6. store      one short transaction: insert new, close missing, log run
   7. notify     new matches -> Discord (now), web push (Phase 2)
   8. publish    jobs.json + health.json -> GitHub Pages (no git commits)
```

**Data model (Postgres):**

| Table | Holds | Size |
|---|---|---|
| `boards` | each board's last success, last error, consecutive failures | 1,148 rows |
| `jobs` | candidates only: key (system, slug, job id), title, link, location, country, remote, role type, level, min years, 3 warning flags, posted, first seen, closed, alerted, classifier version | ~15,000 open + 90 days of closed, about 10 MB |
| `runs` | one row per run: counts, duration, errors | 1,440 a month |
| `subscriptions` | Phase 2: push endpoint, keys, chosen filters | small |
| `deliveries` | Phase 2: which subscription got which job | pruned with jobs |

### Key decisions

**1. Store candidates, not every job.** The diff only needs the open
candidates for each board (about 15,000 keys, read once per run). Writes per
run are tens of rows instead of 75,000 updates, so storage and database time
stay tiny. *Cost:* a job that later becomes a candidate (after a classifier
change) looks new. Guard: alerts only go out for jobs posted in the last 72
hours.

**2. Descriptions are read, not stored.** Lever and Ashby descriptions are
already in memory. Greenhouse descriptions are fetched per job, only for new
candidates (tens per run), instead of downloading all 44,000 every time.
Facts extracted from them are stored; the text is dropped.

**3. Closing only counts on success.** A candidate is closed when its board
was fetched successfully and the job is gone. A board that errors keeps its
jobs open and its failure count goes up.

**4. Alert once, at least once.** New jobs get `alerted_at` only after the
send succeeds, so a failed send retries next run. A crash between sending and
marking can repeat one alert, which is better than losing one.

**5. First run alerts nothing.** The first run (and any newly added board)
marks its jobs as already alerted, otherwise 15,000 notifications go out at
once.

**6. The database wakes briefly.** All fetching and classifying happens
before connecting. One connection, one transaction, then disconnect. The
site reads `jobs.json`, never the database, so traffic can't wake it.

**7. Publish without commits.** `jobs.json` and `health.json` go to GitHub
Pages as a build artifact each run, so the repo history isn't flooded with a
commit every 30 minutes.

### Load and cost

| Resource | Estimate | Limit |
|---|---|---|
| Actions minutes | about 2 per run, 96 a day | Private repo: 2,000 a month free, so about 960 until launch on Oct 10. Public: unlimited |
| Neon storage | about 10 MB | 0.5 GB |
| Neon compute | a few seconds of work, then about 5 minutes idle before suspend, 48 times a day | measured in the Day 4 24-hour run |
| Board APIs | 1 request per board per 30 minutes, plus a few description fetches | polite |
| Discord | a handful of messages per run, 10 jobs per message | 30 messages a minute per webhook |

### Failure handling and monitoring

- Per request: 20 second timeout, 3 tries with backoff on timeouts, 429 and
  5xx. A 404 marks the board failed for this run.
- `health.json`: time of last run, boards failing now, boards failing for
  more than a day (candidates to remove), jobs published.
- Discord warning to the owner when more than 10% of boards fail in one run.
- `concurrency` group in the workflow so two runs never overlap.

### Test plan

The classifiers decide what reaches a student, so they get a labelled test
set. Everything else gets fast unit tests on saved data.

| Area | Test type | How | Target |
|---|---|---|---|
| Role type (AI/ML, SWE, Data, none) from title | Labelled eval | 120 titles in `data/labels/titles.csv` | Precision 90%+ |
| Level (intern, entry, experienced, unclear) from title | Labelled eval | Same sheet | Entry recall 95%+, precision 85%+ |
| Min years, "no sponsorship", "citizens only", clearance | Labelled eval | 40 description snippets in `data/labels/descriptions.csv` | "No sponsorship" recall 95%+; years exact 90%+ |
| US and remote detection | Unit | Real location strings from the snapshot, one case per pattern | Every pattern passes |
| Board adapters | Unit | One saved, trimmed API response per system | Every field mapped; missing fields don't crash |
| Diff: new, closed, failed board, first run, 72-hour guard | Unit | Pure function, no database | Every branch covered |
| Discord notifier | Unit | httpx mock transport | 10 per message; 429 waits; failure leaves `alerted_at` empty |
| Postgres store | Integration | Neon `dev` branch, skipped when `DEV_DATABASE_URL` isn't set | Same snapshot twice gives 0 new jobs |
| Whole run | End to end | 3 saved boards + mock HTTP + dev branch | `jobs.json` has the expected jobs and evidence |

The targets are targets, not results. Results get recorded here after
the first scoring.

**Why the classifier weights differ.** A senior job in a new grad's alerts is
annoying. A missed entry-level job costs an application. A missed "we cannot
sponsor" sends someone to a job that will reject them. So recall matters most
for entry-level and "no sponsorship", and precision matters most for role
type.

**How the labelled sets were built (2026-09-30, seed 2026).**
- **Titles:** 33,235 open jobs with engineering, data or AI words in the
  title, split into rough buckets by keyword: entry-ish (2,618), plain
  (16,924), senior-ish (13,693). 45, 50 and 25 were drawn from each, because a
  plain random sample would hold almost no entry-level titles. Scores are
  reported per bucket and weighted back to the bucket sizes.
- **Descriptions:** 1,855 tech jobs from 120 random Lever and Ashby boards.
  543 of them (29%) mention sponsorship, visas, citizenship or clearance. 20
  were drawn from those and 20 from the rest. Each row shows only the
  sentences around those words and around "N years", so labelling is quick.
- **The rules are written before the labels are read.** The labels are the
  held-out test, scored once. After fixing failures, the same set becomes a
  development set, and a fresh sample is needed for the next honest score.

**Labelling guide.**
- `role`: `a` AI/ML, `s` software, `d` data, `n` none (sales engineer,
  mechanical, support, product manager).
- `level`: `i` intern, `e` entry (open to a new grad), `x` experienced, `?`
  can't tell from the title alone.
- `min_years`: the smallest number of years required, blank if none stated.
- flags: `y` or `n`. "Regardless of citizenship" in equal-opportunity text is
  `n`, because it isn't a restriction.

### Classifier results (2026-10-01)

Labels: 120 titles and 40 description snippets, labelled by the owner.

**First score, held out (rules v2, written before the labels were read):**

| Measure | Target | Result |
|---|---|---|
| SWE role precision | 90%+ | 55% |
| AI / Data role precision | 90%+ | 86% / 80% |
| Entry level precision / recall | 85%+ / 95%+ | 89% / 65% |
| Intern, experienced (precision / recall) | | 94% / 94%, 93% / 96% |
| "No sponsorship" precision / recall | recall 95%+ | 100% / 100% (only 3 positives) |
| "Citizens only" recall | | 22% |
| Required years, "2 or fewer" split | | 40 of 40 |

The SWE number was the main failure: any title containing "Engineer" counted
as software, and most of those are hardware, field or sales roles.

**After fixes (rules v3).** This is now a development score, not a held-out
one, because the fixes were made after reading where the rules failed. A fresh
sample is needed for the next honest number.

- A bare "Engineer" no longer means software; a language name ("Java
  Engineer") or software word does.
- Strong entry words ("Junior", "New Grad", "Engineer in Training") win over a
  seniority word in the same title.
- More ways of writing "US persons only" (the ITAR list of citizen, permanent
  resident, refugee, asylee), clearances read from the title, and clearance
  jobs counted as citizens-only, because US clearances require citizenship.
- Government contractors (federal, government, public sector) count as
  citizens-only. Owner's rule.
- Titles from the excluded gig marketplaces (30 of 120) are left out of the
  score, because the poller no longer fetches those boards.

| Measure (90 titles, 40 snippets) | Target | Result |
|---|---|---|
| SWE role precision / recall | 90%+ | 96% / 100% |
| AI role precision | 90%+ | 100% (10 of 10, after 3 label corrections) |
| Data role precision / recall | 90%+ | 100% / 57% |
| Entry level precision / recall | 85%+ / 95%+ | 95% / 90% (94% weighted) |
| Blocked for F-1 (any flag) precision / recall | | 79% / 100% |
| Required years, "2 or fewer" split | | 40 of 40 |

Every "blocked" disagreement comes from the government and clearance rules,
which were added after labelling.

Three labels were corrected after discussion. A sales engineer is a sales
role, so "none". Two research engineer roles at AI labs were labelled "none"
because the owner doesn't want research roles; they are AI roles, so they're
labelled AI. The owner's personal alert filter skips research titles only at
frontier labs (Anthropic, OpenAI, xAI, Mistral, Cohere), because at startups
"Research Engineer" is often applied ML engineering (Exa's content
understanding role, for example). With those corrections, AI role precision is 100% (10 of 10) and any
tech role is 98%.

**Effect on the live data:** candidates went from 6,401 to 4,627 (1,829
closed, 55 added). Jobs stored under older rules are re-read once when the
rules version changes, keeping their first-seen time and alert state.

### Revisit

- **Schedule drift:** GitHub's cron can start runs late when it's busy.
  Time-to-alert is measured on Day 7. If it's poor, trigger the workflow from
  an external cron.
- **Dead-man switch:** if runs stop entirely, nothing inside the poller
  notices. A free healthchecks.io ping would email the owner.
- **Classifier:** rules first. Move to a model only if the labelled test set
  shows rules plateauing.
- **Workday:** 45% of Simplify's listings, mostly large companies. Separate
  adapter, stretch goal.
