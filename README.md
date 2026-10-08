# FiledFor

**New-grad tech jobs at companies that have filed H-1B applications for that kind of role.**

**Use it here: [jawaadjariwala.github.io/FiledFor](https://jawaadjariwala.github.io/FiledFor/)**. It's free, with no sign-up.

Most "visa-friendly" job lists tell you a company sponsors. FiledFor shows each job next to what the company actually filed with the Department of Labor for that kind of role: how many H-1B applications, how many were new hires, and what share sat at the lower wage levels where new grads usually land. The list refreshes about every 30 minutes, so you can apply while a posting is still fresh.

## What you can do with it

- **Browse the site.** Filter by field (Software, AI/ML, Data), level, how recently it was posted, and remote. Postings that say citizens only, no sponsorship, or need a clearance are hidden by default.
- **Follow an RSS feed** for new jobs in your field: [all](https://jawaadjariwala.github.io/FiledFor/feeds/all.xml), [software](https://jawaadjariwala.github.io/FiledFor/feeds/swe.xml), [AI/ML](https://jawaadjariwala.github.io/FiledFor/feeds/ai.xml), [data](https://jawaadjariwala.github.io/FiledFor/feeds/data.xml). Works in any feed reader, and in Slack or Discord through an RSS bot.
- **Use the data.** [`jobs.json`](https://jawaadjariwala.github.io/FiledFor/jobs.json) has every listed job with its flags and evidence. The sponsor tables in `data/` are described below.

## How it works

```
DOL LCA filings ──> sponsors.parquet, role_evidence.parquet ──┐
                                                              ├──> poller ──> Postgres ──> site, jobs.json, RSS
Greenhouse / Lever / Ashby boards ──> companies.csv ──────────┘   (every 30 min on GitHub Actions)
```

1. **Sponsor data** (`lca.py`, `sponsors.py`). Every H-1B Labor Condition Application certified from October 2024 to June 2026, about a million rows from the DOL's public disclosure files, reduced to counts per employer and occupation. Contact and attorney details are dropped on read.
2. **Watchlist** (`watchlist.py`). About 2,600 company job boards on Greenhouse, Lever, Ashby, Workday and SmartRecruiters, each matched to the employer's federal tax ID (FEIN) in the DOL data. Matching uses tiered rules plus hand-reviewed aliases rather than fuzzy scores, because a wrong match puts false evidence next to a job. Why: [ADR-001](docs/decisions.md#adr-001-matching-job-feed-companies-to-dol-employers).
3. **Poller** (`poll.py`). Fetches every board, keeps US tech roles that aren't clearly senior, reads each new description for the years asked and for phrases that rule out sponsorship, attaches the evidence, and stores what changed in Postgres. Design and failure handling: [ADR-002](docs/decisions.md#adr-002-the-poller).
4. **Publish** (`publish.py`). Writes the static site, `jobs.json`, `health.json` and the RSS feeds, which GitHub Pages serves.

The classifier is rules, scored against hand-labelled titles and descriptions. The scores and known misses are in [ADR-002](docs/decisions.md#classifier-results-2026-10-01).

## Limits

- **Filings are history, not a promise.** A company that filed for software engineers before may not sponsor this particular role. Check the posting and ask the recruiter. Nothing here is legal advice.
- **Other systems aren't covered yet**, like Oracle and iCIMS. Workday boards are read in full about twice a day, so a closed Workday job can stay listed for up to half a day. Why: [ADR-003](docs/decisions.md#adr-003-workday-and-smartrecruiters).
- **Some companies file under a different legal name.** "No filings found" can mean the match missed, not that the company never sponsors.
- **The flags come from rules**, which get most jobs right and some wrong.
- **Wage level here is the prevailing wage level on the filing.** The FY2027 lottery weights by the level the offered wage reaches, which can be higher.

## Run your own

You need Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
```

To run the poller you also need a Postgres database (a free [Neon](https://neon.tech) project works):

```sh
echo 'DATABASE_URL="postgresql://..."' > .env
uv run python -m filedfor.poll --no-alerts   # writes public/
python -m http.server -d public              # open http://localhost:8000
```

The first run records every open job without alerting. After that, each run alerts only on new ones.

**To host it**, fork the repo, add `DATABASE_URL` as an Actions secret, set Settings, Pages, Source to "GitHub Actions", and run the Poll workflow once. Add a `DISCORD_WEBHOOK` secret to get Discord alerts for new matches.

**To rebuild the sponsor data**, download the LCA disclosure files from the [DOL performance data page](https://www.dol.gov/agencies/eta/foreign-labor/performance) into `data/raw/`, then:

```sh
uv run python -m filedfor.lca        # Excel to Parquet
uv run python -m filedfor.sponsors   # sponsor tables
uv run python -m filedfor.watchlist  # match boards to employers, probe feeds
```

## Data

| File | What it is |
|---|---|
| `data/sponsors.parquet` | One row per employer (FEIN): filings, tech filings, wage-level mix, median tech wage |
| `data/sponsor_roles.parquet` | One row per employer and occupation (SOC code) |
| `data/role_evidence.parquet` | One row per employer and field (software, AI/ML, data), as shown on the site |
| `data/companies.csv` | Every polled job board, its open job count and matched FEINs |
| `data/aliases.csv`, `data/match_reviews.csv` | Hand-made matching decisions |

The LCA data is public and comes from the U.S. Department of Labor. Company job boards were found through [SimplifyJobs](https://github.com/SimplifyJobs)' public listings, which are used only to discover boards and are never republished.

## License

Code under the [MIT License](LICENSE).
