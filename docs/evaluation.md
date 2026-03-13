# Evaluation guide

## Running evaluations

```bash
# Quick run — 5 analyst questions (~2-3 min)
uv run python src/evaluate.py

# Full run — 15 questions (analyst + search + hybrid, ~10-15 min)
uv run python src/evaluate.py --full

# Analyst-only — 8 questions (skip Cortex Search / hybrid)
uv run python src/evaluate.py --analyst-only

# With Agent GPA metrics (6 additional scores per question)
uv run python src/evaluate.py --gpa

# Save results and open dashboard
uv run python src/evaluate.py --full --output results.json --dashboard
```

## CLI options

```
uv run python src/evaluate.py [OPTIONS]

  --full              Run all 15 questions (analyst + search + hybrid).
  --analyst-only      Run only the 8 Cortex Analyst questions.
  --gpa               Enable Agent GPA metrics (Plan, Action, G-P-A axes).
  --app-name NAME     TruLens app name in the dashboard
                      (default: cortex-agent-sales-intelligence).
  --app-version VER   TruLens app version in the dashboard (default: 1).
  --feedback-model M  Cortex model for RAG Triad scoring (default: mistral-large2).
  --gpa-model M       Cortex model for GPA scoring (default: mistral-large2).
  --max-workers N     Concurrent agent calls (default: 3).
  --output FILE       JSON results path (default: reports/eval_results_<timestamp>.json).
  --debug             Write GPA debug log to reports/debug_<timestamp>.log.
  --dashboard         Launch TruLens Streamlit dashboard after evaluation.
```

### Feedback model options

| Model | Characteristics |
|---|---|
| `mistral-large2` | Default — good quality/cost balance |
| `llama3.1-70b` | Most thorough, highest cost |
| `llama3.1-8b` | Fastest and cheapest; weaker for GPA metrics |
| `snowflake-arctic` | Snowflake native option |

## Output

### Summary table (stdout)

```
==============================================================================
 EVALUATION SUMMARY
==============================================================================
  App : cortex-agent-sales-intelligence v1
  Model: mistral-large2
  Questions evaluated: 5
------------------------------------------------------------------------------
  Question                                 Answer Relevance  Context Relevance  Groundedness
------------------------------------------------------------------------------
  What is the total deal value for all...              1.00               1.00          0.80
  ...
  AVERAGE                                              0.95               0.98          0.75
==============================================================================
  Results saved → reports/eval_results_20260312_153820.json
```

### JSON report

Each run saves a timestamped JSON file to `reports/` (git-ignored):

```json
{
  "metadata": {
    "app_name": "cortex-agent-sales-intelligence",
    "feedback_model": "mistral-large2",
    "evaluated_at": "2026-03-12T15:38:20",
    "question_count": 5
  },
  "aggregate": {
    "Answer Relevance": 0.95,
    "Context Relevance": 0.98,
    "Groundedness": 0.75
  },
  "results": [
    {
      "index": 1,
      "question": "...",
      "answer": "...",
      "error": null,
      "metrics": {
        "Answer Relevance": 1.0,
        "Context Relevance": 1.0,
        "Groundedness": 0.8
      }
    }
  ]
}
```

### TruLens dashboard (optional)

Pass `--dashboard` to open the Streamlit dashboard at **http://localhost:8501** after evaluation:

- **Leaderboard** — aggregate scores per app across all metrics
- **Records** — per-question breakdown with question, answer, context, and feedback scores with chain-of-thought reasoning

## Test cases

The question bank (`config/test_cases.yaml`) contains **15 questions** in three categories:

| Category | Count | Description |
|---|---|---|
| `analyst` | 8 | Structured data — Cortex Analyst (text-to-SQL) |
| `search` | 4 | Unstructured data — Cortex Search (semantic search) |
| `hybrid` | 3 | Both tools required |

Three prebuilt question sets:

| Set | Size | CLI flag |
|---|---|---|
| `QUICK_EVAL_QUESTIONS` | 5 (`analyst` only) | default |
| `analyst_questions()` | 8 (`analyst` only) | `--analyst-only` |
| `EVALUATION_QUESTIONS` | 15 (all) | `--full` |

### Adding test questions

Edit `config/test_cases.yaml`:

```yaml
- question: "How many deals did Rachel Torres close in Q1 2024?"
  tool_type: analyst       # analyst | search | hybrid
  tags: [sales rep, close date]
  quick_eval: false        # true to include in the 5-question fast subset
```

Guidelines by `tool_type`:

| Type | When to use | Good question characteristics |
|---|---|---|
| `analyst` | Structured sales metrics (counts, sums, averages, rankings) | Specific and measurable — single SQL-answerable answer |
| `search` | Unstructured conversation data (sentiment, themes, objections) | Open-ended and semantic — not answerable by SQL alone |
| `hybrid` | Both a metric and a qualitative insight in one answer | Ask for a number/ranking *and* a theme in the same question |

Tips:
- Be specific — vague questions make Answer Relevance hard to judge
- Avoid yes/no questions — short answers reduce Groundedness signal
- Add `quick_eval: true` to include in the 5-question smoke-test set

## Extending the evaluation

### Custom feedback metrics

Add a new `Metric` to `src/feedback.py` or extend `build_rag_triad_metrics()` in `src/evaluate.py`:

```python
from trulens.core import Metric, Selector
from trulens.feedback import GroundTruth

ground_truth_data = [
    {"query": "What is the total deal value?", "expected_response": "$630,000"}
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

### Custom GPA prompts

Edit `config/feedback.yaml` to tune judge prompts or tool descriptions without touching Python.
The YAML supports these template variables:

| Variable | Available in |
|---|---|
| `{available_tools}` | GPA system prompts |
| `{user_query}` | GPA user prompts |
| `{plan_str}` | GPA user prompts |
| `{trace_str}` | GPA user prompts |
| `{response}` | GPA user prompts (truncated to 400 chars) |
| `{n_steps}` | Execution Efficiency user prompt |
| `{source_text}` | Groundedness user prompt |
| `{statement}` | Groundedness user prompt |

### Storing results in Snowflake

Replace the local SQLite session with a Snowflake-backed session:

```python
from trulens.core import TruSession
from trulens.connectors.snowflake import SnowflakeConnector

connector = SnowflakeConnector(snowpark_session=snowpark_session)
tru = TruSession(connector=connector)
```
