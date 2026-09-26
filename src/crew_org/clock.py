"""The one clock that decides which sprint it is (#193).

The crew picked the current sprint by the machine's local date while GitHub's
iterations are bare dates, and a person reading UTC saw a different sprint:
on 2026-09-26 at 00:28 UTC (19:28 CDT) stories moved into "Sprint 7" by UTC
sat untouched for a tick that was delivering Sprint 6. One clock, named in
org.yaml, answers "what day is it" for every sprint decision.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


def timezone(org: dict) -> str:
    """The timezone sprint dates are read in, as org.yaml names it."""
    return str((org.get("sprint") or {}).get("timezone") or "UTC")


def sprint_today(org: dict) -> date:
    """Today, by the organization's sprint clock."""
    return datetime.now(ZoneInfo(timezone(org))).date()
