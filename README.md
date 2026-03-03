# snowflake-cortex-agent-evaluations-sample

Evaluate a [Snowflake Cortex Agent](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents) that uses both **Cortex Analyst** (text-to-SQL) and **Cortex Search** (semantic search) with the [TruLens](https://www.trulens.org/) RAG Triad framework.

> [!NOTE]
> This is a community sample that evaluates a Cortex Agent backed by **both Cortex Analyst** (structured text-to-SQL)
> and **Cortex Search** (unstructured semantic search) using the TruLens RAG Triad.
> For the official Snowflake evaluation guide, see:
> [Getting Started with Cortex Agent Evaluations](https://www.snowflake.com/en/developers/guides/getting-started-with-cortex-agent-evaluations/)

## Background / Motivation

Snowflake provides built-in [AI Observability](https://docs.snowflake.com/en/user-guide/snowflake-cortex/ai-observability) for evaluating Cortex-powered applications.
However, as of now **AI Observability covers Cortex Search but does not support Cortex Analyst** (text-to-SQL).

To fill that gap, Snowflake recommends using **[TruLens](https://www.trulens.org/)** — an open-source evaluation library originally developed by a startup that Snowflake acquired — to write custom evaluation scripts for Cortex Analyst-backed agents.

This repository is a sample implementation of that recommended approach.

## What this does

The target is a **Cortex Agent** ([Sales Intelligence Agent](https://www.snowflake.com/en/developers/guides/getting-started-with-snowflake-intelligence/)) that answers questions about:
- **Structured data** — sales metrics (revenue, win rate, deal stages) via Cortex Analyst (text-to-SQL)
- **Unstructured data** — sales conversation transcripts via Cortex Search (semantic search)

This project wraps the agent in a TruLens evaluation harness that automatically scores every response on three dimensions:

| Metric | Question it answers |
|---|---|
| **Answer Relevance** | Is the agent's response on-topic for the question? |
| **Context Relevance** | Are the retrieved tool results relevant to the question? |
| **Groundedness** | Is the answer supported by the retrieved context? |

Feedback is computed by Snowflake Cortex LLMs — no external API key needed.

## Repository layout

```
agent_client.py    — Cortex Agent REST API client (SSE streaming, context extraction)
evaluate.py        — TruLens evaluation script (RAG Triad feedback, summary table, JSON output)
test_cases.py      — Evaluation question bank (analyst / search / hybrid queries; 15 total)
pyproject.toml     — Python project & dependencies (managed by uv)
.env.example       — Required environment variables template
reports/           — JSON result files and TruLens SQLite DB (git-ignored)

sfguide-getting-started-with-cortex-agents/   — Target agent (git submodule)
  data_agent_demo.py          — Original Streamlit demo
  create_agent.json           — Agent definition (tools + instructions)
  sales_metrics_model.yaml    — Cortex Analyst semantic model
  setup.sql / course_setup.sql
```

## Evaluation approach: LLM-as-a-Judge

This project uses the **LLM-as-a-Judge** pattern — a separate LLM (the "judge") scores the agent's outputs rather than relying on hand-crafted rules or human annotation.

### How it works

```
User question
      │
      ▼
Cortex Agent ──► answer text  ─────────────────────────────┐
      │                                                     │
      └──► tool results (SQL rows / search passages)        │
                    │ (= retrieved context)                 │
                    │                                       │
                    ▼                                       ▼
            ┌─────────────────────────────────────────────────┐
            │          Judge LLM (mistral-large2)             │
            │   TruLens Cortex provider calls Snowflake LLM   │
            ├─────────────────────────────────────────────────┤
            │  Answer Relevance   → score 0–1 + reasoning     │
            │  Context Relevance  → score 0–1 + reasoning     │
            │  Groundedness       → score 0–1 + reasoning     │
            └─────────────────────────────────────────────────┘
```

### Judge LLM

| Item | Detail |
|---|---|
| **Provider** | `trulens-providers-cortex` — calls Snowflake Cortex LLMs internally |
| **Default model** | `mistral-large2` (good quality/cost balance) |
| **Alternatives** | `llama3.1-70b` (most thorough), `snowflake-arctic`, `llama3.1-8b` (fastest/cheapest) |
| **No external key needed** | The judge runs entirely inside your Snowflake account |

Switch the judge model with `--feedback-model <model>`.

### Metrics and scoring

All three metrics use **`_with_cot_reasons`** variants — the judge LLM produces a numeric score **plus a chain-of-thought explanation** of why it gave that score, making results interpretable.

| Metric | What the judge evaluates | Score |
|---|---|---|
| **Answer Relevance** | Does the agent's answer address the question? | 0 (off-topic) → 1 (fully relevant) |
| **Context Relevance** | Is each retrieved chunk (SQL row / search passage) relevant to the question? Averaged across all chunks. | 0 → 1 per chunk, then mean |
| **Groundedness** | Is every factual claim in the answer supported by the retrieved context? | 0 (hallucinated) → 1 (fully grounded) |

### Groundedness: extra instruction for numeric claims

A known failure mode of LLM judges is treating numbers, monetary values, and dates as "trivial" and skipping them.
This repo passes an **additional instruction** to the judge to prevent that:

> *"Do NOT classify numeric values, monetary amounts, percentages, dates, or proper nouns as trivial statements. These are key factual claims that MUST be evaluated for groundedness."*

## Test cases (`test_cases.py`)

The evaluation question bank contains **15 questions** categorised by the tool the agent is expected to invoke:

| Category | Count | Description |
|---|---|---|
| `analyst` | 8 | Structured data questions answered via **Cortex Analyst** (text-to-SQL) — e.g. total deal value, win rate, top sales rep |
| `search` | 4 | Unstructured data questions answered via **Cortex Search** — e.g. common customer objections, pricing sentiment |
| `hybrid` | 3 | Questions that require **both tools** — e.g. "Which product line has the best win rate and what do customers say about it?" |

Three question sets are available:

| Set | Questions | Used by |
|---|---|---|
| `QUICK_EVAL_QUESTIONS` | 5 (`analyst` only) | Default run — `uv run python evaluate.py` |
| `analyst_questions()` | 8 (`analyst` only) | `--analyst-only` flag |
| `EVALUATION_QUESTIONS` | 15 (all categories) | `--full` flag |

### Writing good test cases

Add a `TestCase` to `EVALUATION_QUESTIONS` in `test_cases.py`:

```python
TestCase(
    question="How many deals did Rachel Torres close in Q1 2024?",
    tool_type="analyst",           # "analyst" | "search" | "hybrid"
    tags=["sales rep", "close date"],
)
```

Guidelines by `tool_type`:

| Type | When to use | Good question characteristics |
|---|---|---|
| `analyst` | Querying structured sales metrics (counts, sums, averages, rankings) | Specific, measurable — has a single correct SQL-answerable answer. Avoid ambiguity on column names. |
| `search` | Exploring unstructured conversation data (sentiment, themes, objections) | Open-ended, semantic — cannot be answered by SQL alone. Reference topics that appear in conversation transcripts. |
| `hybrid` | Combining structured metrics with conversation insights in one answer | Ask for both a number/ranking **and** a qualitative insight in the same question. |

> [!TIP]
> - **Be specific**: vague questions make it hard to judge Answer Relevance (the LLM judge may score a generic answer as relevant).
> - **Avoid yes/no questions**: they produce short answers with little context for Groundedness scoring.
> - **Test edge cases**: questions about date ranges, specific reps, or product lines stress-test both the SQL generation and context retrieval.
> - Add to `QUICK_EVAL_QUESTIONS` (plain string) if you want the question in the fast 5-question smoke-test set.

See [AGENTS.md](AGENTS.md) for setup instructions, CLI options, authentication, architecture notes, and how to extend the evaluation.
