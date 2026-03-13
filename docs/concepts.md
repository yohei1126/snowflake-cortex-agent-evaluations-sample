# Key concepts

## Why evaluate AI agents?

Deploying an AI agent to production without evaluation is a risk.
An agent that looks good in demos can still:

- Return answers that sound plausible but contradict the source data (hallucination)
- Call the wrong tool for a task, producing silently wrong results
- Retrieve irrelevant context and then fabricate an answer based on it
- Follow an inefficient or inconsistent reasoning path that works on simple inputs but breaks on edge cases

Manual review does not scale — checking every response by hand is impractical once the question space grows.
This project automates quality measurement so problems surface before they reach users.

---

## LLM-as-a-Judge

Traditional evaluation requires a labelled ground-truth dataset: "the correct answer to question X is Y."
Building and maintaining such a dataset is expensive, especially for open-ended questions like
"What are common customer objections?" where there is no single correct answer.

The **LLM-as-a-Judge** pattern solves this by using a separate, capable LLM (the "judge") to score the
agent's outputs against the retrieved context and the original question.
The judge does not need pre-labelled answers — it reasons about quality the same way a human reviewer would,
but at scale and at a fraction of the cost.

This project uses Snowflake Cortex LLMs as the judge, which means:

- No external API key is required — the judge runs entirely inside your Snowflake account
- The same infrastructure that runs the agent runs the evaluation
- Results are reproducible and auditable (chain-of-thought reasoning is captured alongside every score)

---

## RAG Triad

**Origin:** The RAG Triad was pioneered by **TruEra**, an AI quality company, as the standard evaluation
framework for Retrieval-Augmented Generation (RAG) applications.

The framework identifies three checkpoints where RAG systems typically fail:

```
User question
      │
      ▼
  Retrieval ──────────────────────────────────────────►  Retrieved context
      │                          Context Relevance?              │
      │                          Is what we fetched relevant     │
      │                          to the question?                │
      │                                                          ▼
      └──────────────────────────────────────────────►  Final answer
                    Answer Relevance?          Groundedness?
                    Does the answer            Is every claim in the
                    address the question?      answer supported by
                                               the retrieved context?
```

| Metric | Failure it detects | Score |
|---|---|---|
| **Answer Relevance** | Agent responded off-topic or changed the subject | 0 (off-topic) → 1 (fully relevant) |
| **Context Relevance** | Retrieval fetched unrelated data; averaged per chunk | 0 → 1 per chunk, then mean |
| **Groundedness** | Answer contains facts not present in retrieved context (hallucination) | 0 (hallucinated) → 1 (fully grounded) |

All three metrics together form a **"hallucination-free triangle"**: if the retrieved context is relevant to
the question, and the answer is both relevant and grounded in that context, the response is reliable.

---

## TruLens

**Built by:** TruEra (founded 2019). Acquired by **Snowflake** in 2024.

**What it is:** TruLens is an open-source Python library for evaluating and tracking LLM-based applications.
It wraps your application code with lightweight instrumentation, runs feedback metrics (including the RAG Triad),
and stores results in a local SQLite database or Snowflake.

**Why this project uses it:**

Snowflake provides built-in
[AI Observability](https://docs.snowflake.com/en/user-guide/snowflake-cortex/ai-observability)
for evaluating Cortex-powered applications — but as of 2026 it covers **Cortex Search only**.
It does not support **Cortex Analyst** (text-to-SQL).

Snowflake's own recommendation for filling this gap is to use TruLens to write custom evaluation scripts.
This project is a sample implementation of that recommended approach.

**How it instruments code:**

TruLens uses two decorators to trace execution:

```python
class CortexAgentApp:
    @instrument           # marks this as the "retrieval" step
    def retrieve(question): ...

    @instrument           # marks this as the "generation" step
    def query(question): ...
```

TruLens records what each method receives and returns, then applies the feedback metrics to those values.
The `Selector` API specifies which part of the trace each metric reads:

```python
Metric(
    implementation=provider.relevance_with_cot_reasons,
    selectors={"prompt": Selector.select_record_input(),
               "response": Selector.select_record_output()},
)
```

**Key packages:**

| Package | Role |
|---|---|
| `trulens-core` | Evaluation session, `TruApp`, `Metric`, `Selector` |
| `trulens-providers-cortex` | Calls Snowflake Cortex LLMs as the judge |
| `trulens-dashboard` | Streamlit dashboard for browsing results |

**Official docs:** [trulens.org](https://www.trulens.org/)

---

## Agent GPA framework

**Built by:** Snowflake AI Research team
(Allison Jia, Daniel Huang, Nikhil Vytla, Shayak Sen, Anupam Datta, and others).

**Published:** October 2025 — arXiv paper [2510.08847](https://arxiv.org/abs/2510.08847),
Snowflake Engineering Blog [post](https://www.snowflake.com/en/engineering-blog/ai-agent-evaluation-gpa-framework/).

### The problem it solves

Traditional evaluation only looks at the **final output**: "Was the answer correct?"
This misses a large class of failures that happen *inside* the agent's reasoning process — wrong tool chosen,
plan ignored halfway through, contradictory steps, redundant calls.

Agent GPA evaluates the **entire agent trace** — goal setting, planning, and execution — to pinpoint exactly
where a failure occurred rather than just observing that an output was wrong.

### The three axes

An agent operates in a loop: it interprets a **Goal** (the user's intent), devises a **Plan**
(which tools to use and in what order), and takes **Actions** (actual tool calls and their results).
GPA evaluates the alignment between each pair of these:

```
         Goal
        /    \
Goal-Plan    Goal-Action
      /          \
   Plan ─────── Action
      \    Plan-Action
       \
        Goal-Plan-Action (full trace)
```

| Axis | Metrics | Question answered |
|---|---|---|
| **Goal-Plan** | Plan Quality, Tool Selection | Did the agent design a sound plan for this goal? Did it pick the right tools? |
| **Plan-Action** | Plan Adherence, Tool Calling | Did execution follow the plan? Were tool calls valid? |
| **Goal-Plan-Action** | Logical Consistency, Execution Efficiency | Is the full trace coherent? Were there wasted steps? |

### Performance on benchmarks

Evaluated against the public TRAIL/GAIA benchmark dataset (117 traces, 570 annotated internal errors):

| Metric | Agent GPA | Baseline judge |
|---|---|---|
| Error detection | **95%** (267/281) | 55% |
| Error localization | **86%** (241/281) | 49% |

Agent GPA achieves roughly **1.8× better error detection** than a baseline LLM judge that only looks
at the final answer.

### How this project implements GPA

GPA metrics are defined entirely in `config/feedback.yaml` as LLM judge prompts.
`src/feedback.py` loads them at runtime, fills in template variables
(`{user_query}`, `{plan_str}`, `{trace_str}`, `{response}`, etc.),
and fires all 6 LLM calls in parallel per question using a `ThreadPoolExecutor`.

The prompts can be customised without touching Python — see [evaluation.md](evaluation.md#custom-gpa-prompts).

Enable GPA scoring with the `--gpa` flag:

```bash
uv run python src/evaluate.py --gpa
```
