# Development commands

Run from the `AI Agent` repository with Python 3.11 and installed requirements.
Activate `.venv`, or replace `python` below with `.\.venv\Scripts\python.exe`
on Windows / `./.venv/bin/python` on Unix. The runner uses that interpreter for subprocesses.

| Command | What it does |
| --- | --- |
| `python scripts/dev.py check` | Compile project Python, reject staged local-secret/generated filenames, check staged and working-tree whitespace, and run offline tests |
| `python scripts/dev.py test` | Run tests excluding `gemini_integration`, even if live-test environment variables are set |
| `python scripts/dev.py index` | Build or validate the configured local filing index |
| `python scripts/dev.py chat` | Start the research CLI using an existing compatible index |
| `python scripts/dev.py install-hooks` | Set this repository's `core.hooksPath` to `.githooks` |
| `python scripts/dev.py --help` | List commands |

`index` requires the filing and cached embedding model. `chat` can call Gemini and
Tavily and may consume quota. `check` and `test` do not call those providers or load `.env`.

Tests allocate a fresh `apple-research-tests-*` directory directly under the system
temporary folder for each run, bypassing existing `pytest-of-*` folders and shared
project directories. An explicit `--basetemp` still takes precedence. The optional
pytest cache is disabled. Temporary results remain available for debugging and can
be removed later from the system temporary folder.

## Git hook

The tracked `.githooks/pre-commit` calls `scripts/dev.py check --staged` using `.venv`.
It runs before a commit, checks staged whitespace, and rejects staged `.env` files
(except `.env.example`), `.secrets` files, and generated local environments/indexes.
This is a filename guard, not a complete secret scanner.

Compilation and tests use the working tree, including unstaged edits. When partially
staging, review `git diff --cached` so you know which version is being committed.
The hook does not modify or stage files and does not run live tests. A failure blocks
the commit; fix it and retry. Keep normal hooks enabled.

Hooks require setup once per clone; install the Python requirements and run
`python scripts/dev.py install-hooks`. The installer preserves an existing different
hook configuration or active default pre-commit hook by refusing to replace it.
Uninstall this setup with `git config --local --unset core.hooksPath`.

## Coding-assistant requests

Open this repository as the coding workspace so the assistant loads `AGENTS.md` and
discovers `.agents/skills/`. These are development skills; the application's Gemini
agent reads `prompts/` instead. Project skills are available in a new assistant session,
and `AGENTS.md` links them for environments that need explicit loading.

Examples:

```text
Use $apple-agent-workflow to improve handling of unavailable evidence.
Use $apple-filing-pipeline to fix citations for a financial table.
Run python scripts/dev.py check and fix any failures.
```

For a new backend responsibility or user-visible research flow, update its skill and
the architecture map with the code. Reuse the two existing skills when the change fits.

## Live verification

After authorization to use provider quota, activate the environment and set the key
without printing it, then opt in explicitly:

```powershell
$env:RUN_GEMINI_INTEGRATION = "1"
python -m pytest -m gemini_integration -q
```

That test reads environment variables directly; unlike the CLI it does not load `.env`.
Report the exact checks run and distinguish a provider contract test from an end-to-end
answer to a demo query.
