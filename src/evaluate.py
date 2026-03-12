"""TruLens evaluation runner for the Snowflake Cortex Agent (Sales Intelligence).

Implements the full Agent GPA (Goal-Plan-Action) framework plus RAG Triad.
Feedback metrics are defined in feedback.py.

RAG Triad (Goal axis):
  Answer Relevance, Context Relevance, Groundedness

Agent GPA — Plan axis (Goal-Plan alignment):
  Plan Quality, Tool Selection

Agent GPA — Action axis (Plan-Action alignment):
  Plan Adherence, Tool Calling

Agent GPA — Full alignment (Goal-Plan-Action):
  Logical Consistency, Execution Efficiency

Usage
-----
    uv run python src/evaluate.py          # RAG Triad only (fast)
    uv run python src/evaluate.py --gpa    # RAG Triad + full Agent GPA

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

load_dotenv()  # loads .env from the current directory or parent if present

# ---------------------------------------------------------------------------
# SSL bypass for Snowflake accounts whose hostname contains an underscore
# (e.g. xxx-yyy_zzz.snowflakecomputing.com).  Underscores are not valid in
# DNS labels, so no SSL certificate covers them.  TruLens makes its own
# requests calls that don't inherit Snowpark's SSL context, so we patch
# requests globally — matching what agent_client.py does with verify=False.
# ---------------------------------------------------------------------------
import ssl
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context  # noqa: SLF001

import requests as _requests

_orig_send = _requests.Session.send


def _send_no_ssl_verify(self, request, **kwargs):  # type: ignore[override]
    kwargs["verify"] = False
    return _orig_send(self, request, **kwargs)


_requests.Session.send = _send_no_ssl_verify  # type: ignore[method-assign]

from snowflake.snowpark import Session
from trulens.apps.app import TruApp
from trulens.core import TruSession
from trulens.core.otel.instrument import instrument
from trulens.dashboard.run import run_dashboard
from trulens.otel.semconv.trace import SpanAttributes
from trulens.providers.cortex import Cortex

from agent_client import AgentResponse, CortexAgentClient
from feedback import build_gpa_metrics, build_rag_triad_metrics
from test_cases import EVALUATION_QUESTIONS, QUICK_EVAL_QUESTIONS, analyst_questions, load_test_cases

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
    The cache is also shared with feedback.py's GPA metric builders so they
    can access tool_uses and tool_results for Plan/Action scoring.
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
        self.retrieve(question)  # traced for context selectors
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

    host = os.getenv("SNOWFLAKE_HOST") or os.getenv("CORTEX_AGENT_DEMO_HOST")
    if host:
        config["host"] = host

    authenticator = os.getenv("SNOWFLAKE_AUTHENTICATOR", "snowflake")
    config["authenticator"] = authenticator

    if authenticator == "externalbrowser":
        pass  # browser popup handles auth
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
# Results serialisation
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


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------


def run_evaluation(
    questions: list[str] | None = None,
    app_name: str = "cortex-agent-sales-intelligence",
    app_version: str = "1",
    feedback_model: str = "mistral-large2",
    reset_db: bool = True,
    launch_dashboard: bool = False,
    output_json: str | None = None,
    max_workers: int = 3,
    use_gpa: bool = False,
    gpa_model: str = "mistral-large2",
    debug: bool = False,
    debug_log: str | None = None,
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
        output_json:      Path to write JSON results. Defaults to a timestamped filename.
        max_workers:      Number of concurrent agent calls. Default 3.
        use_gpa:          If True, add the 6 Agent GPA metrics (Plan + Action axes)
                          on top of the 3 RAG Triad metrics. Enables --gpa flag.
    """
    if questions is None:
        questions = QUICK_EVAL_QUESTIONS

    os.makedirs("reports", exist_ok=True)
    tru = TruSession(database_url="sqlite:///reports/trulens.sqlite")
    if reset_db:
        tru.reset_database()

    snowpark_session = _create_snowpark_session()
    provider = Cortex(snowpark_session, model_engine=feedback_model)

    agent_client = CortexAgentClient()
    agent_app = CortexAgentApp(client=agent_client)

    metrics = build_rag_triad_metrics(provider)
    if use_gpa:
        gpa_provider = (
            Cortex(snowpark_session, model_engine=gpa_model)
            if gpa_model != feedback_model
            else provider
        )
        metrics += build_gpa_metrics(gpa_provider, agent_app._cache, debug=debug, debug_log=debug_log)

    tru_app = TruApp(
        agent_app,
        app_name=app_name,
        app_version=app_version,
        feedbacks=metrics,
    )

    total = len(questions)
    mode = "RAG Triad + Agent GPA" if use_gpa else "RAG Triad"
    print(f"\nEvaluating {total} question(s) — app='{app_name}' v{app_version} "
          f"mode={mode} (max_workers={max_workers})\n")

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
                        "index": i, "question": question,
                        "answer": None, "error": str(result), "metrics": {},
                    }
                else:
                    preview = result[:200] + ("..." if len(result) > 200 else "")
                    print(f"  → {preview}\n")
                    qa_results[i - 1] = {
                        "index": i, "question": question,
                        "answer": result, "error": None, "metrics": {},
                    }

    print("Waiting for feedback computation to complete...")
    records_df, _ = tru.get_records_and_feedback()
    if not records_df.empty and "record_id" in records_df.columns:
        try:
            tru.wait_for_feedback_results(
                record_ids=records_df["record_id"].tolist(),
                feedback_names=[m.name for m in metrics],
                timeout=600,
            )
        except RuntimeError as e:
            print(f"  Warning: feedback wait timed out ({e}). Results may be incomplete.")

    records_df, _ = tru.get_records_and_feedback()

    metric_names = [m.name for m in metrics]
    if not records_df.empty:
        for _, row in records_df.iterrows():
            question = row.get("input", "")
            for entry in qa_results:
                if entry and entry["question"] == question:
                    for mn in metric_names:
                        if mn in row and row[mn] is not None:
                            entry["metrics"][mn] = float(row[mn])
                    break

    # --- Summary table ---
    col_w = 42
    metric_w = 14 if len(metric_names) > 4 else 18
    header_metrics = "".join(f"{mn[:metric_w]:>{metric_w}}" for mn in metric_names)
    separator = "-" * (col_w + metric_w * len(metric_names) + 6)
    print("\n" + "=" * len(separator))
    print(" EVALUATION SUMMARY ")
    print("=" * len(separator))
    print(f"  App  : {app_name} v{app_version}")
    print(f"  Model: {feedback_model}   Mode: {mode}")
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

    if output_json is None:
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

    if launch_dashboard:
        print("Launching TruLens dashboard at http://localhost:8501")
        print("Press Ctrl+C to stop.\n")
        run_dashboard()


def run_full_evaluation() -> None:
    """Run the full evaluation set grouped by tool type for easier analysis."""
    run_evaluation(
        questions=[tc.question for tc in EVALUATION_QUESTIONS],
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
    parser.add_argument("--test-cases", default=None, metavar="FILE",
                        help="Path to a YAML test-cases config file "
                             "(default: config/test_cases.yaml). "
                             "Overrides the EVAL_TEST_CASES env var.")
    parser.add_argument("--full", action="store_true",
                        help="Run the full question set instead of the quick subset.")
    parser.add_argument("--analyst-only", action="store_true",
                        help="Run only Cortex Analyst questions (skips Cortex Search / hybrid).")
    parser.add_argument("--gpa", action="store_true",
                        help="Enable full Agent GPA metrics (Plan + Action axes) in addition to RAG Triad.")
    parser.add_argument("--app-name", default="cortex-agent-sales-intelligence",
                        help="TruLens app name shown in the dashboard.")
    parser.add_argument("--app-version", default="1",
                        help="TruLens app version shown in the dashboard.")
    parser.add_argument("--feedback-model", default="mistral-large2",
                        help="Snowflake Cortex model for feedback evaluation.")
    parser.add_argument("--dashboard", action="store_true",
                        help="Launch the TruLens Streamlit dashboard after evaluation.")
    parser.add_argument("--output", default=None, metavar="FILE",
                        help="Path for the JSON results file (default: reports/eval_results_<ts>.json).")
    parser.add_argument("--max-workers", type=int, default=3,
                        help="Number of concurrent agent calls (default: 3).")
    parser.add_argument("--gpa-model", default="mistral-large2",
                        help="Cortex model for GPA metrics (default: mistral-large2). "
                             "Use llama3.1-8b for faster but less accurate scoring.")
    parser.add_argument("--debug", action="store_true",
                        help="Emit raw tool_uses payloads and judge return values to diagnose scoring issues.")
    parser.add_argument("--debug-log", default=None, metavar="FILE",
                        help="Write GPA debug output to FILE instead of stdout "
                             "(default: reports/debug_<ts>.log when --debug is set).")
    args = parser.parse_args()

    eval_questions, quick_questions = (
        load_test_cases(args.test_cases)
        if args.test_cases
        else (EVALUATION_QUESTIONS, QUICK_EVAL_QUESTIONS)
    )

    if args.analyst_only:
        questions = [tc.question for tc in eval_questions if tc.tool_type == "analyst"]
    elif args.full:
        questions = [tc.question for tc in eval_questions]
    else:
        questions = quick_questions or None

    debug_log = args.debug_log
    if args.debug and debug_log is None:
        os.makedirs("reports", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_log = os.path.join("reports", f"debug_{ts}.log")

    run_evaluation(
        questions=questions,
        app_name=args.app_name,
        app_version=args.app_version,
        feedback_model=args.feedback_model,
        launch_dashboard=args.dashboard,
        output_json=args.output,
        max_workers=args.max_workers,
        use_gpa=args.gpa,
        gpa_model=args.gpa_model,
        debug=args.debug,
        debug_log=debug_log,
    )
