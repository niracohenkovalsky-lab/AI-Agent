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
cited evidence; claims that fail this check are omitted with an explicit limitation,
preserving supported findings. Unknown citations still fail validation.
Registered fact IDs used as citations resolve to their parent evidence records;
invented fact IDs still fail. Calculation claims link a
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


### Recorded agent answers (November 1, 2026)

These results come from the supplied CLI transcript. The Apple answer uses the
locally indexed **fiscal 2025** filing; it does not establish that this is the
latest available filing.

**Question 1 — What are Apple's top 3 risk factors mentioned in their latest 10-K, and what percentage of total revenue did they spend on R&D?** Its actual
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

**Question 2 — How does Apple's gross margin compare to Microsoft's current gross margin, and what reasons does Apple cite in their 10-K for any margin pressure?** Its actual
output was:

```text
Unable to complete the research request safely: Tool-call limit reached before Gemini returned a final structured response
```

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
