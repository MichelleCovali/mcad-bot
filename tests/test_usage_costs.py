"""Provider usage accounting checks use synthetic responses and no credentials."""
from copy import deepcopy

import pytest

from mcad.ai_monitor import _usage, get_monitor_status, run_dict, update_monitor_settings
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import AIRun


RATES = {
    "discoveryInput": 0.1,
    "discoveryOutput": 0.5,
    "verificationInput": 2,
    "verificationOutput": 10,
    "webCalls": 10,
}


@pytest.fixture
def usage_database():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    migrate(engine)
    try:
        yield get_session_factory(engine)
    finally:
        engine.dispose()


def record_usage(factory, rates, usage, actions=("search", "open_page", "find_in_page"),
                 *, statuses=None, provider_requests="auto"):
    with factory.begin() as session:
        run = AIRun()
        session.add(run)
        session.flush()
        run_id = run.id
    response = {
        "id": "response_usage_fixture",
        "model": "gpt-6.1-sol",
        "service_tier": "default",
        "usage": usage,
        "output": [{"type": "web_search_call", "action": {"type": action},
                    "status": statuses[index] if statuses else "completed"} for index, action in enumerate(actions)],
    }
    if provider_requests == "auto":
        provider_requests = actions.count("search")
    if provider_requests is not None:
        response["tool_usage"] = {"web_search": {"num_requests": provider_requests}}
    _usage(factory, run_id, response, len(actions), stage="verification", model="gpt-6.1-sol", rates=rates)
    with factory() as session:
        run = session.get(AIRun, run_id)
        return deepcopy(run.stages[0]), run_dict(run)


def test_page_opens_and_find_actions_do_not_add_search_fees(usage_database):
    usage = {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
             "output_tokens_details": {"reasoning_tokens": 40}}
    stage, run = record_usage(usage_database, RATES, usage)
    assert stage["usage"] == usage
    assert stage["responseModel"] == "gpt-6.1-sol"
    assert stage["serviceTier"] == "default"
    assert stage["toolUsage"] == {"web_search": {"num_requests": 1}}
    assert run["webSearchCalls"] == 3
    assert run["billableSearchCalls"] == 1
    assert run["estimatedUsd"] == 0.013
    assert run["estimateBasis"] == ["configured_rates"]


def test_cache_reads_and_each_write_lifetime_are_priced_once(usage_database):
    rates = {**RATES, "verificationCachedInput": 0.1,
             "verificationCacheWrite30m": 2.5, "verificationCacheWrite12h": 5}
    usage = {"input_tokens": 1000, "output_tokens": 100,
             "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 150,
                 "cache_write_tokens_details": {"ttl_30m_tokens": 100, "ttl_12h_tokens": 50}}}
    stage, run = record_usage(usage_database, rates, usage)
    assert stage["costRates"] == rates
    assert run["estimatedUsd"] == 0.01168


def test_missing_cached_rate_keeps_a_labelled_full_input_estimate(usage_database):
    usage = {"input_tokens": 1000, "output_tokens": 100,
             "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 0}}
    _, conservative = record_usage(usage_database, RATES, usage)
    _, configured = record_usage(usage_database, {**RATES, "verificationCachedInput": 0.1}, usage)
    assert conservative["estimatedUsd"] == 0.013
    assert conservative["estimateBasis"] == ["full_input_rate_for_cached_tokens"]
    assert configured["estimatedUsd"] == 0.01148


@pytest.mark.parametrize("usage, basis", [
    ({"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {
        "cache_write_tokens": 500,
        "cache_write_tokens_details": {"ttl_30m_tokens": 500, "ttl_12h_tokens": 0}}},
     "cache_write_rate_unavailable"),
    ({"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {"cache_write_tokens": 500}},
     "cache_write_lifetime_unavailable"),
    ({"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {"cached_tokens": 1001}},
     "token_details_invalid"),
    ({"input_tokens": 1000}, "token_usage_unavailable"),
])
def test_incomplete_usage_does_not_masquerade_as_a_complete_cost(usage_database, usage, basis):
    stage, run = record_usage(usage_database, RATES, usage)
    assert run["estimatedUsd"] is None
    assert stage["estimateBasis"] == basis
    assert stage["usage"] == usage


def test_unknown_search_action_is_not_counted_as_free(usage_database):
    stage, run = record_usage(usage_database, RATES,
        {"input_tokens": 100, "output_tokens": 40}, actions=("future_action",), provider_requests=None)
    assert run["webSearchCalls"] == 1
    assert run["billableSearchCalls"] is None
    assert stage["estimatedMicros"] is None
    assert stage["estimateBasis"] == "search_actions_unavailable"


def test_provider_billable_count_excludes_an_unfinished_search_item(usage_database):
    actions = ("search", "search", "search", "search", "open_page", "search")
    statuses = ("completed", "completed", "completed", "completed", "completed", "searching")
    stage, run = record_usage(usage_database, RATES,
        {"input_tokens": 1000, "output_tokens": 100}, actions=actions, statuses=statuses, provider_requests=4)
    assert run["webSearchCalls"] == 6
    assert run["billableSearchCalls"] == 4
    assert run["estimatedUsd"] == 0.043
    assert stage["billableSearchBasis"] == "provider_tool_usage"
    assert stage["webActionCounts"] == {"search": 5, "open_page": 1}
    assert stage["webActionStatusCounts"] == {"completed": 5, "searching": 1}
    assert stage["incompleteResearchCount"] == 1


def test_completed_search_fallback_requires_complete_action_statuses(usage_database):
    usage = {"input_tokens": 1000, "output_tokens": 100}
    stage, run = record_usage(usage_database, RATES, usage, provider_requests=None)
    assert run["billableSearchCalls"] == 1
    assert stage["billableSearchBasis"] == "completed_search_actions"
    pending_stage, pending_run = record_usage(usage_database, RATES, usage,
        actions=("search", "search"), statuses=("completed", "searching"), provider_requests=None)
    assert pending_run["estimatedUsd"] is None
    assert pending_stage["incompleteResearchCount"] == 1
    assert pending_stage["billableSearchCalls"] is None


def test_optional_cache_rates_preserve_existing_five_rate_contract(usage_database):
    settings = Settings(enable_workers=False)
    assert update_monitor_settings(usage_database, settings, {"costRates": RATES})["costRates"] == RATES
    extended = {**RATES, "verificationCachedInput": 0.1, "discoveryCacheWrite30m": 0.125}
    assert update_monitor_settings(usage_database, settings, {"costRates": extended})["costRates"] == extended
    for rates in ({"verificationCachedInput": 0.1}, {**RATES, "unknownRate": 1}):
        with pytest.raises(ValueError):
            update_monitor_settings(usage_database, settings, {"costRates": rates})
    assert get_monitor_status(usage_database, settings)["effectiveEnabled"] is False
