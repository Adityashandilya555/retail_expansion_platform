// `/app/admin/people` (S2-23): the client admin invites people, sets their department and level and
// deactivates them. A fixed route of the shell for client admins, not an agent-configured page (D19).
// An invite answers its one-time link (#169), shown once to copy: email may not be set up.
import { ActionIcon, Button, Drawer, Group, Modal, NativeSelect, SegmentedControl, Stack, Text, TextInput } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components, Problem } from "@zm/api-client";
import { DataTable, InviteLink, notifyError, PageHeader, StatusPill, TONE_COLORS, type StatusTone } from "@zm/ui";
import { Plus, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api } from "./session";

type UserOut = components["schemas"]["UserOut"];
type InviteOut = components["schemas"]["InviteOut"];
type Membership = components["schemas"]["Membership"];
type Department = components["schemas"]["Department"];
type Filter = UserOut["status"] | "all";
/** JSON Pointer → message, from the API's 409 or 422 (`/email`, `/memberships/0`, …). */
export type Errors = Record<string, string>;

const STATUS: Record<UserOut["status"], [StatusTone, string]> = {
  invited: ["queued", "Invited"],
  active: ["done", "Active"],
  deactivated: ["closed", "Deactivated"],
};
const FILTERS = [
  { value: "all", label: "All" },
  { value: "invited", label: "Invited" },
  { value: "active", label: "Active" },
  { value: "deactivated", label: "Deactivated" },
];

export async function must<T>(call: Promise<{ data?: T; error?: Problem }>): Promise<T> {
  const { data, error } = await call;
  if (data === undefined) throw new Error(error?.detail ?? "Request failed");
  return data;
}

/** Sends a change: null once saved, else the per-field errors (empty when it notified instead). */
export async function save(call: Promise<{ data?: unknown; error?: Problem }>): Promise<Errors | null> {
  try {
    const { data, error } = await call;
    if (data !== undefined) return null;
    if (error?.errors?.length) return Object.fromEntries(error.errors.map((e) => [e.path, e.message]));
    notifyError(error?.detail ?? "Couldn't save the change.");
  } catch {
    notifyError("Can't reach Z-Matrix right now. Check your connection and try again.");
  }
  return {};
}

/** One department and level per row, from the published org. */
function MembershipsInput({
  departments,
  value,
  onChange,
  errors,
}: {
  departments: Department[];
  value: Membership[];
  onChange: (value: Membership[]) => void;
  errors: Errors;
}) {
  const set = (i: number, change: Partial<Membership>) => onChange(value.map((m, j) => (j === i ? { ...m, ...change } : m)));
  const free = departments.filter((d) => !value.some((m) => m.department === d.key));
  const next = free[0];
  return (
    <Stack gap="xs">
      <Text size="sm" fw={500}>
        Departments
      </Text>
      {value.length === 0 && (
        <Text size="sm" c="dimmed">
          No department yet.
        </Text>
      )}
      {value.map((m, i) => {
        const dept = departments.find((d) => d.key === m.department);
        const label = dept?.name ?? m.department; // a department a later release dropped: the API says so on save
        const options = departments.filter((d) => d === dept || free.includes(d));
        return (
          <Group key={i} align="flex-start" wrap="nowrap" gap="xs">
            <NativeSelect
              flex={1}
              aria-label={`Department ${i + 1}`}
              value={m.department}
              onChange={(e) => set(i, { department: e.currentTarget.value, level: 1 })}
              error={errors[`/memberships/${i}`]}
              data={[...(dept ? [] : [{ value: m.department, label }]), ...options.map((d) => ({ value: d.key, label: d.name }))]}
            />
            <NativeSelect
              aria-label={`Level in ${label}`}
              value={String(m.level)}
              onChange={(e) => set(i, { level: Number(e.currentTarget.value) })}
              data={dept?.levels.map((l) => ({ value: String(l.n), label: `L${l.n} · ${l.name}` })) ?? [{ value: String(m.level), label: `L${m.level}` }]}
            />
            <ActionIcon variant="subtle" color="gray" size="lg" aria-label={`Remove ${label}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
              <X size={16} strokeWidth={1.5} aria-hidden />
            </ActionIcon>
          </Group>
        );
      })}
      {next && (
        <Group>
          <Button
            variant="subtle"
            leftSection={<Plus size={14} strokeWidth={1.5} aria-hidden />}
            onClick={() => onChange([...value, { department: next.key, level: next.levels[0]?.n ?? 1, scope: "own" }])}
          >
            Add department
          </Button>
        </Group>
      )}
    </Stack>
  );
}

/** The one-time link an invite answered, on this app. */
function SentLink({ invite }: { invite: InviteOut }) {
  return <InviteLink url={window.location.origin + invite.invite_path} expiresAt={invite.invite_expires_at} />;
}

function InviteForm({ departments, onInvited, onDone }: { departments: Department[]; onInvited: () => void; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [errors, setErrors] = useState<Errors>({});
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState<InviteOut | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    const call = api.POST("/app/admin/users", { body: { email: email.trim(), name: name.trim(), memberships } });
    const failed = await save(call);
    setPending(false);
    if (failed) return setErrors(failed);
    setSent((await call).data ?? null);
    onInvited();
  }

  if (sent) {
    return (
      <Stack>
        <SentLink invite={sent} />
        <Group justify="flex-end">
          <Button onClick={onDone}>Done</Button>
        </Group>
      </Stack>
    );
  }
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <TextInput label="Email" type="email" required autoFocus value={email} onChange={(e) => setEmail(e.currentTarget.value)} error={errors["/email"]} />
        <TextInput label="Name" required value={name} onChange={(e) => setName(e.currentTarget.value)} error={errors["/name"]} />
        <MembershipsInput departments={departments} value={memberships} onChange={setMemberships} errors={errors} />
        <Group justify="flex-end">
          <Button type="submit" loading={pending}>
            Send invite
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

function EditPerson({
  user,
  departments,
  selfId,
  onInvited,
  onDone,
}: {
  user: UserOut;
  departments: Department[];
  selfId: string;
  onInvited: () => void;
  onDone: () => void;
}) {
  const [memberships, setMemberships] = useState(user.memberships);
  const [errors, setErrors] = useState<Errors>({});
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState<InviteOut | null>(null);

  async function patch(body: components["schemas"]["UserPatch"]) {
    setPending(true);
    const failed = await save(api.PATCH("/app/admin/users/{user_id}", { params: { path: { user_id: user.id } }, body }));
    setPending(false);
    setConfirming(false);
    if (failed) return setErrors(failed);
    onDone();
  }

  /** Someone who never accepted: the same invite again, with a new link (the old one stops working).
   * Memberships are left out, so the API keeps the saved ones and unsaved edits stay unsaved. */
  async function resend() {
    setPending(true);
    const call = api.POST("/app/admin/users", { body: { email: user.email, name: user.name } });
    const failed = await save(call);
    setPending(false);
    if (failed) {
      // e.g. a 409 on /email when they accepted meanwhile: the drawer has no email field to show it on
      for (const message of Object.values(failed)) notifyError(message);
      return;
    }
    setSent((await call).data ?? null);
    onInvited();
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          {user.email}
        </Text>
        <StatusPill tone={STATUS[user.status][0]}>{STATUS[user.status][1]}</StatusPill>
      </Group>
      {sent && <SentLink invite={sent} />}
      <MembershipsInput departments={departments} value={memberships} onChange={setMemberships} errors={errors} />
      <Group justify="space-between">
        {user.status === "deactivated" ? (
          <Button variant="default" loading={pending} onClick={() => void patch({ status: "active" })}>
            Reactivate
          </Button>
        ) : (
          user.id !== selfId && (
            <Button variant="light" color={TONE_COLORS.error} onClick={() => setConfirming(true)}>
              Deactivate
            </Button>
          )
        )}
        {/* a client admin's invite is the platform owner's to renew (the API answers 403) */}
        {user.status === "invited" && !user.is_client_admin && (
          <Button variant="default" loading={pending} onClick={() => void resend()}>
            Resend invite
          </Button>
        )}
        <Button ml="auto" loading={pending} onClick={() => void patch({ memberships })}>
          Save
        </Button>
      </Group>
      <Modal opened={confirming} onClose={() => setConfirming(false)} title={`Deactivate ${user.name}?`}>
        <Stack>
          <Text size="sm">They can't sign in any more and drop out of every department's tasks. You can reactivate them later.</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button color={TONE_COLORS.error} loading={pending} onClick={() => void patch({ status: "deactivated" })}>
              Deactivate
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}

export function People({ selfId }: { selfId: string }) {
  const queries = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<UserOut | null>(null);
  const users = useQuery({
    queryKey: ["people", filter],
    queryFn: () => must(api.GET("/app/admin/users", { params: { query: filter === "all" ? {} : { status: filter } } })),
  });
  const departments = useQuery({ queryKey: ["departments"], queryFn: () => must(api.GET("/app/admin/departments")) });
  const org = departments.data ?? [];
  const names = new Map(org.map((d) => [d.key, d.name]));
  const refresh = () => void queries.invalidateQueries({ queryKey: ["people"] });
  const done = () => {
    setInviting(false);
    setEditing(null);
    refresh();
  };

  return (
    <>
      <PageHeader
        title="People"
        description="Invite people, set their department and level, and deactivate them."
        actions={<Button onClick={() => setInviting(true)}>Invite person</Button>}
      />
      <SegmentedControl mb="md" aria-label="Status" value={filter} onChange={(v) => setFilter(v as Filter)} data={FILTERS} />
      <DataTable
        caption="People"
        rows={users.data ?? []}
        rowKey={(u) => u.id}
        onRowClick={setEditing}
        loading={users.isPending}
        error={users.isError ? "Couldn't load the people." : null}
        onRetry={() => void users.refetch()}
        emptyText="Nobody here yet"
        columns={[
          { key: "name", header: "Name", sortValue: (u) => u.name },
          { key: "email", header: "Email" },
          {
            key: "memberships",
            header: "Department and level",
            render: (u) => u.memberships.map((m) => `${names.get(m.department) ?? m.department} L${m.level}`).join(", ") || "—",
          },
          { key: "status", header: "Status", render: (u) => <StatusPill tone={STATUS[u.status][0]}>{STATUS[u.status][1]}</StatusPill> },
        ]}
      />
      <Modal opened={inviting} onClose={() => setInviting(false)} title="Invite person">
        <InviteForm departments={org} onInvited={refresh} onDone={() => setInviting(false)} />
      </Modal>
      <Drawer opened={editing !== null} onClose={() => setEditing(null)} position="right" title={editing?.name}>
        {editing && <EditPerson key={editing.id} user={editing} departments={org} selfId={selfId} onInvited={refresh} onDone={done} />}
      </Drawer>
    </>
  );
}
