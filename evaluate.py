"""TruLens evaluation for the Snowflake Cortex Agent (Sales Intelligence).

Evaluates the agent using the RAG Triad:
  - Answer Relevance  : Is the final response relevant to the question?
  - Context Relevance : Is the retrieved context (tool results) relevant to the question?
  - Groundedness      : Is the answer supported by the retrieved context?

The feedback functions are evaluated by Snowflake Cortex LLMs via TruLens'
Cortex provider, so no external LLM API key is needed.

Usage
-----
Set the required environment variables in .env (see .env.example), then run:

    uv run python evaluate.py

Options can be customised via CLI flags or by calling run_evaluation() directly.

Environment variables
---------------------
Agent API (PAT-based auth):
  CORTEX_AGENT_DEMO_PAT      - Snowflake PAT token
  CORTEX_AGENT_DEMO_HOST     - Snowflake account URL  (e.g. abc123.snowflakecomputing.com)
  CORTEX_AGENT_DEMO_DATABASE - Agent database (default: SNOWFLAKE_INTELLIGENCE)
  CORTEX_AGENT_DEMO_SCHEMA   - Agent schema   (default: AGENTS)
  CORTEX_AGENT_DEMO_AGENT    - Agent name     (default: SALES_INTELLIGENCE_AGENT)

Snowpark session (for TruLens Cortex feedback provider):
  SNOWFLAKE_ACCOUNT          - Snowflake account identifier
  SNOWFLAKE_USER             - Snowflake username
  SNOWFLAKE_AUTHENTICATOR    - externalbrowser (SSO) | snowflake (default)
  SNOWFLAKE_PASSWORD         - Password  (when AUTHENTICATOR=snowflake)
  SNOWFLAKE_PRIVATE_KEY_PATH - Path to PEM private key (alternative to password)
  SNOWFLAKE_ROLE             - Role       (default: sales_intelligence_role)
  SNOWFLAKE_WAREHOUSE        - Warehouse  (default: SALES_INTELLIGENCE_WH)
  SNOWFLAKE_DATABASE         - Database   (default: SALES_INTELLIGENCE)
  SNOWFLAKE_SCHEMA           - Schema     (default: DATA)
"""

from __future__ import annotations

import concurrent.futures
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()  # loads .env from the current directory if present

# ---------------------------------------------------------------------------
# SSL bypass for Snowflake accounts whose hostname contains an underscore
# (e.g. xxx-yyy_zzz.snowflakecomputing.com).  Underscores are
# not valid in DNS labels, so no SSL certificate covers them.  TruLens makes
# its own requests calls that don't inherit Snowpark's SSL context, so we
# patch requests globally — matching what agent_client.py does with verify=False.
# This mirrors the workaround already applied in agent_client.py.
# ---------------------------------------------------------------------------
import ssl
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context  # noqa: SLF001

import requests as _requests

_orig_send = _requests.Session.send


def _send_no_ssl_verify(self, request, **kwargs):  # type: ignore[override]
    kwargs["verify"] = False  # force-override; setdefault won't win against explicit verify=True
    return _orig_send(self, request, **kwargs)


_requests.Session.send = _send_no_ssl_verify  # type: ignore[method-assign]

import numpy as np
from snowflake.snowpark import Session
from trulens.apps.app import TruApp
from trulens.core import Metric, Selector, TruSession
from trulens.core.otel.instrument import instrument
from trulens.dashboard.run import run_dashboard
from trulens.otel.semconv.trace import SpanAttributes
from trulens.providers.cortex import Cortex

from agent_client import AgentResponse, CortexAgentClient
from test_cases import EVALUATION_QUESTIONS, QUICK_EVAL_QUESTIONS, analyst_questions

# ---------------------------------------------------------------------------
# TruLens-instrumented wrapper around the Cortex Agent
# ---------------------------------------------------------------------------


class CortexAgentApp:
    """
    TruLens-instrumented wrapper around the Cortex Agent API.

    Two instrumented methods let TruLens trace both the retrieved context
    (tool results) and the generated answer in a single record:

      retrieve(question) → list[str]  — context chunks from tool results
      query(question)    → str         — final text answer from the agent

    The agent API is called only once per question; the result is cached so
    that calling retrieve() inside query() does not trigger a second request.
    """

    def __init__(self, client: CortexAgentClient) -> None:
        self.client = client
        self._cache: dict[str, AgentResponse] = {}

    def _get_response(self, question: str) -> AgentResponse:
        if question not in self._cache:
            self._cache[question] = self.client.run(question)
        return self._cache[question]

    @instrument(
        span_type=SpanAttributes.SpanType.RETRIEVAL,
        attributes={
            SpanAttributes.RETRIEVAL.QUERY_TEXT: "question",
            SpanAttributes.RETRIEVAL.RETRIEVED_CONTEXTS: "return",
        },
    )
    def retrieve(self, question: str) -> list[str]:
        """Return context chunks extracted from the agent's tool results."""
        return self._get_response(question).context_chunks

    @instrument(
        attributes={
            SpanAttributes.RECORD_ROOT.INPUT: "question",
            SpanAttributes.RECORD_ROOT.OUTPUT: "return",
        },
    )
    def query(self, question: str) -> str:
        """Run the full agent pipeline and return the text answer."""
        # Call retrieve so TruLens traces context alongside the answer.
        self.retrieve(question)
        return self._get_response(question).answer


# ---------------------------------------------------------------------------
# Snowpark session for TruLens Cortex feedback provider
# ---------------------------------------------------------------------------


def _create_snowpark_session() -> Session:
    """Build a Snowpark session from environment variables."""
    config: dict = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "role": os.getenv("SNOWFLAKE_ROLE", "sales_intelligence_role"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "SALES_INTELLIGENCE_WH"),
        "database": os.getenv("SNOWFLAKE_DATABASE", "SALES_INTELLIGENCE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA", "DATA"),
    }

    # Pass the explicit hostname so TruLens Cortex provider hits the correct
    # Snowflake URL (avoids SSL hostname-mismatch when account identifier format
    # differs from the actual serving hostname).
    # Falls back to CORTEX_AGENT_DEMO_HOST if SNOWFLAKE_HOST is not set.
    host = os.getenv("SNOWFLAKE_HOST") or os.getenv("CORTEX_AGENT_DEMO_HOST")
    if host:
        config["host"] = host

    authenticator = os.getenv("SNOWFLAKE_AUTHENTICATOR", "snowflake")
    config["authenticator"] = authenticator

    if authenticator == "externalbrowser":
        # SSO / Okta: a browser window will open to complete authentication.
        pass
    elif password := os.getenv("SNOWFLAKE_PASSWORD"):
        config["password"] = password
    elif key_path := os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH"):
        import base64

        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            load_pem_private_key,
        )

        with open(key_path, "rb") as fh:
            private_key = load_pem_private_key(fh.read(), password=None)
        config["private_key"] = base64.b64encode(
            private_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
        ).decode()
    else:
        raise ValueError(
            "Snowflake auth required: set SNOWFLAKE_AUTHENTICATOR=externalbrowser "
            "for SSO, or set SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH."
        )

    return Session.builder.configs(config).create()


# ---------------------------------------------------------------------------
# RAG Triad feedback metrics (TruLens 2.x OTEL API)
# ---------------------------------------------------------------------------


def _build_feedback_metrics(provider: Cortex) -> list[Metric]:
    """
    Return the three RAG Triad Metric objects using TruLens 2.x OTEL selectors.

    Selector.select_context() picks up the RETRIEVED_CONTEXTS span attribute
    set by the @instrument decorator on retrieve().
    Selector.select_record_input() / select_record_output() pick up the
    RECORD_ROOT INPUT / OUTPUT set by the @instrument decorator on query().
    """
    f_answer_relevance = Metric(
        implementation=provider.relevance_with_cot_reasons,
        name="Answer Relevance",
        selectors={
            "prompt": Selector.select_record_input(),
            "response": Selector.select_record_output(),
        },
    )

    f_context_relevance = Metric(
        implementation=provider.context_relevance_with_cot_reasons,
        name="Context Relevance",
        selectors={
            "question": Selector.select_record_input(),
            "context": Selector.select_context(collect_list=False),
        },
        agg=np.mean,
    )

    f_groundedness = Metric(
        implementation=lambda source, statement: provider.groundedness_measure_with_cot_reasons(
            source=source,
            statement=statement,
            additional_instructions=(
                "IMPORTANT: Do NOT classify numeric values, monetary amounts, "
                "percentages, dates, or proper nouns as trivial statements. "
                "These are key factual claims that MUST be evaluated for groundedness."
            ),
        ),
        name="Groundedness",
        selectors={
            "source": Selector.select_context(collect_list=True),
            "statement": Selector.select_record_output(),
        },
    )

    return [f_answer_relevance, f_context_relevance, f_groundedness]


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------


def _save_results_json(
    output_path: str,
    app_name: str,
    app_version: str,
    feedback_model: str,
    qa_results: list[dict],
    leaderboard_row: dict,
) -> None:
    """Serialize evaluation results to a JSON file."""
    payload = {
        "metadata": {
            "app_name": app_name,
            "app_version": app_version,
            "feedback_model": feedback_model,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "question_count": len(qa_results),
        },
        "aggregate": leaderboard_row,
        "results": qa_results,
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    print(f"  Results saved → {output_path}")


def run_evaluation(
    questions: list[str] | None = None,
    app_name: str = "cortex-agent-sales-intelligence",
    app_version: str = "1",
    feedback_model: str = "mistral-large2",
    reset_db: bool = True,
    launch_dashboard: bool = False,
    output_json: str | None = None,
    max_workers: int = 3,
) -> None:
    """
    Run TruLens evaluation over a list of questions.

    Args:
        questions:        Questions to evaluate. Defaults to QUICK_EVAL_QUESTIONS.
        app_name:         TruLens app name shown in the dashboard.
        app_version:      TruLens app version shown in the dashboard.
        feedback_model:   Snowflake Cortex model used for feedback evaluation.
                          Options: mistral-large2, llama3.1-70b, snowflake-arctic, etc.
        reset_db:         If True, wipe the TruLens DB before running.
        launch_dashboard: If True, open the TruLens Streamlit dashboard after evaluation.
                          Defaults to False — use --dashboard flag to enable.
        output_json:      Path to write JSON results. Defaults to a timestamped filename.
        max_workers:      Number of concurrent agent calls. Default 3.
    """
    if questions is None:
        questions = QUICK_EVAL_QUESTIONS

    # --- TruLens session (local SQLite, kept in reports/ to stay out of git) ---
    os.makedirs("reports", exist_ok=True)
    tru = TruSession(database_url="sqlite:///reports/trulens.sqlite")
    if reset_db:
        tru.reset_database()

    # --- Snowpark session + Cortex feedback provider ---
    snowpark_session = _create_snowpark_session()
    provider = Cortex(snowpark_session, model_engine=feedback_model)

    # --- Agent client and TruLens-instrumented app ---
    agent_client = CortexAgentClient()
    agent_app = CortexAgentApp(client=agent_client)

    metrics = _build_feedback_metrics(provider)

    tru_app = TruApp(
        agent_app,
        app_name=app_name,
        app_version=app_version,
        feedbacks=metrics,
    )

    total = len(questions)
    print(f"\nEvaluating {total} question(s) — app='{app_name}' v{app_version} "
          f"(max_workers={max_workers})\n")

    # Collect per-question results for the summary / JSON output.
    qa_results: list[dict] = [None] * total  # type: ignore[list-item]

    def _run(args: tuple[int, str]) -> tuple[int, str, str | Exception]:
        i, question = args
        try:
            return i, question, agent_app.query(question)
        except Exception as exc:
            return i, question, exc

    with tru_app:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_run, (i, q)): i
                for i, q in enumerate(questions, start=1)
            }
            for future in concurrent.futures.as_completed(futures):
                i, question, result = future.result()
                print(f"[{i}/{total}] {question}")
                if isinstance(result, Exception):
                    print(f"  ERROR: {result}\n")
                    qa_results[i - 1] = {
                        "index": i,
                        "question": question,
                        "answer": None,
                        "error": str(result),
                        "metrics": {},
                    }
                else:
                    preview = result[:200] + ("..." if len(result) > 200 else "")
                    print(f"  → {preview}\n")
                    qa_results[i - 1] = {
                        "index": i,
                        "question": question,
                        "answer": result,
                        "error": None,
                        "metrics": {},  # filled in after feedback completes
                    }

    # Wait for all background feedback computations to finish before reading
    # the leaderboard. Without this, the leaderboard is empty and background
    # threads are still running when the interpreter starts to shut down.
    print("Waiting for feedback computation to complete...")
    records_df, _ = tru.get_records_and_feedback()
    if not records_df.empty and "record_id" in records_df.columns:
        try:
            tru.wait_for_feedback_results(
                record_ids=records_df["record_id"].tolist(),
                feedback_names=[m.name for m in metrics],
                timeout=300,  # 5 min — Cortex LLM feedback can be slow
            )
        except RuntimeError as e:
            print(f"  Warning: feedback wait timed out ({e}). "
                  "Results may be incomplete.")

    # --- Pull final records + feedback scores ---
    records_df, feedback_cols = tru.get_records_and_feedback()

    # Attach per-question metric scores to qa_results.
    metric_names = [m.name for m in metrics]
    if not records_df.empty:
        # Map record rows back to questions via the input field.
        for _, row in records_df.iterrows():
            question = row.get("input", "")
            for entry in qa_results:
                if entry and entry["question"] == question:
                    for mn in metric_names:
                        if mn in row and row[mn] is not None:
                            entry["metrics"][mn] = float(row[mn])
                    break

    # --- Rich per-question summary table ---
    col_w = 46
    metric_w = 18
    header_metrics = "".join(f"{mn[:metric_w]:>{metric_w}}" for mn in metric_names)
    separator = "-" * (col_w + metric_w * len(metric_names) + 6)
    print("\n" + "=" * len(separator))
    print(" EVALUATION SUMMARY ")
    print("=" * len(separator))
    print(f"  App : {app_name} v{app_version}")
    print(f"  Model: {feedback_model}")
    print(f"  Questions evaluated: {total}")
    print(separator)
    print(f"  {'Question':<{col_w}}{header_metrics}")
    print(separator)
    for entry in qa_results:
        if entry is None:
            continue
        q = entry["question"][:col_w - 1]
        scores = "".join(
            f"{entry['metrics'].get(mn, float('nan')):>{metric_w}.2f}"
            for mn in metric_names
        )
        print(f"  {q:<{col_w}}{scores}")
    print(separator)

    # Aggregate averages.
    leaderboard = tru.get_leaderboard()
    leaderboard_row: dict = {}
    if not leaderboard.empty:
        row = leaderboard.iloc[0]
        leaderboard_row = row.to_dict()
        avg_scores = "".join(
            f"{leaderboard_row.get(mn, float('nan')):>{metric_w}.2f}"
            for mn in metric_names
        )
        print(f"  {'AVERAGE':<{col_w}}{avg_scores}")
    print("=" * len(separator) + "\n")

    # --- Save JSON results ---
    if output_json is None:
        os.makedirs("reports", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_json = os.path.join("reports", f"eval_results_{ts}.json")
    _save_results_json(
        output_path=output_json,
        app_name=app_name,
        app_version=app_version,
        feedback_model=feedback_model,
        qa_results=qa_results,
        leaderboard_row=leaderboard_row,
    )

    # --- Optional dashboard ---
    if launch_dashboard:
        print("Launching TruLens dashboard at http://localhost:8501")
        print("Press Ctrl+C to stop.\n")
        run_dashboard()


def run_full_evaluation() -> None:
    """Run the full evaluation set grouped by tool type for easier analysis."""
    all_questions = [tc.question for tc in EVALUATION_QUESTIONS]
    run_evaluation(
        questions=all_questions,
        app_name="cortex-agent-sales-intelligence-full",
        reset_db=True,
        launch_dashboard=False,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="TruLens evaluation for Snowflake Cortex Agent"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run the full evaluation set instead of the quick subset.",
    )
    parser.add_argument(
        "--analyst-only",
        action="store_true",
        help="Run only Cortex Analyst questions (skips Cortex Search / hybrid).",
    )
    parser.add_argument(
        "--app-name",
        default="cortex-agent-sales-intelligence",
        help="TruLens app name shown in the dashboard.",
    )
    parser.add_argument(
        "--app-version",
        default="1",
        help="TruLens app version shown in the dashboard.",
    )
    parser.add_argument(
        "--feedback-model",
        default="mistral-large2",
        help="Snowflake Cortex model for feedback evaluation.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the TruLens Streamlit dashboard after evaluation (default: off).",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Path for the JSON results file (default: eval_results_<timestamp>.json).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=3,
        help="Number of concurrent agent calls (default: 3).",
    )
    args = parser.parse_args()

    if args.analyst_only:
        questions = analyst_questions()
    elif args.full:
        questions = [tc.question for tc in EVALUATION_QUESTIONS]
    else:
        questions = None  # defaults to QUICK_EVAL_QUESTIONS (analyst-only)

    run_evaluation(
        questions=questions,
        app_name=args.app_name,
        app_version=args.app_version,
        feedback_model=args.feedback_model,
        launch_dashboard=args.dashboard,
        output_json=args.output,
        max_workers=args.max_workers,
    )
