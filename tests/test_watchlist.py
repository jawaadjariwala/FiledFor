"""Regression tests for the name and URL traps found while building the watchlist."""

import pytest

from filedfor.watchlist import Employer, Feed, find_candidates, norm, parse_feed_url


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Amazon.com Services, LLC", "amazon com services"),
        ("AMAZON.COM SERVICES LLC", "amazon com services"),  # case variants merge
        ("Ernst & Young U.S. LLP", "ernst and young"),  # dotted abbreviations
        ("ACME L.L.C.", "acme"),
        ("US Bank", "us bank"),  # suffixes only come off the end
        ("The Home Depot", "home depot"),
        ("Jerry’s Drug & Surgical LLC", "jerrys drug and surgical"),  # curly apostrophe
        ("Anthropic, PBC", "anthropic"),
        ("Palantir.net Inc.", "palantir net"),  # must stay distinct from...
        ("Palantir Technologies Inc.", "palantir technologies"),  # ...the real Palantir
    ],
)
def test_norm(raw, expected):
    assert norm(raw) == expected


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://job-boards.greenhouse.io/stripe/jobs/123",
            ("greenhouse", "stripe", False),
        ),
        (
            "https://boards.greenhouse.io/anduril/jobs/1?gh_src=x",
            ("greenhouse", "anduril", False),
        ),
        (
            "https://job-boards.eu.greenhouse.io/ebury/jobs/4",
            ("greenhouse", "ebury", True),
        ),
        ("https://jobs.lever.co/palantir/abc-123", ("lever", "palantir", False)),
        ("https://jobs.eu.lever.co/cirrus/abc", ("lever", "cirrus", True)),
        (
            "https://jobs.ashbyhq.com/par%20technology/abc",
            ("ashby", "par technology", False),
        ),
        # Greenhouse paths that aren't a company's board
        ("https://boards.greenhouse.io/embed/job_app?token=7619102003", None),
        ("https://job-boards.eu.greenhouse.io/agency/jobs/4778238101", None),
        ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/1", None),
    ],
)
def test_parse_feed_url(url, expected):
    assert parse_feed_url(url) == expected


def employers(*rows):
    return {fein: Employer(fein, name, n, {norm(name)}) for fein, name, n in rows}


def feed(slug, *names):
    f = Feed("greenhouse", slug, False)
    f.names.update(names)
    return f


def test_every_listed_name_is_tried():
    # "Sigma" alone hits the wrong companies; the same board is also listed
    # as "Sigma Computing", which must be offered too
    emps = employers(("1", "Sigma Group Inc", 6), ("2", "Sigma Computing, Inc.", 52))
    got = find_candidates(
        [feed("sigmacomputing", "Sigma", "Sigma", "Sigma Computing")], emps
    )
    assert {c.fein for c in got[("greenhouse", "sigmacomputing")]} == {"1", "2"}


def test_prefix_candidates_are_capped_and_stable():
    emps = employers(
        *[(str(i), f"Planet {w} Inc", 1) for i, w in enumerate("abcdefgh")]
    )
    runs = [find_candidates([feed("planetlabs", "Planet")], emps) for _ in range(3)]
    feins = [[c.fein for c in r[("greenhouse", "planetlabs")]] for r in runs]
    assert len(feins[0]) == 5
    assert feins[0] == feins[1] == feins[2]


def test_rejections_fall_through_to_the_next_rule_only():
    emps = employers(
        ("1", "Sigma Group Inc", 6),
        ("2", "Sigma Computing, Inc.", 52),
        *[(str(i), f"Sigma {w} Inc", 1) for i, w in zip(range(10, 18), "abcdefgh")],
    )
    key = ("greenhouse", "sigma")
    # Both exact hits rejected: the prefix rule gets a turn
    got = find_candidates([feed("sigma", "Sigma")], emps, {(*key, "1")})
    assert [c.rule for c in got[key]] == ["prefix"] * 5
    # Every prefix candidate rejected too: no new page of five
    rejected = {(*key, "1")} | {(*key, c.fein) for c in got[key]}
    assert key not in find_candidates([feed("sigma", "Sigma")], emps, rejected)
