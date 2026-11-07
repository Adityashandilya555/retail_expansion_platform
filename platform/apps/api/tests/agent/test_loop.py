"""The agent loop (#38): turns against a fake Anthropic client answering with recorded response
shapes, on test_tools' draft and change log held in memory and a conversation store in memory.
The vault and the API run against the pilot vault (`pytest -m integration` after dev-up.sh). One
smoke test calls the real API: `pytest -m live` by hand with ANTHROPIC_API_KEY, never in CI."""

import asyncio
import copy
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import anthropic
import anyio
import httpx2
import jwt
import psycopg
import pytest
from anthropic.types.beta import BetaMessage
from fastapi.testclient import TestClient

from zm_api import drafts
from zm_api.agent import loop, tools
from zm_api.context import VAULT_POOL, OwnerContext, Resolver
from zm_api.errors import Conflict, NotFound
from zm_api.main import create_app

from ..conftest import bearer, token
from ..test_auth import PILOT_DSN
from ..test_draft import pilot  # noqa: F401 (the vault fixture)
from ..test_publish import JWT_SECRET, Clients, Stack, clients, vault_sql  # noqa: F401
from ..test_publish_unit import FakeRegistry, matrix_retail
from .test_tools import ARGS, held, sha, task  # noqa: F401 (held: the draft in memory)

Doc = dict[str, Any]
Reply = Doc | Callable[[Doc], Doc] | Exception | tuple[Doc, Exception]
OWNER = uuid.uuid4()
ME = OwnerContext(OWNER)
FIRE_NOC = "add a Fire NOC upload to Legal licensing with L2 approval"
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

# --- recorded response shapes -------------------------------------------------------------------

THINKING = {"type": "thinking", "thinking": "", "signature": "EqQBCkgIBhABGAIiQPx0c2lnbmF0dXJl"}


def text(value: str) -> Doc:
    return {"type": "text", "text": value, "citations": None}


def tool_use(name: str, args: Doc) -> Doc:
    return {"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:24]}", "name": name, "input": args}


def reply(*content: Doc, stop: str = "end_turn", usage: Doc | None = None, **more: Any) -> Doc:
    """A Messages API response body."""
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": list(content),
        "stop_reason": stop,
        "stop_sequence": None,
        "stop_details": None,
        "usage": {
            "input_tokens": 1200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 300,
            **(usage or {}),
        },
        **more,
    }


FALLBACK = {
    "type": "fallback",
    "from": {"model": "claude-opus-5-5"},
    "to": {"model": "claude-opus-5"},
    "trigger": {"type": "refusal", "category": "cyber"},
}


def iteration(kind: str, **more: Any) -> Doc:
    counts = {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    return {"type": kind, "input_tokens": 1200, "output_tokens": 40, **counts, **more}


def api_error(
    kind: type[anthropic.APIStatusError], status: int, error: str, message: str
) -> anthropic.APIStatusError:
    """What the SDK raises for an error response; status 200 is an `error` event mid-stream."""
    body = {"type": "error", "error": {"type": error, "message": message}}
    return kind(str(body), response=httpx2.Response(status, request=REQUEST, json=body), body=body)


class FakeStream:
    """What `client.beta.messages.stream(...)` gives inside `async with`: `message_start`, the
    text, and `error` raised after them, each event a network wait."""

    def __init__(self, message: BetaMessage | None, error: Exception | None = None) -> None:
        self.message, self.error = message, error

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        for block in [] if self.message is None else [None, *self.message.content]:
            await asyncio.sleep(0)
            if block is None:
                yield SimpleNamespace(type="message_start")
            elif block.type == "text":
                yield SimpleNamespace(type="text", text=block.text)
        if self.error:
            raise self.error  # as the SDK's stream iterator raises

    @property
    def current_message_snapshot(self) -> BetaMessage:
        assert self.message is not None  # as the SDK asserts before message_start
        return self.message

    async def get_final_message(self) -> BetaMessage:
        assert self.message is not None and self.error is None
        return self.message


class Fake:
    """`AsyncAnthropic` as the loop calls it. Each reply is a response body, a function of the
    request giving one, an exception the stream raises before anything, or a body and the
    exception it is cut by; every request is kept."""

    def __init__(self, *replies: Reply, tokens: int = 2_000) -> None:
        self.replies, self.tokens = list(replies), tokens
        self.requests: list[Doc] = []
        self.counted: list[Doc] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))
        self.messages = SimpleNamespace(count_tokens=self.count_tokens)

    async def count_tokens(self, **params: Any) -> SimpleNamespace:
        self.counted.append(copy.deepcopy(params))
        blocks = [
            b for m in params["messages"] if isinstance(m["content"], list) for b in m["content"]
        ]
        if any(b["type"] == "fallback" for b in blocks):  # a beta block, and this isn't the beta
            raise api_error(anthropic.BadRequestError, 400, "invalid_request_error", "fallback")
        return SimpleNamespace(input_tokens=self.tokens)

    def stream(self, **params: Any) -> FakeStream:
        self.requests.append(copy.deepcopy(params))
        found = self.replies.pop(0)
        if isinstance(found, Exception):
            return FakeStream(None, found)
        found, error = found if isinstance(found, tuple) else (found, None)
        return FakeStream(
            BetaMessage.model_validate(found(params) if callable(found) else found), error
        )


# --- reading a request ----------------------------------------------------------------------------


def state(request: Doc) -> Doc:
    """The draft state the request's turn opened with (its last system message)."""
    content = [m for m in request["messages"] if m["role"] == "system"][-1]["content"]
    return json.loads(content.split(": ", 1)[1])


def last_result(request: Doc) -> Doc:
    return json.loads(request["messages"][-1]["content"][-1]["content"])


SLOT = {
    "task_id": "legal_licensing",
    "key": "fire_noc",
    "label": "Fire NOC upload",
    "accept": ["pdf"],
    "max_mb": 25,
    "required": True,
    "keep_versions": True,
}
L2 = {
    "task_id": "legal_licensing",
    "chain": [{"department": "legal", "level": 2}],
    "send_back_to": None,
    "allow_reject": True,
}


def fire_noc() -> tuple[Reply, ...]:
    """How the model answers FIRE_NOC: a read, an edit on the sha it returned, then the next edit
    on the sha the first returned."""
    return (
        reply(
            THINKING,
            text("Adding the upload."),
            tool_use("get_task", {"task_id": "legal_licensing"}),
            stop="tool_use",
        ),
        lambda r: reply(
            THINKING,
            tool_use("add_document_slot", {**SLOT, "draft_sha256": last_result(r)["draft_sha256"]}),
            stop="tool_use",
        ),
        lambda r: reply(
            THINKING,
            tool_use("set_approval_chain", {**L2, "draft_sha256": last_result(r)["draft_sha256"]}),
            stop="tool_use",
        ),
        reply(text("Done: Fire NOC upload with Legal L2 approval.")),
    )


# --- the conversation store in memory -----------------------------------------------------------


@pytest.fixture
def chats(held: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> dict[uuid.UUID, Doc]:  # noqa: F811
    """`{conversation id: {mode, owner, started_at, rows}}` for the vault's two tables, and their
    claims (the vault's advisory locks) as a set; `held.ctx` gets a client."""
    found: dict[uuid.UUID, Doc] = {}
    busy: set[uuid.UUID] = set()
    held.ctx.client = SimpleNamespace(slug="pilot-local")

    @asynccontextmanager
    async def claimed(_ctx: Any, cid: uuid.UUID) -> AsyncIterator[None]:
        if cid in busy:
            raise Conflict(loop.BUSY)
        busy.add(cid)
        try:
            yield
        finally:
            busy.remove(cid)

    async def stored(_ctx: Any, cid: uuid.UUID, owner: uuid.UUID) -> tuple[str, list[Doc]]:
        if cid not in found or found[cid]["owner"] != owner:
            raise NotFound(loop.NO_CONVERSATION)
        return found[cid]["mode"], copy.deepcopy(found[cid]["rows"])

    async def append(_ctx: Any, cid: uuid.UUID, seq: int, rows: list[Doc]) -> None:
        await asyncio.sleep(0)  # a round trip to the vault
        assert seq == len(found[cid]["rows"])  # right after what the turn read
        found[cid]["rows"] += copy.deepcopy(rows)

    async def mine(_ctx: Any, owner: uuid.UUID) -> list[Doc]:
        listed = [{"id": cid, "owner_id": c["owner"], **c} for cid, c in found.items()]
        return [c for c in reversed(listed) if c["owner"] == owner]  # newest first

    async def changes(_ctx: Any, cid: uuid.UUID) -> list[Doc]:  # held.log's ids count from 1
        log = [{**c, "id": i} for i, c in enumerate(held.log, 1)]
        return [c for c in log if c["conversation_id"] == cid]

    monkeypatch.setattr(loop, "_stored", stored)
    monkeypatch.setattr(loop, "_append", append)
    monkeypatch.setattr(loop, "_claimed", claimed)
    monkeypatch.setattr(loop, "_mine", mine)
    monkeypatch.setattr(loop, "_changes", changes)
    return found


def new(chats: dict[uuid.UUID, Doc], mode: str = "edit", owner: uuid.UUID = OWNER) -> uuid.UUID:
    cid = uuid.uuid4()
    chats[cid] = {"mode": mode, "owner": owner, "started_at": datetime.now(UTC), "rows": []}
    return cid


def run(client: Any, held: SimpleNamespace, cid: uuid.UUID, said: str) -> list[tuple[str, Doc]]:  # noqa: F811
    """One turn of `cid`, as the route runs it: its events."""

    async def go() -> list[tuple[str, Doc]]:
        conversation = await loop.load(held.ctx, cid, OWNER)
        return [e async for e in loop.turn(client, held.ctx, OWNER, conversation, said)]

    return asyncio.run(go())


def names(events: list[tuple[str, Doc]]) -> list[str]:
    return [name for name, _ in events]


def roles(chats: dict[uuid.UUID, Doc], cid: uuid.UUID) -> list[str]:
    return [r["role"] for r in chats[cid]["rows"]]


# --- unit ----------------------------------------------------------------------------------------


def test_a_request_streams_tool_calls_and_changes_and_edits_the_draft(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    fake, cid = Fake(*fire_noc()), new(chats)
    events = run(fake, held, cid, FIRE_NOC)
    assert events == [
        ("text_delta", {"text": "Adding the upload."}),
        ("tool_call", {"name": "get_task"}),
        ("tool_call", {"name": "add_document_slot"}),
        ("change", {"change_id": 1, "summary": "Added: Fire NOC upload"}),
        ("tool_call", {"name": "set_approval_chain"}),
        ("change", {"change_id": 2, "summary": "Approval of legal_licensing: legal L2"}),
        ("text_delta", {"text": "Done: Fire NOC upload with Legal L2 approval."}),
        ("done", {"usage": events[-1][1]["usage"]}),
    ]
    assert held.doc["fields"]["fire_noc"]["type"] == "document"
    assert task(held.doc, "legal_licensing")["approval"]["chain"] == L2["chain"]
    assert [(c["tool"], c["conversation_id"]) for c in held.log] == [
        ("add_document_slot", cid),
        ("set_approval_chain", cid),
    ]
    # the history only grows: each request is the last one plus its reply, unchanged (thinking
    # blocks too), and the tool results
    first, second, third, fourth = fake.requests
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert second["messages"][2]["content"][0] == THINKING
    assert third["messages"][: len(second["messages"])] == second["messages"]
    assert fourth["messages"][: len(third["messages"])] == third["messages"]
    # stored: the message and the state, then each reply (with its usage) and its tool results
    assert roles(chats, cid) == ["user", "system", *["assistant", "user"] * 3, "assistant"]
    stored = [{"role": r["role"], "content": r["content"]} for r in chats[cid]["rows"]]
    assert stored[:8] == fourth["messages"]
    usage = [bool(r.get("usage")) for r in chats[cid]["rows"]]
    assert usage == [False, False, *[True, False] * 3, True]
    assert first["output_config"] == {"effort": "medium"}  # an edit conversation


def test_a_replys_edits_run_in_order_each_on_the_sha_the_one_before_left(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    def edits(r: Doc) -> Doc:
        read = last_result(r)["draft_sha256"]
        return reply(
            tool_use("add_field", {**ARGS["add_field"], "draft_sha256": read}),
            tool_use("update_field", {**ARGS["update_field"], "draft_sha256": read}),
            tool_use("remove_field", {"key": "gst_no", "draft_sha256": "f" * 64}),  # not read
            stop="tool_use",
        )

    fake = Fake(reply(tool_use("get_overview", {}), stop="tool_use"), edits, reply(text("Done.")))
    run(fake, held, new(chats), "Add a GST number and relabel the site name")
    results = [json.loads(r["content"]) for r in fake.requests[2]["messages"][-1]["content"]]
    assert [r["ok"] for r in results] == [True, True, False]
    assert results[2]["summary"] == drafts.STALE
    assert [c["tool"] for c in held.log] == ["add_field", "update_field"]
    assert results[1]["draft_sha256"] == sha(held.doc)


def test_each_turn_tells_the_model_whether_a_person_changed_the_draft_since_its_last_read(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    fake = Fake(*fire_noc(), reply(text("Yes.")), reply(text("Yes.")), tokens=10)
    cid = new(chats)
    run(fake, held, cid, FIRE_NOC)
    run(fake, held, cid, "Is that all?")
    held.doc = copy.deepcopy(held.doc)  # a person relabels the upload between turns
    held.doc["fields"]["fire_noc"]["label"] = "Fire NOC certificate"
    run(fake, held, cid, "Still there?")
    first, unchanged, changed = (state(fake.requests[i]) for i in (0, 4, 5))
    assert [first["draft"], unchanged["draft"], changed["draft"]] == [
        loop.READ_FIRST,
        loop.UNCHANGED,
        loop.CHANGED,
    ]
    assert "legal" in changed["departments"] and changed["stages"]["store_launch"][0]
    # no sha to edit with: only the model's own reads and edits give it one
    notes = [m["content"] for m in fake.requests[5]["messages"] if m["role"] == "system"]
    assert len(notes) == 3 and not any("draft_sha256" in n or sha(held.doc) in n for n in notes)
    # the first turn's state message is still there, as it was
    assert (
        notes[0] == [m for m in fake.requests[0]["messages"] if m["role"] == "system"][0]["content"]
    )


def test_the_second_turn_reads_the_first_from_the_cache(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    fake = Fake(
        reply(text("Hello."), usage={"cache_creation_input_tokens": 9000}),
        reply(text("Again."), usage={"cache_read_input_tokens": 9000, "input_tokens": 80}),
    )
    cid = new(chats, "configure")
    first = run(fake, held, cid, "Hi")
    second = run(fake, held, cid, "Hi again")
    assert first[-1][1]["usage"]["cache_read_input_tokens"] == 0
    assert second[-1][1]["usage"] == {
        "input_tokens": 80,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 9000,
        "output_tokens": 300,
    }
    one, two = fake.requests
    # the cached prefix is the same: tools and system as they were, the history appended to
    assert json.dumps(two["tools"]) == json.dumps(one["tools"]) and two["system"] == one["system"]
    assert two["messages"][:3] == [
        *one["messages"],
        {"role": "assistant", "content": [text("Hello.")]},
    ]
    # the breakpoints: last tool, the system block, and the history (top-level)
    assert [t.get("cache_control") for t in one["tools"]] == [None] * (len(one["tools"]) - 1) + [
        {"type": "ephemeral"}
    ]
    assert one["system"] == [
        {"type": "text", "text": one["system"][0]["text"], "cache_control": {"type": "ephemeral"}}
    ]
    assert one["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in one["tools"]] == sorted(tools.TOOLS)
    assert all(t["eager_input_streaming"] for t in one["tools"])
    # the loop sends each tool's strictness as the tools module defines it (#143's split)
    strict = {d["name"]: d["strict"] for d in tools.definitions()}
    assert [t["strict"] for t in one["tools"]] == [strict[t["name"]] for t in one["tools"]]
    assert one["tool_choice"] == {"type": "auto"} and "thinking" not in one
    assert one["output_config"] == {"effort": "high"}  # a configure conversation
    assert (one["model"], one["max_tokens"]) == ("claude-opus-5-5", 64_000)
    assert (one["betas"], one["fallbacks"]) == (["server-side-fallback-2026-07-01"], "default")
    # count_tokens sized each call before it was made
    assert [c["messages"] for c in fake.counted] == [one["messages"], two["messages"]]


def test_the_reference_says_where_a_block_goes_only_by_layout() -> None:
    # a manifest's `regions` name no layout; validation checks each layout's `allowed_blocks`
    ref = json.loads(loop.system()[0]["text"].split("Reference: ", 1)[1])
    assert ref["layouts"]["case"]["regions"][0]["allowed_blocks"] == ["case_header"]
    header = ref["blocks"]["case_header"]["1"]
    assert header["props"]["properties"].keys() == {"title_field", "fields"}
    manifests = [m for versions in ref["blocks"].values() for m in versions.values()]
    assert manifests and not [m["name"] for m in manifests if "regions" in m]


def test_a_tool_that_raises_answers_is_error_and_leaves_the_draft(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_doc: Doc, _args: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setitem(tools.TOOLS, "add_field", replace(tools.TOOLS["add_field"], edit=boom))
    fake = Fake(
        reply(
            tool_use("add_field", {**ARGS["add_field"], "draft_sha256": sha(held.doc)}),  # read
            tool_use("get_task", {"task_id": 5}),  # an input that doesn't fit the schema
            stop="tool_use",
        ),
        reply(text("Neither worked.")),
    )
    events = run(fake, held, new(chats), "Add a GST number")
    results = fake.requests[1]["messages"][-1]["content"]  # all results in one user message
    assert [(r["type"], r["is_error"]) for r in results] == [("tool_result", True)] * 2
    assert json.loads(results[0]["content"]) == {"ok": False, "summary": "add_field failed"}
    assert json.loads(results[1]["content"])["errors"][0]["path"] == "task_id"
    assert names(events) == ["tool_call", "tool_call", "text_delta", "done"]
    assert held.doc == matrix_retail() and held.log == []


def test_past_the_budget_the_turn_stops_without_another_call(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # room for a whole reply (MAX_TOKENS) and 6000 more
    monkeypatch.setenv("AGENT_MAX_TOKENS_PER_CONVERSATION", str(loop.MAX_TOKENS + 6000))
    # fresh tokens: 1000 in + 1000 cache writes + 500 out = 2500 a call; cache reads don't count
    usage = {"input_tokens": 1000, "cache_creation_input_tokens": 1000, "output_tokens": 500}
    usage["cache_read_input_tokens"] = 50_000
    read = reply(tool_use("get_overview", {}), stop="tool_use", usage=usage)
    fake, cid = Fake(read, read, tokens=2000), new(chats)
    events = run(fake, held, cid, "Show me the draft")
    # 0 + 2000, then 2500 + 2000 fit 6000; 5000 + 2000 doesn't: no third call
    assert len(fake.requests) == 2 and len(fake.counted) == 3
    assert names(events) == ["tool_call", "tool_call", "error", "done"]
    assert "token budget" in events[2][1]["message"]
    again = run(fake, held, cid, "One more thing")
    assert len(fake.requests) == 2 and names(again) == ["error", "done"]
    # stored: up to the last tool result, which a later turn replays; nothing of the refused one
    assert roles(chats, cid) == ["user", "system", "assistant", "user", "assistant", "user"]


def test_a_refusal_after_the_fallback_reaches_the_dock_and_applies_nothing(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
    caplog: pytest.LogCaptureFixture,
) -> None:
    refused = reply(
        text("Adding"),
        FALLBACK,
        tool_use("add_field", ARGS["add_field"]),
        stop="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "Declined."},
        usage={"iterations": [iteration("message"), iteration("fallback_message", model="x")]},
    )
    fake, cid = Fake(refused, reply(text("Hello."))), new(chats)
    with caplog.at_level(logging.INFO, logger="zm_api.agent"):
        events = run(fake, held, cid, "Add a GST number")
    assert events[1:] == [("refusal", {"category": "cyber"}), ("done", events[-1][1])]
    assert held.doc == matrix_retail() and held.log == []
    [call] = [r for r in caplog.records if r.getMessage() == "agent call"]
    assert call.usage["fallback"] is True and call.usage["stop_reason"] == "refusal"  # type: ignore[attr-defined]
    # only the refused call's usage is kept; the next turn replays none of it
    assert [(r["role"], r["content"]) for r in chats[cid]["rows"]] == [("assistant", None)]
    run(fake, held, cid, "Hello")
    assert [m["role"] for m in fake.requests[1]["messages"]] == ["user", "system"]


def test_after_a_fallback_mid_reply_only_the_serving_models_tool_calls_run(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    declined = tool_use("add_field", ARGS["add_field"])
    served = tool_use("get_task", {"task_id": "legal_licensing"})
    then = {**THINKING, "signature": "Eq0BCkgIBhABGAIiQHNlcnZlZA"}
    fake = Fake(
        reply(THINKING, text("Let me"), declined, FALLBACK, then, served, stop="tool_use"),
        reply(text("Done.")),
        reply(text("Nothing else.")),
    )
    cid = new(chats)
    events = run(fake, held, cid, "What does legal licensing ask for?")
    assert [d for n, d in events if n == "tool_call"] == [{"name": "get_task"}]
    assert held.log == []
    # sent back: the declined model's text only, then everything after the switch but its marker
    assert fake.requests[1]["messages"][2]["content"] == [text("Let me"), then, served]
    # and the conversation goes on: count_tokens (no fallback beta) never sees the marker
    assert names(run(fake, held, cid, "Anything else?")) == ["text_delta", "done"]
    assert len(fake.counted) == len(fake.requests) == 3


def test_a_reply_cut_at_max_tokens_runs_no_tool_and_an_empty_one_isnt_replayed(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    cut = reply(tool_use("add_field", ARGS["add_field"]), stop="max_tokens")
    fake, cid = Fake(cut, reply(), reply(text("Hi."))), new(chats)
    events = run(fake, held, cid, "Add a GST number")
    assert names(events) == ["error", "done"] and "cut off" in events[0][1]["message"]
    assert held.log == [] and [r["content"] for r in chats[cid]["rows"]] == [None]
    # an empty reply (the API refuses an empty assistant message): only its usage is kept
    assert names(run(fake, held, cid, "Hello?")) == ["done"]
    assert [r["content"] for r in chats[cid]["rows"]] == [None, None]
    run(fake, held, cid, "Hello")
    assert [m["role"] for m in fake.requests[2]["messages"]] == ["user", "system"]


def test_a_call_that_doesnt_finish_still_counts_and_a_retry_drops_its_text(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    bad_json = ValueError("Unable to parse tool parameter JSON from model")
    down = anthropic.APIConnectionError(request=REQUEST)
    fake, cid = Fake((reply(text("Let me add")), bad_json), reply(text("Hi.")), down), new(chats)
    events = run(fake, held, cid, "Hi")
    assert events[:-1] == [
        ("text_delta", {"text": "Let me add"}),
        ("retry", {}),  # the console drops "Let me add"
        ("text_delta", {"text": "Hi."}),
    ]
    assert names(run(fake, held, cid, "Hi again")) == ["error", "done"]  # the API is down
    # each call is stored with its usage, content None for one that didn't finish: as streamed
    # until the reply couldn't be read, or as counted when nothing came back
    rows = chats[cid]["rows"]
    assert [(r["role"], r["content"] is None) for r in rows] == [
        ("assistant", True),
        ("user", False),
        ("system", False),
        ("assistant", False),
        ("assistant", True),
    ]
    assert [loop._fresh(r["usage"]) for r in rows if r["content"] is None] == [1500, 2000]
    assert events[-1][1]["usage"]["output_tokens"] == 600  # both calls of the turn
    three = Fake(bad_json, bad_json, bad_json)
    assert names(run(three, held, new(chats), "Hi")) == ["retry", "retry", "error", "done"]


def test_a_call_the_console_leaves_mid_stream_still_keeps_its_usage(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    """The route's stream runs the turn in a task that a disconnect cancels (an anyio cancel
    scope), or leaves it at a yield to be closed: either way the call's usage is stored."""
    hello = reply(text("Hel"), text("lo."))
    fake, cancelled, closed = Fake(hello, hello), new(chats), new(chats)

    async def go() -> None:
        conversation = await loop.load(held.ctx, cancelled, OWNER)
        with anyio.CancelScope() as scope:
            async for _ in loop.turn(fake, held.ctx, OWNER, conversation, "Hi"):
                scope.cancel()  # gone after the first text
        conversation = await loop.load(held.ctx, closed, OWNER)
        stream = loop.turn(fake, held.ctx, OWNER, conversation, "Hi")
        await anext(stream)
        await stream.aclose()

    asyncio.run(go())
    for cid in (cancelled, closed):
        assert [(r["content"], r["usage"]["output_tokens"]) for r in chats[cid]["rows"]] == [
            (None, 300)
        ]


def test_an_api_error_says_whether_sending_again_can_help(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    refused = api_error(anthropic.BadRequestError, 400, "invalid_request_error", "prompt too long")
    passing = [
        anthropic.APIConnectionError(request=REQUEST),
        api_error(anthropic.RateLimitError, 429, "rate_limit_error", "Slow down"),
        api_error(anthropic.OverloadedError, 529, "overloaded_error", "Overloaded"),
        api_error(anthropic.APIStatusError, 200, "overloaded_error", "Overloaded"),  # mid-stream
    ]
    said = [run(Fake(e), held, new(chats), "Hi")[0][1]["message"] for e in [refused, *passing]]
    assert said[0] == "The request was refused: prompt too long"
    assert all(s.startswith("The model is unavailable") and "Try again" in s for s in said[1:])


def test_a_failure_outside_the_api_still_ends_the_stream_with_an_error_and_done(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def down(*_: Any) -> None:
        raise OSError("the vault went away")

    monkeypatch.setattr(loop, "_append", down)
    with caplog.at_level(logging.ERROR, logger="zm_api.agent"):
        events = run(Fake(reply(text("Hello."))), held, new(chats), "Hi")
    assert events[1:] == [("error", {"message": loop.FAILED}), ("done", events[-1][1])]
    [failed] = [r for r in caplog.records if r.getMessage() == "agent turn failed"]
    assert failed.exc_info and isinstance(failed.exc_info[1], OSError)


def test_a_turn_overlapping_one_on_its_conversation_is_refused_before_any_call(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    """The route claims the conversation (`opened`) before it reads it, until its stream ends."""
    fake, cid = Fake(*fire_noc()), new(chats)
    opened = asynccontextmanager(loop.opened)

    async def go() -> tuple[list[tuple[str, Doc]], loop.Conversation]:
        async with opened(cid, held.ctx, ME) as conversation:
            stream = loop.turn(fake, held.ctx, OWNER, conversation, FIRE_NOC)
            first = await anext(stream)  # its first call is streaming
            with pytest.raises(Conflict, match="already running"):
                async with opened(cid, held.ctx, ME):
                    pytest.fail("the second turn ran")
            events = [first, *[e async for e in stream]]
        async with opened(cid, held.ctx, ME) as after:  # let go: the next turn reads all of it
            return events, after

    events, after = asyncio.run(go())
    assert names(events)[-1] == "done" and len(fake.requests) == len(fake.counted) == 4
    assert [c["tool"] for c in held.log] == ["add_document_slot", "set_approval_chain"]
    assert len(after.rows) == len(chats[cid]["rows"]) == 9


def test_past_a_clients_turn_limit_a_turn_is_refused_before_its_claim(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
) -> None:
    """Each running turn holds a vault connection for its claim and needs one for its work, so a
    client runs at most VAULT_POOL - 1 at once (per API process)."""
    opened = asynccontextmanager(loop.opened)
    cids = [new(chats) for _ in range(VAULT_POOL)]

    async def go() -> None:
        async with AsyncExitStack() as running:
            for cid in cids[:-1]:
                await running.enter_async_context(opened(cid, held.ctx, ME))
            with pytest.raises(Conflict, match="already running for this client"):
                async with opened(cids[-1], held.ctx, ME):
                    pytest.fail("one turn too many ran")
        async with opened(cids[-1], held.ctx, ME):  # once they've ended
            pass

    asyncio.run(go())


def sse(body: str) -> list[tuple[str, Doc]]:
    """The events of a text/event-stream body (keep-alive comments skipped)."""
    events = []
    for chunk in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in chunk.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


def test_the_console_gets_the_turn_as_server_sent_events(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
    rows: dict[str, Any],
) -> None:
    app = create_app(resolver=Resolver(FakeRegistry(rows)))
    app.dependency_overrides[drafts.owner_client] = lambda: held.ctx
    app.state.anthropic = fake = Fake(*fire_noc())
    cid = new(chats)
    url = f"/owner/clients/{rows['pilot-local'].id}/agent/conversations/{cid}/messages"
    owner = bearer(token("owner", client=None, sub=str(OWNER)))

    async def meanwhile() -> httpx2.Response:
        async with loop._claimed(held.ctx, cid):  # a turn of cid is running
            return await asyncio.to_thread(api.post, url, headers=owner, json={"text": "Hi"})

    with TestClient(app) as api:
        response = api.post(url, headers=owner, json={"text": FIRE_NOC})
        unknown = api.post(
            url.replace(str(cid), str(uuid.uuid4())), headers=owner, json={"text": "Hi"}
        )
        stranger = api.post(url, headers=bearer(token("owner", client=None)), json={"text": "Hi"})
        empty = api.post(url, headers=owner, json={"text": ""})
        client = api.post(url, headers=bearer(token("client")), json={"text": "Hi"})
        busy = asyncio.run(meanwhile())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse(response.text)
    assert names(events) == [
        "text_delta",
        "tool_call",
        "tool_call",
        "change",
        "tool_call",
        "change",
        "text_delta",
        "done",
    ]
    assert events[3][1] == {"change_id": 1, "summary": "Added: Fire NOC upload"}
    assert events[-1][1]["usage"]["output_tokens"] == 1200
    # another owner's conversation is as missing as one that doesn't exist
    for missing in (unknown, stranger):
        assert missing.status_code == 404 and missing.json()["detail"] == loop.NO_CONVERSATION
    assert empty.status_code == 422 and client.status_code == 403
    assert busy.status_code == 409 and busy.json()["detail"] == loop.BUSY
    assert len(fake.requests) == len(fake.counted) == 4  # the first turn's; none for the others


def test_the_dock_reads_a_conversation_back_as_it_streamed(
    held: SimpleNamespace,  # noqa: F811
    chats: dict[uuid.UUID, Doc],
    rows: dict[str, Any],
) -> None:
    """#41: what the dock lists, and shows after a dropped stream or a reload, from the stored
    rows: the texts, each done edit's chip, refusals and the conversation's usage."""
    refused = reply(
        text("Adding"),
        stop="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "Declined."},
    )
    older, theirs = new(chats, "configure"), new(chats, owner=uuid.uuid4())
    fake, cid = Fake(*fire_noc(), refused), new(chats)
    first, second = run(fake, held, cid, FIRE_NOC), run(fake, held, cid, "Add a GST number")
    app = create_app(resolver=Resolver(FakeRegistry(rows)))
    app.dependency_overrides[drafts.owner_client] = lambda: held.ctx
    listing = f"/owner/clients/{rows['pilot-local'].id}/agent/conversations"
    url = f"{listing}/{cid}"
    owner = bearer(token("owner", client=None, sub=str(OWNER)))
    with TestClient(app) as api:
        listed = api.get(listing, headers=owner)
        read = api.get(url, headers=owner)
        stranger = api.get(url, headers=bearer(token("owner", client=None)))
        unknown = api.get(url.replace(str(cid), str(uuid.uuid4())), headers=owner)
        client = api.get(listing, headers=bearer(token("client")))
    # the owner's own, newest first
    assert [(c["id"], c["mode"]) for c in listed.json()] == [
        (str(cid), "edit"),
        (str(older), "configure"),
    ]
    assert str(theirs) not in listed.text and client.status_code == 403
    assert read.status_code == 200
    # the refused turn kept only its usage (and why it stopped), not the owner's message
    assert read.json()["entries"] == [
        {"kind": "owner", "text": FIRE_NOC},
        {"kind": "agent", "text": "Adding the upload."},
        {"kind": "change", "change_id": 1, "summary": "Added: Fire NOC upload"},
        {"kind": "change", "change_id": 2, "summary": "Approval of legal_licensing: legal L2"},
        {"kind": "agent", "text": "Done: Fire NOC upload with Legal L2 approval."},
        {"kind": "refusal", "category": "cyber"},
    ]
    turns = first[-1][1]["usage"], second[-1][1]["usage"]
    assert read.json()["usage"] == {k: sum(u[k] for u in turns) for k in loop.USAGE}
    for missing in (stranger, unknown):  # another owner's is as missing as none
        assert missing.status_code == 404 and missing.json()["detail"] == loop.NO_CONVERSATION


def test_the_edits_of_a_turn_stopped_mid_reply_read_back_where_they_happened() -> None:
    """The console leaving stops a turn between its tool runs, before the reply and its results
    are stored; the edits it made stay in the change log, and read back once each, in time."""
    t = [datetime(2026, 11, 6, 8, tzinfo=UTC) + timedelta(seconds=s) for s in range(4)]

    def said(role: str, content: Any, at: datetime, usage: Doc | None = None) -> Doc:
        return {"role": role, "content": content, "usage": usage, "at": at}

    def results(*found: Doc) -> list[Doc]:
        return [{"type": "tool_result", "content": json.dumps(r)} for r in found]

    used = dict.fromkeys(loop.USAGE, 1)
    rows = [  # turn 1: a read was stored, then it stopped after edit 1 (t[1])
        said("user", [text(FIRE_NOC)], t[0]),
        said("system", "Draft state: {}", t[0]),
        said("assistant", [text("Adding the upload."), tool_use("get_task", {})], t[0], used),
        said("user", results({"ok": True, "draft_sha256": "a"}), t[0]),
        # turn 2: edit 2 (t[2]) stored with its result at t[3]
        said("user", [text("Add a GST number")], t[3]),
        said("system", "Draft state: {}", t[3]),
        said("assistant", [tool_use("add_field", {})], t[3], used),
        said("user", results({"ok": True, "change_id": 2, "summary": "Added: GST"}), t[3]),
        said("assistant", [text("Done.")], t[3], used),
    ]
    changes = [
        {"id": 1, "summary": "Added: Fire NOC upload", "at": t[1]},
        {"id": 2, "summary": "Added: GST", "at": t[2]},
    ]
    read = loop.transcript(rows, changes).model_dump()
    assert read["entries"] == [
        {"kind": "owner", "text": FIRE_NOC},
        {"kind": "agent", "text": "Adding the upload."},
        {"kind": "change", "change_id": 1, "summary": "Added: Fire NOC upload"},
        {"kind": "owner", "text": "Add a GST number"},
        {"kind": "change", "change_id": 2, "summary": "Added: GST"},
        {"kind": "agent", "text": "Done."},
    ]
    assert read["usage"] == dict.fromkeys(loop.USAGE, 3)
    # stopped in the last turn: after everything stored
    assert loop.transcript(rows[:4], changes[:1]).model_dump()["entries"][-1] == read["entries"][2]


# --- integration: the pilot vault ---------------------------------------------------------------


EMPTY = (
    "set session_replication_role = replica; truncate app.agent_messages, app.agent_conversations"
)


@pytest.fixture
def agent_vault(pilot: Clients) -> Iterator[Clients]:  # noqa: F811
    """pilot's vault with the Matrix Retail draft and no conversations (the triggers refuse
    deletes and truncate, so they are switched off for the session that empties the tables)."""
    vault_sql("pilot", EMPTY)
    yield pilot
    vault_sql("pilot", EMPTY)


def owner_token(owner: uuid.UUID) -> dict[str, str]:
    claims = {"sub": str(owner), "kind": "owner", "exp": 4102444800}
    return bearer(jwt.encode(claims, JWT_SECRET, algorithm="HS256"))


def agent_api(c: Clients, fake: Fake) -> tuple[TestClient, dict[str, str], str]:
    app = create_app()
    app.state.anthropic = fake
    url = f"/owner/clients/{c.ids['pilot']}/agent/conversations"
    return TestClient(app), owner_token(c.owner), url


@pytest.mark.integration
def test_a_turn_edits_the_vaults_draft_and_stores_the_conversation(
    agent_vault: Clients,
) -> None:
    fake = Fake(*fire_noc())
    api, auth, url = agent_api(agent_vault, fake)
    with api:
        started = api.post(url, headers=auth, json={"mode": "edit"})
        cid = started.json()["id"]
        response = api.post(f"{url}/{cid}/messages", headers=auth, json={"text": FIRE_NOC})
        stranger = api.post(
            f"{url}/{cid}/messages", headers=owner_token(uuid.uuid4()), json={"text": "Hi"}
        )
    assert stranger.status_code == 404 and stranger.json()["detail"] == loop.NO_CONVERSATION
    assert len(fake.requests) == 4  # the owner's turn; nothing for another owner
    assert started.status_code == 201
    assert {k: started.json()[k] for k in ("owner_id", "mode")} == {
        "owner_id": str(agent_vault.owner),
        "mode": "edit",
    }
    events = sse(response.text)
    [(draft,)] = vault_sql("pilot", "select workflow from app.drafts")
    assert draft["fields"]["fire_noc"]["type"] == "document"
    assert task(draft, "legal_licensing")["approval"]["chain"] == L2["chain"]
    changes = vault_sql(
        "pilot", "select id, conversation_id::text from app.draft_changes order by id"
    )
    assert [d for n, d in events if n == "change"] == [
        {"change_id": changes[0][0], "summary": "Added: Fire NOC upload"},
        {"change_id": changes[1][0], "summary": "Approval of legal_licensing: legal L2"},
    ]
    assert {c for _, c in changes} == {cid}
    stored = vault_sql(
        "pilot",
        "select seq, role, content::text, usage is not null from app.agent_messages"
        " where conversation_id = %s order by seq",
        cid,
    )
    assert [(s, r, u) for s, r, _, u in stored] == [
        (0, "user", False),
        (1, "system", False),
        (2, "assistant", True),
        (3, "user", False),
        (4, "assistant", True),
        (5, "user", False),
        (6, "assistant", True),
        (7, "user", False),
        (8, "assistant", True),
    ]
    # json, not jsonb: a tool call's input comes back in the order the model wrote it
    assert '"input": {"task_id": "legal_licensing", "key": "fire_noc"' in stored[4][2]


@pytest.mark.integration
def test_the_conversation_tables_refuse_updates_deletes_and_truncate(agent_vault: Clients) -> None:
    api, auth, url = agent_api(agent_vault, Fake(reply(text("Hello."))))
    with api:
        cid = api.post(url, headers=auth, json={"mode": "configure"}).json()["id"]
        assert (
            api.post(f"{url}/{cid}/messages", headers=auth, json={"text": "Hi"}).status_code == 200
        )
    for statement in (
        "update app.agent_messages set content = '[]'",
        "delete from app.agent_messages",
        "truncate app.agent_messages",
        "update app.agent_conversations set mode = 'edit'",
        "delete from app.agent_conversations",
        "truncate app.agent_conversations cascade",
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="insert-only"):
            vault_sql("pilot", statement)  # the tables' owner: the triggers
        with (
            psycopg.connect(PILOT_DSN) as conn,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            conn.execute(statement)  # zm_api, the API's role: no grant
    assert vault_sql("pilot", "select count(*) from app.agent_messages") == [(3,)]


@pytest.mark.integration
def test_one_turn_at_a_time_per_conversation_and_others_run_meanwhile(
    agent_vault: Clients,
) -> None:
    fake = Fake(reply(text("Hello.")), reply(text("Hi.")))
    api, auth, url = agent_api(agent_vault, fake)

    def send(cid: str) -> httpx2.Response:
        return api.post(f"{url}/{cid}/messages", headers=auth, json={"text": "Hi"})

    async def go(cid: str, other: str) -> list[httpx2.Response]:
        async with Stack(agent_vault) as stack:
            ctx = await stack.ctx("pilot")
            async with loop._claimed(ctx, uuid.UUID(cid)):  # a turn of cid is running
                sent = [await asyncio.to_thread(send, cid), await asyncio.to_thread(send, other)]
            sent.append(await asyncio.to_thread(send, cid))
            async with loop._claimed(ctx, uuid.UUID(cid)):  # that turn let go when it ended
                return sent

    with api:
        cid, other = (api.post(url, headers=auth, json={"mode": "edit"}).json()["id"] for _ in "ab")
        busy, meanwhile, after = asyncio.run(go(cid, other))
    assert busy.status_code == 409 and busy.json()["detail"] == loop.BUSY
    assert meanwhile.status_code == after.status_code == 200
    assert len(fake.requests) == len(fake.counted) == 2  # none for the refused turn
    stored = vault_sql(
        "pilot", "select conversation_id::text, count(*) from app.agent_messages group by 1"
    )
    assert dict(stored) == {cid: 3, other: 3}


@pytest.mark.integration
def test_the_dock_lists_an_owners_conversations_and_reads_one_back(agent_vault: Clients) -> None:
    api, auth, url = agent_api(agent_vault, Fake(*fire_noc()))
    stranger = owner_token(uuid.uuid4())
    with api:
        older = api.post(url, headers=auth, json={"mode": "configure"}).json()
        newer = api.post(url, headers=auth, json={"mode": "edit"}).json()
        theirs = api.post(url, headers=stranger, json={"mode": "edit"}).json()
        api.post(f"{url}/{newer['id']}/messages", headers=auth, json={"text": FIRE_NOC})
        listed = [api.get(url, headers=h).json() for h in (auth, stranger)]
        read = api.get(f"{url}/{newer['id']}", headers=auth)
        hidden = api.get(f"{url}/{newer['id']}", headers=stranger)
    assert listed == [[newer, older], [theirs]]  # each owner's own, newest first
    changes = vault_sql(
        "pilot",
        "select id from app.draft_changes where conversation_id = %s order by id",
        newer["id"],
    )
    entries = read.json()["entries"]
    assert entries[0] == {"kind": "owner", "text": FIRE_NOC}
    assert [(e["change_id"], e["summary"]) for e in entries if e["kind"] == "change"] == [
        (changes[0][0], "Added: Fire NOC upload"),
        (changes[1][0], "Approval of legal_licensing: legal L2"),
    ]
    assert read.json()["usage"]["output_tokens"] == 1200
    assert hidden.status_code == 404 and hidden.json()["detail"] == loop.NO_CONVERSATION


@pytest.mark.integration
def test_the_edit_of_a_turn_stopped_mid_reply_reads_back_where_it_happened(
    agent_vault: Clients,
) -> None:
    """The console going away stops a turn between its tool runs, so its reply and their results
    aren't stored; the edit it made is in the change log and reads back in its place."""
    api, auth, url = agent_api(agent_vault, Fake(reply(text("Anything else?"))))

    async def stop(cid: uuid.UUID) -> None:
        async with Stack(agent_vault) as stack:
            ctx = await stack.ctx("pilot")
            conversation = await loop.load(ctx, cid, agent_vault.owner)
            events = loop.turn(Fake(*fire_noc()), ctx, agent_vault.owner, conversation, FIRE_NOC)
            async for name, _ in events:
                if name == "change":
                    break
            await events.aclose()  # stopped there, as a console gone away stops it

    with api:
        cid = api.post(url, headers=auth, json={"mode": "edit"}).json()["id"]
        asyncio.run(stop(uuid.UUID(cid)))
        api.post(f"{url}/{cid}/messages", headers=auth, json={"text": "Thanks"})
        read = api.get(f"{url}/{cid}", headers=auth)
    [(change,)] = vault_sql(
        "pilot", "select id from app.draft_changes where conversation_id = %s", cid
    )
    assert read.json()["entries"] == [
        {"kind": "owner", "text": FIRE_NOC},
        {"kind": "agent", "text": "Adding the upload."},
        {"kind": "change", "change_id": change, "summary": "Added: Fire NOC upload"},
        {"kind": "owner", "text": "Thanks"},
        {"kind": "agent", "text": "Anything else?"},
    ]


# --- live: the real API, by hand ----------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_live_smoke(held: SimpleNamespace, chats: dict[uuid.UUID, Doc]) -> None:  # noqa: F811
    """`uv run pytest -m live`: two turns on the in-memory draft with AGENT_MODEL."""
    cid = new(chats)

    async def go() -> list[list[tuple[str, Doc]]]:
        client = anthropic.AsyncAnthropic()
        turns = []
        for said in (FIRE_NOC, "Thanks. Is the draft valid now?"):
            conversation = await loop.load(held.ctx, cid, OWNER)
            turns.append([e async for e in loop.turn(client, held.ctx, OWNER, conversation, said)])
        return turns

    first, second = asyncio.run(go())
    assert "change" in names(first), first
    added = set(held.doc["fields"]) - set(matrix_retail()["fields"])
    assert [held.doc["fields"][k]["type"] for k in added] == ["document"]
    assert {"department": "legal", "level": 2} in task(held.doc, "legal_licensing")["approval"][
        "chain"
    ]
    assert second[-1][1]["usage"]["cache_read_input_tokens"] > 0
