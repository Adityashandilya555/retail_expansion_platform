// The agent loop's events (#38, zm_api.agent.loop.turn) as the dock reads them (#41). A turn
// streams over POST, which EventSource can't send, so the body is read and parsed here.
import type { components } from "@zm/api-client";

export type Usage = components["schemas"]["Usage"];
/** What the conversation shows: the stored entries, plus an undo's chip and errors, which only the console keeps. */
export type Entry =
  | components["schemas"]["Transcript"]["entries"][number]
  | { kind: "undo"; change_id: number; summary: string }
  | { kind: "error"; message: string };

/** One turn's events, typed by hand: server-sent events aren't in the OpenAPI document. */
export type AgentEvent =
  | { event: "text_delta"; data: { text: string } }
  | { event: "retry"; data: Record<string, never> } // drop the text this call streamed so far
  | { event: "tool_call"; data: { name: string } }
  | { event: "change"; data: { change_id: number; summary: string } }
  | { event: "refusal"; data: { category: string | null } }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: { usage: Usage } }; // always the last: the turn's sums

/** `open`: the last entry is the text the current call is still streaming, which `retry` drops. */
export type Chat = { entries: Entry[]; open: boolean; usage: Usage; last: Usage | null };

export const EMPTY: Chat = {
  entries: [],
  open: false,
  usage: { input_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, output_tokens: 0 },
  last: null,
};

/** Calls `on` with each event of a text/event-stream body until it ends; rejects if the connection
 * drops, a frame isn't JSON or `on` throws, and then cancels the body so the connection closes. */
export async function readEvents(body: ReadableStream<Uint8Array>, on: (event: AgentEvent) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? ""; // an event cut across chunks waits for the rest
      for (const block of blocks) {
        let event = "";
        let data = "";
        for (const line of block.split("\n")) {
          const [, field, rest = ""] = /^(event|data): ?(.*)$/.exec(line) ?? [];
          if (field === "event") event = rest;
          else if (field === "data") data += rest;
        }
        if (event && data) on({ event, data: JSON.parse(data) } as AgentEvent); // a keep-alive comment has neither
      }
    }
  } finally {
    reader.cancel().catch(() => undefined); // ended or dropped already: nothing to cancel
  }
}

/** The chat after one event. */
export function step(chat: Chat, e: AgentEvent): Chat {
  const last = chat.entries.at(-1);
  const add = (entry: Entry): Chat => ({ ...chat, open: false, entries: [...chat.entries, entry] });
  switch (e.event) {
    case "text_delta":
      if (chat.open && last?.kind === "agent") {
        return { ...chat, entries: [...chat.entries.slice(0, -1), { ...last, text: last.text + e.data.text }] };
      }
      return { ...add({ kind: "agent", text: e.data.text }), open: true };
    case "retry":
      return { ...chat, open: false, entries: chat.open ? chat.entries.slice(0, -1) : chat.entries };
    case "change":
      return add({ kind: "change", ...e.data });
    case "refusal":
      return add({ kind: "refusal", ...e.data });
    case "error":
      return add({ kind: "error", ...e.data });
    case "done": {
      const usage = { ...chat.usage };
      for (const k of Object.keys(usage) as (keyof Usage)[]) usage[k] += e.data.usage[k];
      return { ...chat, open: false, usage, last: e.data.usage };
    }
    default: // tool_call, and any event a newer API adds
      return { ...chat, open: false };
  }
}
