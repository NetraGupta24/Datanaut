import json
from types import SimpleNamespace

import groq
import httpx
import pytest

from app.llm import MAX_TOOL_ROUNDS, ChatEngine, build_messages
from app.main import app, get_chat_engine, limiter
from tests.conftest import SALES_CSV, upload


# --- a scripted stand-in for the Groq client --------------------------------


def tool_call(name, args, call_id="call_1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def reply(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeGroq:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Copy: the engine keeps appending to the same messages list
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_groq():
    return FakeGroq([])


@pytest.fixture
def chat_client(client, fake_groq):
    app.dependency_overrides[get_chat_engine] = lambda: ChatEngine(fake_groq, "test-model")
    limiter.reset()
    yield client
    limiter.reset()


def ask(client, dataset_id, message="Which region has the most revenue?", history=None):
    return client.post("/chat", json={"dataset_id": dataset_id, "message": message, "history": history or []})


# --- tests ------------------------------------------------------------------


def test_tool_call_then_answer_with_evidence(chat_client, fake_groq):
    ds_id = upload(chat_client, SALES_CSV).json()["id"]
    fake_groq.responses = [
        reply(tool_calls=[tool_call("group_by", {"column": "Region", "metric": "Revenue", "agg": "sum"})]),
        reply(content="South leads with 29,350 in revenue (33.4% of the total)."),
    ]

    res = ask(chat_client, ds_id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answer"].startswith("South leads")
    [evidence] = body["evidence"]
    assert evidence["tool"] == "group_by"
    assert evidence["args"]["column"] == "Region" and evidence["args"]["filters"] == []
    assert evidence["result_table"]["rows"][0][:2] == ["South", 29350]

    first, second = fake_groq.requests
    system = first["messages"][0]["content"]
    assert 'dataset named "Sales"' in system
    assert "Use only numbers from the profile or tool results; if they don't cover the question, say so." in system
    assert "- Revenue [number]:" in system
    assert first["tool_choice"] == "auto" and len(first["tools"]) == 7
    tool_message = second["messages"][-1]
    assert tool_message["role"] == "tool" and json.loads(tool_message["content"])["rows"][0][0] == "South"


def test_history_is_trimmed_to_last_10_turns():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(14)]
    messages = build_messages("Sales", {"rows": 1, "cols": 1, "columns": {}}, history, "now?")
    assert [m["content"] for m in messages[1:-1]] == [f"turn {i}" for i in range(4, 14)]
    assert messages[-1] == {"role": "user", "content": "now?"}


def test_history_is_sent_to_the_model(chat_client, fake_groq):
    ds_id = upload(chat_client, SALES_CSV).json()["id"]
    fake_groq.responses = [reply(content="It has 6 rows.")]
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello!"}]
    assert ask(chat_client, ds_id, "How many rows?", history).status_code == 200
    roles = [m["role"] for m in fake_groq.requests[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_tool_errors_go_back_to_the_model_and_are_not_evidence(chat_client, fake_groq):
    ds_id = upload(chat_client, SALES_CSV).json()["id"]
    fake_groq.responses = [
        reply(tool_calls=[tool_call("group_by", {"column": "Regoin", "metric": "Revenue"})]),
        reply(tool_calls=[tool_call("group_by", {"column": "Region", "metric": "Revenue"}, "call_2")]),
        reply(content="South."),
    ]
    body = ask(chat_client, ds_id).json()
    assert len(body["evidence"]) == 1
    error_message = json.loads(fake_groq.requests[1]["messages"][-1]["content"])
    assert "Did you mean 'Region'" in error_message["error"]


def test_tool_rounds_are_capped(chat_client, fake_groq):
    ds_id = upload(chat_client, SALES_CSV).json()["id"]
    loop = [reply(tool_calls=[tool_call("describe_column", {"column": "Units"}, f"c{i}")]) for i in range(MAX_TOOL_ROUNDS)]
    fake_groq.responses = loop + [reply(content="Units average 358.3.")]

    body = ask(chat_client, ds_id).json()
    assert body["answer"] == "Units average 358.3." and len(body["evidence"]) == MAX_TOOL_ROUNDS
    assert len(fake_groq.requests) == MAX_TOOL_ROUNDS + 1
    final = fake_groq.requests[-1]
    assert "tools" not in final  # the forced final answer can't call more tools


def test_groq_failures_become_friendly_errors(chat_client, fake_groq):
    ds_id = upload(chat_client, SALES_CSV).json()["id"]
    request = httpx.Request("POST", "https://api.groq.com")
    fake_groq.responses = [groq.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None)]
    res = ask(chat_client, ds_id)
    assert res.status_code == 503 and "busy" in res.json()["detail"]

    fake_groq.responses = [groq.APITimeoutError(request=request)]
    res = ask(chat_client, ds_id)
    assert res.status_code == 504

    missing = httpx.Response(404, request=request)
    fake_groq.responses = [groq.NotFoundError("model_not_found", response=missing, body=None)]
    res = ask(chat_client, ds_id)
    assert res.status_code == 502 and "GROQ_MODEL" in res.json()["detail"]


def test_reasoning_effort_only_for_gpt_oss(fake_groq):
    import pandas as pd

    df = pd.DataFrame({"a": [1, 2]})
    profile = {"rows": 2, "cols": 1, "columns": {}}
    fake_groq.responses = [reply(content="x"), reply(content="y")]
    ChatEngine(fake_groq, "openai/gpt-oss-120b").answer(df, "d", profile, [], "q")
    ChatEngine(fake_groq, "qwen/qwen3.8-27b").answer(df, "d", profile, [], "q")
    assert fake_groq.requests[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in fake_groq.requests[1]


def test_malformed_tool_call_from_groq_falls_back_to_plain_answer(chat_client, fake_groq):
    ds_id = upload(chat_client, SALES_CSV).json()["id"]
    request = httpx.Request("POST", "https://api.groq.com")
    bad = groq.BadRequestError("tool_use_failed: invalid tool call", response=httpx.Response(400, request=request), body=None)
    fake_groq.responses = [bad, reply(content="I couldn't run a calculation for that.")]
    res = ask(chat_client, ds_id)
    assert res.status_code == 200 and res.json()["evidence"] == []


def test_unknown_and_legacy_datasets(chat_client, fake_metadata):
    assert ask(chat_client, "nope").status_code == 404
    fake_metadata.create_dataset("user-a", "legacy", {"id": "legacy", "name": "Old", "data": [{"a": 1}]})
    res = ask(chat_client, "legacy")
    assert res.status_code == 409 and "Re-upload" in res.json()["detail"]


def test_rate_limit_is_20_per_minute_per_user(chat_client, fake_groq, current_uid):
    ds_id = upload(chat_client, SALES_CSV).json()["id"]
    fake_groq.responses = [reply(content="ok") for _ in range(25)]
    codes = [ask(chat_client, ds_id).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    assert "Too many chat requests" in ask(chat_client, ds_id).json()["detail"]

    current_uid["uid"] = "user-b"  # another user has their own budget
    assert ask(chat_client, "missing").status_code == 404


def test_chat_requires_a_token(unauthenticated_client):
    res = unauthenticated_client.post("/chat", json={"dataset_id": "abc", "message": "hi"})
    assert res.status_code == 401


def test_chat_without_groq_key_is_503(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "groq_api_key", None)
    res = client.post("/chat", json={"dataset_id": "abc", "message": "hi"})
    assert res.status_code == 503 and "GROQ_API_KEY" in res.json()["detail"]


def test_request_validation(chat_client):
    assert chat_client.post("/chat", json={"dataset_id": "../x", "message": "hi"}).status_code == 422
    assert chat_client.post("/chat", json={"dataset_id": "abc", "message": ""}).status_code == 422
    assert chat_client.post("/chat", json={"dataset_id": "abc", "message": "x" * 2001}).status_code == 422
