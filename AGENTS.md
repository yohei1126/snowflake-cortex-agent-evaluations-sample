# Detailed Setup and Usage Guide

## Quick start

```bash
# 1. Install dependencies (Python 3.12+, uses uv)
uv sync

# 2. Configure credentials
cp .env.example .env
# Edit .env with your PAT token, account URL, and Snowflake credentials

# 3. Run the quick evaluation (5 analyst questions, ~2-3 min)
uv run python evaluate.py

# 4. Run the full evaluation (15 questions: analyst + search + hybrid)
uv run python evaluate.py --full

# 5. Run only Cortex Analyst questions (skips Cortex Search / hybrid)
uv run python evaluate.py --analyst-only

# 6. Save results to a custom path and launch the TruLens dashboard
uv run python evaluate.py --full --output results.json --dashboard
```

## CLI options

```
uv run python evaluate.py [OPTIONS]

  --full              Run the full 15-question set (analyst + search + hybrid).
  --analyst-only      Run only Cortex Analyst questions (8 questions).
  --app-name NAME     TruLens app name in the dashboard (default: cortex-agent-sales-intelligence).
  --app-version VER   TruLens app version in the dashboard (default: 1).
  --feedback-model M  Cortex model for scoring (default: mistral-large2).
  --max-workers N     Concurrent agent calls (default: 3).
  --output FILE       JSON results path (default: reports/eval_results_<timestamp>.json).
  --dashboard         Launch the TruLens Streamlit dashboard after evaluation (default: off).
```

## Output

After each run the script prints a **rich summary table** to stdout, then saves a **JSON report** to `reports/`:

```
==============================================================================
 EVALUATION SUMMARY
==============================================================================
  App : cortex-agent-sales-intelligence v1
  Model: mistral-large2
  Questions evaluated: 5
------------------------------------------------------------------------------
  Question                                       Answer Relevance  Context Relevance  Groundedness
------------------------------------------------------------------------------
  What is the total deal value for all...                    1.00               1.00          0.80
  ...
  AVERAGE                                                    0.95               0.98          0.75
==============================================================================
  Results saved → reports/eval_results_20260228_013848.json
```

The JSON file contains metadata, per-question answers and metric scores, and aggregate averages.
Optionally, launch `--dashboard` to explore results interactively at **http://localhost:8501**.

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Python 3.12+
- A Snowflake account with the Sales Intelligence Agent already deployed
  (follow the [quickstart guide](https://quickstarts.snowflake.com/guide/getting_started_with_cortex_agents/index.html)
  in `sfguide-getting-started-with-cortex-agents/` first)
- Access to Snowflake Cortex LLMs (e.g. `mistral-large2`) for feedback evaluation

---

## 1. Install dependencies

```bash
uv sync
```

uv reads `pyproject.toml`, creates an isolated virtual environment under `.venv/`,
and installs all pinned dependencies — no manual `python -m venv` or `pip install` needed.

The key packages installed are:

| Package | Purpose |
|---|---|
| `trulens-core` | TruLens evaluation session, `TruApp`, `Metric`, `Selector` API |
| `trulens-providers-cortex` | Snowflake Cortex LLM as the feedback evaluator |
| `trulens-dashboard` | Local Streamlit dashboard for browsing results |
| `snowflake-snowpark-python` | Snowpark session used by the Cortex feedback provider |
| `requests` / `sseclient-py` | Cortex Agent REST API client (SSE streaming) |
| `cryptography` | RSA private-key auth support |
| `python-dotenv` | `.env` file loading |
| `numpy` | Metric aggregation (mean of context relevance per question) |

---

## 2. Set up environment variables

Copy the template and fill in your values:

```bash
cp .env.example .env
```

### Agent API credentials (PAT-based)

The Cortex Agent REST API uses a **Programmatic Access Token (PAT)**. Generate one in Snowsight:

> Profile (bottom-left) → Settings → Authentication → Programmatic access tokens → Generate new token  
> Select `Single Role` and pick `sales_intelligence_role`.

```bash
CORTEX_AGENT_DEMO_PAT=<your-pat-token>
CORTEX_AGENT_DEMO_HOST=<account>.snowflakecomputing.com
CORTEX_AGENT_DEMO_DATABASE=SNOWFLAKE_INTELLIGENCE   # default
CORTEX_AGENT_DEMO_SCHEMA=AGENTS                    # default
CORTEX_AGENT_DEMO_AGENT=SALES_INTELLIGENCE_AGENT   # default
```

### Snowpark session credentials (for TruLens feedback)

TruLens calls Cortex LLMs via a Snowpark session. Three authentication methods
are supported via `SNOWFLAKE_AUTHENTICATOR` (PAT is not supported by Snowpark).

**Option A — SSO / Okta (recommended):**

Sets `SNOWFLAKE_AUTHENTICATOR=externalbrowser`. A browser window opens to
complete Okta login; no password or key file is needed.

```bash
SNOWFLAKE_ACCOUNT=<account-identifier>   # e.g. abc12345 or orgname-accountname
SNOWFLAKE_USER=<username>
SNOWFLAKE_AUTHENTICATOR=externalbrowser
SNOWFLAKE_ROLE=sales_intelligence_role
SNOWFLAKE_WAREHOUSE=SALES_INTELLIGENCE_WH
SNOWFLAKE_DATABASE=SALES_INTELLIGENCE
SNOWFLAKE_SCHEMA=DATA
```

**Option B — password:**
```bash
SNOWFLAKE_ACCOUNT=<account-identifier>
SNOWFLAKE_USER=<username>
SNOWFLAKE_AUTHENTICATOR=snowflake
SNOWFLAKE_PASSWORD=<password>
SNOWFLAKE_ROLE=sales_intelligence_role
SNOWFLAKE_WAREHOUSE=SALES_INTELLIGENCE_WH
SNOWFLAKE_DATABASE=SALES_INTELLIGENCE
SNOWFLAKE_SCHEMA=DATA
```

**Option C — private key (RSA key pair):**
```bash
SNOWFLAKE_ACCOUNT=<account-identifier>
SNOWFLAKE_USER=<username>
SNOWFLAKE_AUTHENTICATOR=snowflake
SNOWFLAKE_PRIVATE_KEY_PATH=/path/to/rsa_key.p8
SNOWFLAKE_ROLE=sales_intelligence_role
SNOWFLAKE_WAREHOUSE=SALES_INTELLIGENCE_WH
SNOWFLAKE_DATABASE=SALES_INTELLIGENCE
SNOWFLAKE_SCHEMA=DATA
```

**Optional — explicit host (SSL hostname fix):**

If your Snowflake account identifier contains an underscore (e.g. `org-name_account`),
set `SNOWFLAKE_HOST` explicitly. If not set, `CORTEX_AGENT_DEMO_HOST` is used as a fallback.

```bash
SNOWFLAKE_HOST=<account>.snowflakecomputing.com
```

---

## 3. Run the evaluation

### Quick run (5 Cortex Analyst questions, ~2-3 min)

```bash
uv run python evaluate.py
```

### Full run (15 questions: analyst + search + hybrid)

```bash
uv run python evaluate.py --full
```

### Analyst-only run (8 Cortex Analyst questions, no Cortex Search)

```bash
uv run python evaluate.py --analyst-only
```

### All CLI options

```
uv run python evaluate.py [OPTIONS]

  --full              Use the full 15-question set (analyst + search + hybrid).
  --analyst-only      Use only the 8 Cortex Analyst questions.
  --app-name NAME     TruLens app name shown in the dashboard
                      (default: cortex-agent-sales-intelligence).
  --app-version VER   TruLens app version shown in the dashboard (default: 1).
  --feedback-model M  Snowflake Cortex model used to score feedback
                      (default: mistral-large2).
                      Other options: llama3.1-70b, snowflake-arctic, llama3.1-8b.
  --max-workers N     Number of concurrent agent calls (default: 3).
  --output FILE       Path for the JSON results file
                      (default: reports/eval_results_<timestamp>.json).
  --dashboard         Launch the TruLens Streamlit dashboard after evaluation
                      (default: off).
```

### Examples

```bash
# Full run, no dashboard (CI/batch use)
uv run python evaluate.py --full

# Run with dashboard open at http://localhost:8501
uv run python evaluate.py --full --dashboard

# Compare a different feedback model
uv run python evaluate.py --full --feedback-model llama3.1-70b --app-name cortex-agent-llama

# Save results to a custom file
uv run python evaluate.py --full --output my_results.json

# Analyst questions only (useful when Cortex Search is not yet set up)
uv run python evaluate.py --analyst-only

# Run programmatically from another script
from evaluate import run_evaluation
run_evaluation(questions=["What is the total deal value?"], launch_dashboard=False)
```

---

## 4. Browse results

### Summary table (stdout)

After every run the script prints a human-readable summary table to stdout:

```
==============================================================================
 EVALUATION SUMMARY
==============================================================================
  App : cortex-agent-sales-intelligence v1
  Model: mistral-large2
  Questions evaluated: 5
------------------------------------------------------------------------------
  Question                                       Answer Relevance  Context Relevance  Groundedness
------------------------------------------------------------------------------
  What is the total deal value for all...                    1.00               1.00          0.80
  ...
  AVERAGE                                                    0.95               0.98          0.75
==============================================================================
  Results saved → reports/eval_results_20260228_013848.json
```

### JSON report (`reports/`)

Each run saves a timestamped JSON file to `reports/` (git-ignored) with:

```json
{
  "metadata": { "app_name": "...", "feedback_model": "...", "evaluated_at": "...", "question_count": 5 },
  "aggregate": { "Answer Relevance": 0.95, "Context Relevance": 0.98, "Groundedness": 0.75 },
  "results": [
    {
      "index": 1,
      "question": "...",
      "answer": "...",
      "error": null,
      "metrics": { "Answer Relevance": 1.0, "Context Relevance": 1.0, "Groundedness": 0.8 }
    }
  ]
}
```

### TruLens dashboard (optional)

When `--dashboard` is set, the script opens the TruLens Streamlit dashboard at **http://localhost:8501** after evaluation. It shows:

- **Leaderboard** — aggregate scores per app across all three RAG metrics
- **Records** — per-question breakdown with the question, answer, retrieved context,
  and individual feedback scores with chain-of-thought reasoning

---

## 5. How the evaluation works

### Architecture

```
CortexAgentApp (TruApp wrapper)
├── retrieve(question) → list[str]    # @instrument — context from tool results
└── query(question)    → str          # @instrument — calls retrieve(), returns answer
        │
        └── CortexAgentClient.run(question)
                │
                └── POST /api/v2/.../agents/SALES_INTELLIGENCE_AGENT:run  (SSE stream)
                        ├── response.text.delta   → answer text
                        └── response.tool_result  → context chunks
                                ├── cortex_analyst_text_to_sql  → SQL query + result rows
                                └── cortex_search               → conversation passages
```

### Why two `@instrument` methods?

TruLens needs to observe both the **context** (what the agent retrieved) and the
**answer** (what the agent said) in a single traced record. Because both come from
the same streaming API call, the result is cached internally (`_cache` dict), so the
API is called only once per question.

### Feedback metrics (TruLens 2.x OTEL API)

All three RAG Triad metrics are built using `Metric` + `Selector` (TruLens 2.x OTEL-style):

| Metric | Provider method | Notes |
|---|---|---|
| **Answer Relevance** | `relevance_with_cot_reasons` | Scores input vs output of `query()` |
| **Context Relevance** | `context_relevance_with_cot_reasons` | Averaged across all context chunks |
| **Groundedness** | `groundedness_measure_with_cot_reasons` | Extra instructions prevent false negatives on numeric/monetary claims |

### Feedback model selection

`mistral-large2` is the default — good balance of quality and cost within Snowflake Cortex.  
For faster/cheaper evaluation during development, use `llama3.1-8b`.  
For the most thorough evaluation, use `llama3.1-70b`.

### SSL bypass for underscore hostnames

Snowflake account identifiers containing underscores (e.g. `org-name_account.snowflakecomputing.com`)
are not valid DNS labels and have no matching SSL certificate. The evaluation script
patches `requests.Session.send` globally to disable SSL verification. This matches
the same workaround applied in `agent_client.py`.

### Retry logic

`CortexAgentClient.run()` automatically retries up to 5 times with exponential back-off
(starting at 15 s) on transient Snowflake service-warmup errors (code `399113` /
`"not yet loaded"`). This is common when the Cortex Agent service is cold-starting.

---

## 6. Extending the evaluation

### Adding more test questions

Edit `test_cases.py`. Add a `TestCase` to `EVALUATION_QUESTIONS`:

```python
TestCase(
    question="How many deals did Rachel Torres close in Q1 2024?",
    tool_type="analyst",       # "analyst" | "search" | "hybrid"
    tags=["sales rep", "close date"],
)
```

Add to `QUICK_EVAL_QUESTIONS` (plain string list) if you want it in the 5-question fast subset.

### Adding custom feedback functions

In `evaluate.py`, extend `_build_feedback_metrics()`:

```python
from trulens.core import Metric, Selector
from trulens.feedback import GroundTruth

# Example: exact-match ground truth for known questions
ground_truth_data = [
    {"query": "What is the total deal value for all closed deals?", "expected_response": "$630,000"}
]
f_ground_truth = Metric(
    implementation=GroundTruth(ground_truth_data).agreement_measure,
    name="Ground Truth Match",
    selectors={
        "prompt": Selector.select_record_input(),
        "response": Selector.select_record_output(),
    },
)
```

### Storing results in Snowflake (instead of local SQLite)

Replace the local `TruSession()` with a Snowflake-backed session:

```python
from trulens.core import TruSession
from trulens.connectors.snowflake import SnowflakeConnector

connector = SnowflakeConnector(snowpark_session=snowpark_session)
tru = TruSession(connector=connector)
```

---

## 7. File reference

| File | Description |
|---|---|
| `evaluate.py` | Main evaluation script. Entry point, TruLens setup, feedback metrics, summary table, JSON output |
| `agent_client.py` | `CortexAgentClient` — POST to Agent REST API, parse SSE stream, extract context chunks |
| `test_cases.py` | `TestCase` dataclass + `EVALUATION_QUESTIONS` (15), `QUICK_EVAL_QUESTIONS` (5), `analyst_questions()` |
| `pyproject.toml` | Dependencies and project metadata (Python ≥ 3.12) |
| `.env.example` | Template for all required environment variables |
| `reports/` | Auto-created; holds `trulens.sqlite` and timestamped JSON result files (git-ignored) |
| `sfguide-getting-started-with-cortex-agents/` | Git submodule: target agent SQL, semantic model YAML, Streamlit demo |
