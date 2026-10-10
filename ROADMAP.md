# Roadmap

What's built, what's next, and ideas that aren't scheduled. Design decisions
behind each piece are in [docs/decisions.md](docs/decisions.md).

## Built

- **Sponsor data:** every certified H-1B filing from October 2024 to June
  2026, reduced to counts per employer, occupation and wage level
  ([data notes](docs/data.md)).
- **Watchlist:** about 2,600 company job boards on Greenhouse, Lever, Ashby,
  Workday and SmartRecruiters, matched to employers by tiered rules plus a
  reviewed queue ([ADR-001](docs/decisions.md#adr-001-matching-job-feed-companies-to-dol-employers),
  [ADR-003](docs/decisions.md#adr-003-workday-and-smartrecruiters)).
- **Poller:** runs every 30 minutes on GitHub Actions, classifies each job by
  field, level and sponsorship language, stores changes in Postgres
  ([ADR-002](docs/decisions.md#adr-002-the-poller)).
- **Classifier:** rules scored against hand-labelled titles and descriptions.
- **Site and RSS:** static page on GitHub Pages with shareable filters, metro
  and state filtering, a company panel with each field's filing record, saved
  and applied jobs, logos, and one RSS feed per field.
- **Discord alerts** for anyone running their own copy.
- **Own domain, analytics and search:** filedfor.com, anonymous GoatCounter
  counts, sitemap and link previews.

## Next

### 1. All tech roles, all levels
- [x] Eight fields: Software, AI/ML, Data, Hardware & Embedded, IT & Cloud,
      Security, Product, Design. Rules scored on a fresh labelled sample
- [x] Filing evidence for each new field (occupation code mapping)
- [x] All levels stored and listed; Level filter gains Mid and Senior.
      Default view stays new grad, across all eight fields
- [x] Data split so the page stays fast: a 7-day file loads first, 30 days
      on demand, filing records stored once per company
- [x] Green card (PERM) suspension notices for affected employers

### 2. A job portal people rely on
- [ ] Search with its own filter panel, so earlier filters never hide
      matches, and ranked matching that finds similar jobs: abbreviations
      (SWE, ML, PM, QA), related roles, plurals and typos
- [ ] Salary ranges from postings, with a pay filter and sort
- [ ] Job detail view inside FiledFor
- [ ] "Checked X min ago" on every job, and a status page
- [ ] Hide a job or a company
- [ ] Application tracker: statuses, notes, CSV export
- [ ] Saved searches with "new since last visit" counts
- [ ] Remote / hybrid / on-site, strong-sponsor and years filters
- [ ] Install as an app on phones

### 3. Pages for search
- [ ] A page per sponsor: filings over time, wage levels per field, top
      filed titles, open roles
- [ ] Field and city pages ("Data scientist jobs at H-1B sponsors in New York")
- [ ] Sponsor rankings and two or three guides (reading H-1B filings, the
      FY2027 weighted lottery)
- [ ] Sitemap index, structured data, internal links

### Also
- [ ] **Real lottery levels:** compare each offered wage with the OEWS wage
      levels for its occupation and area, since the FY2027 lottery weights by
      the level the offered wage reaches
- [ ] **Measure time from posting to alert** and show it on the site
- [ ] **Second reviewer** for the match decisions
- [ ] **Store and end-to-end tests** against a Postgres dev branch

## Ideas

- A model for the jobs rules can't decide (level not in the title, no years
  stated): rules first, the model judges only those, measured against rules
  alone on fresh labels.
- More job systems: Oracle Cloud and iCIMS are the next largest in the
  listings.
- An email digest.
- Cap-exempt employers (universities, hospitals, nonprofit research) as their
  own section, since they sponsor outside the lottery.
