from intelligence.storage import WorkerAPIClient
import json
import urllib.error
from unittest.mock import patch


def recorder(client):
    calls = []

    def request(method, path, body=None, headers=None, authenticated=True):
        calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": headers,
                "authenticated": authenticated,
            }
        )
        return {"ok": True}

    client._request = request
    return calls


def test_all_worker_read_routes_use_expected_query_contracts():
    client = WorkerAPIClient("https://worker.example", "token")
    calls = recorder(client)

    client.get_due_channels(limit=7, now="2026-09-06T00:00:00Z", target_id="target")
    client.get_pending_analysis(limit=8, target_id="target", channel_id="channel")
    client.get_report_input(
        window_start="2026-09-05T00:00:00Z",
        window_end="2026-09-06T00:00:00Z",
        min_importance=4,
        tag="mcp",
    )
    client.list_audit_events(entity_type="channel", entity_id="one", limit=9)

    assert calls[0]["path"].startswith("/v1/channels/due?")
    assert "target_id=target" in calls[0]["path"]
    assert calls[1]["path"].startswith("/v1/items/pending-analysis?")
    assert "channel_id=channel" in calls[1]["path"]
    assert calls[2]["path"].startswith("/v1/reports/input?")
    assert "min_importance=4" in calls[2]["path"]
    assert calls[3]["path"].startswith("/v1/audit-events?")


def test_all_worker_write_routes_set_idempotency_keys():
    client = WorkerAPIClient("https://worker.example", "token")
    calls = recorder(client)

    client.write_items([], idempotency_key="items", channel_state={"channel_id": "c"})
    client.write_analyses([], idempotency_key="analyses")
    client.create_report({"id": "r"}, idempotency_key="report")
    client.update_report_status("r/one", "ready", idempotency_key="status")
    client.create_run({"id": "run"}, idempotency_key="run")
    client.update_run("run/one", {"run_status": "failed"}, idempotency_key="update")
    client.create_audit_event({"id": "audit"}, idempotency_key="audit")

    assert [call["headers"]["Idempotency-Key"] for call in calls] == [
        "items",
        "analyses",
        "report",
        "status",
        "run",
        "update",
        "audit",
    ]
    assert calls[3]["path"] == "/v1/reports/r%2Fone/status"
    assert calls[5]["path"] == "/v1/runs/run%2Fone"


def test_transient_write_retries_identical_request_and_idempotency_key():
    from unittest.mock import MagicMock
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"upserted":1}'
    with patch("urllib.request.urlopen", side_effect=[urllib.error.URLError("temporary EOF"), response]) as call, patch("time.sleep"):
        result = WorkerAPIClient("https://worker.example", "token").write_analyses([], idempotency_key="stable")
    assert result["upserted"] == 1
    first, second = [c.args[0] for c in call.call_args_list]
    assert first is second
    assert first.get_header("Idempotency-key") == "stable"
