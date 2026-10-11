"""Job descriptions as safe, tidy HTML for FiledFor's job pages.

Each job system returns the employer's HTML: Greenhouse escapes it, Workday
nests it in divs and spans, everyone pastes inline styles. clean() keeps the
structure a reader needs (paragraphs, lists, headings, emphasis) and drops
everything else, including every attribute, so nothing from another site can
run or restyle the page.
"""

import html
import re

import nh3

TAGS = {"p", "br", "ul", "ol", "li", "strong", "em", "h3", "h4"}
MAX_CHARS = 40_000  # a few postings paste whole handbooks


def clean(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    s = raw
    if "&lt;" in s and "<" not in s:  # Greenhouse's escaped HTML
        s = html.unescape(s)
    # Map what employers use to the few tags kept, before sanitising: divs
    # become paragraphs, bold and italic become strong and em, page titles
    # become section headings
    s = re.sub(r"<(/?)div\b[^>]*>", r"<\1p>", s, flags=re.IGNORECASE)
    s = re.sub(r"<(/?)b\b[^>]*>", r"<\1strong>", s, flags=re.IGNORECASE)
    s = re.sub(r"<(/?)i\b[^>]*>", r"<\1em>", s, flags=re.IGNORECASE)
    s = re.sub(r"<(/?)h[12]\b[^>]*>", r"<\1h3>", s, flags=re.IGNORECASE)
    s = re.sub(r"<(/?)h[56]\b[^>]*>", r"<\1h4>", s, flags=re.IGNORECASE)
    s = nh3.clean(s, tags=TAGS, attributes={}, link_rel=None)
    s = s.replace("\xa0", " ").replace("&nbsp;", " ")
    # A paragraph that is only bold text is a heading in all but name
    s = re.sub(
        r"<p>\s*<strong>([^<]{2,90}?)</strong>\s*(?:<br>\s*)*</p>",
        r"<h3>\1</h3>",
        s,
    )
    s = re.sub(r"<(h3|h4)>\s*<strong>(.*?)</strong>\s*</\1>", r"<\1>\2</\1>", s)
    empty = re.compile(r"<(p|li|h3|h4|strong|em)>\s*(?:<br>\s*)*</\1>")
    while empty.search(s):  # removing one can empty its parent
        s = empty.sub("", s)
    s = re.sub(r"(?:<br>\s*){3,}", "<br><br>", s)
    s = re.sub(r"[ \t\r\n]+", " ", s).strip()
    if not re.sub(r"<[^>]+>", "", s).strip():
        return None
    if len(s) > MAX_CHARS:
        s = nh3.clean(s[:MAX_CHARS], tags=TAGS, attributes={})  # re-close cut tags
    return s
