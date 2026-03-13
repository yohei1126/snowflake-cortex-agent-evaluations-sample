# Setup

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- Python 3.12+
- A Snowflake account with the Sales Intelligence Agent deployed
  (follow the [quickstart guide](https://quickstarts.snowflake.com/guide/getting_started_with_cortex_agents/index.html)
  in `sfguide-getting-started-with-cortex-agents/` first)
- Access to Snowflake Cortex LLMs (e.g. `mistral-large2`) for feedback scoring

## Installation

```bash
uv sync
```

`uv` reads `pyproject.toml`, creates `.venv/`, and installs all pinned dependencies.

### Key packages

| Package | Purpose |
|---|---|
| `trulens-core` | TruLens session, `TruApp`, `Metric`, `Selector` API |
| `trulens-providers-cortex` | Snowflake Cortex LLM as the feedback evaluator |
| `trulens-dashboard` | Local Streamlit dashboard for browsing results |
| `snowflake-snowpark-python` | Snowpark session used by the Cortex feedback provider |
| `requests` / `sseclient-py` | Cortex Agent REST API client (SSE streaming) |
| `cryptography` | RSA private-key auth support |
| `python-dotenv` | `.env` file loading |
| `pyyaml` | Loading `config/feedback.yaml` and `config/test_cases.yaml` |

## Environment variables

```bash
cp .env.example .env
```

### Cortex Agent API (PAT-based)

The Cortex Agent REST API uses a **Programmatic Access Token (PAT)**.
Generate one in Snowsight:

> Profile (bottom-left) → Settings → Authentication → Programmatic access tokens → Generate new token
> Select `Single Role` and pick `sales_intelligence_role`.

```bash
CORTEX_AGENT_DEMO_PAT=<your-pat-token>
CORTEX_AGENT_DEMO_HOST=<account>.snowflakecomputing.com
CORTEX_AGENT_DEMO_DATABASE=SNOWFLAKE_INTELLIGENCE   # default
CORTEX_AGENT_DEMO_SCHEMA=AGENTS                    # default
CORTEX_AGENT_DEMO_AGENT=SALES_INTELLIGENCE_AGENT   # default
```

### Snowpark session (for TruLens feedback)

TruLens calls Cortex LLMs via a Snowpark session. PAT is not supported by Snowpark;
choose one of three auth methods via `SNOWFLAKE_AUTHENTICATOR`.

**Option A — SSO / Okta (recommended):**
A browser window opens for Okta login; no password or key file is needed.

```bash
SNOWFLAKE_ACCOUNT=<account-identifier>
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

**Option C — RSA key pair:**

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

**Optional — explicit host:**
If your Snowflake account identifier contains an underscore (e.g. `org-name_account`),
set `SNOWFLAKE_HOST` explicitly. If not set, `CORTEX_AGENT_DEMO_HOST` is used as a fallback.

```bash
SNOWFLAKE_HOST=<account>.snowflakecomputing.com
```
