# Apple Financial Research Agent

Python 3.11 CLI for researching Apple's latest non-amended 10-K with local HTML retrieval, Tavily web search, Gemini function calling, and deterministic financial calculations. The default model is `gemini-3.8-flash`; set `GEMINI_MODEL` if you prefer another Gemini model that supports the Interactions API and function calling.

## Architecture

`src/main.py` provides two separate paths:

- `python src/main.py index` parses `data/apple_10k_2025.htm`, normalizes sections and tables, embeds chunks locally, and writes a persistent Chroma index.
- `python src/main.py` only queries a compatible existing index. It never rebuilds the filing index.

If you update the parser or pull a newer filing, run `python src/main.py index` again before querying; the manifest intentionally rejects stale extracted table metadata.

The manual Gemini Interactions API tool loop calls exactly three tools:

1. `search_documents` for Apple 10-K evidence.
2. `search_web` for current external evidence through Tavily.
3. `calculate_financial_metric` for verified ratios, gross margins, and percentage-point differences.

Evidence records have IDs. The model returns structured claims referencing those IDs; the CLI validates each ID against the current execution before rendering Sources. This proves reference integrity, not semantic entailment, so the agent also restricts calculations to numeric facts extracted from returned table evidence.

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

## Required demo queries

```text
What are Apple's top 3 risk factors mentioned in their latest 10-K, and what percentage of total revenue did they spend on R&D?
```

```text
How does Apple's gross margin compare to Microsoft's current gross margin, and what reasons does Apple cite in their 10-K for any margin pressure?
```

The first query must use document retrieval and the calculator only. The second must use Apple filing retrieval, Tavily for Microsoft’s current metric, and disclose non-aligned reporting periods.

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
- The model must not supply numeric inputs to the calculator; it can only reference extracted fact IDs or prior calculation IDs.
- Live end-to-end outputs must be captured after a real filing, Gemini key, and Tavily key are supplied. Do not treat this README as evidence that the two live queries have already passed.
