"""Shaping LangSmith runs into Tracing rows (app/service/trace_sync_service.py)."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.service import trace_sync_service as ts

T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)


def run(
    name, run_type, dotted, parent=None, *, start=0.0, end=1.0, trace_id=None, **kw
):
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        trace_id=trace_id,
        parent_run_id=parent,
        name=name,
        run_type=run_type,
        dotted_order=dotted,
        start_time=T0 + timedelta(seconds=start),
        end_time=None if end is None else T0 + timedelta(seconds=end),
        error=kw.pop("error", None),
        inputs=kw.pop("inputs", {}),
        outputs=kw.pop("outputs", {}),
        prompt_tokens=kw.pop("prompt_tokens", None),
        completion_tokens=kw.pop("completion_tokens", None),
        total_tokens=kw.pop("total_tokens", None),
        total_cost=kw.pop("total_cost", None),
        extra=kw.pop("extra", {}),
        tags=kw.pop("tags", []),
        app_path=kw.pop("app_path", None),
    )


def lc(kind, content):
    return {
        "lc": 1,
        "type": "constructor",
        "id": ["langchain", "schema", "messages", kind],
        "kwargs": {"content": content},
    }


def chat_trace(user_id, end=5.0):
    root_id = uuid.uuid4()
    thread_id = uuid.uuid4()
    root = run(
        "agentic_rag_chat",
        "chain",
        "20261113T100000Z" + str(root_id),
        id=root_id,
        trace_id=root_id,
        end=end,
        inputs={"messages": [{"role": "user", "content": "What is our leave policy?"}]},
        outputs={"response": "Employees get 24 days of paid leave [1]."},
        extra={
            "metadata": {
                "user_id": str(user_id),
                "thread_id": str(thread_id),
                "knowledge_type": "company",
                "trench_kind": "chat_turn",
            }
        },
        app_path="/o/org/projects/p/proj/r/run",
    )
    base = root.dotted_order
    retrieve = run(
        "retrieve",
        "retriever",
        base + ".a",
        parent=root_id,
        trace_id=root_id,
        start=0.5,
        end=1.5,
    )
    generate = run(
        "ChatGroq",
        "llm",
        base + ".b",
        parent=root_id,
        trace_id=root_id,
        start=2,
        end=4.5,
        prompt_tokens=900,
        completion_tokens=60,
        total_tokens=960,
        total_cost=0.0004,
        inputs={
            "messages": [[lc("SystemMessage", "cite"), lc("HumanMessage", "leave?")]]
        },
        outputs={
            "generations": [[{"text": "", "message": lc("AIMessage", "24 days")}]]
        },
        extra={
            "metadata": {
                "ls_model_name": "openai/gpt-oss-120b",
                "langgraph_node": "generate",
            }
        },
    )
    return root, [generate, root, retrieve], thread_id


def test_chat_trace_summary():
    user = uuid.uuid4()
    root, runs, thread_id = chat_trace(user)
    spans = ts.build_spans(runs)
    summary = ts.summarize(root, spans)
    assert [s["run_type"] for s in spans] == ["chain", "retriever", "llm"]
    assert summary["kind"] == "chat_turn" and summary["name"] == "Chat answer"
    assert summary["knowledge_type"] == "company" and summary["thread_id"] == thread_id
    assert (
        summary["llm_calls"],
        summary["retriever_calls"],
        summary["span_count"],
    ) == (1, 1, 3)
    assert summary["total_tokens"] == 960 and summary["total_cost"] == 0.0004
    assert summary["models"] == ["openai/gpt-oss-120b"]
    assert summary["input_preview"] == "What is our leave policy?"
    assert summary["output_preview"] == "Employees get 24 days of paid leave [1]."
    assert summary["latency_ms"] == 5000 and summary["complete"] is True
    assert "langsmith_url" not in summary
    assert spans[2]["inputs"] == {
        "messages": [
            {"role": "system", "content": "cite"},
            {"role": "user", "content": "leave?"},
        ]
    }


def test_title_trace_uses_openai_style_choices_and_users_message():
    root_id = uuid.uuid4()
    root = run(
        "trench.thread_title",
        "chain",
        "x" + str(root_id),
        id=root_id,
        trace_id=root_id,
        extra={
            "metadata": {
                "user_id": "u",
                "thread_id": None,
                "trench_kind": "thread_title",
            }
        },
    )
    llm = run(
        "Groq chat",
        "llm",
        root.dotted_order + ".a",
        parent=root_id,
        trace_id=root_id,
        inputs={
            "messages": [
                {"role": "system", "content": "title it"},
                {"role": "user", "content": 'Message: """Plan our offsite"""\nTitle:'},
            ]
        },
        outputs={
            "choices": [{"message": {"role": "assistant", "content": "Offsite Plan"}}]
        },
    )
    summary = ts.summarize(root, ts.build_spans([root, llm]))
    assert summary["name"] == "Thread title"
    assert summary["input_preview"] == "Plan our offsite"
    assert summary["output_preview"] == "Offsite Plan"


def test_unfinished_trace_is_pending_and_incomplete():
    root, runs, _ = chat_trace(uuid.uuid4(), end=None)
    summary = ts.summarize(root, ts.build_spans(runs))
    assert summary["status"] == "pending" and summary["complete"] is False


def test_large_payloads_are_trimmed():
    value, cut = ts.trim_payload({"text": "x" * 50_000}, limit=1_000)
    assert cut and len(value["_truncated"]) == 1_000


async def test_trace_of_another_user_is_never_stored(monkeypatch):
    root, runs, _ = chat_trace(uuid.uuid4())
    monkeypatch.setattr(ts.ls, "fetch_trace_runs", lambda trace_id: runs)
    stored = []

    async def fake_store(*args):
        stored.append(args)

    monkeypatch.setattr(ts, "_store", fake_store)
    assert await ts.sync_trace(str(root.id), uuid.uuid4()) is None
    assert stored == []
