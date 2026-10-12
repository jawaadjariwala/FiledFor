import pytest

from filedfor import pages


@pytest.fixture(autouse=True)
def no_record_only_filers(tmp_path, monkeypatch):
    """Tests build their own watchlist; the real data/filers.csv would add
    1,000+ record-only companies to it. Tests that want filers point FILERS
    at their own file."""
    monkeypatch.setattr(pages, "FILERS", tmp_path / "no-filers.csv")
