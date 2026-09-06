import json
import sys

import pytest

from intelligence.mcp.codex_bridge import _run_bounded, capture_batch, parse_capture


CALLS = [{"call_id": "article-1", "tool_name": "post_firecrawl_scrape",
          "arguments": {"url": "https://example.com", "formats": ["markdown"]}}]


def event(**changes):
    item = {"id": "item_1", "type": "mcp_tool_call", "server": "aisa-tools",
            "tool": CALLS[0]["tool_name"], "arguments": CALLS[0]["arguments"],
            "status": "completed", "result": {"content": [{"type": "text", "text": '{"success":true}'}]}}
    item.update(changes)
    return json.dumps({"type": "item.completed", "item": item})


def test_matches_actual_envelope_and_preserves_payload():
    result = parse_capture(event(), CALLS)
    assert result.payloads["article-1"]["content"][0]["text"] == '{"success":true}'
    assert result.diagnostics == {}


@pytest.mark.parametrize("changes", [
    {"type": "agent_message", "text": '{"success": true}'},
    {"server": "other-server"}, {"tool": "post_firecrawl_map"},
    {"arguments": {"url": "https://wrong.example"}},
    {"status": "failed"}, {"result": {"isError": True, "content": ["error"]}},
    {"result": {}},
])
def test_rejects_unproven_failed_mismatched_or_retyped_results(changes):
    result = parse_capture(event(**changes), CALLS)
    assert not result.payloads
    assert "article-1" in result.diagnostics


def test_serialized_arguments_supported():
    assert parse_capture(event(arguments=json.dumps(CALLS[0]["arguments"])), CALLS).payloads


def test_router_matches_call_id_tool_and_exact_arguments():
    args = {"search_id": "search", "calls": [{"call_id": "article-1", "tool": CALLS[0]["tool_name"], "arguments": CALLS[0]["arguments"]}]}
    data = {"success": True, "data": {"markdown": "Actual upstream article"}}
    result = {"structured_content": {"results": [{"call_id": "article-1", "tool": CALLS[0]["tool_name"], "successful": True, "data": data}]}}
    parsed = parse_capture(event(tool="AISA_BATCH_USE", arguments=args, result=result), CALLS)
    assert parsed.payloads == {"article-1": data}
    args["calls"][0]["arguments"] = {"url": "https://other.example"}
    assert not parse_capture(event(tool="AISA_BATCH_USE", arguments=args, result=result), CALLS).payloads


def test_router_rejects_failed_and_duplicate_result_rows():
    args = {"calls": [{"call_id": "article-1", "tool": CALLS[0]["tool_name"], "arguments": CALLS[0]["arguments"]}]}
    row = {"call_id": "article-1", "tool": CALLS[0]["tool_name"], "successful": True, "data": {"success": False}}
    result = {"structured_content": {"results": [row]}}
    assert not parse_capture(event(tool="AISA_BATCH_USE", arguments=args, result=result), CALLS).payloads
    row["data"] = {"success": True}
    result["structured_content"]["results"].append(row)
    assert not parse_capture(event(tool="AISA_BATCH_USE", arguments=args, result=result), CALLS).payloads


def test_rejects_mutating_tool_before_spawning():
    with pytest.raises(ValueError, match="permitted"):
        capture_batch([{**CALLS[0], "tool_name": "post_twitter_tweet"}])


def test_rejects_duplicate_correlations():
    with pytest.raises(ValueError, match="duplicate tool"):
        capture_batch([CALLS[0], {**CALLS[0], "call_id": "other"}])


def test_subprocess_uses_explicit_model_no_shell_and_never_returns_prose(monkeypatch):
    def run(argv, prompt, timeout, max_bytes):
        assert argv[argv.index("--model") + 1] == "gpt-5.6-terra"
        assert 'model_provider="openai"' in argv
        assert 'model_reasoning_effort="medium"' in argv
        assert "--json" in argv and "--ephemeral" in argv
        assert CALLS[0]["tool_name"] in prompt
        return json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}}), None
    monkeypatch.setattr("intelligence.mcp.codex_bridge._run_bounded", run)
    result = capture_batch(CALLS)
    assert result.diagnostics == {"article-1": "missing_native_tool_result"}


def test_bounded_process_timeout_and_output_limit():
    _, failure = _run_bounded([sys.executable, "-c", "import time; time.sleep(10)"], "", 0.05, 4096)
    assert failure == "capture_timeout"
    output, failure = _run_bounded([sys.executable, "-c", "print('x' * 100000)"], "", 2, 4096)
    assert failure == "capture_output_limit"
    assert len(output) <= 4096


def test_explicit_mcp_model_and_effort_reach_subprocess(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_MCP_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("INTELLIGENCE_MCP_REASONING_EFFORT", "low")
    def run(argv, *args):
        assert argv[argv.index("--model") + 1] == "gpt-5.6-luna"
        assert 'model_reasoning_effort="low"' in argv
        assert 'model_provider="openai"' in argv
        return event(), None
    monkeypatch.setattr("intelligence.mcp.codex_bridge._run_bounded", run)
    assert capture_batch(CALLS).payloads


def test_invalid_model_never_starts_subprocess(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_MCP_MODEL", "")
    def forbidden(*args):
        pytest.fail("invalid configuration must not start Codex")
    monkeypatch.setattr("intelligence.mcp.codex_bridge._run_bounded", forbidden)
    with pytest.raises(ValueError, match="INTELLIGENCE_MCP_MODEL"):
        capture_batch(CALLS)


def test_partial_success_kept_if_other_call_times_out(monkeypatch):
    monkeypatch.setattr("intelligence.mcp.codex_bridge._run_bounded", lambda *args: (event(), "capture_timeout"))
    calls = [*CALLS, {"call_id": "second", "tool_name": "post_firecrawl_map", "arguments": {"url": "https://example.com"}}]
    result = capture_batch(calls)
    assert "article-1" in result.payloads
    assert result.diagnostics == {"second": "capture_timeout"}
