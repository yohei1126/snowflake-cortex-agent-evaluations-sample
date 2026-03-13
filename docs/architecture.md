# Architecture

## Overview

```
User question
      │
      ▼
CortexAgentApp (TruLens TruApp wrapper)
├── retrieve(question) → list[str]    @instrument — returns context chunks
└── query(question)    → str          @instrument — calls retrieve(), returns answer
        │
        └── CortexAgentClient.run(question)
                │
                └── POST /api/v2/.../agents/SALES_INTELLIGENCE_AGENT:run  (SSE stream)
                        ├── response.text.delta        → answer text
                        └── response.tool_result
                                ├── cortex_analyst_text_to_sql  → SQL query + result rows
                                ├── cortex_search               → conversation passages
                                └── data_to_chart               → chart render result
```

The result is cached in `CortexAgentApp._cache` so the API is called only once per question,
even though TruLens invokes both `retrieve()` and `query()` separately.

## SSE streaming

`CortexAgentClient.run()` opens a server-sent event (SSE) stream to the Cortex Agent REST API.
It collects three types of events:

| SSE event | What it carries | Where it goes |
|---|---|---|
| `response.text.delta` | Incremental answer text | `AgentResponse.answer` |
| `response.tool_use` | Tool call metadata (`type`, `name`, `input`) | `AgentResponse.tool_uses` |
| `response.tool_result` | Tool output (`content` array) | `AgentResponse.tool_results` |

**Important**: the `response.tool_use` event has two name fields:

| Field | Value | Used for |
|---|---|---|
| `name` | `"Sales_metrics_model"` | Cortex Analyst semantic model display name |
| `type` | `"cortex_analyst_text_to_sql"` | Actual tool type — used in GPA judge prompts |

`feedback.py` always reads the `type` field (falling back to `name`) so judge prompts show
the canonical tool type, not the semantic model's display name.

### Retry logic

`CortexAgentClient.run()` retries up to 5 times with exponential back-off (starting at 15 s)
on Snowflake service-warmup errors (code `399113` / `"not yet loaded"`).
This is common when the Cortex Agent service is cold-starting.

### SSL bypass for underscore hostnames

Snowflake account identifiers containing underscores (e.g. `org-name_account.snowflakecomputing.com`)
are not valid DNS labels and have no matching SSL certificate. `agent_client.py` patches
`requests.Session.send` globally to disable SSL verification for these accounts.

## TruLens instrumentation

`CortexAgentApp` uses two `@instrument`-decorated methods so TruLens can trace both the
context retrieval and the final answer in a single record:

```python
class CortexAgentApp:
    @instrument
    def retrieve(self, question: str) -> list[str]:
        # Returns context_chunks from the cached AgentResponse.
        # TruLens reads RETRIEVED_CONTEXTS from this span for Context Relevance + Groundedness.

    @instrument
    def query(self, question: str) -> str:
        # Calls retrieve() then returns the answer.
        # TruLens reads record input/output for Answer Relevance.
```

Because both methods share `_cache`, the actual Cortex Agent API call happens once inside
`retrieve()`. The `query()` call reuses the cached result.

## RAG Triad metrics

All three metrics use `_with_cot_reasons` variants so the judge produces a score
**and** a chain-of-thought explanation.

| Metric | Provider method | Selector |
|---|---|---|
| Answer Relevance | `relevance_with_cot_reasons` | input → output of `query()` |
| Context Relevance | `context_relevance_with_cot_reasons` | input → each context chunk (averaged) |
| Groundedness | custom `_sql_groundedness` | context chunks → output of `query()` |

### SQL-aware groundedness

The built-in TruLens groundedness scorer fails on Cortex Analyst contexts because SQL result
rows contain raw numbers (`355000`) while the answer formats them as currency (`$355,000`) or
percentages (`60%`). A custom scorer in `feedback.py` uses a tailored system prompt that
explicitly instructs the judge to normalise numeric formatting before scoring.

Prompts are stored in `config/feedback.yaml` under `groundedness:` and can be tuned without
modifying Python source.

## Agent GPA metrics (`--gpa`)

The [Agent GPA framework](https://arxiv.org/abs/2412.10108) evaluates agents across three axes:

| Axis | Metrics | What it measures |
|---|---|---|
| Goal-Plan | Plan Quality, Tool Selection | Did the agent choose the right tools for the task? |
| Plan-Action | Plan Adherence, Tool Calling | Did execution follow the plan? Were tool inputs valid? |
| G-P-A | Logical Consistency, Execution Efficiency | Is the reasoning coherent? Were there redundant steps? |

### Parallel batch evaluation

All 6 GPA LLM calls for a given question are fired simultaneously in a `ThreadPoolExecutor`:

```
Question arrives
      │
      ├── Plan Quality        ┐
      ├── Tool Selection      │
      ├── Plan Adherence      ├── 6 concurrent LLM calls
      ├── Tool Calling        │
      ├── Logical Consistency │
      └── Execution Efficiency┘
              │
              └── results cached per question
```

The first metric call for a question computes all 6; subsequent calls read from the cache via
a `threading.Event`. This reduces 6×N sequential calls to N parallel bursts — a 6× speedup.

### Config-driven prompts

GPA metric definitions (name, system prompt, user prompt template) live entirely in
`config/feedback.yaml`. `feedback.py` loads them at runtime and uses `str.format_map()`
to fill in template variables. To adapt the evaluator to a different Cortex Agent, only
`config/feedback.yaml` needs to change — no Python edits required.

Set `EVAL_FEEDBACK_CONFIG` env var or pass `config_path` to `build_gpa_metrics()` to
load a different config file.
