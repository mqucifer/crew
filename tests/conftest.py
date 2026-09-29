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
