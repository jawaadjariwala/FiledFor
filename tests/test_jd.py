"""Employer HTML in, safe and tidy HTML out."""

from filedfor.jd import clean


def test_keeps_structure_and_drops_everything_else():
    raw = (
        '<div style="color:red"><p class="x"><b>About us</b></p>'
        "<p>We <span>build</span> things.<script>alert(1)</script></p>"
        '<ul><li onclick="x()">Python</li></ul><a href="http://evil">click</a>'
        "<img src=x onerror=alert(1)></div>"
    )
    out = clean(raw)
    assert (
        out == "<h3>About us</h3><p>We build things.</p><ul><li>Python</li></ul>click"
    )


def test_greenhouse_escaped_html():
    assert (
        clean("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;")
        == "<p>Hello &amp; welcome</p>"
    )


def test_empty_wrappers_go_and_nothing_left_is_none():
    assert clean("<p><strong> </strong></p><p>Text</p>") == "<p>Text</p>"
    assert clean("<div><p>&nbsp;</p></div>") is None
    assert clean(None) is None


def test_headings_are_normalised():
    assert clean("<h1>Role</h1><h2><strong>Team</strong></h2><h6>x</h6>") == (
        "<h3>Role</h3><h3>Team</h3><h4>x</h4>"
    )
