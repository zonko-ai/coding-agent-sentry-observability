from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .model import NormalizedTrace


DEFAULT_PRICING_AS_OF = "2026-04-20"


@dataclass(frozen=True)
class PricingRule:
    name: str
    pattern: str
    provider: str | None
    input_per_mtok_usd: float
    output_per_mtok_usd: float
    cached_input_per_mtok_usd: float | None = None
    cache_write_5m_per_mtok_usd: float | None = None
    cache_write_1h_per_mtok_usd: float | None = None
    inferred: bool = False
    note: str | None = None
    source: str = "builtin"

    def matches(self, model: str | None, provider: str | None) -> bool:
        if not model:
            return False
        if self.provider and provider and provider != self.provider:
            return False
        return re.match(self.pattern, model) is not None


def infer_provider(provider: str | None, model: str | None) -> str | None:
    if provider:
        normalized = provider.strip().lower()
        if normalized:
            return normalized
    model_text = (model or "").strip().lower()
    if not model_text:
        return None
    if model_text.startswith("gpt-") or model_text.startswith("o"):
        return "openai"
    if model_text.startswith("claude"):
        return "anthropic"
    return None


def _builtin_rules() -> list[PricingRule]:
    anthropic_note = (
        "Anthropic base input/output rates are current. Cache read and cache write rates use the standard Anthropic family ratios; "
        "cache creation is priced exactly only when the logs expose 5m vs 1h cache writes."
    )
    return [
        PricingRule(
            name="gpt-5.4",
            pattern=r"^gpt-5\.4$",
            provider="openai",
            input_per_mtok_usd=2.50,
            cached_input_per_mtok_usd=0.25,
            output_per_mtok_usd=15.00,
            source="builtin-openai-2026-04-20",
            note="OpenAI standard API pricing.",
        ),
        PricingRule(
            name="gpt-5.4-mini",
            pattern=r"^gpt-5\.4-mini$",
            provider="openai",
            input_per_mtok_usd=0.75,
            cached_input_per_mtok_usd=0.075,
            output_per_mtok_usd=4.50,
            source="builtin-openai-2026-04-20",
            note="OpenAI standard API pricing.",
        ),
        PricingRule(
            name="gpt-5.4-nano",
            pattern=r"^gpt-5\.4-nano$",
            provider="openai",
            input_per_mtok_usd=0.20,
            cached_input_per_mtok_usd=0.02,
            output_per_mtok_usd=1.25,
            source="builtin-openai-2026-04-20",
            note="OpenAI standard API pricing.",
        ),
        PricingRule(
            name="claude-sonnet-4.5/4.6",
            pattern=r"^claude-sonnet-4-(?:5|6)(?:[-\w.]*)?$",
            provider="anthropic",
            input_per_mtok_usd=3.0,
            output_per_mtok_usd=15.0,
            cached_input_per_mtok_usd=0.30,
            cache_write_5m_per_mtok_usd=3.75,
            cache_write_1h_per_mtok_usd=6.0,
            source="builtin-anthropic-2026-04-20",
            inferred=True,
            note=anthropic_note,
        ),
        PricingRule(
            name="claude-opus-4.5/4.6/4.7",
            pattern=r"^claude-opus-4-(?:5|6|7)(?:[-\w.]*)?$",
            provider="anthropic",
            input_per_mtok_usd=5.0,
            output_per_mtok_usd=25.0,
            cached_input_per_mtok_usd=0.50,
            cache_write_5m_per_mtok_usd=6.25,
            cache_write_1h_per_mtok_usd=10.0,
            source="builtin-anthropic-2026-04-20",
            inferred=True,
            note=anthropic_note,
        ),
        PricingRule(
            name="claude-haiku-4.5",
            pattern=r"^claude-haiku-4-5(?:[-\w.]*)?$",
            provider="anthropic",
            input_per_mtok_usd=1.0,
            output_per_mtok_usd=5.0,
            cached_input_per_mtok_usd=0.10,
            cache_write_5m_per_mtok_usd=1.25,
            cache_write_1h_per_mtok_usd=2.0,
            source="builtin-anthropic-2026-04-20",
            inferred=True,
            note=anthropic_note,
        ),
    ]


def _rule_from_mapping(payload: dict[str, Any]) -> PricingRule:
    return PricingRule(
        name=str(payload["name"]),
        pattern=str(payload["pattern"]),
        provider=payload.get("provider"),
        input_per_mtok_usd=float(payload["input_per_mtok_usd"]),
        output_per_mtok_usd=float(payload["output_per_mtok_usd"]),
        cached_input_per_mtok_usd=_maybe_float(payload.get("cached_input_per_mtok_usd")),
        cache_write_5m_per_mtok_usd=_maybe_float(payload.get("cache_write_5m_per_mtok_usd")),
        cache_write_1h_per_mtok_usd=_maybe_float(payload.get("cache_write_1h_per_mtok_usd")),
        inferred=bool(payload.get("inferred", False)),
        note=payload.get("note"),
        source=str(payload.get("source", "env")),
    )


def _maybe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


@lru_cache(maxsize=1)
def pricing_rules() -> tuple[PricingRule, ...]:
    rules = list(_builtin_rules())
    file_path = os.environ.get("AGENT_VM_MODEL_PRICING_FILE")
    inline_json = os.environ.get("AGENT_VM_MODEL_PRICING_JSON")
    raw = None
    if file_path:
        path = Path(file_path).expanduser()
        if path.exists():
            raw = path.read_text()
    elif inline_json:
        raw = inline_json

    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = []
        if isinstance(payload, list):
            rules = [_rule_from_mapping(item) for item in payload if isinstance(item, dict)] + rules
    return tuple(rules)


def pricing_rule_for(model: str | None, provider: str | None) -> PricingRule | None:
    resolved_provider = infer_provider(provider, model)
    for rule in pricing_rules():
        if rule.matches(model, resolved_provider):
            return rule
    return None


def estimate_cost(model: str | None, provider: str | None, usage: dict[str, int | float]) -> dict[str, Any] | None:
    rule = pricing_rule_for(model, provider)
    if rule is None:
        return None

    input_tokens = float(usage.get("input_tokens") or 0)
    output_tokens = float(usage.get("output_tokens") or 0)
    cached_input_tokens = float(usage.get("cache_read_input_tokens") or 0)
    cache_write_5m_tokens = float(usage.get("cache_creation_5m_input_tokens") or 0)
    cache_write_1h_tokens = float(usage.get("cache_creation_1h_input_tokens") or 0)
    cache_creation_tokens = float(usage.get("cache_creation_input_tokens") or 0)

    input_cost = input_tokens * rule.input_per_mtok_usd / 1_000_000
    output_cost = output_tokens * rule.output_per_mtok_usd / 1_000_000
    cached_input_cost = 0.0
    if rule.cached_input_per_mtok_usd is not None:
        cached_input_cost = cached_input_tokens * rule.cached_input_per_mtok_usd / 1_000_000

    cache_write_cost = 0.0
    cache_write_basis = None
    if cache_write_5m_tokens and rule.cache_write_5m_per_mtok_usd is not None:
        cache_write_cost += cache_write_5m_tokens * rule.cache_write_5m_per_mtok_usd / 1_000_000
        cache_write_basis = "5m"
    if cache_write_1h_tokens and rule.cache_write_1h_per_mtok_usd is not None:
        cache_write_cost += cache_write_1h_tokens * rule.cache_write_1h_per_mtok_usd / 1_000_000
        cache_write_basis = "mixed" if cache_write_basis else "1h"
    if not cache_write_cost and cache_creation_tokens and rule.cache_write_5m_per_mtok_usd is not None:
        cache_write_cost += cache_creation_tokens * rule.cache_write_5m_per_mtok_usd / 1_000_000
        cache_write_basis = "assumed_5m"

    total = input_cost + output_cost + cached_input_cost + cache_write_cost
    if total <= 0:
        return None

    return {
        "cost_usd": total,
        "input_cost_usd": input_cost,
        "output_cost_usd": output_cost,
        "cache_read_cost_usd": cached_input_cost,
        "cache_write_cost_usd": cache_write_cost,
        "pricing_model": rule.name,
        "pricing_source": rule.source,
        "pricing_inferred": rule.inferred,
        "pricing_note": rule.note,
        "cache_write_basis": cache_write_basis,
    }


def apply_cost_estimate(trace: NormalizedTrace) -> None:
    provider = infer_provider(trace.provider, trace.model)
    if provider and provider != trace.provider:
        trace.provider = provider
    usage = trace.token_usage.copy()
    cache_creation_tokens = float(usage.get("cache_creation_input_tokens") or 0)
    if not cache_creation_tokens:
        cache_creation_tokens = float(usage.get("cache_creation_5m_input_tokens") or 0) + float(
            usage.get("cache_creation_1h_input_tokens") or 0
        )
    input_tokens_total = (
        float(usage.get("input_tokens") or 0)
        + float(usage.get("cache_read_input_tokens") or 0)
        + cache_creation_tokens
    )
    output_tokens = float(usage.get("output_tokens") or 0)
    total_tokens = input_tokens_total + output_tokens
    if input_tokens_total:
        trace.measurements.setdefault("input_tokens_total", int(input_tokens_total))
    if total_tokens:
        trace.measurements.setdefault("total_tokens", int(total_tokens))
    cost = estimate_cost(trace.model, trace.provider, usage)
    if not cost:
        return

    trace.measurements["cost_usd"] = round(float(cost["cost_usd"]), 8)
    for key in ("input_cost_usd", "output_cost_usd", "cache_read_cost_usd", "cache_write_cost_usd"):
        value = float(cost.get(key) or 0)
        if value:
            trace.measurements[key] = round(value, 8)
    trace.tags.setdefault("pricing_model", cost["pricing_model"])
    trace.tags.setdefault("pricing_source", cost["pricing_source"])
    if cost.get("pricing_inferred"):
        trace.tags.setdefault("pricing_inferred", "true")
    trace.extra["cost_estimate"] = {
        "as_of": DEFAULT_PRICING_AS_OF,
        **cost,
    }
