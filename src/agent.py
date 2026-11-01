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
    calculation_id: str | None = None


@dataclass(frozen=True)
class ResearchResponse:
    claims: tuple[Claim, ...]
    calculation_ids: tuple[str, ...]
    limitations: tuple[str, ...]


class GeminiResearchAgent:
    """Manual loop retaining local control of tools, evidence, and retries."""

    def __init__(self, api_key: str, model: str, system_instruction: str, registry: EvidenceRegistry,
                 tool_handlers: dict[str, ToolHandler], client=None, max_rounds: int = 12) -> None:
        self.model, self.system_instruction = model, system_instruction
        self.registry, self.tool_handlers = registry, tool_handlers
        self.client = client or self._create_client(api_key)
        self.max_rounds = max_rounds

    def run(self, user_query: str) -> ResearchResponse:
        if not isinstance(user_query, str) or not user_query.strip():
            raise ValueError("Research question cannot be empty")
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
        # Deliver the last tool results before asking for a bounded, evidence-only answer.
        response = self._generate(input_value, previous_interaction_id, final_only=True)
        if any(getattr(item, "type", None) == "function_call" for item in getattr(response, "steps", []) or []):
            raise RuntimeError("Gemini requested more tools during the final answer step")
        return self._parse_final_response(self._response_text(response))

    def _generate(self, input_value: Any, previous_interaction_id: str | None, final_only: bool = False):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request: dict[str, Any] = {
                    "model": self.model,
                    "input": input_value,
                    "system_instruction": self.system_instruction,
                    "tools": self._function_declarations(),
                }
                if final_only:
                    request["generation_config"] = {"tool_choice": "none"}
                    request["system_instruction"] += (
                        "\nThe research tool budget is exhausted. Return the final JSON now using only "
                        "retrieved evidence. Preserve supported findings and explain any unanswered "
                        "parts in limitations. Do not invent missing values or calculations."
                    )
                if previous_interaction_id:
                    request["previous_interaction_id"] = previous_interaction_id
                return self.client.interactions.create(**request)
            except Exception as exc:
                if not self._is_transient_provider_error(exc):
                    raise
                last_error = exc
                if attempt < 2:
                    delay = 2 ** attempt
                    LOGGER.warning("Transient Gemini request failure on attempt %s; retrying in %ss (%s)", attempt + 1, delay, type(exc).__name__)
                    time.sleep(delay)
        raise RuntimeError(f"Gemini request failed after retry: {last_error}") from last_error

    @staticmethod
    def _is_transient_provider_error(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        try:
            from httpx import TransportError
            transport_error = isinstance(exc, TransportError)
        except ImportError:
            transport_error = False
        return transport_error or isinstance(exc, (TimeoutError, ConnectionError)) or status in {408, 429, 500, 502, 503, 504}

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self.tool_handlers.get(name)
        if handler is None:
            return {"ok": False, "error": f"unregistered_tool:{name}"}
        for attempt in range(3):
            try:
                cleaned_arguments = {key: value for key, value in arguments.items() if value is not None}
                return {"ok": True, "result": self._serialize(handler(**cleaned_arguments))}
            except (EvidenceError, KeyError, TypeError, ValueError) as exc:
                LOGGER.warning("Tool %s rejected invalid input (%s)", name, type(exc).__name__)
                return {"ok": False, "error": str(exc)}
            except Exception as exc:
                if self._is_transient_provider_error(exc) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                LOGGER.error("Tool %s failed (%s)", name, type(exc).__name__)
                return {"ok": False, "error": f"tool_failure:{name}:{type(exc).__name__}"}

    def _parse_final_response(self, raw: str) -> ResearchResponse:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini did not return the required structured JSON response") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Gemini structured response must be a JSON object")
        for field in ("claims", "calculation_ids", "limitations"):
            if not isinstance(data.get(field, []), list):
                raise RuntimeError(f"Gemini response field {field} must be a list")
        if any(not isinstance(item, dict) or not isinstance(item.get("text"), str)
               or not item["text"].strip() or not isinstance(item.get("evidence_ids"), list)
               or any(not isinstance(value, str) for value in item["evidence_ids"])
               for item in data.get("claims", [])):
            raise RuntimeError("Gemini response contains a malformed claim")
        limitations = data.get("limitations", [])
        calculation_ids = data.get("calculation_ids", [])
        if any(not isinstance(value, str) or not value.strip() for value in limitations + calculation_ids):
            raise RuntimeError("Gemini limitations and calculation IDs must be nonempty strings")
        claims = tuple(Claim(item["text"], tuple(item["evidence_ids"]), item.get("calculation_id"))
                       for item in data.get("claims", []))
        if not claims and (not limitations or calculation_ids):
            raise RuntimeError("A response without claims must explain unavailable information and omit calculations")
        for claim in claims:
            if not claim.evidence_ids:
                raise RuntimeError("Each final factual claim must reference evidence")
            self.registry.validate_evidence_ids(claim.evidence_ids)
            if claim.calculation_id is not None:
                if not isinstance(claim.calculation_id, str):
                    raise RuntimeError("Claim calculation_id must be a string")
                sources = self.registry.calculation_sources(claim.calculation_id)
                if set(claim.evidence_ids) != sources:
                    raise RuntimeError("Calculation claim must cite its exact input evidence")
            elif not all(" ".join(claim.text.split()).casefold() in
                         " ".join(self.registry.get(identifier).excerpt.split()).casefold()
                         for identifier in claim.evidence_ids):
                raise RuntimeError("Narrative claims must be verbatim excerpts of their cited evidence")
        calculation_ids = tuple(calculation_ids)
        for calculation_id in calculation_ids:
            self.registry.get_calculation(calculation_id)
        linked = {claim.calculation_id for claim in claims if claim.calculation_id}
        if set(calculation_ids) != linked:
            raise RuntimeError("Every calculation must be linked to a claim")
        return ResearchResponse(claims, calculation_ids, tuple(limitations))

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
        text = claim.text
        if claim.calculation_id:
            calculation = registry.get_calculation(claim.calculation_id)
            unit = 'percentage points' if calculation.operation == 'percentage_point_difference' else '%'
            periods = ', '.join(calculation.reporting_periods)
            text = (f"[{calculation.calculation_id}] {calculation.operation}: {calculation.value} {unit} "
                    f"({calculation.formula}; reporting periods: {periods})")
            if len(set(calculation.reporting_periods)) > 1:
                text += '; comparison uses different reporting periods'
            if 'unknown' in calculation.reporting_periods:
                text += '; reporting period is unverified'
        rendered_claims.append(f"- {text} {' '.join(f'[{item}]' for item in claim.evidence_ids)}")
        citations.extend(claim.evidence_ids)
    lines = rendered_claims or ["The requested information is not available from the retrieved evidence."]
    if response.limitations:
        lines.extend(["", "Limitations:", *[f"- {item}" for item in response.limitations]])
    if citations:
        lines.extend(["", "Sources:"])
    for source in registry.sources_for(citations):
        location = f"{source.title}, {source.source_locator}"
        if source.page_number:
            location += f", p. {source.page_number}"
        lines.append(f"- [{source.evidence_id}] {location} — {source.url or 'local filing'}")
    return "\n".join(lines)
