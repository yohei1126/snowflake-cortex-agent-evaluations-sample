# Evaluating Snowflake Cortex Agents with TruLens

> [!NOTE]
> Community sample. For the official Snowflake guide, see
> [Getting Started with Cortex Agent Evaluations](https://www.snowflake.com/en/developers/guides/getting-started-with-cortex-agent-evaluations/).

Evaluate a [Snowflake Cortex Agent](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents) using [TruLens](https://www.trulens.org/).
The target agent answers sales questions with **Cortex Analyst** (text-to-SQL) and **Cortex Search** (semantic search).
All feedback is scored by Snowflake Cortex LLMs — no external API key needed.

## Metrics

| Metric | What it measures |
|---|---|
| **Answer Relevance** | Is the agent's response on-topic for the question? |
| **Context Relevance** | Are the retrieved tool results relevant to the question? |
| **Groundedness** | Is the answer supported by the retrieved context? |
| **Plan Quality** | Did the agent decompose the task and pick the right tools? *(GPA, `--gpa`)* |
| **Tool Selection** | Were the correct tools chosen for each subtask? *(GPA, `--gpa`)* |
| **Plan Adherence** | Did execution follow the intended plan? *(GPA, `--gpa`)* |
| **Tool Calling** | Were tool inputs valid and outputs correctly used? *(GPA, `--gpa`)* |
| **Logical Consistency** | Is the reasoning coherent across all steps? *(GPA, `--gpa`)* |
| **Execution Efficiency** | Were there any redundant or unnecessary steps? *(GPA, `--gpa`)* |

## Quick start

```bash
uv sync
cp .env.example .env   # fill in credentials
uv run python src/evaluate.py
```

## Documentation

- [docs/concepts.md](docs/concepts.md) — why we evaluate this way, what TruLens is, what Agent GPA is
- [docs/setup.md](docs/setup.md) — prerequisites, installation, environment variables
- [docs/evaluation.md](docs/evaluation.md) — CLI options, test cases, output format, extending
- [docs/architecture.md](docs/architecture.md) — how TruLens, SSE streaming, and GPA metrics work

See [AGENTS.md](AGENTS.md) for repository structure and agent-facing command reference.
