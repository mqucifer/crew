"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    """No test reads a real container registry (#364): lookups are said to be unreachable."""
    from crew_org.tools import base_images

    def unreachable(host, name, reference):
        raise base_images.Unreadable("no registry in tests")

    monkeypatch.setattr(base_images, "LOOKUP", unreachable)
    base_images._READ.clear()
    yield
    base_images._READ.clear()


@pytest.fixture(autouse=True)
def _criteria_pass(monkeypatch):
    """No test calls the model to check a split's criteria (#428): they all pass.

    Tests of the check itself set their own.
    """
    from crew_org.crews.criteria_crew import CriteriaCheck
    from crew_org.flows import board_flow

    monkeypatch.setattr(board_flow, "check_criteria", lambda **_: CriteriaCheck())
