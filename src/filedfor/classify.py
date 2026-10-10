"""Facts about a job, read from its title, location and description.

Rules rather than a model: every decision can be traced to a pattern, and the
labelled sets in data/labels measure how often the rules are right (see
ADR-002). Bump VERSION whenever a rule changes, so stored jobs record which
rules classified them.

    role       swe | ai | data | hardware | it | security | product | design
               | None   (None: not a role FiledFor covers)
    level      intern | entry | mid | senior | unclear
    is_us      True | False | None      (None: can't tell, e.g. "Remote")
    min_years  smallest years of experience the description asks for
    flags      no_sponsorship, citizens_only, clearance
"""

import re
from dataclasses import dataclass

VERSION = 5
FIELDS = ("swe", "ai", "data", "hardware", "it", "security", "product", "design")


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), re.IGNORECASE)


# --- role ------------------------------------------------------------------

# Engineering and technical titles that aren't software, data or AI work.
# Checked first, unless the title also says "software".
NOT_SOFTWARE = _rx(
    r"\b(sales|solutions?|pre-?sales|customer|support|field|forward deployed sales)\s+engineer",
    r"\b(mechanical|electrical|civil|structural|chemical|manufacturing|process|industrial"
    r"|propulsion|avionics|rf|optical|hardware|materials?|quality|reliability|test|validation"
    r"|systems|mission|launch|production|facilities|construction|environmental|nuclear"
    r"|thermal|design|integration|packaging|power|electronics|asic|fpga|analog|pcb"
    r"|network|audio|acoustic|manufacturing)\s+(design\s+)?engineer",
    r"\b(product|program|project|account|marketing|sales|recruit|talent|people|legal"
    r"|finance|operations)\b.*\b(manager|lead|director|partner|specialist|coordinator)",
    r"\b(recruiter|designer|counsel|accountant|technician|mechanic|machinist|welder"
    r"|scientist,? (chemistry|biology)|biologist|chemist)\b",
    r"\bdata cent(er|re)\b",
    r"\bbusiness develop",
)
# Jobs that are never software, data or AI, whatever else the title says.
# Big retailers on Workday post "Front End Cashier", "Back End Clerk" and
# "Mobile Associate - Retail Sales" (v4)
NEVER_TECH = _rx(
    r"cashier",
    r"\bclerk\b",
    r"pharmacy",
    r"\bnurs(e|ing)\b",
    r"\bteller\b",
    r"\b(delivery|truck|cdl)\s+driver",
    r"^driver\b",
    r"\bsales (trainee|associate|representative|rep|executive|consultant|specialist)\b",
    r"\bretail sales\b",
    r"account executive",
    r"\bstore (associate|manager|team)",
)
# Overrides the list above: "Distributed Systems Engineer" is software
SOFTWARE_HINT = _rx(r"software", r"distributed", r"machine learning")
AI = _rx(
    r"machine learning",
    r"\bml\b",
    r"\bai\b",
    r"\bllm",
    r"artificial intelligence",
    r"deep learning",
    r"\bnlp\b",
    r"natural language",
    r"computer vision",
    r"\bperception\b",
    r"applied scien",
    r"research (scientist|engineer)",
    r"\bmlops\b",
    r"generative",
    r"reinforcement learning",
    r"\bgenai\b",
)
DATA = _rx(
    r"\bdata\b",
    r"analytics",
    r"business intelligence",
    r"\bbi\b",
    r"data scien",
    r"statistic",
    r"\betl\b",
)
SWE = _rx(
    r"software",
    r"developer",
    r"\bswe\b",
    r"platform engineer",
    r"infrastructure engineer",
    r"site reliability",
    r"\bsre\b",
    r"devops",
    r"cloud engineer",
    r"security engineer",
    r"firmware",
    r"embedded",
    r"\bsdet\b",
    r"automation engineer",
    r"member of technical staff",
    r"forward deployed engineer",
    r"programmer",
    r"distributed systems",
    r"systems software",
)
# Software only next to an engineering word: "Frontend Engineer" is, "Front
# End Cashier" isn't; "Java Developer" is, "1348 Java Lane" isn't (v4)
SWE_AREA = _rx(
    r"back-?\s?end",
    r"front-?\s?end",
    r"full-?\s?stack",
    r"\bweb\b",
    r"mobile",
    r"\bios\b",
    r"android",
    r"\b(java|python|c\+\+|c#|\.net|javascript|typescript|golang|rust|ruby|scala|kotlin|swift)\b",
)
ENGINEERING_WORD = _rx(
    r"engineer",
    r"\bengr\b",
    r"developer",
    r"development",
    r"programmer",
    r"\bdev\b",
    r"\bsde\b",
)


# "AI" or "data" alone isn't a job: "AI Trainer" and "Data Contributor" are
# gig work labelling data. These roles also need a word for the work itself.
JOB_WORD = _rx(
    r"engineer",
    r"scien",
    r"research",
    r"developer",
    r"analyst",
    r"architect",
    r"programm",
    r"\bintern\b",
    r"co-?op",
    r"\bmts\b",
    r"technical staff",
)


# --- v5: eight fields --------------------------------------------------------

# Business functions that borrow tech words ("Partner Marketing, Security",
# "Technical Recruiter, AI/ML", "Strategic Sourcing Manager, Infrastructure").
# Out, unless the title also names hands-on tech work ("Marketing Data Scientist")
NON_TECH_FUNCTION = _rx(
    r"\bmarketing\b",
    r"recruit",
    r"talent acquisition",
    r"\bsourcing\b",
    r"procurement",
    r"\bsupply\b",
    r"\bsupplier\b",
    r"commodity",
    r"accounting",
    r"\baccount (director|manager)\b",
    r"customer success",
    r"administrative assistant",
    r"\bconstruction\b",
    r"facilit",
    r"\bhse\b",
    r"inspector",
    r"project controls",
    r"\bsales\b",
    r"partnership",
)
HANDS_ON_TECH = _rx(
    r"software",
    r"developer",
    r"data (scien|analy|engineer)",
    r"machine learning",
    r"\bml\b",
)
# Physical security is a different job from information security
NOT_INFOSEC = _rx(
    r"officer",
    r"guard",
    r"loss prevention",
    r"physical",
    r"patrol",
    r"shift supervisor",
    r"national security",
    r"operator",
)
DESIGN = _rx(
    r"\bux\b",
    r"\bui\s*(/|and|&)?\s*(ux|design)",  # "UI Designer", not "Web UI" developer work
    r"user experience",
    r"product design",
    r"interaction design",
    r"user research",
    r"design technologist",
)
PRODUCT = _rx(
    r"product manag",
    r"product owner",
    r"technical program manag",
    r"\btpm\b",
    r"(technical|hardware|engineering|software)\b.*\bprogram manag",
)
SECURITY = _rx(
    r"security",
    r"\bcyber",
    r"infosec",
    r"appsec",
    r"penetration",
    r"vulnerabilit",
    r"\bthreat",
    r"\bsoc analyst",
    r"identity (and|&) access",
    r"\biam\b",
)
SECURITY_JOB = _rx(
    r"engineer",
    r"analyst",
    r"architect",
    r"specialist",
    r"manager",
    r"intern",
    r"research",
    r"\bmts\b",
    r"technical staff",
    r"consultant",
    r"tester",
)
HARDWARE = _rx(
    r"electrical",
    r"electronics?",
    r"hardware",
    r"embedded",
    r"firmware",
    r"\bfpga\b",
    r"\basic\b",
    r"\brtl\b",
    r"design verification",
    r"\banalog\b",
    r"mixed[- ]signal",
    r"\brf\b",
    r"antenna",
    r"silicon",
    r"\bpcb\b",
    r"circuit",
    r"\bchip\b",
    r"signal integrity",
    r"\bdft\b",
    r"power electronics",
    r"system[- ]level test",
    r"\bslt\b",
)
HARDWARE_JOB = _rx(
    r"engineer",
    r"designer",
    r"intern",
    r"co-?op",
    r"associate",
    r"architect",
    r"scientist",
    r"developer",
)
NOT_HARDWARE = _rx(
    r"technician",
    r"maintenance",
    r"manufactur",
    r"nuclear",
    r"equipment",
    r"instrumentation",
)
IT = _rx(
    r"\bit\b",
    r"information technology",
    r"\bis/it\b",
    r"system(s)? admin",
    r"sysadmin",
    r"network (engineer|admin|architect|planning|& infrastructure|and infrastructure|manager|specialist|analyst)",
    r"cloud (engineer|admin|architect|system)",
    r"help ?desk",
    r"service desk",
    r"desktop support",
    r"database admin",
    r"\bdba\b",
    r"business systems analyst",
    r"infrastructure (manager|leader|lead|director)",
    r"manager, infrastructure",
    r"(director|head|vp)\b[^,]*,?\s*(of\s+)?product infrastructure",
    r"cloud platform",
    r"technical support (specialist|analyst|technician)",
    r"\bit support",
)
# "Director Product, Discovery & AI", "Sr. Director, Product & UX", "Head of Product".
# Not "Director, Product Infrastructure" or "Head of Product Design"
PRODUCT_LEAD = _rx(
    r"\b(director|head|vp|vice president)\b[^,]*,?\s*(of\s+)?product\b(?!\s*(design|infrastructure|engineering|security|marketing))",
)
# HR and ERP systems work is IT, even when the title says "security"
IT_SYSTEMS = _rx(r"\bhcm\b", r"\bhris\b", r"\berp\b")
ENGINEERING_LEAD = _rx(r"(director|vp|head|vice president) of engineering")
# The job is software, not a domain like "Software-Defined Radio"
SOFTWARE_JOB = _rx(r"software (engineer|developer|development)", r"\bdeveloper\b")
SRE = _rx(r"site reliability", r"reliability engineering", r"engineering manager")


def role(title: str) -> str | None:
    """The field a title belongs to, most specific first. AI/ML beats data
    beats software, so an 'ML Data Engineer' is AI and a 'Senior Software
    Engineer, ML Systems' is AI too."""
    t = title.lower()
    if NEVER_TECH.search(t):
        return None
    if re.search(r"data cent(er|re)", t) and "software" not in t:
        return None
    if PRODUCT_LEAD.search(t):
        return "product"
    if DESIGN.search(t):
        return "design"
    if PRODUCT.search(t):
        return "product"
    if NON_TECH_FUNCTION.search(t) and not HANDS_ON_TECH.search(t):
        return None
    if ENGINEERING_LEAD.search(t):
        return "swe"
    if IT_SYSTEMS.search(t) and not SOFTWARE_JOB.search(t):
        return "it"
    if SECURITY.search(t) and SECURITY_JOB.search(t) and not NOT_INFOSEC.search(t):
        return "security"
    if AI.search(t) and JOB_WORD.search(t):
        return "ai"
    if SRE.search(t):
        return "swe"
    if DATA.search(t) and JOB_WORD.search(t) and "software" not in t:
        return "data"
    if (
        HARDWARE.search(t)
        and HARDWARE_JOB.search(t)
        and not NOT_HARDWARE.search(t)
        and not SOFTWARE_JOB.search(t)
    ):
        return "hardware"
    if IT.search(t) and not SOFTWARE_JOB.search(t):
        return "it"
    area = bool(SWE_AREA.search(t) and ENGINEERING_WORD.search(t))
    if NOT_SOFTWARE.search(t) and not (SOFTWARE_HINT.search(t) or area):
        return None
    if SWE.search(t) or area:
        return "swe"
    return None


# --- level -----------------------------------------------------------------

INTERN = _rx(r"\bintern(ship)?s?\b", r"\bco-?op\b")
EXPERIENCED = _rx(
    r"\bsenior\b",
    r"\bsr\.?\b",
    r"\bstaff\b",
    r"\bprincipal\b",
    r"\blead\b",
    r"\bmanager\b",
    r"\bleader\b",
    r"\bdirector\b",
    r"\bhead of\b",
    r"\bvp\b",
    r"vice president",
    r"\barchitect\b",
    r"\bdistinguished\b",
    r"\bexpert\b",
    r"\bmid-?(level|senior)\b",
    r"\bexperienced\b",
    # "Software Engineer II", "Engineer 3", "L5", but not "Engineer I" or "1"
    r"\b(ii|iii|iv|v)\b",
    r"\b[2-6]\b\s*$",
    r"\b[2-6]\s*[,(-]",
    r"\b(l|e|ic)[4-9]\b",
)
# Explicit enough to win over a seniority word in the same title
STRONG_ENTRY = _rx(
    r"new\s*grad",
    r"new graduate",
    r"entry[- ]level",
    r"\bjunior\b",
    r"early[- ]career",
    r"early talent",
    r"engineer in training",
    r"\beit\b",
)
ENTRY = _rx(
    r"new\s*grad",
    r"new graduate",
    r"recent grad",
    r"\bgraduate\b",
    r"entry[- ]level",
    r"\bjunior\b",
    r"\bjr\.?\b",
    r"\bassociate\b",
    r"early[- ]career",
    r"\buniversity\b",
    r"\bcampus\b",
    r"\bapprentice",
    r"\bfellowship\b",
    r"rotational",
    r"class of 20\d\d",
    r"\b(i|1)\b\s*$",
    r"\b(i|1)\s*[,(-]",
    r"\blevel (i|1)\b",
    r"\b(l|e|ic)[1-3]\b",
)


MID = _rx(
    r"\b(ii|2)\b\s*$",
    r"\b(ii|2)\s*[,(-]",
    r"\b(ii|2)\b",
    r"mid[- ]?level",
    r"intermediate",
    r"\blevel (ii|2)\b",
)
# Titles that name a job, not a rank: a "Product Manager" isn't senior
ROLE_NOT_RANK = re.compile(r"(product|program|project) manager", re.IGNORECASE)
OPEN_TO_ENTRY = re.compile(r"\b(i|1)\s*/\s*(ii|2)\b", re.IGNORECASE)  # "Engineer 1/2"


def level(title: str) -> str:
    """Intern first (a 'Senior Intern' is still an intern), then entry words
    strong enough to win, then senior, mid and entry. Titles with none are
    'unclear', and the description's required years decide them."""
    # A title used across every level, so the "staff" in it means nothing
    t = title.lower().replace("member of technical staff", "mts")
    t = t.replace("member of product staff", "mps")
    if INTERN.search(t):
        return "intern"
    if STRONG_ENTRY.search(t) or OPEN_TO_ENTRY.search(t):
        return "entry"
    ranked = ROLE_NOT_RANK.sub("role", t)
    # "Associate Director" is senior; "Associate Software Engineer" is entry
    if EXPERIENCED.search(ranked) and not MID.search(ranked):
        return "senior"
    if re.search(
        r"\b(senior|sr\.?|staff|principal|lead|leader|director|head of|vp)\b", ranked
    ):
        return "senior"
    if MID.search(ranked):
        return "mid"
    if EXPERIENCED.search(ranked):
        return "senior"
    if ENTRY.search(t):
        return "entry"
    return "unclear"


# --- location --------------------------------------------------------------

US_STATES = {
    "AL": "alabama",
    "AK": "alaska",
    "AZ": "arizona",
    "AR": "arkansas",
    "CA": "california",
    "CO": "colorado",
    "CT": "connecticut",
    "DE": "delaware",
    "FL": "florida",
    "GA": "georgia",
    "HI": "hawaii",
    "ID": "idaho",
    "IL": "illinois",
    "IN": "indiana",
    "IA": "iowa",
    "KS": "kansas",
    "KY": "kentucky",
    "LA": "louisiana",
    "ME": "maine",
    "MD": "maryland",
    "MA": "massachusetts",
    "MI": "michigan",
    "MN": "minnesota",
    "MS": "mississippi",
    "MO": "missouri",
    "MT": "montana",
    "NE": "nebraska",
    "NV": "nevada",
    "NH": "new hampshire",
    "NJ": "new jersey",
    "NM": "new mexico",
    "NY": "new york",
    "NC": "north carolina",
    "ND": "north dakota",
    "OH": "ohio",
    "OK": "oklahoma",
    "OR": "oregon",
    "PA": "pennsylvania",
    "RI": "rhode island",
    "SC": "south carolina",
    "SD": "south dakota",
    "TN": "tennessee",
    "TX": "texas",
    "UT": "utah",
    "VT": "vermont",
    "VA": "virginia",
    "WA": "washington",
    "WV": "west virginia",
    "WI": "wisconsin",
    "WY": "wyoming",
    "DC": "district of columbia",
}
US_WORDS = _rx(
    r"united states",
    r"\busa\b",
    r"\bu\.s\.a?\.?",
    r"\bus\b",
    r"\bamericas?\b",
    r"\b(" + "|".join(US_STATES.values()) + r")\b",
    # Big US job markets often listed without a state
    r"\b(san francisco|new york|nyc|seattle|boston|austin|chicago|los angeles|bay area"
    r"|silicon valley|palo alto|mountain view|menlo park|sunnyvale|san jose|san diego"
    r"|denver|atlanta|miami|dallas|houston|philadelphia|pittsburgh|washington,? d\.?c)\b",
)
STATE_CODE = re.compile(r"(?:,|\s)\s*(" + "|".join(US_STATES) + r")\b(?!\.)")
NON_US = _rx(
    r"\b(united kingdom|uk|england|scotland|ireland|london|canada|toronto|vancouver|montreal"
    r"|ontario|british columbia|quebec|india|bangalore|bengaluru|hyderabad|pune|mumbai"
    r"|delhi|germany|berlin|munich|france|paris|spain|madrid|barcelona|netherlands|amsterdam"
    r"|poland|warsaw|portugal|lisbon|italy|switzerland|zurich|sweden|stockholm|denmark"
    r"|israel|tel aviv|singapore|japan|tokyo|china|shanghai|beijing|hong kong|taiwan|korea"
    r"|seoul|australia|sydney|melbourne|new zealand|brazil|sao paulo|mexico|argentina"
    r"|colombia|chile|philippines|manila|vietnam|thailand|bangkok|indonesia|malaysia"
    r"|uae|dubai|south africa|nigeria|kenya|egypt|romania|ukraine|czech|prague|hungary"
    r"|greece|austria|vienna|belgium|brussels|finland|norway|estonia|serbia|emea|apac"
    r"|latam|europe|\bnz\b|auckland|saudi|riyadh|lebanon|beirut|pakistan|karachi|lahore"
    r"|cape town|johannesburg|manchester|edinburgh|dublin|international)\b",
)
US_COUNTRY = {"us", "usa", "united states", "united states of america"}


def is_us(location: str | None, country: str | None = None) -> bool | None:
    """US if any listed location is in the US. A structured country field
    (Lever, Ashby) wins over the free-text location."""
    if country:
        return country.strip().lower() in US_COUNTRY
    if not location:
        return None
    parts = re.split(r"\s*(?:\||;|/| or |\band\b)\s*", location)
    seen_non_us = False
    for p in parts:
        # "Toronto, ON, CA" is Canada, so check non-US names before state codes
        if NON_US.search(p) and not re.search(
            r"united states|\busa\b", p, re.IGNORECASE
        ):
            seen_non_us = True
            continue
        if US_WORDS.search(p) or STATE_CODE.search(p):
            return True
    return False if seen_non_us else None


REMOTE = _rx(
    r"\bremote\b", r"\banywhere\b", r"work from home", r"\bwfh\b", r"distributed"
)


def is_remote(location: str | None, workplace: str | bool | None = None) -> bool:
    if isinstance(workplace, bool):
        return workplace
    if isinstance(workplace, str) and workplace.lower() in (
        "remote",
        "onsite",
        "hybrid",
    ):
        return workplace.lower() == "remote"
    return bool(location and REMOTE.search(location))


# Cities that name their metro area, for the site's location filter. Only
# cities that aren't ambiguous on their own (no "Arlington", "Cambridge")
METROS = {
    "Bay Area": (
        "CA",
        (
            "san francisco|south san francisco|san jose|sunnyvale|mountain view|palo alto"
            "|menlo park|redwood city|santa clara|cupertino|oakland|fremont|milpitas|san mateo"
            "|foster city|berkeley|emeryville|san bruno|burlingame|los gatos|campbell|san carlos"
            "|newark, ca|pleasanton|livermore|union city|hayward|belmont"
        ),
    ),
    "New York": (
        "NY",
        "new york|new york city|nyc|brooklyn|manhattan|jersey city|hoboken",
    ),
    "Seattle": ("WA", "seattle|bellevue|redmond|kirkland|bothell"),
    "Los Angeles": (
        "CA",
        (
            "los angeles|santa monica|irvine|pasadena|culver city|el segundo"
            "|long beach|burbank|torrance|playa vista|hawthorne|costa mesa"
        ),
    ),
    "Boston": (
        "MA",
        "boston|waltham|somerville|burlington, ma|cambridge, ma|lexington, ma|woburn",
    ),
    "Austin": ("TX", "austin|round rock"),
    "Chicago": ("IL", "chicago|evanston|naperville"),
    "Washington DC": (
        "DC",
        (
            "washington,? d\\.?c|mclean|reston|herndon|bethesda|tysons|arlington, va"
            "|chantilly|fairfax|alexandria, va|rockville"
        ),
    ),
    "Dallas": ("TX", "dallas|plano|irving|fort worth|richardson|frisco|addison"),
    "Denver": ("CO", "denver|boulder|broomfield|englewood, co"),
    "Atlanta": ("GA", "atlanta|alpharetta"),
    "Houston": ("TX", "houston"),
    "San Diego": ("CA", "san diego|la jolla|carlsbad"),
    "Raleigh-Durham": ("NC", "raleigh|durham|cary|research triangle|morrisville"),
    "Philadelphia": ("PA", "philadelphia"),
    "Phoenix": ("AZ", "phoenix|tempe|chandler|scottsdale"),
    "Salt Lake City": ("UT", "salt lake city|lehi|draper"),
    "Minneapolis": ("MN", "minneapolis|st\\.? paul"),
    "Pittsburgh": ("PA", "pittsburgh"),
    "Miami": ("FL", "miami"),
    "Charlotte": ("NC", "charlotte"),
}
_METRO_RX = {
    m: re.compile(rf"\b({cities})\b", re.IGNORECASE)
    for m, (_, cities) in METROS.items()
}
_STATE_NAME = re.compile(
    r"\b(" + "|".join(sorted(US_STATES.values(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_STATE_CODE_TOKEN = re.compile(
    r"(?<![A-Za-z])(" + "|".join(US_STATES) + r")(?![A-Za-z.])"
)
_NAME_TO_CODE = {v: k for k, v in US_STATES.items()}


def places(location: str | None) -> tuple[list[str], list[str]]:
    """(states, metros) named in a location string, for filtering. Empty when
    the text doesn't say ("Remote", "3 Locations")."""
    if not location:
        return [], []
    metros = [m for m, rx in _METRO_RX.items() if rx.search(location)]
    states = set(_STATE_CODE_TOKEN.findall(location))
    # "New York, NY" names the city, "New York, New York" names both
    for name in _STATE_NAME.findall(location.lower()):
        if name == "washington" and re.search(
            r"washington,? d\.?c", location, re.IGNORECASE
        ):
            continue
        if name == "new york" and "New York" in metros and not states:
            continue
        states.add(_NAME_TO_CODE[name])
    # A bare city ("Seattle") implies its metro's state; a stated one wins
    if not states:
        states = {METROS[m][0] for m in metros}
    return sorted(states), metros


# --- description -----------------------------------------------------------

NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twelve": 12,
    "fifteen": 15,
}
_NUM = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")"
YEARS = re.compile(
    _NUM + r"\s*\+?\s*(?:(?:-|–|to|or)\s*" + _NUM + r"\s*\+?\s*)?(?:\(\d+\)\s*)?"
    r"(?:years?|yrs?)\b(?:\s+of)?",
    re.IGNORECASE,
)
EXPERIENCE_NEAR = re.compile(
    r"experience|background|industry|professional|working|hands-on", re.IGNORECASE
)


def min_years(description: str) -> int | None:
    """Smallest 'N years' that sits in a sentence about experience.
    '3+ years of Python and 1+ year of Go' -> 1."""
    found = []
    for sentence in re.split(r"(?<=[.!?•\n])\s+|\s+[-*•]\s+", description):
        if not EXPERIENCE_NEAR.search(sentence):
            continue
        for m in YEARS.finditer(sentence):
            n = m.group(1).lower()
            n = NUMBER_WORDS.get(n, n)
            n = int(n)
            if n <= 20:  # "30 years in business" is about the company
                found.append(n)
    return min(found) if found else None


NO_SPONSORSHIP = _rx(
    r"(unable|not able|cannot|can ?not|can't|will not|won't|do(es)? not|don't|doesn't|are not"
    r"|is not|not in a position to|no longer)\s+(\w+\s+){0,4}sponsor",
    r"sponsorship\s+(\w+\s+){0,3}(is\s+|are\s+)?(not|unavailable)",
    r"\bno\s+(visa\s+|immigration\s+|h-?1b\s+)?sponsorship",
    r"without\s+(\w+\s+){0,6}sponsorship",
    r"not\s+(\w+\s+){0,3}eligible\s+for\s+(\w+\s+){0,3}sponsorship",
    r"sponsorship(\s+available)?\s*:\s*(no|none|not available)\b",
)
CITIZENS_ONLY = _rx(
    r"(must|required to|need to)\s+be\s+(a\s+)?(u\.?s\.?|united states)\s+citizen",
    r"(u\.?s\.?|united states)\s+citizenship\s+(is\s+)?(required|mandatory|a must)",
    r"(u\.?s\.?|united states)\s+citizens?\s+only",
    r"only\s+(u\.?s\.?|united states)\s+citizens",
    r"must\s+be\s+(a\s+)?(u\.?s\.?|us)\s+person",
    r"(u\.?s\.?|us)\s+persons?\b.{0,80}(itar|export|required|must|only)",
    r"(itar|export control).{0,120}(u\.?s\.?|us)\s+(person|citizen)",
    r"requires?\s+(u\.?s\.?|united states)\s+citizenship",
    # ITAR's list: citizen, permanent resident, refugee, asylee
    r"citizen(ship)?\s*(,|or)?\s*(lawful\s*,?\s*)?(permanent resident|green card)",
    r"(green card|permanent resident)s?(\s+holders?)?\s+only",
    r"permanent resident.{0,80}(refugee|asylee|asylum)",
    r"(citizen|u\.?s\.? persons?)\b.{0,150}\b(itar|export control)",
)
# Government contractors (2026-10-01): treated as citizens-only, since most of
# their roles need citizenship in practice
GOVERNMENT = _rx(
    r"\bfederal\b", r"\bgovernment\b", r"public sector", r"\bgovcloud\b", r"\bdod\b"
)
CLEARANCE = _rx(
    r"security clearance",
    r"\bts/sci\b",
    r"top secret",
    r"secret clearance",
    r"(active|current|obtain|maintain|eligib\w*\s+for)\s+(a\s+)?(dod\s+|government\s+)?clearance",
    r"clearance\s+(is\s+)?required",
    r"\bpolygraph\b",
)


@dataclass(frozen=True)
class Flags:
    no_sponsorship: bool
    citizens_only: bool
    clearance: bool


TITLE_CLEARANCE = _rx(
    r"\bts/sci\b", r"top secret", r"\bsecret\b", r"\bcleared\b", r"clearance"
)


def flags(description: str, title: str = "", company: str = "") -> Flags:
    """US security clearances require US citizenship, so a clearance job is
    also citizens-only. So is work for a government contractor."""
    d = " ".join(description.split())
    clearance = bool(CLEARANCE.search(d) or TITLE_CLEARANCE.search(title))
    citizens = bool(
        CITIZENS_ONLY.search(d) or clearance or GOVERNMENT.search(f"{title} {company}")
    )
    return Flags(
        no_sponsorship=bool(NO_SPONSORSHIP.search(d)),
        citizens_only=citizens,
        clearance=clearance,
    )
