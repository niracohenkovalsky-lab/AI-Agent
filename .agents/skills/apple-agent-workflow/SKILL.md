---
name: apple-agent-workflow
description: Maintain this project's Gemini tool loop, CLI research flow, prompts, planning, configuration, and provider error handling. Use when changing runtime agent behavior or its contract tests.
---

# Maintain the research agent

Read `ARCHITECTURE.md` for the request lifecycle and `AGENTS.md` for shared contracts.
Keep coding-assistant instructions here separate from the runtime policy in `prompts/`.

## Implementation

Inspect the affected prompt, Python tool declaration, handler, and final-response
validator together. Preserve interaction IDs, function call IDs, bounded retries,
and delivery of the last tool results to final synthesis. Keep the supported tool
names synchronized with CLI wiring and prompts.

An unavailable result may have no claims and a nonempty limitations list. Supported
claims still need current-run evidence IDs. A tool error is not evidence; a malformed
model response must become a readable failure rather than an uncited answer.
Narrative claims must match cited excerpts; calculation claims must link registry
calculations and their exact input evidence. Test rendered registry values against
contradictory model prose, and test automatic period caveats. Keep tool retries inside
the round budget and block duplicate calls only after success or retry exhaustion.

Use fake clients to test failures and tool sequences in `tests/test_queries.py` and
`tests/test_constraints.py`. When changing retries, cover a transient failure, exhausted
retries, and a permanent authentication/request failure. Keep raw provider details out
of logs because those exceptions may contain credentials.

Provider API changes need current official documentation or Context7 when available.
Confirm schemas against the installed SDK; avoid guessing parameter names or model IDs.

## Completion

Run `python scripts/dev.py check`. Update the README's behavior description when needed,
preserving recorded transcripts as historical results. Report live verification only
when it was actually run with authorization. Offline fake-provider tests establish
local contracts, not successful live answers or current provider availability.
