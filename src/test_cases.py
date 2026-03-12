"""Evaluation test cases for the Snowflake Cortex Agent.

Test cases are loaded from a YAML config file (default: config/test_cases.yaml).
Pass a custom path to load_test_cases() or set the EVAL_TEST_CASES env var to
point to a different agent's config without modifying this file.

Questions are categorised by the tool they are expected to invoke:
  - "analyst"  → Cortex Analyst (text-to-SQL)
  - "search"   → Cortex Search (semantic search)
  - "hybrid"   → both tools in a single query

QUICK_EVAL_QUESTIONS and EVALUATION_QUESTIONS are loaded from the default config
at import time so existing call sites continue to work unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Default config: config/test_cases.yaml relative to the project root
# (two levels up from this file: src/ → project root → config/)
_DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "test_cases.yaml"


@dataclass
class TestCase:
    question: str
    tool_type: str  # "analyst" | "search" | "hybrid"
    tags: list[str] = field(default_factory=list)


def load_test_cases(path: str | Path | None = None) -> tuple[list[TestCase], list[str]]:
    """
    Load test cases from a YAML config file.

    Args:
        path: Path to the YAML config file.
              Defaults to the EVAL_TEST_CASES env var, then config/test_cases.yaml.

    Returns:
        (evaluation_questions, quick_eval_questions)
          evaluation_questions: full list of TestCase objects
          quick_eval_questions: short list of question strings for smoke tests
    """
    if path is None:
        path = os.getenv("EVAL_TEST_CASES", str(_DEFAULT_CONFIG))

    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    evaluation_questions = [
        TestCase(
            question=tc["question"],
            tool_type=tc["tool_type"],
            tags=tc.get("tags", []),
        )
        for tc in data.get("test_cases", [])
    ]

    quick_eval_questions: list[str] = data.get("quick_eval", [])

    return evaluation_questions, quick_eval_questions


# ---------------------------------------------------------------------------
# Module-level defaults loaded from the default config at import time.
# These keep existing call sites (evaluate.py, etc.) unchanged.
# ---------------------------------------------------------------------------

EVALUATION_QUESTIONS, QUICK_EVAL_QUESTIONS = load_test_cases()


def analyst_questions(path: str | Path | None = None) -> list[str]:
    """Return only the Cortex Analyst questions from the evaluation set."""
    questions, _ = load_test_cases(path)
    return [tc.question for tc in questions if tc.tool_type == "analyst"]
