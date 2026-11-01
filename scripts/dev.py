"""Project commands shared by developers, coding assistants, and Git hooks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str) -> None:
    subprocess.run(arguments, cwd=ROOT, check=True)


def git_output(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=ROOT, encoding="utf-8"
    ).rstrip("\r\n")


def blocked_staged_paths(paths: list[str]) -> list[str]:
    """Guard local secret filenames and generated artifacts without reading their contents."""
    blocked = []
    for name in paths:
        path = PurePosixPath(name.lower())
        secret = (path.name == ".env" or path.name.startswith(".env.")) and path.name != ".env.example"
        secret = secret or path.name.startswith(".secrets")
        generated = any(part in {".venv", ".python", ".uv-cache", "__pycache__", ".pytest_cache", ".pytest_tmp"}
                        for part in path.parts)
        generated = generated or path.as_posix().startswith("data/chroma/") or path.as_posix() == "data/index_manifest.json"
        if secret or generated:
            blocked.append(name)
    return blocked


def offline_tests() -> None:
    run(sys.executable, "-m", "pytest", "tests", "-q", "-m", "not gemini_integration")


def check(staged: bool = False) -> None:
    names = git_output("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    blocked = blocked_staged_paths([name for name in names.split("\0") if name])
    if blocked:
        raise ValueError("Unstage local-only files before committing:\n" + "\n".join(blocked))
    run(sys.executable, "-m", "compileall", "-q", "src", "scripts", "tests")
    run("git", "diff", "--cached", "--check")
    if not staged:
        run("git", "diff", "--check")
    offline_tests()


def install_hooks() -> None:
    configured = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if configured.returncode not in {0, 1}:
        raise RuntimeError("Cannot read this repository's Git hook configuration")
    existing = configured.stdout.strip()
    if existing and existing != ".githooks":
        raise ValueError(f"Existing core.hooksPath={existing}; integrate its hooks before switching")
    default_hook = Path(git_output("rev-parse", "--git-path", "hooks/pre-commit"))
    if not default_hook.is_absolute():
        default_hook = ROOT / default_hook
    if not existing and default_hook.exists():
        raise ValueError("An active default pre-commit hook exists; integrate it before switching")
    hook = ROOT / ".githooks" / "pre-commit"
    if not hook.is_file():
        raise FileNotFoundError("Missing .githooks/pre-commit")
    if os.name != "nt":
        hook.chmod(hook.stat().st_mode | 0o111)
    run("git", "config", "--local", "core.hooksPath", ".githooks")
    print("Enabled .githooks/pre-commit for this repository.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="Run offline checks and tests")
    check_parser.add_argument("--staged", action="store_true", help="Check staged whitespace only; tests use the working tree")
    commands.add_parser("test", help="Run tests with live integration excluded")
    commands.add_parser("index", help="Build or validate the local filing index")
    commands.add_parser("chat", help="Start the research CLI (uses provider APIs)")
    commands.add_parser("install-hooks", help="Enable this repository's pre-commit hook")
    arguments = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        parser.error("Use the project's Python 3.11 interpreter")
    try:
        if arguments.command == "check":
            check(arguments.staged)
        elif arguments.command == "test":
            offline_tests()
        elif arguments.command == "install-hooks":
            install_hooks()
        else:
            run(sys.executable, "src/main.py", *(["index"] if arguments.command == "index" else []))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Command failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
