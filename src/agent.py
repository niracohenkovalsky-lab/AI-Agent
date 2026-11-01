"""Explicit Gemini Interactions API function-calling orchestration."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Callable

from src.utils.evidence import Calculation, EvidenceError, EvidenceRegistry

LOGGER = logging.getLogger(__name__)
ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResearchResponse:
    claims: tuple[Claim, ...]
    calculation_ids: tuple[str, ...]
    limitations: tuple[str, ...]


class GeminiResearchAgent:
    """Manual loop retaining local control of tools, evidence, and retries."""

    def __init__(self, api_key: str, model: str, system_instruction: str, registry: EvidenceRegistry,
                 tool_handlers: dict[str, ToolHandler], client=None, max_rounds: int = 6) -> None:
        self.model, self.system_instruction = model, system_instruction
        self.registry, self.tool_handlers = registry, tool_handlers
        self.client = client or self._create_client(api_key)
        self.max_rounds = max_rounds

    def run(self, user_query: str) -> ResearchResponse:
        input_value: Any = user_query
        previous_interaction_id: str | None = None
        seen_calls: set[str] = set()
        for _ in range(self.max_rounds):
            response = self._generate(input_value, previous_interaction_id)
            calls = [item for item in getattr(response, "steps", []) or [] if getattr(item, "type", None) == "function_call"]
            if not calls:
                return self._parse_final_response(self._response_text(response))
            function_results = []
            for call in calls:
                name, call_id = str(getattr(call, "name", "")), str(getattr(call, "id", ""))
                try:
                    args = getattr(call, "arguments", {})
                    if isinstance(args, str):
                        args = json.loads(args)
                    if not isinstance(args, dict):
                        raise ValueError("tool_arguments_must_be_an_object")
                except (json.JSONDecodeError, ValueError) as exc:
                    result: dict[str, Any] = {"ok": False, "error": str(exc) or "invalid_tool_arguments"}
                else:
                    signature = json.dumps({"name": name, "args": args}, sort_keys=True, default=str)
                    result = ({"ok": False, "error": "repeated_unproductive_tool_call"}
                              if signature in seen_calls else self._execute_tool(name, args))
                    seen_calls.add(signature)
                function_results.append({
                    "type": "function_result",
                    "name": name,
                    "call_id": call_id,
                    "result": [{"type": "text", "text": json.dumps(result)}],
                })
            input_value = function_results
            previous_interaction_id = str(getattr(response, "id", ""))
            if not previous_interaction_id:
                raise RuntimeError("Gemini returned a tool call without an interaction ID")
        raise RuntimeError("Tool-call limit reached before Gemini returned a final structured response")

    def _generate(self, input_value: Any, previous_interaction_id: str | None):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request: dict[str, Any] = {
                    "model": self.model,
                    "input": input_value,
                    "system_instruction": self.system_instruction,
                    "tools": self._function_declarations(),
                }
                if previous_interaction_id:
                    request["previous_interaction_id"] = previous_interaction_id
                return self.client.interactions.create(**request)
            except Exception as exc:
                if not self._is_transient_provider_error(exc):
                    raise
                last_error = exc
                if attempt < 2:
                    delay = 2 ** attempt
                    LOGGER.warning("Transient Gemini request failure on attempt %s; retrying in %ss: %s", attempt + 1, delay, exc)
                    time.sleep(delay)
        raise RuntimeError(f"Gemini request failed after retry: {last_error}") from last_error

    @staticmethod
    def _is_transient_provider_error(exc: Exception) -> bool:
        return isinstance(exc, (TimeoutError, ConnectionError))

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self.tool_handlers.get(name)
        if handler is None:
            return {"ok": False, "error": f"unregistered_tool:{name}"}
        try:
            cleaned_arguments = {key: value for key, value in arguments.items() if value is not None}
            return {"ok": True, "result": self._serialize(handler(**cleaned_arguments))}
        except (EvidenceError, KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            LOGGER.exception("Tool %s failed", name)
            return {"ok": False, "error": f"tool_failure:{exc}"}

    def _parse_final_response(self, raw: str) -> ResearchResponse:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini did not return the required structured JSON response") from exc
        claims = tuple(Claim(str(item["text"]), tuple(str(e) for e in item.get("evidence_ids", []))) for item in data.get("claims", []))
        if not claims:
            raise RuntimeError("Gemini structured response contained no claims")
        for claim in claims:
            if not claim.evidence_ids:
                raise RuntimeError("Each final factual claim must reference evidence")
            self.registry.validate_evidence_ids(claim.evidence_ids)
        calculation_ids = tuple(str(value) for value in data.get("calculation_ids", []))
        for calculation_id in calculation_ids:
            self.registry.get_calculation(calculation_id)
        return ResearchResponse(claims, calculation_ids, tuple(str(value) for value in data.get("limitations", [])))

    @staticmethod
    def _serialize(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, Calculation) or hasattr(value, "__dataclass_fields__"):
            return {key: GeminiResearchAgent._serialize(item) for key, item in asdict(value).items()}
        if isinstance(value, (list, tuple)):
            return [GeminiResearchAgent._serialize(item) for item in value]
        if isinstance(value, dict):
            return {str(key): GeminiResearchAgent._serialize(item) for key, item in value.items()}
        return value

    @staticmethod
    def _response_text(response) -> str:
        value = getattr(response, "output_text", None)
        if not value:
            raise RuntimeError("Gemini returned no final text")
        return str(value)

    @staticmethod
    def _create_client(api_key: str):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("google-genai is not installed. Run `pip install -r requirements.txt`.") from exc
        return genai.Client(api_key=api_key)

    @staticmethod
    def _function_declarations() -> list[dict[str, Any]]:
        string = {"type": "string"}
        simple = lambda name, description: {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": {"query": string}, "required": ["query"]}}
        return [
            simple("search_documents", "Search the locally indexed Apple 10-K for cited evidence."),
            simple("search_web", "Search current web sources with Tavily when the filing cannot answer the question."),
            {"type": "function", "name": "calculate_financial_metric", "description": "Calculate only with evidence fact IDs or prior calculation IDs returned by tools.",
             "parameters": {"type": "object", "properties": {"operation": {"type": "string", "enum": ["percentage", "gross_margin_percentage", "percentage_point_difference"]}, "numerator_fact_id": string, "denominator_fact_id": string, "revenue_fact_id": string, "cost_fact_id": string, "left_reference": string, "right_reference": string}, "required": ["operation"]}},
        ]


def format_response(response: ResearchResponse, registry: EvidenceRegistry) -> str:
    citations: list[str] = []
    rendered_claims = []
    for claim in response.claims:
        rendered_claims.append(f"- {claim.text} {' '.join(f'[{item}]' for item in claim.evidence_ids)}")
        citations.extend(claim.evidence_ids)
    calculations = [registry.get_calculation(identifier) for identifier in response.calculation_ids]
    lines = [*rendered_claims]
    if calculations:
        lines.extend(["", "Calculations:", *[f"- {item.operation}: {item.value} ({item.formula})" for item in calculations]])
    if response.limitations:
        lines.extend(["", "Limitations:", *[f"- {item}" for item in response.limitations]])
    lines.extend(["", "Sources:"])
    for source in registry.sources_for(citations):
        lines.append(f"- [{source.evidence_id}] {source.title} — {source.url or source.source_locator}")
    return "\n".join(lines)
