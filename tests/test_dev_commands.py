"""Local tooling checks use isolated repositories and never create a real commit."""

from pathlib import Path
import subprocess

import pytest

from scripts import dev


@pytest.fixture
def repository(tmp_path: Path, monkeypatch):
    # A hook can export its own index/worktree; isolate Git operations in these tests.
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        monkeypatch.delenv(name, raising=False)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    return tmp_path


def test_staged_filename_guard_distinguishes_examples_from_local_data():
    blocked = [".env", "config/.env.production", ".secrets", "config/.secrets.local",
               ".venv/lib/file.py", "data/chroma/chroma.sqlite3", "data/index_manifest.json"]
    allowed = [".env.example", "config/.env.example", "src/utils/config.py", "data/apple_10k_2025.htm"]
    assert dev.blocked_staged_paths(blocked + allowed) == blocked


def test_staged_secret_blocks_check_before_tests_or_compilation(repository, monkeypatch):
    (repository / ".env").write_text("EXAMPLE_VALUE=placeholder\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env"], cwd=repository, check=True)
    calls = []
    monkeypatch.setattr(dev, "run", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="Unstage local-only files"):
        dev.check(staged=True)
    assert calls == []


def test_hook_install_is_local_and_repeatable(repository):
    (repository / ".githooks").mkdir()
    (repository / ".githooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    dev.install_hooks()
    dev.install_hooks()
    assert dev.git_output("config", "--local", "--get", "core.hooksPath") == ".githooks"


def test_hook_install_preserves_a_different_existing_hook_path(repository):
    subprocess.run(["git", "config", "--local", "core.hooksPath", "existing-hooks"], cwd=repository, check=True)
    with pytest.raises(ValueError, match="Existing core.hooksPath"):
        dev.install_hooks()
    assert dev.git_output("config", "--local", "--get", "core.hooksPath") == "existing-hooks"


def test_hook_install_preserves_active_default_hook(repository):
    hook = repository / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="active default pre-commit hook"):
        dev.install_hooks()
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


def test_offline_command_excludes_live_tests_even_when_opted_in(monkeypatch):
    monkeypatch.setenv("RUN_GEMINI_INTEGRATION", "1")
    calls = []
    monkeypatch.setattr(dev, "run", lambda *args: calls.append(args))
    dev.offline_tests()
    assert calls[0][-2:] == ("-m", "not gemini_integration")


def test_failed_subprocess_produces_a_nonzero_command_exit(monkeypatch, capsys):
    def fail(*args):
        raise subprocess.CalledProcessError(1, ["pytest"])
    monkeypatch.setattr(dev, "run", fail)
    assert dev.main(["test"]) == 1
    assert "Command failed" in capsys.readouterr().err
