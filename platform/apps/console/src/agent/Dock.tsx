// The agent dock (#41, S2-07): an owner's conversations with a client's agent, a chip per change
// it makes (click: that change's patch), Undo, and Preview/Publish. A turn streams as server-sent
// events (stream.ts). A dropped stream can't be resumed (the API stops the turn and keeps its done
// edits), so the dock reads the conversation back, with backoff and offline too, until the API says
// no turn is running in it, then shows what was kept; the turn stays locked until then.
import { Button, Code, Group, Loader, Modal, NativeSelect, Paper, SegmentedControl, Stack, Text, Textarea } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@zm/api-client";
import { formatNum, Notice, notifyError, notifyInfo, StatusPill } from "@zm/ui";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, must } from "../session";
import { EMPTY, readEvents, step, type Chat, type Entry, type Usage } from "./stream";

type Mode = components["schemas"]["ConversationOut"]["mode"];

const MODES: Record<Mode, string> = { configure: "Configure", edit: "Edit" };
const LOST = "The connection dropped. This is what the conversation kept; the draft keeps every change the agent finished.";
const OFFLINE = "Can't reach Z-Matrix right now. Check your connection and try again.";
const NOT_SENT = "Your message wasn't sent.";
const WAITING = "The connection dropped. Waiting for the agent's turn to end…";
const RETRY_MS = 1000; // a dropped turn's read-back: 1 s, 2 s, 4 s… up to MAX_RETRY_MS apart
const MAX_RETRY_MS = 30_000;

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

const tokens = (u: Usage) =>
  `${formatNum(u.input_tokens + u.cache_creation_input_tokens)} in, ${formatNum(u.cache_read_input_tokens)} cached, ${formatNum(u.output_tokens)} out`;
// fetch rejects with a TypeError when there's no connection
const reason = (e: unknown, fallback: string) => (e instanceof Error && !(e instanceof TypeError) && e.message !== "Request failed" ? e.message : fallback);

function Line({ entry, onOpen }: { entry: Entry; onOpen: (changeId: number) => void }) {
  switch (entry.kind) {
    case "owner":
    case "agent":
      return (
        <div>
          <Text size="xs" fw={600}>
            {entry.kind === "owner" ? "You" : "Agent"}
          </Text>
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {entry.text}
          </Text>
        </div>
      );
    case "change":
    case "undo":
      return (
        <Group gap="xs">
          {entry.kind === "undo" && <StatusPill tone="queued">Undone</StatusPill>}
          <Button variant="light" size="compact-sm" onClick={() => onOpen(entry.change_id)}>
            {entry.summary}
          </Button>
        </Group>
      );
    case "refusal":
      return (
        <Notice tone="warning" title="The agent declined this request">
          Nothing in it was applied{entry.category ? ` (${entry.category})` : ""}.
        </Notice>
      );
    case "error":
      return <Notice tone="error" title={entry.message} />;
  }
}

/** `onTurn` hears when a turn starts and ends, so the client page's own Publish can wait for it. */
export function Dock({ clientId, onTurn }: { clientId: string; onTurn?: (turning: boolean) => void }) {
  const queries = useQueryClient();
  const path = { params: { path: { client_id: clientId } } };
  const conversations = useQuery({
    queryKey: ["conversations", clientId],
    queryFn: () => must(api.GET("/owner/clients/{client_id}/agent/conversations", path)),
  });
  const problems = useQuery({ queryKey: ["validate", clientId], queryFn: () => must(api.GET("/owner/clients/{client_id}/draft/validate", path)) });
  const changes = useQuery({
    queryKey: ["changes", clientId],
    queryFn: () => must(api.GET("/owner/clients/{client_id}/draft/changes", { params: { ...path.params, query: { limit: 500 } } })),
  });
  const [current, setCurrent] = useState<string | null>(null); // null: a new conversation, started by its first message
  const showing = useRef<string | null>(null); // the same, for a read landing after the owner moved on
  const [mode, setMode] = useState<Mode>("configure");
  const [chat, setChat] = useState<Chat>(EMPTY);
  const [said, setSaid] = useState("");
  const [running, setRunning] = useState<string | null>(null); // a turn: "" thinking, else the tool it runs
  const [waiting, setWaiting] = useState(false); // its stream dropped: reading back until it ends
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => void (mounted.current = false);
  }, []);
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState<number | null>(null); // the change whose patch is open
  const [confirming, setConfirming] = useState(false);
  const ids = { preview: useId(), invalid: useId(), turning: useId(), unchecked: useId() };

  const add = (entry: Entry) => setChat((c) => ({ ...c, open: false, entries: [...c.entries, entry] }));
  const refresh = () => Promise.all(["changes", "validate", "draft"].map((key) => queries.invalidateQueries({ queryKey: [key, clientId] })));
  const read = async (id: string): Promise<{ chat: Chat; running: boolean }> => {
    const conversation = { params: { path: { client_id: clientId, conversation_id: id } } };
    const stored = await must(api.GET("/owner/clients/{client_id}/agent/conversations/{conversation_id}", conversation));
    return { chat: { ...EMPTY, entries: stored.entries, usage: stored.usage }, running: stored.running };
  };
  /** The conversation once no turn runs in it: what a dropped turn kept, all stored. Null if the dock went away. */
  const settled = async (id: string): Promise<Chat | null> => {
    for (let wait = RETRY_MS; mounted.current; wait = Math.min(wait * 2, MAX_RETRY_MS)) {
      try {
        const stored = await read(id);
        if (!stored.running) return stored.chat;
      } catch {
        // offline, or the API is: read again
      }
      await sleep(wait);
    }
    return null;
  };

  async function open(id: string | null, as: Mode) {
    showing.current = id;
    setCurrent(id);
    setMode(as);
    setChat(EMPTY);
    if (!id) return;
    try {
      const { chat: stored } = await read(id);
      if (showing.current === id) setChat(stored);
    } catch (e) {
      if (showing.current === id) add({ kind: "error", message: reason(e, "Couldn't load this conversation.") });
    }
  }

  async function send(e?: FormEvent) {
    e?.preventDefault();
    const text = said.trim();
    if (!text || running !== null) return;
    setRunning("");
    onTurn?.(true);
    let id = current;
    let streaming = false;
    try {
      if (!id) {
        id = (await must(api.POST("/owner/clients/{client_id}/agent/conversations", { ...path, body: { mode } }))).id;
        showing.current = id;
        setCurrent(id);
        void queries.invalidateQueries({ queryKey: ["conversations", clientId] });
      }
      const { data, error } = await api.POST("/owner/clients/{client_id}/agent/conversations/{conversation_id}/messages", {
        params: { path: { client_id: clientId, conversation_id: id } },
        body: { text },
        parseAs: "stream",
      });
      if (!data) return add({ kind: "error", message: error?.detail ?? "Couldn't send the message." }); // 409: busy or crowded
      streaming = true;
      setSaid("");
      add({ kind: "owner", text });
      let done = false;
      await readEvents(data, (event) => {
        done ||= event.event === "done";
        setChat((c) => step(c, event));
        setRunning(event.event === "tool_call" ? event.data.name : "");
        if (event.event === "change") void refresh();
      });
      if (!done) throw new Error("The stream ended before the turn did");
    } catch (e) {
      if (!id || !streaming) return add({ kind: "error", message: `${NOT_SENT} ${reason(e, OFFLINE)}` });
      // the turn may still be running and editing the draft: Send and Publish wait until it ends
      setWaiting(true);
      const stored = await settled(id);
      if (stored && showing.current === id) setChat({ ...stored, entries: [...stored.entries, { kind: "error", message: LOST }] });
    } finally {
      setWaiting(false);
      setRunning(null);
      onTurn?.(false);
      void refresh();
    }
  }

  async function undo() {
    setBusy(true);
    try {
      const { error } = await api.POST("/owner/clients/{client_id}/draft/undo", path);
      if (error) return add({ kind: "error", message: error.detail ?? "Couldn't undo." }); // 409: nothing to undo, or a published field
      // the undo route answers the draft: its change is the log's newest undo (another edit may have landed since)
      const rows = await must(api.GET("/owner/clients/{client_id}/draft/changes", { params: { ...path.params, query: { limit: 10 } } }));
      const row = rows.find((r) => r.undoes !== null);
      if (row) add({ kind: "undo", change_id: row.id, summary: row.summary });
      await refresh();
    } catch (e) {
      add({ kind: "error", message: reason(e, OFFLINE) });
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    setBusy(true);
    try {
      // #43 replaces this confirm with the publish dialog (diff summary, versioning warnings)
      const done = await must(api.POST("/owner/clients/{client_id}/publish", path));
      setConfirming(false);
      if (done.created) notifyInfo("Published", `Release v${done.version} is live.`);
      else notifyInfo("No new release", `Nothing changed since v${done.version}.`);
      await Promise.all([queries.invalidateQueries({ queryKey: ["client", clientId] }), refresh()]);
    } catch (e) {
      notifyError(reason(e, "Couldn't publish."));
    } finally {
      setBusy(false);
    }
  }

  const first = problems.data?.[0];
  const unchecked = problems.isError ? reason(problems.error, OFFLINE) : null;
  const diff = changes.data?.find((c) => c.id === shown);
  const turning = running !== null;
  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <Group align="flex-end">
          <NativeSelect
            label="Conversation"
            value={current ?? ""}
            disabled={turning}
            onChange={(e) => {
              const picked = conversations.data?.find((c) => c.id === e.currentTarget.value);
              void open(picked?.id ?? null, picked?.mode ?? mode);
            }}
            data={[
              { value: "", label: "New conversation" },
              ...(conversations.data ?? []).map((c) => ({ value: c.id, label: `${new Date(c.started_at).toLocaleString()} · ${MODES[c.mode]}` })),
            ]}
          />
          <SegmentedControl
            aria-label="Mode"
            value={mode}
            disabled={turning}
            onChange={(m) => void open(null, m as Mode)}
            data={Object.entries(MODES).map(([value, label]) => ({ value, label }))}
          />
        </Group>
        <Group gap="xs">
          <Button variant="default" loading={busy} disabled={turning} onClick={() => void undo()}>
            Undo last change
          </Button>
          <Button variant="default" disabled aria-describedby={ids.preview}>
            Preview
          </Button>
          <Button
            disabled={turning || problems.isFetching || !problems.data || problems.data.length > 0 || unchecked !== null}
            aria-describedby={turning ? ids.turning : unchecked !== null ? ids.unchecked : first && ids.invalid}
            onClick={() => setConfirming(true)}
          >
            Publish
          </Button>
        </Group>
      </Group>
      <Text id={ids.preview} size="sm">
        Preview comes with #42.
      </Text>
      {turning && (
        <Text id={ids.turning} size="sm">
          Publish waits until the agent's turn ends.
        </Text>
      )}
      {unchecked !== null && (
        <Text id={ids.unchecked} size="sm">
          Couldn't check the draft, so it can't be published yet. {unchecked}
        </Text>
      )}
      {first && (
        <Text id={ids.invalid} size="sm">
          The draft can't be published yet: <Code>{first.path}</Code> {first.message}
        </Text>
      )}

      <Paper withBorder p="md">
        <Stack gap="sm" role="log" aria-live="polite" aria-label="Chat" mih={160}>
          {chat.entries.length === 0 && <Text size="sm">Describe the client's process, or what to change; the agent edits the draft.</Text>}
          {chat.entries.map((entry, i) => (
            <Line key={"change_id" in entry ? `change-${entry.change_id}` : i} entry={entry} onOpen={setShown} />
          ))}
        </Stack>
      </Paper>
      {turning && (
        <Group gap="xs" role="status">
          <Loader size="xs" aria-hidden />
          <Text size="sm">{waiting ? WAITING : running ? `Running ${running}…` : "The agent is working…"}</Text>
        </Group>
      )}

      <form onSubmit={send}>
        <Stack gap="xs">
          <Textarea
            label="Message"
            description="Enter sends; Shift+Enter starts a new line."
            autosize
            minRows={2}
            value={said}
            onChange={(e) => setSaid(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <Group justify="space-between">
            <Text size="sm">
              Tokens: {chat.last && `last turn ${tokens(chat.last)}; `}this conversation {tokens(chat.usage)}.
            </Text>
            <Button type="submit" loading={turning}>
              Send
            </Button>
          </Group>
        </Stack>
      </form>

      <Modal opened={shown !== null} onClose={() => setShown(null)} title={diff?.summary ?? "Change"}>
        {diff ? <Code block>{JSON.stringify(diff.patch, null, 2)}</Code> : <Text size="sm">This change isn't among the latest 500.</Text>}
      </Modal>
      <Modal opened={confirming} onClose={() => setConfirming(false)} title="Publish the draft?">
        <Stack>
          <Text size="sm">The draft is compiled and deployed to the client's own Operaton as its next release. Sites already running stay on theirs.</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button loading={busy} onClick={() => void publish()}>
              Publish
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
