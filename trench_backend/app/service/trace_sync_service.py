"""Copy LangSmith traces into Trench's database.

Every chat turn (and thread-title call) runs with a pre-assigned LangSmith run
id and metadata (user, thread, stream, knowledge type), so after it ends we
fetch exactly that trace -- no searching, no duplicates. LangSmith ingests
runs a few seconds after they finish, so the fetch retries until the trace is
complete and stable. A manual refresh catches anything a restart interrupted.

Storage: one `traces` row per trace (summary for lists/charts), one
`trace_spans` row per run. Inputs/outputs are trimmed to PAYLOAD_LIMIT chars;
LLM messages are normalised to {role, content, tool_calls}. Rows older than
RETENTION_DAYS are purged. A trace is only stored for the user named in its
own metadata.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.database.models import Trace, TraceSpan
from app.database.session import async_session_factory
from app.utils import langsmith_client as ls

logger = logging.getLogger(__name__)

RETENTION_DAYS = 14
PAYLOAD_LIMIT = 16_000
PREVIEW_LIMIT = 300
ERROR_LIMIT = 4_000
SYNC_DELAYS_S = (3, 4, 6, 10, 15, 20, 30, 45)

KIND_CHAT_TURN = "chat_turn"
KIND_THREAD_TITLE = "thread_title"
KIND_NAMES = {KIND_CHAT_TURN: "Chat answer", KIND_THREAD_TITLE: "Thread title"}

_META_KEYS = (
    "ls_provider", "ls_model_name", "ls_model_type", "ls_temperature", "ls_max_tokens",
    "langgraph_node", "langgraph_step", "langgraph_triggers",
    "knowledge_type", "trench_kind",
)  # fmt: skip


# -- Pure helpers (unit-tested) ------------------------------------------------


def to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def latency_ms(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, round((end - start).total_seconds() * 1000))


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (
                "text",
                "output_text",
                None,
            ):
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


_ROLE_BY_TYPE = {
    "human": "user", "humanmessage": "user", "user": "user",
    "ai": "assistant", "aimessage": "assistant", "aimessagechunk": "assistant",
    "assistant": "assistant",
    "system": "system", "systemmessage": "system",
    "tool": "tool", "toolmessage": "tool",
}  # fmt: skip


def normalize_message(raw: Any) -> dict | None:
    """A LangChain/OpenAI-style message as {role, content, tool_calls?, name?}."""
    if not isinstance(raw, dict):
        return None
    serialized = raw.get("lc") and isinstance(raw.get("kwargs"), dict)
    data = raw["kwargs"] if serialized else raw
    if serialized and isinstance(raw.get("id"), list):
        kind = raw["id"][-1]
    else:
        kind = data.get("type") or data.get("role") or ""
    role = _ROLE_BY_TYPE.get(str(kind).lower())
    if role is None:
        return None
    message: dict = {"role": role, "content": _text(data.get("content"))}
    calls = (
        data.get("tool_calls")
        or (data.get("additional_kwargs") or {}).get("tool_calls")
        or []
    )
    if calls:
        message["tool_calls"] = [
            {
                "name": c.get("name") or (c.get("function") or {}).get("name"),
                "args": c.get("args")
                if "args" in c
                else (c.get("function") or {}).get("arguments"),
            }
            for c in calls
            if isinstance(c, dict)
        ]
    if data.get("name"):
        message["name"] = data["name"]
    return message


def messages_in(value: Any) -> list[dict]:
    """Messages in run inputs/outputs: {'messages': [...]} (possibly nested one
    level), LangChain `generations`, or OpenAI-style `choices`."""
    if not isinstance(value, dict):
        return []
    msgs = value.get("messages")
    if isinstance(msgs, list):
        flat = msgs[0] if len(msgs) == 1 and isinstance(msgs[0], list) else msgs
        found = [m for m in (normalize_message(x) for x in flat) if m]
        if found:
            return found
    found: list[dict] = []
    for group in value.get("generations") or []:
        for gen in group if isinstance(group, list) else [group]:
            if isinstance(gen, dict):
                m = normalize_message(gen.get("message"))
                if m is None and gen.get("text"):
                    m = {"role": "assistant", "content": _text(gen["text"])}
                if m:
                    found.append(m)
    for choice in value.get("choices") or []:
        if isinstance(choice, dict):
            m = normalize_message(choice.get("message"))
            if m:
                found.append(m)
    return found


def trim_payload(value: Any, limit: int = PAYLOAD_LIMIT) -> tuple[Any, bool]:
    """(value, truncated): larger values become `{"_truncated": "<json prefix>"}`."""
    if value is None:
        return None, False
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) <= limit:
        return json.loads(text), False
    return {"_truncated": text[:limit]}, True


def shape_payload(run_type: str, value: Any) -> tuple[Any, bool]:
    if run_type == "llm":
        messages = messages_in(value)
        if messages:
            value = {"messages": messages}
    return trim_payload(value)


def span_order(runs: Iterable[Any]) -> list[Any]:
    return sorted(
        runs,
        key=lambda r: (getattr(r, "dotted_order", None) or "", to_utc(r.start_time)),
    )


def span_depth(run: Any) -> int:
    dotted = getattr(run, "dotted_order", None) or ""
    return dotted.count(".") if dotted else 0


def _status(run: Any) -> str:
    if getattr(run, "error", None):
        return "error"
    if getattr(run, "end_time", None) is None:
        return "pending"
    return "success"


def _metadata(run: Any) -> dict:
    return (getattr(run, "extra", None) or {}).get("metadata") or {}


def _model(run: Any) -> str | None:
    extra = getattr(run, "extra", None) or {}
    return _metadata(run).get("ls_model_name") or (
        extra.get("invocation_params") or {}
    ).get("model")


def _cost(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float, Decimal)) else None


def build_spans(runs: list[Any]) -> list[dict]:
    rows = []
    for position, run in enumerate(span_order(runs)):
        start, end = to_utc(run.start_time), to_utc(run.end_time)
        inputs, in_cut = shape_payload(run.run_type, run.inputs)
        outputs, out_cut = shape_payload(run.run_type, run.outputs)
        meta = {k: v for k, v in _metadata(run).items() if k in _META_KEYS}
        if run.tags:
            meta["tags"] = list(run.tags)[:10]
        rows.append({
            "id": uuid.UUID(str(run.id)),
            "trace_id": uuid.UUID(str(run.trace_id)),
            "parent_id": (
                uuid.UUID(str(run.parent_run_id)) if run.parent_run_id else None
            ),
            "position": position,
            "depth": span_depth(run),
            "name": (run.name or run.run_type)[:255],
            "run_type": run.run_type,
            "status": _status(run),
            "error": str(run.error)[:ERROR_LIMIT] if run.error else None,
            "start_time": start,
            "end_time": end,
            "latency_ms": latency_ms(start, end),
            "prompt_tokens": run.prompt_tokens or 0,
            "completion_tokens": run.completion_tokens or 0,
            "total_tokens": run.total_tokens or 0,
            "total_cost": _cost(run.total_cost),
            "model": _model(run) if run.run_type == "llm" else None,
            "inputs": inputs,
            "outputs": outputs,
            "inputs_truncated": in_cut,
            "outputs_truncated": out_cut,
            "meta": meta or None,
        })  # fmt: skip
    return rows


def _preview(messages: list[dict], role: str) -> str | None:
    for message in reversed(messages):
        if message["role"] == role and message["content"].strip():
            return message["content"].strip()[:PREVIEW_LIMIT]
    return None


_TITLE_MESSAGE = re.compile(r'Message: """(.*?)(?:"""|$)', re.S)


def title_subject(prompt: str | None) -> str | None:
    """The user's message inside the title prompt (thread_title_service quotes
    it in triple quotes after "Message:"), not the prompt wrapper."""
    if not prompt:
        return None
    match = _TITLE_MESSAGE.search(prompt)
    return (match.group(1).strip() if match else prompt)[:PREVIEW_LIMIT] or None


def summarize(root: Any, spans: list[dict]) -> dict:
    """The `traces` row for a trace, from its root run and built spans."""
    meta = _metadata(root)
    kind = meta.get("trench_kind") or KIND_CHAT_TURN
    start, end = to_utc(root.start_time), to_utc(root.end_time)
    llm = [s for s in spans if s["run_type"] == "llm"]
    costs = [s["total_cost"] for s in llm if s["total_cost"] is not None]
    first_llm_in = messages_in(llm[0]["inputs"]) if llm else []
    last_llm_out = messages_in(llm[-1]["outputs"]) if llm else []
    question = _preview(messages_in(root.inputs), "user") or _preview(
        first_llm_in, "user"
    )
    answer = None
    if isinstance(root.outputs, dict) and isinstance(root.outputs.get("response"), str):
        answer = root.outputs["response"].strip()[:PREVIEW_LIMIT] or None
    answer = (
        answer
        or _preview(messages_in(root.outputs), "assistant")
        or _preview(last_llm_out, "assistant")
    )
    app_path = getattr(root, "app_path", None)
    thread_id = meta.get("thread_id")
    return {
        "id": uuid.UUID(str(root.trace_id)),
        "thread_id": uuid.UUID(thread_id) if thread_id else None,
        "stream_id": meta.get("stream_id"),
        "kind": kind,
        "knowledge_type": meta.get("knowledge_type"),
        "name": KIND_NAMES.get(kind, root.name or "Run"),
        "status": _status(root),
        "error": str(root.error)[:ERROR_LIMIT] if root.error else None,
        "start_time": start,
        "end_time": end,
        "latency_ms": latency_ms(start, end),
        # LLM spans hold the token counts; chain spans repeat their children's.
        "prompt_tokens": sum(s["prompt_tokens"] for s in llm),
        "completion_tokens": sum(s["completion_tokens"] for s in llm),
        "total_tokens": sum(s["total_tokens"] for s in llm),
        "total_cost": round(sum(costs), 6) if costs else _cost(root.total_cost),
        "span_count": len(spans),
        "llm_calls": len(llm),
        "tool_calls": sum(1 for s in spans if s["run_type"] == "tool"),
        "retriever_calls": sum(1 for s in spans if s["run_type"] == "retriever"),
        "error_count": sum(1 for s in spans if s["status"] == "error"),
        "models": sorted({s["model"] for s in llm if s["model"]}),
        "input_preview": title_subject(question)
        if kind == KIND_THREAD_TITLE
        else question,
        "output_preview": answer,
        "langsmith_url": f"https://smith.langchain.com{app_path}" if app_path else None,
        "complete": end is not None and all(s["end_time"] is not None for s in spans),
    }


# -- Database + LangSmith ------------------------------------------------------


async def _store(trace: dict, spans: list[dict], user_id: uuid.UUID) -> None:
    async with async_session_factory() as session:
        values = {**trace, "user_id": user_id, "synced_at": datetime.now(UTC)}
        stmt = insert(Trace).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Trace.id],
                set_={k: stmt.excluded[k] for k in values if k != "id"},
            )
        )
        # Replace the spans wholesale: idempotent, and drops runs LangSmith no
        # longer reports.
        await session.execute(
            delete(TraceSpan).where(TraceSpan.trace_id == trace["id"])
        )
        if spans:
            await session.execute(insert(TraceSpan), spans)
        await session.commit()


async def sync_trace(trace_id: str, user_id: uuid.UUID) -> bool | None:
    """Fetch one trace and store it. True = complete, False = still being
    ingested, None = not found (yet) or not this user's."""
    runs = await asyncio.to_thread(ls.fetch_trace_runs, trace_id)
    root = next((r for r in runs if r.parent_run_id is None), None)
    if root is None:
        return None
    if _metadata(root).get("user_id") != str(user_id):
        logger.warning("Trace %s belongs to another user; not stored", trace_id)
        return None
    spans = build_spans(runs)
    trace = summarize(root, spans)
    await _store(trace, spans, user_id)
    return trace["complete"]


async def sync_after_run(trace_ids: list[str], user_id: uuid.UUID) -> None:
    """Background: store a finished run's traces once LangSmith has them.
    Never raises (tracing must not affect chat)."""
    if not ls.tracing_enabled() or not trace_ids:
        return
    pending = list(dict.fromkeys(trace_ids))
    last_counts: dict[str, int] = {}
    for delay in SYNC_DELAYS_S:
        await asyncio.sleep(delay)
        for trace_id in list(pending):
            try:
                runs = await asyncio.to_thread(ls.fetch_trace_runs, trace_id)
                done = bool(runs) and all(r.end_time for r in runs)
                if done and last_counts.get(trace_id) == len(runs):
                    if await sync_trace(trace_id, user_id) is not None:
                        pending.remove(trace_id)
                last_counts[trace_id] = len(runs)
            except Exception as exc:  # network, LangSmith outage
                logger.warning("Trace sync for %s failed: %s", trace_id, exc)
        if not pending:
            break
    for trace_id in pending:  # store whatever exists; a refresh completes it
        try:
            await sync_trace(trace_id, user_id)
        except Exception as exc:
            logger.warning("Final trace sync for %s failed: %s", trace_id, exc)
    await purge_expired()


async def sync_recent(user_id: uuid.UUID) -> dict:
    """Manual refresh: store this user's root runs from the retention window
    that are missing or were incomplete."""
    if not ls.tracing_enabled():
        return {"enabled": False, "synced": 0}
    since = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    roots = await asyncio.to_thread(ls.list_user_root_runs, str(user_id), since)
    async with async_session_factory() as session:
        done = {
            str(i)
            for i in (
                await session.execute(
                    select(Trace.id).where(
                        Trace.user_id == user_id, Trace.complete.is_(True)
                    )
                )
            ).scalars()
        }
    synced = 0
    for root in roots:
        if str(root.trace_id) in done:
            continue
        try:
            if await sync_trace(str(root.trace_id), user_id) is not None:
                synced += 1
        except Exception as exc:
            logger.warning("Trace refresh for %s failed: %s", root.trace_id, exc)
    await purge_expired()
    return {"enabled": True, "synced": synced}


async def purge_expired() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    async with async_session_factory() as session:
        await session.execute(delete(Trace).where(Trace.start_time < cutoff))
        await session.commit()
