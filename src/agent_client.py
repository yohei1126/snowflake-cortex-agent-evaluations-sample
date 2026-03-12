"""Cortex Agent API client for evaluation purposes.

Calls the Snowflake Cortex Agent REST API and parses SSE streaming responses
to extract the answer text and context chunks from tool results.
Context comes from two tools:
  - cortex_analyst_text_to_sql: SQL queries + result sets (structured data)
  - cortex_search: semantic search results (unstructured conversation data)
"""

import json
import os
import time
from dataclasses import dataclass, field

import requests
import sseclient
import urllib3

# Suppress SSL verification warnings (consistent with the original demo)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class AgentResponse:
    """Parsed response from a single Cortex Agent API call."""

    answer: str
    context_chunks: list[str] = field(default_factory=list)
    tool_uses: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)


class CortexAgentClient:
    """
    Client for the Snowflake Cortex Agent REST API.

    Handles SSE streaming and extracts:
      - answer: the concatenated text response from the agent
      - context_chunks: text extracted from tool results (Cortex Analyst SQL +
        results, Cortex Search passages), used for RAG evaluation.
    """

    def __init__(
        self,
        pat: str | None = None,
        host: str | None = None,
        database: str | None = None,
        schema: str | None = None,
        agent: str | None = None,
        model: str = "claude-sonnet-4-5",
    ) -> None:
        self.pat = pat or os.getenv("CORTEX_AGENT_DEMO_PAT")
        self.host = host or os.getenv("CORTEX_AGENT_DEMO_HOST")
        self.database = database or os.getenv(
            "CORTEX_AGENT_DEMO_DATABASE", "SNOWFLAKE_INTELLIGENCE"
        )
        self.schema = schema or os.getenv("CORTEX_AGENT_DEMO_SCHEMA", "AGENTS")
        self.agent = agent or os.getenv(
            "CORTEX_AGENT_DEMO_AGENT", "SALES_INTELLIGENCE_AGENT"
        )
        self.model = model

        if not self.pat:
            raise ValueError(
                "PAT token is required. Set the CORTEX_AGENT_DEMO_PAT env var."
            )
        if not self.host:
            raise ValueError(
                "Host is required. Set the CORTEX_AGENT_DEMO_HOST env var."
            )

    @property
    def _url(self) -> str:
        return (
            f"https://{self.host}/api/v2/databases/{self.database}"
            f"/schemas/{self.schema}/agents/{self.agent}:run"
        )

    def run(
        self,
        question: str,
        max_retries: int = 5,
        retry_delay: float = 15.0,
    ) -> AgentResponse:
        """
        Send a single-turn question to the Cortex Agent and return the parsed response.

        Retries automatically on transient Snowflake service-warmup errors
        (code 399113 / 399504 — "target service not yet loaded").

        Args:
            question:     Natural language question for the agent.
            max_retries:  Max retry attempts for transient errors (default 5).
            retry_delay:  Initial wait in seconds between retries; doubles each attempt (default 15s).

        Returns:
            AgentResponse with answer text and context chunks from tool results.

        Raises:
            RuntimeError: If the API returns an error status or an error SSE event.
        """
        delay = retry_delay
        for attempt in range(max_retries + 1):
            try:
                return self._run_once(question)
            except RuntimeError as exc:
                if attempt == max_retries or not _is_service_warmup_error(exc):
                    raise
                print(
                    f"  [retry {attempt + 1}/{max_retries}] Service not ready, "
                    f"waiting {delay:.0f}s — {exc}"
                )
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")  # pragma: no cover

    def _run_once(self, question: str) -> AgentResponse:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": question}],
                }
            ],
        }

        resp = requests.post(
            url=self._url,
            data=json.dumps(payload),
            headers={
                "Authorization": f"Bearer {self.pat}",
                "Content-Type": "application/json",
            },
            stream=True,
            verify=False,
        )

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Cortex Agent API error {resp.status_code}: {resp.text}"
            )

        return self._parse_stream(resp)

    def _parse_stream(self, response: requests.Response) -> AgentResponse:
        """Parse the SSE event stream and build an AgentResponse."""
        text_buffers: dict[int, str] = {}
        context_chunks: list[str] = []
        tool_uses: list[dict] = []
        tool_results: list[dict] = []

        for event in sseclient.SSEClient(response).events():
            if not event.data or event.data == "[DONE]":
                continue

            try:
                data = json.loads(event.data)
            except json.JSONDecodeError:
                continue

            match event.event:
                case "response.text.delta":
                    idx = data.get("content_index", 0)
                    text_buffers[idx] = text_buffers.get(idx, "") + data.get("text", "")

                case "response.tool_use":
                    tool_uses.append(data)

                case "response.tool_result":
                    tool_results.append(data)
                    context_chunks.extend(_extract_context_from_tool_result(data))

                case "error":
                    raise RuntimeError(
                        f"Agent error: {data.get('message')} (code: {data.get('code')})"
                    )

                case _:
                    pass  # status, thinking, chart, table, response events ignored

        answer = "\n".join(text_buffers[k] for k in sorted(text_buffers))
        return AgentResponse(
            answer=answer,
            context_chunks=context_chunks,
            tool_uses=tool_uses,
            tool_results=tool_results,
        )


def _extract_context_from_tool_result(tool_result: dict) -> list[str]:
    """
    Extract readable context strings from a tool result SSE event.

    The tool result content is a list of items, each with type "text" or "json":
      - type="text": direct text output from the tool
      - type="json": structured output (Cortex Analyst SQL/results or Cortex Search hits)

    For Cortex Analyst JSON:
      {"sql": "SELECT ...", "resultSet": {"data": [...], "columns": [...]}}
    For Cortex Search JSON:
      {"results": [{"field": "value", ...}, ...]}
    """
    chunks: list[str] = []
    tool_name = tool_result.get("name", "")

    # data_to_chart renders charts from existing SQL results; its output contains
    # chart config / image data, not queryable facts.  Exclude it so that chart
    # artefacts do not pollute the context chunks used for groundedness scoring.
    if tool_name == "data_to_chart":
        return chunks

    for item in tool_result.get("content", []):
        if not isinstance(item, dict):
            continue

        match item.get("type"):
            case "text":
                if text := item.get("text", "").strip():
                    chunks.append(text)

            case "json":
                json_val = item.get("json", {})
                if not isinstance(json_val, dict):
                    continue

                # Cortex Analyst: expose the SQL query and any tabular results
                if sql := json_val.get("sql"):
                    chunks.append(f"SQL: {sql.strip()}")

                result_set = json_val.get("resultSet") or json_val.get("result_set", {})
                if isinstance(result_set, dict):
                    columns = [
                        col if isinstance(col, str) else col.get("name", "")
                        for col in result_set.get("columns", [])
                    ]
                    for row in result_set.get("data", []):
                        if isinstance(row, list) and columns:
                            row_text = ", ".join(
                                f"{col}={val}" for col, val in zip(columns, row)
                            )
                            chunks.append(row_text)
                        elif isinstance(row, dict):
                            chunks.append(
                                ", ".join(f"{k}={v}" for k, v in row.items())
                            )

                # Cortex Search: expose each search result's text fields
                for result in json_val.get("results", []):
                    if isinstance(result, dict):
                        for v in result.values():
                            if isinstance(v, str) and v.strip():
                                chunks.append(v.strip())

    return chunks


def _is_service_warmup_error(exc: Exception) -> bool:
    """Return True for transient Snowflake 'service not yet loaded' errors (code 399113)."""
    msg = str(exc)
    return "399113" in msg or "not yet loaded" in msg.lower()
