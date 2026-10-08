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
- **Site and RSS:** static page on GitHub Pages with filters, evidence on every
  job, and one feed per field.
- **Discord alerts** for anyone running their own copy.

## Next

- [ ] **Company pages:** one page per sponsor with filings over time, wage
      levels, top job titles and open roles.
- [ ] **Real lottery levels:** compare each offered wage with the OEWS wage
      levels for its occupation and area, since the FY2027 lottery weights by
      the level the offered wage reaches.
- [ ] **Push alerts** from the site itself, with filters saved per device and
      no account.
- [ ] **Measure time from posting to alert** and show it on the site.
- [ ] **Fresh labelled sample** for an honest classifier score (the current
      set became a development set once rules were fixed against it), and a
      second reviewer for the match decisions.
- [ ] **Store and end-to-end tests** against a Postgres dev branch.
- [ ] **Dead-man switch:** an outside check that emails when runs stop.

## Ideas

- A model for the jobs rules can't decide (level not in the title, no years
  stated): rules first, the model judges only those, measured against rules
  alone on fresh labels.
- More job systems: Oracle Cloud and iCIMS are the next largest in the
  listings.
- An email digest.
- Cap-exempt employers (universities, hospitals, nonprofit research) as their
  own section, since they sponsor outside the lottery.
