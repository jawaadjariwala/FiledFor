"""The shared page layer: URLs, layout and sitemaps. Page modules test
their own content."""

from datetime import UTC, datetime

from filedfor import pages
from filedfor.pages import Context, Page

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


def test_slug_is_stable_and_plain():
    assert pages.slug("Procter & Gamble") == "procter-gamble"
    assert pages.slug("Nestlé USA, Inc.") == "nestle-usa-inc"
    assert pages.slug("1Password") == "1password"
    assert pages.slug("!!!") == "company"


def test_render_escapes_and_links_canonical():
    page = Page(
        path="companies/a-b/",
        title="A & B <jobs>",
        description='say "hi"',
        body="<p>x</p>",
        crumbs=[("Companies", "companies/"), ("A & B", "companies/a-b/")],
    )
    out = pages.render(page, "2026-10-11 12:00")
    assert "<title>A &amp; B &lt;jobs&gt;</title>" in out
    assert '<link rel="canonical" href="https://filedfor.com/companies/a-b/">' in out
    assert '"@type":"BreadcrumbList"' in out
    assert '<a href="/companies/">Companies</a>' in out


def test_json_ld_cannot_close_the_script_tag():
    page = Page(
        path="x/",
        title="t",
        description="d",
        body="",
        jsonld=[{"name": "</script><b>"}],
    )
    assert "</script><b>" not in pages.render(page, "now")


def test_sitemap_index_lists_each_group():
    files = pages.sitemaps(
        [
            Page(
                path="companies/a/",
                title="",
                description="",
                body="",
                group="companies",
            )
        ],
        NOW,
    )
    assert set(files) == {"sitemap.xml", "sitemap-main.xml", "sitemap-companies.xml"}
    assert "https://filedfor.com/companies/a/" in files["sitemap-companies.xml"]
    assert "sitemap-companies.xml" in files["sitemap.xml"]


def test_duplicate_paths_are_refused(monkeypatch):
    import types

    mod = types.SimpleNamespace(
        pages=lambda ctx: [Page(path="a/", title="", description="", body="")]
    )
    monkeypatch.setattr(pages, "MODULES", ("m1", "m2"))
    monkeypatch.setattr(pages.importlib, "import_module", lambda name: mod)
    try:
        pages.build(Context(jobs=[], companies={}, now=NOW, updated=NOW.isoformat()))
    except ValueError as e:
        assert "a/" in str(e)
    else:
        raise AssertionError("expected a ValueError")
