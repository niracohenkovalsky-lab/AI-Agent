"""Explicit Gemini Interactions API function-calling orchestration."""

from __future__ import annotations

import json
import logging
import random
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
        return self._research(user_query)

    def _research(self, input_value: Any) -> ResearchResponse:
        previous_id, seen_calls = None, set()
        for _ in range(self.max_rounds):
            response = self._generate(input_value, previous_id)
            calls = self._tool_calls(response)
            if not calls:
                return self._finish_response(response)
            input_value = [self._function_result(call, seen_calls) for call in calls]
            previous_id = self._tool_interaction_id(response)
        return self._final_answer(input_value, previous_id)

    def _tool_calls(self, response) -> list[Any]:
        return [item for item in getattr(response, "steps", []) or []
                if getattr(item, "type", None) == "function_call"]

    def _tool_interaction_id(self, response) -> str:
        interaction_id = str(getattr(response, "id", ""))
        if not interaction_id:
            raise RuntimeError("Gemini returned a tool call without an interaction ID")
        return interaction_id

    def _finish_response(self, response) -> ResearchResponse:
        raw = self._response_text(response)
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            raw = self._reformat_response(response)
        return self._parse_final_response(raw)

    def _reformat_response(self, response) -> str:
        interaction_id = str(getattr(response, "id", "") or "")
        if not interaction_id:
            raise RuntimeError("Cannot reformat Gemini answer without an interaction ID")
        LOGGER.warning("Gemini returned non-JSON final text; requesting structured final answer once")
        response = self._generate(
            "Return the final answer as the required JSON object, using only evidence "
            "already retrieved in this conversation. Do not call tools or invent evidence.",
            interaction_id, final_only=True)
        return self._final_text(response)

    def _final_text(self, response) -> str:
        if self._tool_calls(response):
            raise RuntimeError("Gemini requested more tools during the final answer step")
        return self._response_text(response)

    def _final_answer(self, input_value: Any, previous_id: str | None) -> ResearchResponse:
        # Deliver the last tool results before requesting a bounded final answer.
        response = self._generate(input_value, previous_id, final_only=True)
        return self._parse_final_response(self._final_text(response))

    def _function_result(self, call, seen_calls: set[str]) -> dict[str, Any]:
        name = str(getattr(call, "name", ""))
        result = self._call_result(call, name, seen_calls)
        return {"type": "function_result", "name": name,
                "call_id": str(getattr(call, "id", "")),
                "result": [{"type": "text", "text": json.dumps(result)}]}

    def _call_result(self, call, name: str, seen_calls: set[str]) -> dict[str, Any]:
        try:
            args = self._tool_arguments(call)
        except (json.JSONDecodeError, ValueError) as exc:
            return {"ok": False, "error": str(exc) or "invalid_tool_arguments"}
        signature = json.dumps({"name": name, "args": args}, sort_keys=True, default=str)
        result = ({"ok": False, "error": "repeated_unproductive_tool_call"}
                  if signature in seen_calls else self._execute_tool(name, args))
        seen_calls.add(signature)
        return result

    def _tool_arguments(self, call) -> dict[str, Any]:
        args = getattr(call, "arguments", {})
        if isinstance(args, str):
            args = json.loads(args)
        if not isinstance(args, dict):
            raise ValueError("tool_arguments_must_be_an_object")
        return args

    def _generate(self, input_value: Any, previous_interaction_id: str | None, final_only: bool = False):
        for attempt in range(5):
            try:
                return self.client.interactions.create(**self._generation_request(input_value, previous_interaction_id, final_only))
            except Exception as exc:
                if not self._is_transient_provider_error(exc):
                    raise
                last_error = exc
                self._wait_for_provider_retry(attempt, exc)
        raise RuntimeError(f"Gemini request failed after retry: {last_error}") from last_error

    def _generation_request(self, input_value: Any, previous_id: str | None, final_only: bool) -> dict[str, Any]:
        request = {"model": self.model, "input": input_value,
                   "system_instruction": self.system_instruction, "tools": self._function_declarations()}
        if final_only:
            self._configure_final_request(request)
        if previous_id:
            request["previous_interaction_id"] = previous_id
        return request

    def _configure_final_request(self, request: dict[str, Any]) -> None:
        request["generation_config"] = {"tool_choice": "none"}
        request["response_format"] = {"type": "text", "mime_type": "application/json",
                                      "schema": self._final_schema()}
        request["system_instruction"] += (
            "\nResearch is complete. Return the final JSON now using only "
            "retrieved evidence. Preserve supported findings and explain any unanswered "
            "parts in limitations. Do not invent missing values or calculations.")

    def _final_schema(self) -> dict[str, Any]:
        strings = {"type": "array", "items": {"type": "string"}}
        claim = {"type": "object", "properties": {
            "text": {"type": "string"}, "evidence_ids": strings,
            "calculation_id": {"type": "string"}}, "required": ["text", "evidence_ids"]}
        return {"type": "object", "properties": {
            "claims": {"type": "array", "items": claim},
            "calculation_ids": strings, "limitations": strings},
            "required": ["claims", "calculation_ids", "limitations"]}

    def _wait_for_provider_retry(self, attempt: int, exc: Exception) -> None:
        if attempt < 4:
            delay = 2 ** (attempt + 1) + random.uniform(0, 1)
            LOGGER.warning("Transient Gemini request failure on attempt %s; retrying in %ss (%s)",
                           attempt + 1, delay, type(exc).__name__)
            time.sleep(delay)

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
            result = self._attempt_tool(name, handler, arguments, attempt)
            if result is not None:
                return result

    def _attempt_tool(self, name: str, handler: ToolHandler, arguments: dict[str, Any], attempt: int):
        try:
            cleaned = {key: value for key, value in arguments.items() if value is not None}
            return {"ok": True, "result": self._serialize(handler(**cleaned))}
        except (EvidenceError, KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("Tool %s rejected invalid input (%s)", name, type(exc).__name__)
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return self._tool_failure(name, exc, attempt)

    def _tool_failure(self, name: str, exc: Exception, attempt: int):
        if self._is_transient_provider_error(exc) and attempt < 2:
            time.sleep(2 ** attempt)
            return None
        LOGGER.error("Tool %s failed (%s)", name, type(exc).__name__)
        return {"ok": False, "error": f"tool_failure:{name}:{type(exc).__name__}"}

    def _parse_final_response(self, raw: str) -> ResearchResponse:
        data = self._load_final_data(raw)
        self._validate_final_fields(data)
        limitations, calculation_ids = data.get("limitations", []), data.get("calculation_ids", [])
        claims = tuple(self._build_claim(item) for item in data.get("claims", []))
        if not claims and (not limitations or calculation_ids):
            raise RuntimeError("A response without claims must explain unavailable information and omit calculations")
        claims = self._supported_claims(claims, limitations)
        self._validate_calculation_links(claims, calculation_ids)
        return ResearchResponse(claims, tuple(calculation_ids), tuple(limitations))

    def _load_final_data(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini did not return the required structured JSON response") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Gemini structured response must be a JSON object")
        return data

    def _validate_final_fields(self, data: dict[str, Any]) -> None:
        for field in ("claims", "calculation_ids", "limitations"):
            if not isinstance(data.get(field, []), list):
                raise RuntimeError(f"Gemini response field {field} must be a list")
        if any(self._malformed_claim(item) for item in data.get("claims", [])):
            raise RuntimeError("Gemini response contains a malformed claim")
        values = data.get("limitations", []) + data.get("calculation_ids", [])
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise RuntimeError("Gemini limitations and calculation IDs must be nonempty strings")

    def _malformed_claim(self, item: Any) -> bool:
        return (not isinstance(item, dict) or not isinstance(item.get("text"), str)
                or not item["text"].strip() or not isinstance(item.get("evidence_ids"), list)
                or any(not isinstance(value, str) for value in item["evidence_ids"]))

    def _build_claim(self, item: dict[str, Any]) -> Claim:
        evidence_ids = tuple(dict.fromkeys(
            self.registry.resolve_evidence_id(reference) for reference in item["evidence_ids"]))
        return Claim(item["text"], evidence_ids, item.get("calculation_id"))

    def _supported_claims(self, claims: tuple[Claim, ...], limitations: list[str]) -> tuple[Claim, ...]:
        supported = tuple(claim for claim in claims if self._claim_is_supported(claim))
        if len(supported) != len(claims):
            limitations.append("Some narrative claims were omitted because their text did not match "
                               "the cited source excerpts; those parts of the answer remain unverified.")
        return supported

    def _claim_is_supported(self, claim: Claim) -> bool:
        if not claim.evidence_ids:
            raise RuntimeError("Each final factual claim must reference evidence")
        self.registry.validate_evidence_ids(claim.evidence_ids)
        if claim.calculation_id is not None:
            self._validate_calculation_claim(claim)
        elif not self._matches_excerpts(claim):
            LOGGER.warning("Omitting a narrative claim that does not match its cited excerpts")
            return False
        return True

    def _validate_calculation_claim(self, claim: Claim) -> None:
        if not isinstance(claim.calculation_id, str):
            raise RuntimeError("Claim calculation_id must be a string")
        sources = self.registry.calculation_sources(claim.calculation_id)
        if set(claim.evidence_ids) != sources:
            raise RuntimeError("Calculation claim must cite its exact input evidence")

    def _matches_excerpts(self, claim: Claim) -> bool:
        text = " ".join(claim.text.split()).casefold()
        return all(text in " ".join(self.registry.get(identifier).excerpt.split()).casefold()
                   for identifier in claim.evidence_ids)

    def _validate_calculation_links(self, claims: tuple[Claim, ...], calculation_ids: list[str]) -> None:
        for calculation_id in calculation_ids:
            self.registry.get_calculation(calculation_id)
        linked = {claim.calculation_id for claim in claims if claim.calculation_id}
        if set(calculation_ids) != linked:
            raise RuntimeError("Every calculation must be linked to a claim")

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
        return [
            GeminiResearchAgent._search_declaration(
                "search_documents", "Search the locally indexed Apple 10-K for cited evidence."),
            GeminiResearchAgent._search_declaration(
                "search_web", "Search current web sources with Tavily when the filing cannot answer the question."),
            GeminiResearchAgent._calculation_declaration()]

    @staticmethod
    def _search_declaration(name: str, description: str) -> dict[str, Any]:
        return {"type": "function", "name": name, "description": description,
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}

    @staticmethod
    def _calculation_declaration() -> dict[str, Any]:
        properties = {name: {"type": "string"} for name in (
            "numerator_fact_id", "denominator_fact_id", "revenue_fact_id", "cost_fact_id",
            "left_reference", "right_reference")}
        properties["operation"] = {"type": "string", "enum": [
            "percentage", "gross_margin_percentage", "percentage_point_difference"]}
        return {"type": "function", "name": "calculate_financial_metric",
                "description": "Calculate only with evidence fact IDs or prior calculation IDs returned by tools.",
                "parameters": {"type": "object", "properties": properties, "required": ["operation"]}}


def format_response(response: ResearchResponse, registry: EvidenceRegistry) -> str:
    citations = [identifier for claim in response.claims for identifier in claim.evidence_ids]
    lines = [_format_claim(claim, registry) for claim in response.claims]
    lines = lines or ["The requested information is not available from the retrieved evidence."]
    if response.limitations:
        lines.extend(["", "Limitations:", *[f"- {item}" for item in response.limitations]])
    if citations:
        lines.extend(["", "Sources:"])
    lines.extend(_format_source(source) for source in registry.sources_for(citations))
    return "\n".join(lines)


def _format_claim(claim: Claim, registry: EvidenceRegistry) -> str:
    text = _format_calculation(registry.get_calculation(claim.calculation_id)) if claim.calculation_id else claim.text
    return f"- {text} {' '.join(f'[{item}]' for item in claim.evidence_ids)}"


def _format_calculation(calculation: Calculation) -> str:
    unit = 'percentage points' if calculation.operation == 'percentage_point_difference' else '%'
    periods = ', '.join(calculation.reporting_periods)
    text = (f"[{calculation.calculation_id}] {calculation.operation}: {calculation.value} {unit} "
            f"({calculation.formula}; reporting periods: {periods})")
    if len(set(calculation.reporting_periods)) > 1:
        text += '; comparison uses different reporting periods'
    if 'unknown' in calculation.reporting_periods:
        text += '; reporting period is unverified'
    return text


def _format_source(source) -> str:
    location = f"{source.title}, {source.source_locator}"
    if source.page_number:
        location += f", p. {source.page_number}"
    return f"- [{source.evidence_id}] {location} ? {source.url or 'local filing'}"
