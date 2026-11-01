# Apple Financial Research Agent

Python 3.11 CLI for researching Apple's latest non-amended 10-K with local HTML retrieval, Tavily web search, Gemini function calling, and deterministic financial calculations. The default model is `gemini-3.8-flash`; set `GEMINI_MODEL` if you prefer another Gemini model that supports the Interactions API and function calling.

## Architecture

Coding assistants: start with [AGENTS.md](AGENTS.md). The module and data-flow map is
in [ARCHITECTURE.md](ARCHITECTURE.md); reusable development commands and Git hook setup
are in [docs/COMMANDS.md](docs/COMMANDS.md). Project skills live in `.agents/skills/`.

`src/main.py` provides two separate paths:

- `python src/main.py index` parses `data/apple_10k_2025.htm`, normalizes sections and tables, embeds chunks locally, and writes a persistent Chroma index.
- `python src/main.py` only queries a compatible existing index. It never rebuilds the filing index.

If you update the parser or pull a newer filing, run `python src/main.py index` again before querying; the manifest intentionally rejects stale extracted table metadata.

The manual Gemini Interactions API tool loop calls exactly three tools:

1. `search_documents` for Apple 10-K evidence.
2. `search_web` for current external evidence through Tavily.
3. `calculate_financial_metric` for verified ratios, gross margins, and percentage-point differences.

Evidence records have IDs. Narrative claims must match verbatim excerpts in their
cited evidence; unsupported paraphrases are rejected. Calculation claims link a
specific `calculation_id` and cite exactly its input sources. The CLI renders their
values and formulas from the registry, ignoring the model's calculation prose.
Excerpt matching verifies text presence, not source reliability or completeness of context.

Web calculator facts require an explicit, unambiguous fiscal year in the same sentence
as a gross margin percentage. Publication dates never supply reporting periods.
Ambiguous, quarterly, and cloud-segment metrics remain excerpts without reusable
numeric facts. This is conservative snippet extraction, not a full annual-report
table parser; missing metrics must be disclosed rather than inferred.
Cross-year comparisons are allowed and display an automatic period caveat.

Prompts live in `prompts/` so policy and tool-selection guidance can be changed without changing tool code. Tool JSON schemas remain in Python as the single machine-readable definition.

## Key constraints and code quality

- Missing information is reported explicitly in `limitations`. An entirely unanswered
  question may return no claims; the agent must never fabricate facts or citations.
- Every factual claim must reference evidence registered during that request.
  Document sources display the section name, locator, and printed page number when
  available. Links include a document anchor only when it exists in the HTML.
- Gemini and tool transport failures, HTTP 429, and retryable HTTP failures use up to
  three attempts with bounded backoff. Tool retries occur within their research round.
  The default budget is twelve research rounds plus a final synthesis step. Repeated
  identical calls after success or retry exhaustion are blocked to prevent loops.
  Authentication and invalid requests fail without retrying. Tool failures are returned
  to the agent as structured errors; malformed final responses and unknown citations
  are rejected. Indexing and query errors receive readable CLI messages and logs.
- Agent orchestration stays in `src/agent.py`, retrieval tools in `src/tools/`, and
  parsing, citations, planning, indexing, and calculations in `src/utils/`.
  Logging records failure types without including raw provider exception details.

## Optional features

| Feature | Implementation and limits |
| --- | --- |
| Structured tables | Existing parser expands row/column spans, merges multirow headers, and preserves tables as whole chunks with year/unit/value relationships. Regression tests cover multiple years and grouped headers. |
| Page-level citations | Added extraction of printed SEC page footers and explicit `data-page-number` wrappers. Narrative chunks stay within a page; tables remain whole. Missing page labels fall back to section citations. HTML anchors are preserved where available. |
| Planning step | The CLI prints a numbered, question-aware research approach before calling the agent. It describes retrieval, calculations, and citation checks. |
| Cross-references | Added financial-note sections, resolution of “See Note 5” references and same-document links, and automatic retrieval of linked chunks. Traversal stops after two levels or eight additional chunks and detects cycles. Missing targets can be searched by the agent; external documents are not followed automatically. |

Parser metadata uses version `sec-html-v6`; rebuild an older index with
`python src/main.py index` before querying.

Development skills are installed separately from this application's runtime:
Ponytail, Matt Pocock's stable skills (excluding `in-progress`), and Context7 MCP.
They are available to Codex on the next turn. Context7's MCP server also requires
account authorization through `codex mcp login context7`.

## Setup

1. Install Python **3.11**. The repository pins it in `.python-version` and `pyproject.toml`.
2. Create and activate a virtual environment, then install exact locked dependencies:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`, then add `GEMINI_API_KEY` and `TAVILY_API_KEY`. Do not commit `.env`.
4. Download Apple’s 2025 **non-amended 10-K HTML** from SEC EDGAR and save it as `data/apple_10k_2025.htm`.
5. Build the local index:

   ```powershell
   python src/main.py index
   ```

6. Start the CLI:

   ```powershell
   python src/main.py
   ```

## Required demo queries

```text
What are Apple's top 3 risk factors mentioned in their latest 10-K, and what percentage of total revenue did they spend on R&D?
```

```text
How does Apple's gross margin compare to Microsoft's current gross margin, and what reasons does Apple cite in their 10-K for any margin pressure?
```

The first query must use document retrieval and the calculator only. The second must use Apple filing retrieval, Tavily for Microsoft’s current metric, and disclose non-aligned reporting periods.

### Recorded agent answers (November 1, 2026)

These results come from the supplied CLI transcript. The Apple answer uses the
locally indexed **fiscal 2025** filing; it does not establish that this is the
latest available filing.

**Question 1 — risk factors and R&D: completed.** Its actual
output was:

```text
- The first risk factor listed in Item 1A of Apple's 2025 Form 10-K is: 'The Company’s operations and performance depend significantly on global and regional economic conditions and adverse economic conditions can materially adversely affect the Company’s business, results of operations, financial condition and stock price.' This reflects the filing order and is not an independent ranking. [doc_14233a4e7f]
- The second risk factor listed in Item 1A is: 'The Company’s business can be impacted by political events, trade and other international disputes, geopolitical tensions, conflict, terrorism, natural disasters, public health issues, industrial accidents and other business interruptions.' This reflects the filing order and is not an independent ranking. [doc_14233a4e7f]
- The third risk factor listed in Item 1A is: 'Global markets for the Company’s products and services are highly competitive and subject to rapid technological change, and the Company may be unable to compete effectively in these markets.' This reflects the filing order and is not an independent ranking. [doc_24bc24371c]
- In fiscal year 2025, Apple spent $34,550 million on research and development on total net sales of $416,161 million, which is calculated as 8.30% of total revenue (and reported as 8% of total net sales rounded). [doc_52550b864b] [doc_778c7e0a7d]

Calculations:
- percentage: 8.30 ((34550 / 416161) * 100)

Limitations:
- The selection of the 'top 3' risk factors is based strictly on the first three substantive headings in Item 1A in filing order and does not represent an independent evaluation or ranking of risk severity or likelihood.

Sources:
- [doc_14233a4e7f] Item 1A. Risk Factors — item-1a-risk-factors
- [doc_24bc24371c] Item 1A. Risk Factors — item-1a-risk-factors
- [doc_52550b864b] Item 8. Financial Statements and Supplementary Data — item-8-financial-statements-and-supplementary-data-table-23
- [doc_778c7e0a7d] Item 7. Management�s Discussion and Analysis of Financial Condition and Results of Operations — item-7-management-s-discussion-and-analysis-of-financial-condition-and-results-of-operations-table-19
```

**Question 2 — Apple/Microsoft gross margin: the supplied run failed.** Its actual
output was:

```text
Unable to complete the research request safely: Tool-call limit reached before Gemini returned a final structured response
```

There is no Microsoft comparison answer in that transcript to record as a
successful result. The loop allowed six model rounds and could execute tools in
the sixth round, then stop before returning their results to Gemini. The 503
provider error was retried; the final reported failure was the round limit.
The loop now reserves one additional synthesis request with tool calling disabled,
so it can return supported findings and explicitly list missing evidence. This
does not guarantee that a current Microsoft metric will be found.

**Live retry after the fix (November 1, 2026): not completed.** After rebuilding
the stale index, the agent reached the additional final response request instead
of raising the old round-limit error. The run encountered retried Gemini 503
responses and a rejected calculator input, then the final request failed with:

```text
Unable to complete the research request safely: Server disconnected without sending a response.
```

The bounded final response and citation validation pass offline regression tests,
but a successful live Microsoft comparison remains unverified. No comparison
figures are recorded here because neither live run produced a final answer.

## Tests

Run the deterministic suite with no network or keys:

```powershell
python -m pytest tests -q
```

The real Gemini manual function-call round-trip is opt-in because it needs a valid key and may consume API quota:

```powershell
$env:RUN_GEMINI_INTEGRATION = "1"
python -m pytest -m gemini_integration -q
```

## Known limitations

- HTML is the only supported filing format.
- The CLI researches the configured local filing. It does not automatically download
  or verify the latest SEC filing; identify the fiscal year and refresh the file when needed.
- The model must not supply numeric inputs to the calculator; it can only reference extracted fact IDs or prior calculation IDs.
- The recorded transcript demonstrates the first demo query only. The Microsoft comparison in that transcript failed; offline tests do not establish a successful live comparison.
