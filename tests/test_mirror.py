"""The jobs mirror: a run reads only what changed since the last run, and
can't drift from the database."""

from datetime import UTC, datetime, timedelta

from filedfor.store import Mirror

NOW = datetime(2026, 10, 11, 22, tzinfo=UTC)


class FakeDB:
    """Just enough of a connection for Mirror: the full read, the changed-
    since read and the row count."""

    def __init__(self, rows):
        self.rows = rows
        self.reads = 0

    def execute(self, sql, params=None):
        db = self

        class Result:
            def fetchall(self):
                if "count(*)" in sql:
                    return [{"n": len(db.rows), "t": NOW}]
                rows = [
                    r for r in db.rows if params is None or r["changed_at"] > params[0]
                ]
                db.reads += len(rows)
                return [dict(r) for r in rows]

            def fetchone(self):
                return self.fetchall()[0]

        return Result()


def job(i, changed, **kw):
    return {
        "system": "lever",
        "slug": "acme",
        "job_id": str(i),
        "title": f"Engineer {i}",
        "is_us": True,
        "no_sponsorship": False,
        "citizens_only": False,
        "clearance": False,
        "posted_at": NOW - timedelta(days=1),
        "first_seen_at": NOW - timedelta(days=1),
        "alerted_at": None,
        "closed_at": None,
        "classifier_version": 7,
        "changed_at": changed,
    } | kw


def test_first_sync_reads_everything_later_ones_only_changes(tmp_path):
    db = FakeDB(
        [job(i, NOW - timedelta(hours=5)) for i in range(100)]
    )  # one shared time
    m = Mirror(tmp_path / "jobs.json.gz")
    assert m.sync(db) == 100
    m.save()
    db.rows[3] = job(3, NOW, closed_at=NOW)  # closed this run
    db.rows.append(job(100, NOW))  # new this run
    again = Mirror(tmp_path / "jobs.json.gz")  # as restored from the cache
    assert again.sync(db) == 2  # only the two changes
    open_keys, closed = again.state()
    assert ("lever", "acme", "3") in closed and ("lever", "acme", "100") in open_keys
    assert again.open_count() == 100


def test_a_deleted_row_forces_a_full_read(tmp_path):
    db = FakeDB([job(i, NOW - timedelta(hours=5 + i)) for i in range(10)])
    m = Mirror(tmp_path / "jobs.json.gz")
    m.sync(db)
    del db.rows[5]  # deleted without the mirror being told
    db.reads = 0
    m.sync(db)
    assert db.reads >= 9  # the counts disagreed, so it read everything again
    assert len(m.rows) == 9 and ("lever", "acme", "5") not in m.rows


def test_listable_and_stale(tmp_path):
    db = FakeDB(
        [
            job(1, NOW),
            job(2, NOW, no_sponsorship=True),
            job(3, NOW, is_us=False),
            job(4, NOW, posted_at=NOW - timedelta(days=40)),
            job(5, NOW, classifier_version=6),
        ]
    )
    m = Mirror(tmp_path / "m.json.gz")
    m.sync(db)
    assert sorted(r["job_id"] for r in m.listable(NOW)) == ["1", "5"]
    assert m.stale(7) == {("lever", "acme", "5"): "Engineer 5"}


def test_a_broken_cache_file_means_a_full_read(tmp_path):
    path = tmp_path / "m.json.gz"
    path.write_bytes(b"not gzip")
    db = FakeDB([job(1, NOW)])
    assert Mirror(path).sync(db) == 1
