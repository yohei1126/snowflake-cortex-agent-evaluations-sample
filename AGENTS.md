# AGENTS.md

Agent-facing index for `snowflake-cortex-agent-evaluations-sample`.
For a human-readable overview see [README.md](README.md).

## Repository structure

```
snowflake-cortex-agent-evaluations-sample/
├── AGENTS.md                        # This file — agent index
├── README.md                        # Human-readable overview
├── pyproject.toml                   # Python project & dependencies (managed by uv)
├── .env.example                     # Required environment variables template
├── src/
│   ├── evaluate.py                  # Entry point — TruLens session, CLI, summary, JSON output
│   ├── feedback.py                  # RAG Triad + GPA Metric builders (loads config/feedback.yaml)
│   ├── agent_client.py              # Cortex Agent REST API client (SSE streaming)
│   └── test_cases.py               # TestCase dataclass + question sets (loads config/test_cases.yaml)
├── config/
│   ├── feedback.yaml                # Judge prompts and tool descriptions (GPA + Groundedness)
│   └── test_cases.yaml             # Evaluation question bank (15 questions, 3 categories)
├── docs/
│   ├── setup.md                     # Prerequisites, installation, environment variables
│   ├── evaluation.md                # CLI options, test cases, output format, extending
│   └── architecture.md              # TruLens internals, SSE streaming, GPA framework
├── reports/                         # JSON results + TruLens SQLite DB (git-ignored)
└── sfguide-getting-started-with-cortex-agents/   # Target agent (git submodule)
    ├── create_agent.json            # Agent definition (tools + instructions)
    ├── sales_metrics_model.yaml     # Cortex Analyst semantic model
    └── setup.sql / course_setup.sql
```

## Available commands

### Run evaluation

```bash
# Quick run — 5 analyst questions (~2-3 min)
uv run python src/evaluate.py

# Full run — 15 questions (analyst + search + hybrid)
uv run python src/evaluate.py --full

# Analyst-only — 8 questions (no Cortex Search)
uv run python src/evaluate.py --analyst-only

# With Agent GPA metrics (6 additional LLM-as-a-Judge scores)
uv run python src/evaluate.py --gpa

# Full run with GPA, debug log, and dashboard
uv run python src/evaluate.py --full --gpa --debug --dashboard
```

### Key CLI flags

| Flag | Default | Description |
|---|---|---|
| `--full` | off | Run all 15 questions |
| `--analyst-only` | off | Run only Cortex Analyst questions (8) |
| `--gpa` | off | Enable Agent GPA metrics (6 additional scores) |
| `--feedback-model M` | `mistral-large2` | Cortex model for RAG Triad scoring |
| `--gpa-model M` | `mistral-large2` | Cortex model for GPA scoring |
| `--max-workers N` | `3` | Concurrent agent calls |
| `--output FILE` | auto-timestamped | JSON results path |
| `--debug` | off | Write GPA debug log to `reports/debug_<timestamp>.log` |
| `--dashboard` | off | Launch TruLens Streamlit dashboard after evaluation |

Full reference: [docs/evaluation.md](docs/evaluation.md)

## Configuration files

| File | Purpose |
|---|---|
| `config/feedback.yaml` | Judge prompts and tool descriptions. Edit to adapt metrics to a different agent. |
| `config/test_cases.yaml` | Question bank. Edit to add, remove, or retag test questions. |

## Source modules

| Module | Role |
|---|---|
| `src/evaluate.py` | CLI entry point. Builds TruLens session, runs questions in parallel, prints summary, saves JSON. |
| `src/feedback.py` | Builds `Metric` objects. Loads prompts from `config/feedback.yaml`. |
| `src/agent_client.py` | `CortexAgentClient` — POSTs to Cortex Agent REST API, parses SSE stream, returns `AgentResponse`. |
| `src/test_cases.py` | `load_test_cases()` — loads `config/test_cases.yaml`, exposes `EVALUATION_QUESTIONS`, `QUICK_EVAL_QUESTIONS`, `analyst_questions()`. |

## Setup

See [docs/setup.md](docs/setup.md) for full instructions.

**Quick setup:**

```bash
uv sync
cp .env.example .env   # fill in PAT token + Snowflake credentials
```
