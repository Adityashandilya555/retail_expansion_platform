// Clients, their provisioning and publishing (#29). Provisioning itself is the worker's
// (#24, #25): the API queues the steps, and the client page polls them every 5 s.
import { Button, Code, Group, Modal, NativeSelect, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@zm/api-client";
import { DataTable, Notice, notifyError, notifyInfo, PageHeader, StatusPill, type StatusTone } from "@zm/ui";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router";
import { api } from "./session";

type ClientOut = components["schemas"]["ClientOut"];
type StepOut = components["schemas"]["StepOut"];
type ReleaseOut = components["schemas"]["ReleaseOut"];

const CLIENT_STATUS: Record<ClientOut["status"], [StatusTone, string]> = {
  new: ["queued", "New"],
  provisioning: ["open", "Provisioning"],
  ready: ["done", "Ready"],
  failed: ["closed", "Failed"],
  suspended: ["closed", "Suspended"],
};
const STEP_STATUS: Record<StepOut["status"], [StatusTone, string]> = {
  pending: ["queued", "Pending"],
  running: ["open", "Running"],
  done: ["done", "Done"],
  failed: ["closed", "Failed"],
  needs_human: ["warning", "Needs a person"],
};

function Status<S extends string>({ of, status }: { of: Record<S, [StatusTone, string]>; status: S }) {
  const [tone, label] = of[status];
  return <StatusPill tone={tone}>{label}</StatusPill>;
}

async function must<T>(call: Promise<{ data?: T; error?: { detail?: string | null } }>): Promise<T> {
  const { data, error } = await call;
  if (data === undefined) throw new Error(error?.detail ?? "Request failed");
  return data;
}

// --- `/`: the clients ------------------------------------------------------------------------

export function ClientsList() {
  const navigate = useNavigate();
  const clients = useQuery({ queryKey: ["clients"], queryFn: () => must(api.GET("/owner/clients")) });
  return (
    <>
      <PageHeader title="Clients" actions={<Button onClick={() => navigate("/clients/new")}>New client</Button>} />
      <DataTable
        caption="Clients"
        rows={clients.data ?? []}
        rowKey={(c) => c.id}
        onRowClick={(c) => navigate(`/clients/${c.id}`)}
        loading={clients.isPending}
        error={clients.isError ? "Couldn't load the clients." : null}
        onRetry={() => void clients.refetch()}
        emptyText="No clients yet"
        columns={[
          { key: "name", header: "Name", sortValue: (c) => c.name },
          { key: "workspace_code", header: "Workspace code", render: (c) => <Code>{c.workspace_code}</Code> },
          { key: "status", header: "Status", render: (c) => <Status of={CLIENT_STATUS} status={c.status} /> },
          { key: "last_release", header: "Last release", render: (c) => (c.last_release ? `v${c.last_release}` : "—") },
        ]}
      />
    </>
  );
}

// --- `/clients/new` ---------------------------------------------------------------------------

const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/;
const CODE = /^[A-Za-z0-9][A-Za-z0-9-]{2,63}$/;
const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

/** Same rule as the API's `slugify`: `Matrix Retail` → `matrix-retail`. */
const slugify = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/, "");

type Field = "name" | "slug" | "workspace_code" | "template_key";

export function NewClient() {
  const navigate = useNavigate();
  const queries = useQueryClient();
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => must(api.GET("/owner/templates")) });
  const [suffix] = useState(() => Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => ALPHABET[b % 36]).join(""));
  const [name, setName] = useState("");
  const [slugTyped, setSlug] = useState<string | null>(null); // null: follows the name
  const [codeTyped, setCode] = useState<string | null>(null); // null: follows the slug
  const [template, setTemplate] = useState("");
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({});
  const [pending, setPending] = useState(false);
  const slug = slugTyped ?? slugify(name);
  const code = codeTyped ?? (slug ? `${slug.toUpperCase()}-${suffix}` : "");

  async function submit(e: FormEvent) {
    e.preventDefault();
    const found: Partial<Record<Field, string>> = {};
    if (!name.trim()) found.name = "Enter the client's name";
    if (!SLUG.test(slug)) found.slug = "Use lowercase letters, digits and hyphens (1–40, not at the ends)";
    if (!CODE.test(code)) found.workspace_code = "Use 3–64 letters, digits and hyphens";
    if (!template) found.template_key = "Choose a template";
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setPending(true);
    try {
      const body = { name: name.trim(), slug, workspace_code: code, template_key: template };
      const { data, error } = await api.POST("/owner/clients", { body });
      if (data) {
        queries.setQueryData(["client", data.id], data);
        void queries.invalidateQueries({ queryKey: ["clients"] });
        return navigate(`/clients/${data.id}`);
      }
      if (error.errors?.length) {
        return setErrors(Object.fromEntries(error.errors.map((f) => [f.path.slice(1), f.message])));
      }
      notifyError(error.detail ?? "Couldn't create the client.");
    } catch {
      notifyError("Can't reach Z-Matrix right now. Check your connection and try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <PageHeader title="New client" onBack={() => navigate("/")} backLabel="Back to clients" />
      <form onSubmit={submit} noValidate>
        <Stack maw={480}>
          <TextInput label="Name" required value={name} onChange={(e) => setName(e.currentTarget.value)} error={errors.name} autoFocus />
          <TextInput
            label="Slug"
            description="Names the client's Supabase project and Operaton service."
            required
            value={slug}
            onChange={(e) => setSlug(e.currentTarget.value)}
            error={errors.slug}
          />
          <TextInput
            label="Workspace code"
            description="People enter it on the sign-in page. It can't be changed once the client exists."
            required
            value={code}
            onChange={(e) => setCode(e.currentTarget.value)}
            error={errors.workspace_code}
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
          />
          <NativeSelect
            label="Template"
            required
            value={template}
            onChange={(e) => setTemplate(e.currentTarget.value)}
            error={templates.isError ? "Couldn't load the templates. Reload the page to try again." : errors.template_key}
            data={[{ value: "", label: "Select a template…" }, ...(templates.data ?? []).map((t) => ({ value: t.key, label: t.name }))]}
          />
          <Group>
            <Button type="submit" loading={pending}>
              Create client
            </Button>
          </Group>
        </Stack>
      </form>
    </>
  );
}

// --- `/clients/:id` ---------------------------------------------------------------------------

export function ClientPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queries = useQueryClient();
  const client = useQuery({
    queryKey: ["client", id],
    queryFn: () => must(api.GET("/owner/clients/{client_id}", { params: { path: { client_id: id } } })),
    refetchInterval: (q) => (q.state.data?.status === "ready" ? false : 5000), // publish refetches it
  });
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  if (client.isPending) return null;
  if (!client.data) {
    return (
      <Notice tone="error" title="Couldn't load this client.">
        <Button mt="sm" variant="default" onClick={() => void client.refetch()}>
          Retry
        </Button>
      </Notice>
    );
  }
  const c = client.data;
  const next = (c.releases[0]?.version ?? 0) + 1;
  const stuck = c.steps.filter((s) => s.status === "failed" || s.status === "needs_human");

  async function run(call: () => Promise<unknown>, failure: string) {
    setBusy(true);
    try {
      await call();
      await queries.invalidateQueries({ queryKey: ["client", id] });
    } catch (e) {
      notifyError(e instanceof Error && e.message !== "Request failed" ? e.message : failure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap="xl">
      <PageHeader
        title={c.name}
        description={
          <>
            <Code>{c.workspace_code}</Code> · {c.slug} · template {c.template_key}
          </>
        }
        actions={<Status of={CLIENT_STATUS} status={c.status} />}
        onBack={() => navigate("/")}
        backLabel="Back to clients"
      />
      {client.isError && (
        <Notice tone="error" title="Couldn't refresh this client">
          <Text size="sm">Last updated {new Date(client.dataUpdatedAt).toLocaleTimeString()}.</Text>
        </Notice>
      )}

      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={2} size="h3">
            Provisioning
          </Title>
          {stuck.length > 0 && (
            <Button
              variant="default"
              loading={busy}
              onClick={() =>
                run(() => must(api.POST("/owner/clients/{client_id}/provisioning/retry", { params: { path: { client_id: id } } })), "Couldn't retry.")
              }
            >
              Retry failed step
            </Button>
          )}
        </Group>
        {stuck
          .filter((s) => s.status === "needs_human")
          .map((s) => (
            <Notice key={s.step} tone="warning" title={`${s.step} needs a person`}>
              <Text size="sm">{s.last_error}</Text>
              <Text size="sm" c="dimmed">
                Then click Retry failed step.
              </Text>
            </Notice>
          ))}
        <DataTable
          caption="Provisioning"
          rows={c.steps}
          rowKey={(s) => s.step}
          emptyText="No steps queued"
          columns={[
            { key: "seq", header: "#", align: "right" },
            { key: "step", header: "Step", render: (s) => <Code>{s.step}</Code> },
            { key: "status", header: "Status", render: (s) => <Status of={STEP_STATUS} status={s.status} /> },
            { key: "attempts", header: "Attempts", align: "right" },
            { key: "last_error", header: "Details", render: (s) => s.last_error ?? "" },
          ]}
        />
      </Stack>

      {c.status === "ready" && (
        <Stack gap="sm">
          <Group justify="space-between">
            <Title order={2} size="h3">
              Releases
            </Title>
            <Button onClick={() => setConfirming(true)}>Publish draft as v{next}</Button>
          </Group>
          <DataTable<ReleaseOut>
            caption="Releases"
            rows={c.releases}
            rowKey={(r) => String(r.version)}
            emptyText="Nothing published yet"
            columns={[
              { key: "version", header: "Version", render: (r) => `v${r.version}` },
              { key: "published_at", header: "Published", render: (r) => new Date(r.published_at).toLocaleString() },
              { key: "deployment_id", header: "Deployment", render: (r) => <Code>{r.deployment_id}</Code> },
            ]}
          />
        </Stack>
      )}

      <Modal opened={confirming} onClose={() => setConfirming(false)} title={`Publish v${next}?`}>
        <Stack>
          <Text size="sm">
            The draft is compiled and deployed to {c.name}'s own Operaton as release v{next}. Sites already running stay on their
            release.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              loading={busy}
              onClick={() =>
                run(async () => {
                  const done = await must(api.POST("/owner/clients/{client_id}/publish", { params: { path: { client_id: id } } }));
                  setConfirming(false);
                  if (!done.created) notifyInfo("No new release", `Nothing changed since v${done.version}.`);
                }, "Couldn't publish.")
              }
            >
              Publish
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
