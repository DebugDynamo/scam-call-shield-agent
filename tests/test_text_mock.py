from conftest import new_session, say


def test_mock_conversation_calculator_and_tasks(client):
    s = new_session(client)
    assert s["status"] == "created" and s["simulated"] is True
    assert s["providers"] == {"llm": "mock", "stt": None, "tts": None}

    r = say(client, s["id"], "calculate 12 * (3 + 4)")
    assert r["simulated"] is True and r["provider"]["name"] == "mock"
    assert r["toolCalls"][0]["tool"] == "calculator" and r["toolCalls"][0]["status"] == "ok"
    assert "84" in r["reply"]["text"]

    r = say(client, s["id"], "create task buy milk")
    task_id = r["toolCalls"][0]["result"]["task"]["id"]
    assert "buy milk" in r["reply"]["text"]

    detail = client.get(f"/api/v1/sessions/{s['id']}").json()
    assert detail["status"] == "active"
    assert [m["role"] for m in detail["messages"]] == ["user", "tool", "assistant", "user", "tool", "assistant"]
    assert [t["tool"] for t in detail["toolTrace"]] == ["calculator", "create_task"]

    r = say(client, s["id"], f"get task {task_id}")
    assert r["toolCalls"][0]["result"]["task"]["title"] == "buy milk"


def test_plain_chat_has_no_tool_calls(client):
    s = new_session(client)
    r = say(client, s["id"], "hello there")
    assert r["toolCalls"] == [] and r["reply"]["role"] == "assistant"


def test_validation_and_state_errors(client):
    s = new_session(client)
    r = client.post(f"/api/v1/sessions/{s['id']}/messages", json={"text": ""})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert client.get("/api/v1/sessions/not-a-uuid").status_code == 404
    assert client.get("/api/v1/sessions/6f1c2f0e-3b7a-4a55-9d3e-0a1b2c3d4e5f").json()["error"]["code"] == "NOT_FOUND"
    client.post(f"/api/v1/sessions/{s['id']}/end")
    r = client.post(f"/api/v1/sessions/{s['id']}/messages", json={"text": "hi"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"


def test_end_is_idempotent_and_listing_paginates(client):
    ids = [new_session(client)["id"] for _ in range(3)]
    assert client.post(f"/api/v1/sessions/{ids[0]}/end").json()["status"] == "ended"
    again = client.post(f"/api/v1/sessions/{ids[0]}/end").json()
    assert again["status"] == "ended" and again["endedAt"]
    page = client.get("/api/v1/sessions?page=1&pageSize=2").json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["pageSize"] == 2
    assert client.get("/api/v1/sessions?pageSize=500").status_code == 400


def test_text_message_on_browser_session_rejected(client):
    s = new_session(client, "browser")
    r = client.post(f"/api/v1/sessions/{s['id']}/messages", json={"text": "hi"})
    assert r.status_code == 409


def test_system_prompt_override_and_room_mode(client):
    s = new_session(client, agent={"name": "x", "systemPrompt": "Be terse."})
    assert s["id"]
    r = client.post("/api/v1/sessions", json={"mode": "room"})
    assert r.status_code == 501 and r.json()["error"]["code"] == "NOT_IMPLEMENTED"
