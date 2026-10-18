def test_what_setup_installed_or_fetched_is_not_the_project(tmp_path):
    """crew-presentation's `.crew-home` grew to 213 MB of installed packages, and its
    design's view of the repository to a million characters (crew#690)."""
    import subprocess

    from crew_org.tools.repo_context import project_files, repository_context

    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(tmp_path)]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".crew-home/\n.package/\nbuild/\n")
    (tmp_path / "build.py").write_text("print('built')\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "first"], check=True)
    (tmp_path / "new_story.py").write_text("x = 1\n")
    for ignored in (".crew-home/lib/site.py", ".package/data.json", "build/index.html"):
        (tmp_path / ignored).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / ignored).write_text("ignored = True\n")

    names = [p.relative_to(tmp_path).as_posix() for p in project_files(tmp_path)]
    assert names == [".gitignore", "build.py", "new_story.py"]
    assert "site.py" not in repository_context(tmp_path)


def test_without_git_the_known_folders_are_still_skipped(tmp_path):
    from crew_org.tools.repo_context import project_files

    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / ".crew-home" / "b.py").parent.mkdir()
    (tmp_path / ".crew-home" / "b.py").write_text("b = 1\n")
    assert [p.name for p in project_files(tmp_path)] == ["a.py"]
