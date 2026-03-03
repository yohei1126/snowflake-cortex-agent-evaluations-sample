"""Evaluation test cases for the Snowflake Cortex Agent (Sales Intelligence).

Questions are categorised by the tool they are expected to invoke:
  - "analyst"  → Cortex Analyst (text-to-SQL against SALES_METRICS table)
  - "search"   → Cortex Search (semantic search over SALES_CONVERSATION_SEARCH)
  - "hybrid"   → both tools in a single query

QUICK_EVAL_QUESTIONS is a shorter list suitable for a fast smoke-test run.
EVALUATION_QUESTIONS is the full set for a thorough evaluation.
"""

from dataclasses import dataclass, field


@dataclass
class TestCase:
    question: str
    tool_type: str  # "analyst" | "search" | "hybrid"
    tags: list[str] = field(default_factory=list)


EVALUATION_QUESTIONS: list[TestCase] = [
    # -------------------------------------------------------------------------
    # Cortex Analyst — structured sales metrics (SALES_METRICS table)
    # -------------------------------------------------------------------------
    TestCase(
        question="What is the total deal value for all closed (won) deals?",
        tool_type="analyst",
        tags=["revenue", "closed deals"],
    ),
    TestCase(
        question="Which sales representative has the highest total deal value?",
        tool_type="analyst",
        tags=["sales rep", "revenue ranking"],
    ),
    TestCase(
        question="What is the overall win rate across all deals?",
        tool_type="analyst",
        tags=["win rate", "percentage"],
    ),
    TestCase(
        question="How many deals are currently in the 'Pending' stage?",
        tool_type="analyst",
        tags=["pipeline", "pending"],
    ),
    TestCase(
        question="What is the average deal value for each product line?",
        tool_type="analyst",
        tags=["product line", "average deal value"],
    ),
    TestCase(
        question="Which product line generates the most total revenue?",
        tool_type="analyst",
        tags=["product line", "revenue"],
    ),
    TestCase(
        question="How many deals were closed in February 2024?",
        tool_type="analyst",
        tags=["close date", "monthly"],
    ),
    TestCase(
        question="What is the win rate for each sales representative?",
        tool_type="analyst",
        tags=["win rate", "sales rep"],
    ),
    # -------------------------------------------------------------------------
    # Cortex Search — unstructured sales conversation analysis
    # -------------------------------------------------------------------------
    TestCase(
        question="What are the most common customer objections mentioned in sales conversations?",
        tool_type="search",
        tags=["objections", "customer concerns"],
    ),
    TestCase(
        question="What do customers say about the Enterprise Suite product?",
        tool_type="search",
        tags=["Enterprise Suite", "customer feedback"],
    ),
    TestCase(
        question="What challenges do customers face according to sales conversations?",
        tool_type="search",
        tags=["challenges", "pain points"],
    ),
    TestCase(
        question="Summarize what customers say about pricing in sales conversations.",
        tool_type="search",
        tags=["pricing", "customer sentiment"],
    ),
    # -------------------------------------------------------------------------
    # Hybrid — both Cortex Analyst and Cortex Search
    # -------------------------------------------------------------------------
    TestCase(
        question=(
            "Which product line has the best win rate and what do customers say about it "
            "in sales conversations?"
        ),
        tool_type="hybrid",
        tags=["win rate", "product line", "customer feedback"],
    ),
    TestCase(
        question=(
            "What is the total revenue from Enterprise Suite deals and what are customers "
            "saying about it in conversations?"
        ),
        tool_type="hybrid",
        tags=["Enterprise Suite", "revenue", "conversation"],
    ),
    TestCase(
        question=(
            "Who is the top-performing sales rep by deal value and what conversation "
            "techniques do they use?"
        ),
        tool_type="hybrid",
        tags=["sales rep", "performance", "conversation style"],
    ),
]

# Shorter list for quick smoke-test runs (Cortex Analyst only — no Cortex Search)
QUICK_EVAL_QUESTIONS: list[str] = [
    "What is the total deal value for all closed (won) deals?",
    "Which sales representative has the highest total deal value?",
    "What is the overall win rate across all deals?",
    "What is the average deal value for each product line?",
    "Which product line generates the most total revenue?",
]


def analyst_questions() -> list[str]:
    """Return only the Cortex Analyst questions from the full evaluation set."""
    return [tc.question for tc in EVALUATION_QUESTIONS if tc.tool_type == "analyst"]
