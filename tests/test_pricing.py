from agent_vm_observability.model import NormalizedTrace
from agent_vm_observability.pricing import apply_cost_estimate, estimate_cost, pricing_rule_for


def test_gpt_55_pricing_rule_estimates_cost() -> None:
    rule = pricing_rule_for("gpt-5.5", "openai")

    assert rule is not None
    assert rule.input_per_mtok_usd == 5.0
    assert rule.cached_input_per_mtok_usd == 0.5
    assert rule.output_per_mtok_usd == 30.0

    cost = estimate_cost(
        "gpt-5.5",
        "openai",
        {"input_tokens": 1000, "cache_read_input_tokens": 2000, "output_tokens": 300},
    )

    assert cost is not None
    assert round(cost["cost_usd"], 6) == 0.015


def test_apply_cost_estimate_adds_gpt_55_cost_measurements() -> None:
    trace = NormalizedTrace(
        agent="codex",
        kind="codex.sse_event",
        model="gpt-5.5",
        token_usage={"input_tokens": 1000, "cache_read_input_tokens": 2000, "output_tokens": 300},
    )

    apply_cost_estimate(trace)

    assert trace.provider == "openai"
    assert trace.measurements["total_tokens"] == 3300
    assert trace.measurements["cost_usd"] == 0.015
    assert trace.measurements["input_cost_usd"] == 0.005
    assert trace.measurements["cache_read_cost_usd"] == 0.001
    assert trace.measurements["output_cost_usd"] == 0.009
    assert trace.tags["pricing_model"] == "gpt-5.5"
