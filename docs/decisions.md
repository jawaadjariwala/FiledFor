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
