"""Decide what changed since the last run. Pure functions, no I/O.

Rules from ADR-002:
- A job is new when it's a candidate now and wasn't open in the database.
- A job is closed only when its board was fetched successfully and the job
  is gone. A board that failed keeps all its jobs open.
- The first time a board is seen, its jobs are recorded without alerts, or
  the first run would send thousands of notifications.
- A job that closed and came back is reopened quietly, not alerted again.
- Alerts only go out for jobs posted in the last 72 hours, in case a rule
  change turns an old job into a candidate.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

ALERT_WINDOW = timedelta(hours=72)

Key = tuple[str, str, str]  # (system, slug, job id)
Board = tuple[str, str]  # (system, slug)


@dataclass
class Changes:
    new: set[Key]
    closed: set[Key]
    silent: set[Key]  # new, but recorded without alerting


def diff(
    open_in_db: set[Key],
    candidates_now: set[Key],
    ok_boards: set[Board],
    known_boards: set[Board],
    closed_in_db: frozenset[Key] | set[Key] = frozenset(),
) -> Changes:
    """open_in_db: candidate jobs the database still has open.
    candidates_now: candidate jobs fetched in this run.
    ok_boards: boards fetched successfully in this run.
    known_boards: boards that have been fetched successfully before.
    closed_in_db: jobs the database has seen and marked closed."""
    new = candidates_now - open_in_db
    closed = {k for k in open_in_db - candidates_now if k[:2] in ok_boards}
    silent = {k for k in new if k[:2] not in known_boards or k in closed_in_db}
    return Changes(new=new, closed=closed, silent=silent)


def should_alert(posted_at: datetime | None, now: datetime) -> bool:
    """Jobs with no posting date are alerted: we only just saw them."""
    return posted_at is None or now - posted_at <= ALERT_WINDOW
