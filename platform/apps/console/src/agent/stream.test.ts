import { expect, test, vi } from "vitest";
import { readEvents, type AgentEvent } from "./stream";

/** A body that sends `chunks`, then stays open; `cancel` hears when the reader lets go. */
function body(chunks: string[], cancel: () => void) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
    },
    cancel,
  });
}

test("a malformed frame rejects and cancels the body, so the connection isn't left open", async () => {
  const cancel = vi.fn();
  const seen: AgentEvent[] = [];
  const read = readEvents(body(['event: text_delta\ndata: {"text": "Hi"}\n\n', "event: done\ndata: {not json\n\n"], cancel), (e) => seen.push(e));
  await expect(read).rejects.toThrow(SyntaxError);
  expect(seen).toEqual([{ event: "text_delta", data: { text: "Hi" } }]);
  expect(cancel).toHaveBeenCalled();
});

test("a handler that throws cancels the body too", async () => {
  const cancel = vi.fn();
  const read = readEvents(body(['event: retry\ndata: {}\n\n'], cancel), () => {
    throw new Error("render failed");
  });
  await expect(read).rejects.toThrow("render failed");
  expect(cancel).toHaveBeenCalled();
});
