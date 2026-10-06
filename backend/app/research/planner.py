import logging
from typing import Protocol

import httpx
from pydantic import ValidationError

from app.research.prompts import SYSTEM_PROMPT, user_prompt
from app.schemas.research import ResearchPlan, ResearchRequest, validate_plan

logger = logging.getLogger("spidermind.research.planner")


class ResearchPlanner(Protocol):
    provider_name: str
    model_name: str

    async def plan(self, request: ResearchRequest) -> ResearchPlan: ...


class OllamaResearchPlanner:
    provider_name = "ollama"

    def __init__(self, model_name: str, base_url: str, timeout: float = 180) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def plan(self, request: ResearchRequest) -> ResearchPlan:
        schema = ResearchPlan.model_json_schema()
        schema["properties"]["subquestions"]["maxItems"] = request.max_subquestions
        schema["$defs"]["PlannedSubquestion"]["properties"]["search_queries"]["maxItems"] = (
            request.search_queries_per_subquestion
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": user_prompt(
                    request.question,
                    request.max_subquestions,
                    request.search_queries_per_subquestion,
                ),
            },
        ]
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            for attempt in range(2):
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model_name,
                        "messages": messages,
                        "format": schema,
                        "stream": False,
                        "think": False,
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                content = None
                try:
                    content = response.json()["message"]["content"]
                    if not isinstance(content, str):
                        raise ValueError("Planner content must be a JSON string")
                    return validate_plan(ResearchPlan.model_validate_json(content), request)
                except (ValidationError, ValueError, KeyError, TypeError) as exc:
                    if attempt:
                        raise ValueError(f"Planner returned invalid plan twice: {exc}") from exc
                    logger.warning("planner schema validation failed; retrying once: %s", exc)
                    if isinstance(content, str):
                        messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Repair the JSON plan to satisfy the schema and limits: {exc}"
                            ),
                        }
                    )
        raise AssertionError("unreachable")
