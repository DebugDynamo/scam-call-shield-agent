from conftest import client_for


def test_health_ready_shape(client):
    h = client.get("/health").json()
    assert set(h) == {"status", "service", "version", "dataMode", "time"} and h["dataMode"] == "mock" and h["service"] == "repo-voice-ai"
    r = client.get("/ready")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ready" and j["checks"]["database"]["status"] == "skipped"


def test_request_id_header_honoured_and_in_errors(client):
    r = client.get("/api/v1/sessions/nope", headers={"X-Request-Id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    e = r.json()["error"]
    assert e["requestId"] == "abc-123" and e["code"] == "NOT_FOUND" and e["timestamp"].endswith("Z")
    assert client.get("/health").headers["x-request-id"]


def test_unknown_route_and_method_use_contract_error_shape(client):
    r = client.get("/api/v1/nothing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert client.delete("/health").json()["error"]["code"] in {"VALIDATION_FAILED", "NOT_FOUND"}


def test_cors_allow_list(client):
    ok = client.options("/api/v1/sessions", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/v1/sessions", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_api_key_protects_admin_and_outbound():
    with client_for(internal_api_key="k1") as c:
        assert c.post("/api/v1/admin/cleanup").status_code == 401
        assert c.post("/api/v1/admin/cleanup", headers={"X-Api-Key": "wrong"}).status_code == 401
        assert c.post("/api/v1/admin/cleanup", headers={"X-Api-Key": "k1"}).json() == {"ended": 0}
        r = c.post("/api/v1/telephony/outbound", json={"to": "+15551234567", "confirm": True})
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
        # read/execute routes remain open per contract
        assert c.post("/api/v1/sessions", json={"mode": "text"}).status_code == 201


def test_openapi_documents_contract_routes(client):
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    for p in ["/api/v1/sessions", "/api/v1/sessions/{session_id}", "/api/v1/sessions/{session_id}/messages",
              "/api/v1/sessions/{session_id}/end", "/api/v1/sessions/{session_id}/offer", "/api/v1/sessions/{session_id}/ice",
              "/api/v1/tools", "/api/v1/telephony/outbound", "/api/v1/telephony/plivo/answer", "/api/v1/telephony/plivo/hangup",
              "/api/v1/providers", "/health", "/ready"]:
        assert p in paths, p
    assert client.get("/docs").status_code == 200 and client.get("/docs-json").status_code == 200
