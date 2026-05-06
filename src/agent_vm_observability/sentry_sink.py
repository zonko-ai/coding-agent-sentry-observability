from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from . import VERSION
from .config import RuntimeConfig
from .model import NormalizedTrace, normalize_level
from .redaction import safe_tag_value, scrub
from .timeutil import to_timestamp


@dataclass
class CapturedTrace:
    title: str
    tags: dict[str, str]
    measurements: dict[str, int | float]


USAGE_SCHEMA = "llm_usage_v9"
USAGE_MEASUREMENT_KEYS = ("input_tokens", "output_tokens", "total_tokens", "cost_usd")


class SentrySink:
    def __init__(self, config: RuntimeConfig, dry_run: bool = False, local_only: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        self.local_only = local_only
        self.enabled = False
        self.captured: list[CapturedTrace] = []
        self._sentry_sdk: Any = None

    def configure(self) -> bool:
        if self.enabled and self._sentry_sdk is not None:
            return True
        if self.dry_run or self.local_only:
            self.enabled = False
            return True
        if not self.config.sentry_dsn:
            self.enabled = False
            return False
        import sentry_sdk

        sentry_sdk.init(
            dsn=self.config.sentry_dsn,
            environment=os.environ.get("SENTRY_ENVIRONMENT", "local-vm"),
            release=f"coding-agent-sentry-observability@{VERSION}",
            traces_sample_rate=self.config.traces_sample_rate,
            transport_queue_size=10000,
            send_default_pii=False,
            server_name=os.environ.get("AGENT_VM_SENTRY_SERVER_NAME", "local-vm"),
            before_send=lambda event, hint: scrub(event),
            before_send_transaction=lambda event, hint: scrub(event),
        )
        sentry_sdk.set_tag("agent_vm_observability.version", VERSION)
        if os.environ.get("AGENT_VM_SENTRY_SET_USER", "").strip().lower() in {"1", "true", "yes", "on"}:
            sentry_sdk.set_user({"id": os.environ.get("USER", "local-user")})
        self._sentry_sdk = sentry_sdk
        self.enabled = True
        return True

    def capture(self, trace: NormalizedTrace) -> None:
        tags = trace.sentry_tags()
        measurements = trace.all_measurements()
        if _has_usage_measurements(measurements) or _is_session_trace(trace):
            tags["usage_schema"] = USAGE_SCHEMA
            tags["usage_canonical"] = "true"
            usage_rollup = trace.tags.get("usage_rollup")
            tags["usage_rollup"] = safe_tag_value(usage_rollup) if isinstance(usage_rollup, str) and usage_rollup else "event"
            usage_model = trace.model or trace.tags.get("usage_model")
            if isinstance(usage_model, str) and usage_model:
                tags["usage_model"] = safe_tag_value(usage_model)
        if self.dry_run or len(self.captured) < 1000:
            self.captured.append(CapturedTrace(trace.title, tags, measurements))
        if self.dry_run:
            print(f"dry-run capture {trace.title} tags={tags} measurements={measurements}", flush=True)
            return
        if self.local_only:
            return
        if not self.enabled or self._sentry_sdk is None:
            return

        event: dict[str, Any] = {
            "message": trace.title,
            "level": normalize_level(trace.level),
            "tags": tags,
            "extra": trace.sentry_extra(),
            "contexts": {
                "agent_usage": {
                    "source": trace.sentry_source,
                    "agent": trace.agent,
                    "kind": trace.kind,
                    "measurements": measurements,
                    "trace_id": trace.trace_id,
                    "span_id": trace.span_id,
                    "parent_span_id": trace.parent_span_id,
                }
            },
        }
        ts = to_timestamp(trace.timestamp)
        if ts:
            event["timestamp"] = ts
        self._sentry_sdk.capture_event(event)

        start_timestamp = trace.timestamp
        transaction_kwargs: dict[str, Any] = {"name": trace.title, "op": _transaction_op(trace)}
        if start_timestamp is not None:
            transaction_kwargs["start_timestamp"] = start_timestamp
        transaction = self._sentry_sdk.start_transaction(**transaction_kwargs)
        for key, value in tags.items():
            transaction.set_tag(key, value)
        for key, value in measurements.items():
            if hasattr(transaction, "set_measurement"):
                transaction.set_measurement(_measurement_name(key), float(value))
        for key, value in _gen_ai_attributes(trace, measurements).items():
            transaction.set_data(key, value)
        transaction.set_data("agent_measurements", measurements)
        transaction.set_data("event_timestamp", ts)
        transaction.set_data("source_event_id", trace.stable_event_id())
        end_timestamp = None
        if trace.timestamp is not None:
            duration_ms = trace.duration_ms if trace.duration_ms is not None else 1.0
            end_timestamp = trace.timestamp + timedelta(milliseconds=max(float(duration_ms), 1.0))
        transaction.finish(end_timestamp=end_timestamp)

    def capture_exception(self, exc: BaseException) -> None:
        if self.enabled and self._sentry_sdk is not None:
            self._sentry_sdk.capture_exception(exc)

    def flush(self, timeout: int = 30) -> None:
        if self.enabled and self._sentry_sdk is not None:
            self._sentry_sdk.flush(timeout=timeout)


def _measurement_name(key: str) -> str:
    return key.replace(".", "_").replace("-", "_")


def _has_usage_measurements(measurements: dict[str, int | float]) -> bool:
    return any(key in measurements for key in USAGE_MEASUREMENT_KEYS)


def _is_session_trace(trace: NormalizedTrace) -> bool:
    return trace.tags.get("usage_rollup") == "session" or trace.kind.startswith("session_v")


def _transaction_op(trace: NormalizedTrace) -> str:
    if trace.tool_name:
        return "gen_ai.execute_tool"
    if _is_session_trace(trace):
        return "gen_ai.agent.session"
    measurements = trace.all_measurements()
    if _has_usage_measurements(measurements):
        return "gen_ai.invoke_agent"
    return f"agent.{trace.agent}.{trace.kind}"


def _gen_ai_attributes(trace: NormalizedTrace, measurements: dict[str, int | float]) -> dict[str, Any]:
    data: dict[str, Any] = {"gen_ai.agent.name": trace.agent}
    if trace.provider:
        data["gen_ai.system"] = trace.provider
    has_usage = _has_usage_measurements(measurements)
    if trace.tool_name:
        data["gen_ai.tool.name"] = trace.tool_name
        data["gen_ai.operation.name"] = "execute_tool"
    elif _is_session_trace(trace):
        data["gen_ai.operation.name"] = "agent_session"
    elif has_usage:
        data["gen_ai.operation.name"] = "invoke_agent"
    else:
        data["gen_ai.operation.name"] = trace.kind

    input_tokens_total = int(
        measurements.get("input_tokens_total")
        or (
            float(measurements.get("input_tokens") or 0)
            + float(measurements.get("cache_read_input_tokens") or 0)
            + float(measurements.get("cache_creation_input_tokens") or 0)
        )
    )
    if input_tokens_total:
        data["gen_ai.usage.input_tokens"] = input_tokens_total
    cached_input_tokens = int(measurements.get("cache_read_input_tokens") or 0)
    if cached_input_tokens:
        data["gen_ai.usage.input_tokens.cached"] = cached_input_tokens
    cache_write_tokens = int(
        measurements.get("cache_creation_input_tokens")
        or (
            float(measurements.get("cache_creation_5m_input_tokens") or 0)
            + float(measurements.get("cache_creation_1h_input_tokens") or 0)
        )
    )
    if cache_write_tokens:
        data["gen_ai.usage.input_tokens.cache_write"] = cache_write_tokens
    output_tokens = int(measurements.get("output_tokens") or 0)
    if output_tokens:
        data["gen_ai.usage.output_tokens"] = output_tokens
    reasoning_tokens = int(measurements.get("reasoning_tokens") or 0)
    if reasoning_tokens:
        data["gen_ai.usage.output_tokens.reasoning"] = reasoning_tokens
    total_tokens = int(measurements.get("total_tokens") or 0)
    if total_tokens:
        data["gen_ai.usage.total_tokens"] = total_tokens
    input_cost_usd = float(measurements.get("input_cost_usd") or 0)
    if input_cost_usd:
        data["gen_ai.cost.input_tokens"] = input_cost_usd
    output_cost_usd = float(measurements.get("output_cost_usd") or 0)
    if output_cost_usd:
        data["gen_ai.cost.output_tokens"] = output_cost_usd
    cost_usd = float(measurements.get("cost_usd") or 0)
    if cost_usd:
        data["gen_ai.cost.total_tokens"] = cost_usd
    return data
