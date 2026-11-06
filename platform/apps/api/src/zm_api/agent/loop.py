"""The agent loop (#38, S2-05; blueprint §10): an owner's conversation with Claude about one
client's draft, streamed to the console as server-sent events.

A turn appends the owner's message and a `{"role": "system"}` message with the draft as it is now
(its `draft_sha256`, which the next edit names, and a short overview), then calls the model until
it stops asking for tools: every `tool_use` of a reply runs through `agent.tools.call`, and all
the results go back in one user message, a failure as `is_error`. Between calls the request only
grows: the tools (sorted, `cache_control` on the last) and the system block (rules and catalogues,
no ids or times) are cached, and so is the history (top-level `cache_control`). Thinking stays
adaptive, effort is set per conversation (`high` to configure a client, `medium` for edits), tool
choice is `auto`, and a refused call falls back server-side (`fallbacks: "default"`).

History lives in the client's vault, insert-only (0009): replies go back unchanged, thinking
blocks too, since Opus 5.5 checks that earlier turns weren't edited. A reply is stored with the
messages it answers and, when it called tools, with their results, so the stored history always
replays. A call that stops (refusal, max_tokens) or answers nothing keeps only its usage, and the
rest of its turn is dropped. The budget, AGENT_MAX_TOKENS_PER_CONVERSATION, counts what each
call read fresh or wrote (input, cache writes, output; cache reads, at a twentieth of the price,
don't). `count_tokens` sizes each call first, and a call that could pass the budget if none of it
were cached isn't made. One turn runs at a time per conversation: the route claims it (a session
advisory lock in the vault) before reading it and lets go when the stream ends, so a turn sent
meanwhile is a 409 that calls nothing.
"""

import json
import logging
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

import anthropic
from fastapi import Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import text

from zm_api import drafts, settings, templates
from zm_api.agent import tools
from zm_api.context import ClientContext, OwnerCtx, owner_router
from zm_api.drafts import OwnedClient
from zm_api.errors import Conflict, NotFound
from zm_schema import catalogue
from zm_schema.models import FieldType

log = logging.getLogger("zm_api.agent")

Doc = dict[str, Any]
Event = tuple[str, Doc]
Mode = Literal["configure", "edit"]
EFFORT: dict[str, str] = {"configure": "high", "edit": "medium"}
BETAS = ["server-side-fallback-2026-07-01"]
MAX_TOKENS = 64_000  # per call: the reply and its thinking
JSON_RETRIES = 2
USAGE = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
NO_CONVERSATION = "No such conversation"
BUSY = "A turn is already running in this conversation. Send again when it has finished."
OVER_BUDGET = (
    "This conversation has used its token budget. Start a new conversation to go on;"
    " the draft keeps every change."
)
CUT_OFF = "The reply was cut off before it finished, so nothing in it was applied. Try again."

RULES = (
    "You are the Z-Matrix admin agent. A platform owner describes one client's process; you"
    " change that client's draft workflow.json, only through your tools. Every edit names the"
    " draft_sha256 of your latest read or edit (the latest draft state message has the current"
    " one). You can't publish, migrate running sites, manage people or change the look (theme"
    " preset and accent): a person does those."
)


@cache
def system() -> list[Doc]:
    """The system block: the rules and what the tools can place. It changes only with a platform
    release, so it is cached (the prompt itself comes with #40)."""
    ui = catalogue.load()
    reference = {
        "field_types": {t: {"v1_renders": t in tools.V1_RENDERS} for t in get_args(FieldType)},
        "layouts": ui["layouts"],
        "blocks": ui["blocks"],
        "templates": {t.key: t.name for t in templates.load_templates()},
    }
    body = f"{RULES}\n\nReference: {json.dumps(reference, sort_keys=True)}"
    return [{"type": "text", "text": body, "cache_control": {"type": "ephemeral"}}]


@cache
def tool_list() -> list[Doc]:
    """`tools.definitions()` sorted, each streaming its input as it is written (`agent.tools.call`
    validates every input), the last one cached."""
    found = [{**d, "eager_input_streaming": True} for d in tools.definitions()]
    found.sort(key=lambda d: d["name"])
    found[-1] = {**found[-1], "cache_control": {"type": "ephemeral"}}
    return found


def _state(doc: Doc, sha: str) -> Doc:
    """The turn's system message: the draft as it is now, in brief (configuration only)."""
    o = tools.overview(doc)
    brief = {
        "draft_sha256": sha,
        "departments": [d["key"] for d in o["departments"]],
        "stages": {f["key"]: [s["key"] for s in f["stages"]] for f in o["flows"]},
        "fields": len(o["fields"]),
        "forms": len(o["forms"]),
    }
    return {"role": "system", "content": f"Draft state: {json.dumps(brief)}"}


# --- the vault ---------------------------------------------------------------------------------


async def _create(ctx: ClientContext, owner: UUID, mode: str) -> Doc:
    async with ctx.db.begin() as conn:
        result = await conn.execute(
            text(
                "insert into app.agent_conversations (owner_id, mode) values (:o, :m)"
                " returning id, owner_id, mode, started_at"
            ),
            {"o": owner, "m": mode},
        )
        return dict(result.mappings().one())


async def _stored(ctx: ClientContext, conversation_id: UUID) -> tuple[str, list[Doc]]:
    """The conversation's mode and its rows in order; 404 if there is none."""
    async with ctx.db.connect() as conn:
        mode = (
            await conn.execute(
                text("select mode from app.agent_conversations where id = :c"),
                {"c": conversation_id},
            )
        ).scalar_one_or_none()
        if mode is None:
            raise NotFound(NO_CONVERSATION)
        rows = await conn.execute(
            text(
                "select role, content, usage from app.agent_messages"
                " where conversation_id = :c order by seq"
            ),
            {"c": conversation_id},
        )
        return mode, [dict(r) for r in rows.mappings()]


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


@asynccontextmanager
async def _claimed(ctx: ClientContext, conversation_id: UUID) -> AsyncIterator[None]:
    """The conversation for one turn, across API processes: a session advisory lock on its id, on
    a connection of its own until the turn ends (the tables are insert-only, so no row can say
    busy); 409 while another turn holds it."""
    k = f"zm_agent:{conversation_id}"
    # ponytail: a running turn holds one of the vault engine's 5 connections; past 4 turns at
    # once per client its tools wait on the pool, so raise pool_size if that ever happens
    async with ctx.db.connect() as lock:
        got = (
            await lock.execute(text("select pg_try_advisory_lock(hashtext(:k))"), {"k": k})
        ).scalar_one()
        await lock.commit()
        if not got:
            raise Conflict(BUSY)
        try:
            yield
        finally:  # the lock is per session, and the connection goes back to the pool
            await lock.execute(text("select pg_advisory_unlock(hashtext(:k))"), {"k": k})
            await lock.commit()


async def _append(ctx: ClientContext, conversation_id: UUID, seq: int, rows: list[Doc]) -> None:
    """Store `rows` after the first `seq`, in one transaction. The claim keeps turns on one
    conversation apart; the primary key is the backstop."""
    async with ctx.db.begin() as conn:
        await conn.execute(
            text(
                "insert into app.agent_messages (conversation_id, seq, role, content, usage)"
                " values (:c, :s, :role, cast(:content as json), cast(:usage as jsonb))"
            ),
            [
                {
                    "c": conversation_id,
                    "s": seq + i,
                    "role": r["role"],
                    "content": _json(r["content"]),
                    "usage": _json(r.get("usage")),
                }
                for i, r in enumerate(rows)
            ],
        )


# --- the loop ----------------------------------------------------------------------------------


@dataclass
class Conversation:
    id: UUID
    mode: str
    rows: list[Doc]
    """Every stored message `{role, content, usage}`; a stopped call's content is None."""
    state: Doc
    """This turn's system message: the draft as it is now."""


async def load(ctx: ClientContext, conversation_id: UUID) -> Conversation:
    """The conversation and the draft for a new turn; 404 if either is missing."""
    mode, rows = await _stored(ctx, conversation_id)
    async with ctx.db.connect() as conn:
        doc, sha = await drafts.read(conn)
    return Conversation(conversation_id, mode, rows, _state(doc, sha))


def _messages(rows: list[Doc]) -> list[Doc]:
    return [{"role": r["role"], "content": r["content"]} for r in rows if r["content"] is not None]


def _fresh(usage: Doc) -> int:
    """What the budget counts of a call."""
    return (
        usage["input_tokens"]
        + (usage.get("cache_creation_input_tokens") or 0)
        + usage["output_tokens"]
    )


def _echo(content: list[Any]) -> list[Doc]:
    """A reply as the next call sends it back: unchanged, except after a fallback mid-reply, where
    the declined model's thinking and tool calls before the last `fallback` block go (its text
    stays), as the API asks."""
    blocks = [b.to_dict(mode="json") for b in content]
    cut = max((i for i, b in enumerate(blocks) if b["type"] == "fallback"), default=0)
    return [b for b in blocks[:cut] if b["type"] == "text"] + blocks[cut:]


async def _run(ctx: ClientContext, owner: UUID, conversation_id: UUID, name: str, args: Any) -> Doc:
    try:
        return await tools.call(ctx, owner, name, args, conversation_id)
    except Exception:  # Claude hears of it; an edit's transaction has left the draft as it was
        log.exception("agent tool failed", extra={"client": ctx.client.slug, "op": name})
        return {"ok": False, "summary": f"{name} failed"}


async def turn(
    client: Any, ctx: ClientContext, owner: UUID, conversation: Conversation, said: str
) -> AsyncIterator[Event]:
    """One turn: `said`, then calls and tool runs until the model is done, as events
    `text_delta {text}`, `tool_call {name}`, `change {change_id, summary}`, `refusal {category}`,
    `error {message}`, always ending with `done {usage}` (the turn's sums). `client` is an
    `anthropic.AsyncAnthropic`."""
    unsaved = [{"role": "user", "content": [{"type": "text", "text": said}]}, conversation.state]
    spent = sum(_fresh(r["usage"]) for r in conversation.rows if r.get("usage"))
    total = dict.fromkeys(USAGE, 0)
    model = settings.agent_model()

    async def save(rows: list[Doc]) -> None:
        await _append(ctx, conversation.id, len(conversation.rows), rows)
        conversation.rows.extend(rows)

    try:
        while True:
            request = {
                "model": model,
                "system": system(),
                "tools": tool_list(),
                "messages": _messages(conversation.rows + unsaved),
            }
            counted = (await client.messages.count_tokens(**request)).input_tokens
            if spent + counted > settings.agent_max_tokens_per_conversation():
                yield "error", {"message": OVER_BUDGET}
                break
            reply = None
            for _ in range(1 + JSON_RETRIES):
                try:
                    async with client.beta.messages.stream(
                        **request,
                        max_tokens=MAX_TOKENS,
                        tool_choice={"type": "auto"},
                        output_config={"effort": EFFORT[conversation.mode]},
                        cache_control={"type": "ephemeral"},
                        betas=BETAS,
                        fallbacks="default",
                    ) as stream:
                        async for event in stream:
                            if event.type == "text":
                                yield "text_delta", {"text": event.text}
                        reply = await stream.get_final_message()
                    break
                except ValueError:  # a tool input the SDK couldn't parse at all: ask again
                    log.warning("agent reply unparseable", extra={"client": ctx.client.slug})
            if reply is None:
                yield "error", {"message": "The model's replies couldn't be read. Try again."}
                break

            usage = reply.usage.to_dict(mode="json")
            spent += _fresh(usage)
            for k in USAGE:
                total[k] += usage.get(k) or 0
            fallback = any(i.type == "fallback_message" for i in reply.usage.iterations or [])
            noted = {k: usage.get(k) for k in USAGE}
            noted |= {"fallback": fallback, "stop_reason": reply.stop_reason}
            log.info("agent call", extra={"client": ctx.client.slug, "usage": noted})

            content = _echo(reply.content)
            # nothing of it is applied; an empty reply can't be sent back either
            if reply.stop_reason in ("refusal", "max_tokens") or not content:
                await save([{"role": "assistant", "content": None, "usage": usage}])
                if reply.stop_reason == "max_tokens":
                    yield "error", {"message": CUT_OFF}
                elif reply.stop_reason == "refusal":
                    details = reply.stop_details
                    yield "refusal", {"category": details.category if details else None}
                break
            unsaved.append({"role": "assistant", "content": content, "usage": usage})
            uses = [b for b in content if b["type"] == "tool_use"]
            if not uses:
                await save(unsaved)
                break
            results, ours, latest = [], set(), None
            for block in uses:
                yield "tool_call", {"name": block["name"]}
                args = block["input"]
                # a reply's edits all name the sha it read: each runs on the one the edit before
                # it left (a person's edit in between still makes it stale)
                if latest and isinstance(args, dict) and args.get("draft_sha256") in ours:
                    args = {**args, "draft_sha256": latest}
                result = await _run(ctx, owner, conversation.id, block["name"], args)
                if "change_id" in result:
                    yield "change", {"change_id": result["change_id"], "summary": result["summary"]}
                    latest = result["draft_sha256"]
                    ours |= {args["draft_sha256"], latest}
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": json.dumps(result),
                        "is_error": not result["ok"],
                    }
                )
            await save([*unsaved, {"role": "user", "content": results}])
            unsaved = []
    except anthropic.APIError as e:  # the SDK has already retried 429s, 5xx and lost connections
        log.exception("agent call failed", extra={"client": ctx.client.slug})
        yield "error", {"message": f"The model is unavailable ({type(e).__name__}). Try again."}
    yield "done", {"usage": total}


# --- API: /owner/clients/{client_id}/agent ----------------------------------------------------


class ConversationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)

    mode: Mode
    """`configure` sets a client up (effort high); `edit` changes it later (effort medium)."""


class ConversationOut(BaseModel):
    id: UUID
    owner_id: UUID
    mode: Mode
    started_at: datetime


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]


def anthropic_client(request: Request) -> anthropic.AsyncAnthropic:
    """The app's client (ANTHROPIC_API_KEY), made on first use; tests set a fake."""
    if getattr(request.app.state, "anthropic", None) is None:
        request.app.state.anthropic = anthropic.AsyncAnthropic()
    return request.app.state.anthropic


async def opened(conversation_id: UUID, ctx: OwnedClient) -> AsyncIterator[Conversation]:
    """Claimed, then loaded, before the stream starts, so a missing one is a plain 404 and one
    with a turn running a plain 409; the claim is held until the stream ends."""
    async with _claimed(ctx, conversation_id):
        yield await load(ctx, conversation_id)


routes = owner_router()
AGENT = "/clients/{client_id}/agent/conversations"


@routes.post(AGENT, status_code=201)
async def start_conversation(
    client_id: UUID, body: ConversationIn, ctx: OwnedClient, owner: OwnerCtx
) -> ConversationOut:
    """A new conversation with the client's agent."""
    return ConversationOut(**await _create(ctx, owner.owner_id, body.mode))


@routes.post(AGENT + "/{conversation_id}/messages", response_class=EventSourceResponse)
async def send_message(
    client_id: UUID,
    body: MessageIn,
    conversation: Annotated[Conversation, Depends(opened)],
    ctx: OwnedClient,
    owner: OwnerCtx,
    client: Annotated[anthropic.AsyncAnthropic, Depends(anthropic_client)],
) -> AsyncIterable[ServerSentEvent]:
    """One turn, streamed: `text_delta {text}`, `tool_call {name}`, `change {change_id, summary}`,
    `refusal {category}` and `error {message}` events, then `done {usage}`."""
    async for name, data in turn(client, ctx, owner.owner_id, conversation, body.text):
        yield ServerSentEvent(event=name, data=data)
