"""TruLens feedback metrics for the Snowflake Cortex Agent evaluation.

Metric prompts and tool descriptions are loaded from a YAML config file
(default: config/feedback.yaml).  Set the EVAL_FEEDBACK_CONFIG env var or
pass a path to build_gpa_metrics() / build_rag_triad_metrics() to use a
different agent's config without modifying this file.

RAG Triad (Goal axis)  — build_rag_triad_metrics()
  Answer Relevance, Context Relevance, Groundedness

Agent GPA Plan axis    — build_gpa_metrics()   (--gpa flag)
  Plan Quality, Tool Selection

Agent GPA Action axis  — build_gpa_metrics()
  Plan Adherence, Tool Calling

Agent GPA full G-P-A   — build_gpa_metrics()
  Logical Consistency, Execution Efficiency
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import threading
from pathlib import Path

import numpy as np
import yaml
from trulens.core import Metric, Selector
from trulens.providers.cortex import Cortex

from agent_client import AgentResponse

# Default config: config/feedback.yaml relative to the project root
_DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "feedback.yaml"


def _load_config(path: str | Path | None = None) -> dict:
    """Load and return the feedback YAML config."""
    if path is None:
        path = os.getenv("EVAL_FEEDBACK_CONFIG", str(_DEFAULT_CONFIG))
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# RAG Triad (Goal axis)
# ---------------------------------------------------------------------------


def build_rag_triad_metrics(
    provider: Cortex,
    config_path: str | Path | None = None,
) -> list[Metric]:
    """
    Return the three RAG Triad Metric objects using TruLens 2.x OTEL selectors.

    Selector.select_context() picks up the RETRIEVED_CONTEXTS span attribute
    set by the @instrument decorator on CortexAgentApp.retrieve().

    Args:
        provider:    TruLens Cortex provider (judge LLM).
        config_path: Path to the feedback YAML config file.
                     Defaults to EVAL_FEEDBACK_CONFIG env var, then config/feedback.yaml.
    """
    cfg = _load_config(config_path)
    groundedness_cfg = cfg.get("groundedness", {})
    sys_tmpl = groundedness_cfg.get("system_prompt", "")
    usr_tmpl = groundedness_cfg.get("user_prompt", "")

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

    def _sql_groundedness(source: list[str] | str, statement: str) -> tuple[float, dict]:
        """
        SQL-aware groundedness scorer.

        TruLens's built-in groundedness_measure_with_cot_reasons fails on Cortex
        Analyst contexts because numbers appear as raw integers (355000) in the
        col=val SQL rows while the answer formats them as currency ($355,000) or
        percentages (60%).  Prompts are loaded from config/feedback.yaml so they
        can be tuned without modifying this file.
        """
        source_text = "\n".join(source) if isinstance(source, list) else source
        return provider.generate_score_and_reasons(
            system_prompt=sys_tmpl.strip(),
            user_prompt=usr_tmpl.format(source_text=source_text, statement=statement).strip(),
        )

    f_groundedness = Metric(
        implementation=_sql_groundedness,
        name="Groundedness",
        selectors={
            "source": Selector.select_context(collect_list=True),
            "statement": Selector.select_record_output(),
        },
    )

    return [f_answer_relevance, f_context_relevance, f_groundedness]


# ---------------------------------------------------------------------------
# Agent GPA — trace formatting helpers
# ---------------------------------------------------------------------------


def _format_plan(tool_uses: list[dict]) -> str:
    """Render the tool-call sequence as a human-readable plan."""
    if not tool_uses:
        return "No tool calls were planned or executed."
    lines = []
    for i, use in enumerate(tool_uses, 1):
        tool_name = use.get("type", use.get("name", "unknown"))
        tool_input = use.get("input", {})
        input_str = (
            json.dumps(tool_input, ensure_ascii=False)
            if isinstance(tool_input, dict)
            else str(tool_input)
        )
        lines.append(f"Step {i}: {tool_name}  input={input_str[:400]}")
    return "\n".join(lines)


def _format_trace(tool_uses: list[dict], tool_results: list[dict]) -> str:
    """Render tool calls + results as a human-readable execution trace."""
    if not tool_uses:
        return "No tool interactions recorded."
    parts = []
    for i, use in enumerate(tool_uses):
        tool_name = use.get("type", use.get("name", "unknown"))
        tool_input = use.get("input", {})
        input_str = (
            json.dumps(tool_input, ensure_ascii=False)
            if isinstance(tool_input, dict)
            else str(tool_input)
        )
        parts.append(f"[Step {i + 1}] {tool_name}")
        parts.append(f"  Input : {input_str[:300]}")
        if i < len(tool_results):
            result = tool_results[i]
            out_parts = []
            for item in result.get("content", [])[:3]:
                if item.get("type") == "text":
                    out_parts.append(item.get("text", "")[:200])
                elif item.get("type") == "json":
                    out_parts.append(json.dumps(item.get("json", {}))[:200])
            parts.append(f"  Output: {' | '.join(out_parts)[:300]}")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Agent GPA — Plan + Action metrics
# ---------------------------------------------------------------------------


def build_gpa_metrics(
    provider: Cortex,
    response_cache: dict[str, AgentResponse],
    debug: bool = False,
    debug_log: str | None = None,
    config_path: str | Path | None = None,
) -> list[Metric]:
    """
    Build Agent GPA Metric objects covering Plan and Action evaluation axes.

    Metric names, system prompts, and user prompt templates are loaded from
    config/feedback.yaml (or config_path / EVAL_FEEDBACK_CONFIG).  This lets
    you adapt the evaluator to a different Cortex Agent by only editing the
    YAML config — no Python changes required.

    Performance: all LLM calls for a given question are fired in parallel
    the first time any metric is requested for that question.  Subsequent
    metric calls for the same question read from the per-question cache and
    return instantly (no extra LLM calls).

    GPA axes covered (default config):
      Goal-Plan   : Plan Quality, Tool Selection
      Plan-Action : Plan Adherence, Tool Calling
      G-P-A       : Logical Consistency, Execution Efficiency

    Args:
        provider:       TruLens Cortex provider (judge LLM).
        response_cache: Live reference to CortexAgentApp._cache.
        debug:          Emit raw tool_uses and judge return values.
        debug_log:      File path to write debug output (default: stdout).
        config_path:    Path to the feedback YAML config file.
                        Defaults to EVAL_FEEDBACK_CONFIG env var, then config/feedback.yaml.
    """
    cfg = _load_config(config_path)

    tool_descriptions = cfg.get("tool_descriptions", {})
    available_tools = "\n".join(
        f"- {name}: {desc}" for name, desc in tool_descriptions.items()
    )

    gpa_metric_defs: list[dict] = cfg.get("gpa_metrics", [])

    # Per-question score cache and locking primitives.
    # First metric to be evaluated for a question computes all in parallel;
    # the others block on the Event then read from the cache.
    _scores: dict[str, dict[str, tuple[float, dict]]] = {}
    _events: dict[str, threading.Event] = {}
    _lock = threading.Lock()
    _log_lock = threading.Lock()

    _debug_logged: set[str] = set()

    _log_fh = open(debug_log, "w", encoding="utf-8") if debug_log else None  # noqa: WPS515
    if _log_fh:
        print(f"[GPA debug] writing to {debug_log}", flush=True)

    def _dprint(msg: str) -> None:
        with _log_lock:
            if _log_fh:
                _log_fh.write(msg + "\n")
                _log_fh.flush()
            else:
                print(msg)

    def _log_debug(user_query: str, resp: AgentResponse) -> None:
        if debug and user_query not in _debug_logged:
            _debug_logged.add(user_query)
            _dprint(f"\n[DEBUG] tool_uses for: {user_query!r}")
            _dprint(json.dumps(resp.tool_uses, indent=2, ensure_ascii=False, default=str))
            _dprint(f"[DEBUG] tool_results count: {len(resp.tool_results)}")

    def _compute_all(user_query: str, response: str, resp: AgentResponse | None) -> None:
        """Fire all GPA judge calls in parallel and store results."""
        plan_str = _format_plan(resp.tool_uses) if resp else "No plan recorded."
        trace_str = (
            _format_trace(resp.tool_uses, resp.tool_results) if resp
            else "No trace recorded."
        )
        n_steps = len(resp.tool_uses) if resp else 0

        template_vars = {
            "available_tools": available_tools,
            "user_query": user_query,
            "plan_str": plan_str,
            "trace_str": trace_str,
            "response": response[:400],
            "n_steps": n_steps,
        }

        def _call(system_prompt: str, user_prompt: str) -> tuple[float, dict]:
            return provider.generate_score_and_reasons(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

        tasks = {
            m["name"]: (
                m["system_prompt"].format_map(template_vars).strip(),
                m["user_prompt"].format_map(template_vars).strip(),
            )
            for m in gpa_metric_defs
        }

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks) or 1) as pool:
            futures = {
                name: pool.submit(_call, sys_p, usr_p)
                for name, (sys_p, usr_p) in tasks.items()
            }
            results = {name: fut.result() for name, fut in futures.items()}

        if debug:
            for name, val in results.items():
                _dprint(f"\n[DEBUG] {name} raw return: {val!r}")

        _scores[user_query] = results

    def _get_score(user_query: str, response: str, metric_name: str) -> tuple[float, dict]:
        """Return the cached score, computing all in parallel on first access."""
        with _lock:
            if user_query in _scores:
                return _scores[user_query][metric_name]
            if user_query not in _events:
                event = threading.Event()
                _events[user_query] = event
                first = True
            else:
                event = _events[user_query]
                first = False

        if first:
            resp = response_cache.get(user_query)
            if resp:
                _log_debug(user_query, resp)
            _compute_all(user_query, response, resp)
            _events[user_query].set()
        else:
            _events[user_query].wait(timeout=120)

        return _scores.get(user_query, {}).get(metric_name, (0.0, {}))

    common_selectors = {
        "user_query": Selector.select_record_input(),
        "response": Selector.select_record_output(),
    }

    def _make_metric(name: str) -> Metric:
        def _fn(user_query: str, response: str) -> tuple[float, dict]:
            return _get_score(user_query, response, name)
        _fn.__name__ = name.lower().replace(" ", "_")
        return Metric(implementation=_fn, name=name, selectors=common_selectors)

    return [_make_metric(m["name"]) for m in gpa_metric_defs]
