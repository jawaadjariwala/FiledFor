"""Hand-picked cases for each rule. The labelled sets in data/labels measure
accuracy; these only stop known cases from regressing."""

import pytest

from filedfor.classify import flags, is_remote, is_us, level, min_years, role


@pytest.mark.parametrize(
    "title, expected_role, expected_level",
    [
        ("Software Engineer, New Grad", "swe", "entry"),
        ("Senior Software Engineer", "swe", "experienced"),
        ("Machine Learning Engineer", "ai", "unclear"),
        ("Data Scientist I", "data", "entry"),
        ("Software Engineer II", "swe", "experienced"),
        ("Associate Director, Engineering", "swe", "experienced"),
        ("Distributed Systems Engineer", "swe", "unclear"),
        ("Member of Technical Staff", "swe", "unclear"),  # "staff" here isn't a level
        ("Software Engineering Intern (Summer 2027)", "swe", "intern"),
        ("Engineer I - Payments", "swe", "entry"),
        ("Staff Engineer", "swe", "experienced"),
        ("Sales Engineer", None, "unclear"),
        ("Systems Engineer, Starship", None, "unclear"),  # hardware systems work
        ("Data Center Technician", None, "unclear"),
        ("Product Manager, AI", None, "experienced"),
        ("Mechanical Engineer I", None, "entry"),
        (
            "Doctors - AI Training - Manchester, UK",
            None,
            "unclear",
        ),  # gig data labelling
        ("AI/ML Data Contributor", None, "unclear"),
        ("Applied AI Researcher", "ai", "unclear"),
    ],
)
def test_title(title, expected_role, expected_level):
    assert (role(title), level(title)) == (expected_role, expected_level)


@pytest.mark.parametrize(
    "location, country, us, remote",
    [
        ("San Francisco, CA | New York City, NY", None, True, False),
        ("Toronto, ON, CA", None, False, False),  # CA here is Canada
        ("Berlin, DE", None, False, False),  # DE here is Germany
        ("Remote", None, None, True),  # country not stated
        ("Remote - United States", None, True, True),
        ("Costa Mesa, California, United States", None, True, False),
        ("Auckland, NZ", None, False, False),
        ("Anywhere", "US", True, True),  # a structured country field wins
        ("Paris", "FR", False, False),
    ],
)
def test_location(location, country, us, remote):
    assert is_us(location, country) is us
    assert is_remote(location) is remote


def test_structured_workplace_wins():
    assert is_remote("New York, NY", "remote") is True
    assert is_remote("Remote", "onsite") is False
    assert is_remote("Remote", True) is True


@pytest.mark.parametrize(
    "text, years",
    [
        (
            "3+ years of professional experience with Python. 1+ year of Go experience.",
            1,
        ),
        ("Two (2) years of experience in data engineering.", 2),
        ("3-5 years of industry experience.", 3),
        (
            "Bachelor's degree or a 4-year degree in CS.",
            None,
        ),  # a degree, not experience
        ("We have been in business for 30 years of experience.", None),  # over the cap
        ("No experience required.", None),
    ],
)
def test_min_years(text, years):
    assert min_years(text) == years


@pytest.mark.parametrize(
    "text, sponsor, citizen, clearance",
    [
        ("We are unable to sponsor visas for this role.", True, False, False),
        ("Visa sponsorship is not available.", True, False, False),
        (
            "Must be authorized to work in the US without the need for sponsorship.",
            True,
            False,
            False,
        ),
        ("Visa sponsorship: No", True, False, False),
        ("We sponsor H-1B visas for this role.", False, False, False),
        # Equal-opportunity boilerplate isn't a restriction
        (
            "Applicants are considered regardless of citizenship status.",
            False,
            False,
            False,
        ),
        (
            "Must be a U.S. citizen. Active TS/SCI clearance required.",
            False,
            True,
            True,
        ),
        ("Must be a U.S. person as defined by ITAR.", False, True, False),
        ("Ability to obtain a security clearance.", False, False, True),
    ],
)
def test_flags(text, sponsor, citizen, clearance):
    f = flags(text)
    assert (f.no_sponsorship, f.citizens_only, f.clearance) == (
        sponsor,
        citizen,
        clearance,
    )
