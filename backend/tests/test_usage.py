from app.core.config import settings
from app.services import usage


def test_totals_sum_across_calls():
    turn = usage.start()
    usage.record("grade", "claude-sonnet-5", 1000, 10)
    usage.record("generate", "claude-sonnet-5", 5000, 900)
    payload = turn.as_payload()
    assert payload["input_tokens"] == 6000
    assert payload["output_tokens"] == 910
    assert payload["total_tokens"] == 6910


def test_cost_uses_the_configured_rate():
    turn = usage.start()
    usage.record("generate", "claude-sonnet-5", 1_000_000, 1_000_000)
    # $3 in + $15 out per million
    assert turn.as_payload()["cost_usd"] == 18.0


def test_unpriced_model_reports_tokens_without_inventing_a_cost():
    turn = usage.start()
    usage.record("generate", "some-local-model", 500, 100)
    payload = turn.as_payload()
    assert payload["total_tokens"] == 600
    assert payload["cost_usd"] is None
    assert payload["priced"] is False


def test_partly_priced_turn_is_flagged():
    turn = usage.start()
    usage.record("grade", "claude-sonnet-5", 1_000_000, 0)
    usage.record("rerank", "mystery-model", 100, 100)
    payload = turn.as_payload()
    assert payload["cost_usd"] == 3.0
    assert payload["priced"] is False


def test_embedding_tokens_are_counted_locally(monkeypatch):
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
    turn = usage.start()
    usage.record_embedding("embed_query", "the Cauchy-Schwarz inequality")
    call = turn.calls[0]
    assert call.input_tokens > 0
    assert call.output_tokens == 0


def test_recording_outside_a_turn_is_a_no_op():
    usage._ledger.set(None)
    usage.record("generate", "claude-sonnet-5", 100, 100)
    assert usage.current() is None


def test_flat_priced_reranker_adds_cost_without_tokens():
    turn = usage.start()
    usage.record_flat("rerank", "rerank-v3.5", 0.002)
    payload = turn.as_payload()
    assert payload["total_tokens"] == 0
    assert payload["cost_usd"] == 0.002


def test_environment_prices_merge_into_the_defaults(monkeypatch):
    monkeypatch.setenv("MODEL_PRICES", '{"my-model": [1.0, 2.0]}')
    from app.core.config import Settings

    merged = Settings().model_prices
    assert merged["my-model"] == (1.0, 2.0)
    assert "claude-sonnet-5" in merged, "adding one model must not drop the defaults"
    assert merged["gpt-5.6-luna"] == (1.0, 6.0)
    assert merged["gpt-5.6-terra"] == (2.5, 15.0)
    assert merged["gpt-5.6-sol"] == (5.0, 30.0)
    assert merged["text-embedding-3-small"] == (0.02, 0.0)
