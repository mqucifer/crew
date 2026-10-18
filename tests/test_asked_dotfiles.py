"""A file asked for by its path is found, a dotfile's leading dot kept (#397)."""

from __future__ import annotations


def test_asking_for_a_dotfile_keeps_its_leading_dot(tmp_path):
    """`lstrip("./")` turned `.github/workflows/tests.yml` into a path that doesn't exist."""
    from crew_org.tools.repo_context import select_files

    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/tests.yml").write_text("name: tests\n")
    chosen, unknown = select_files(tmp_path, "", extra=[".github/workflows/tests.yml"])
    assert ".github/workflows/tests.yml" in chosen and unknown == []
    chosen, unknown = select_files(tmp_path, "", extra=["./.github/workflows/tests.yml"])
    assert ".github/workflows/tests.yml" in chosen and unknown == []
