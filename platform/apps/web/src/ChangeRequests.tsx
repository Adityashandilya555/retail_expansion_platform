// `/app/admin/change-requests` (S2-24a): the client admin asks us for a field or document change and
// follows its review. A fixed route of the shell for client admins, like People (D19).
import { ActionIcon, Button, Group, Loader, Modal, NativeSelect, SegmentedControl, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@zm/api-client";
import { DataTable, notifyError, notifyInfo, PageHeader, StatusPill, type StatusTone } from "@zm/ui";
import { Plus, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { must, save, type Errors } from "./People";
import { api } from "./session";

type ChangeRequest = components["schemas"]["ChangeRequestOut"];
type Payload = ChangeRequest["payload"];
type Kind = Payload["kind"];
type Targets = components["schemas"]["ChangeTargets"];
type FieldType = Targets["field_types"][number];
/** A new field as typed: its choices on one line, comma-separated. */
type FieldRow = { label: string; type: FieldType; options: string };

const KINDS: Record<Kind, string> = {
  add_field: "Add fields to a form",
  add_document_type: "Add a document type",
  rename_field_label: "Rename a field",
  set_required: "Make a field required or optional",
  add_select_option: "Add a choice to a list",
};
const STATUS: Record<ChangeRequest["status"], [StatusTone, string]> = {
  submitted: ["queued", "Submitted"],
  in_review: ["open", "In review"],
  approved: ["done", "Approved"],
  rejected: ["closed", "Rejected"],
  shipped: ["done", "Shipped"],
};
const LISTS: string[] = ["select", "multi_select"];
const SHOWN = ["/payload/form", "/payload/field"]; // errors shown under their selector
const NEW_ROW: FieldRow = { label: "", type: "text", options: "" };
const typeName = (type: string) => type.replaceAll("_", " ");

/** One line per request, in the release's names (ids for what the release no longer has). */
function describe(p: Payload, targets: Targets | undefined): string {
  const forms = targets?.forms ?? [];
  const form = (id: string) => forms.find((f) => f.id === id)?.title ?? id;
  const field = (key: string) => forms.flatMap((f) => f.fields).find((f) => f.key === key)?.label ?? key;
  switch (p.kind) {
    case "add_field":
      return `${form(p.form)}: add ${p.fields.map((f) => `${f.label} (${typeName(f.type)})`).join(", ")}`;
    case "add_document_type":
      return `${form(p.form)}: add document ${p.label}`;
    case "rename_field_label":
      return `Rename ${field(p.field)} to ${p.label}`;
    case "set_required":
      return `Make ${field(p.field)} ${p.required ? "required" : "optional"}`;
    case "add_select_option":
      return `${field(p.field)}: add choice ${p.label}`;
  }
}

function RequestForm({ targets, onDone }: { targets: Targets; onDone: () => void }) {
  const [kind, setKind] = useState<Kind>("add_field");
  const [form, setForm] = useState(targets.forms[0]?.id ?? "");
  const [field, setField] = useState("");
  const [label, setLabel] = useState("");
  const [required, setRequired] = useState("required");
  const [rows, setRows] = useState([NEW_ROW]);
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [pending, setPending] = useState(false);
  const setRow = (i: number, change: Partial<FieldRow>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const choices = (r: FieldRow) => (LISTS.includes(r.type) ? r.options.split(",").map((o) => o.trim()).filter(Boolean) : []);
  const fieldGroups = targets.forms
    .map((f) => ({
      group: f.title,
      items: f.fields.filter((x) => kind !== "add_select_option" || LISTS.includes(x.type)).map((x) => ({ value: x.key, label: x.label })),
    }))
    .filter((g) => g.items.length > 0);

  function payload(): Payload {
    switch (kind) {
      case "add_field":
        return { kind, form, fields: rows.map((r) => ({ label: r.label, type: r.type, options: choices(r) })) };
      case "add_document_type":
        return { kind, form, label };
      case "set_required":
        return { kind, field, required: required === "required" };
      case "rename_field_label":
      case "add_select_option":
        return { kind, field, label };
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    const failed = await save(api.POST("/app/admin/change-requests", { body: { payload: payload(), note: note.trim() || null } }));
    setPending(false);
    if (!failed) {
      notifyInfo("Request sent", "We review every request and answer here.");
      return onDone();
    }
    setErrors(failed);
    const other = Object.entries(failed).filter(([path]) => !SHOWN.includes(path));
    if (other.length > 0) notifyError(other.map(([, message]) => message).join(" "));
  }

  const onForm = kind === "add_field" || kind === "add_document_type";
  return (
    <form onSubmit={submit}>
      <Stack>
        <NativeSelect
          label="What do you need?"
          value={kind}
          onChange={(e) => {
            setKind(e.currentTarget.value as Kind);
            setField("");
            setErrors({});
          }}
          data={Object.entries(KINDS).map(([value, text]) => ({ value, label: text }))}
        />
        {onForm ? (
          <NativeSelect
            label="Form"
            required
            value={form}
            onChange={(e) => setForm(e.currentTarget.value)}
            error={errors["/payload/form"]}
            data={targets.forms.map((f) => ({ value: f.id, label: f.title }))}
          />
        ) : (
          <NativeSelect
            label="Field"
            required
            value={field}
            onChange={(e) => setField(e.currentTarget.value)}
            error={errors["/payload/field"]}
            data={[{ value: "", label: "Choose a field", disabled: true }, ...fieldGroups]}
          />
        )}
        {kind === "add_field" && (
          <Stack gap="xs">
            {rows.map((r, i) => (
              <Stack key={i} gap="xs">
                <Group align="flex-end" wrap="nowrap" gap="xs">
                  <TextInput flex={1} label={`Field ${i + 1}`} required maxLength={200} value={r.label} onChange={(e) => setRow(i, { label: e.currentTarget.value })} />
                  <NativeSelect
                    label="Type"
                    aria-label={`Type of field ${i + 1}`}
                    value={r.type}
                    onChange={(e) => setRow(i, { type: e.currentTarget.value as FieldType })}
                    data={targets.field_types.map((t) => ({ value: t, label: typeName(t) }))}
                  />
                  {rows.length > 1 && (
                    <ActionIcon variant="subtle" color="gray" size="lg" aria-label={`Remove field ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}>
                      <X size={16} strokeWidth={1.5} aria-hidden />
                    </ActionIcon>
                  )}
                </Group>
                {LISTS.includes(r.type) && (
                  <TextInput
                    label={`Choices for field ${i + 1}`}
                    description="Separate them with commas."
                    value={r.options}
                    onChange={(e) => setRow(i, { options: e.currentTarget.value })}
                  />
                )}
              </Stack>
            ))}
            {rows.length < 20 && (
              <Group>
                <Button variant="subtle" leftSection={<Plus size={14} strokeWidth={1.5} aria-hidden />} onClick={() => setRows([...rows, NEW_ROW])}>
                  Add another field
                </Button>
              </Group>
            )}
          </Stack>
        )}
        {kind === "set_required" && (
          <SegmentedControl
            aria-label="Required or optional"
            value={required}
            onChange={setRequired}
            data={[
              { value: "required", label: "Required" },
              { value: "optional", label: "Optional" },
            ]}
          />
        )}
        {(kind === "add_document_type" || kind === "rename_field_label" || kind === "add_select_option") && (
          <TextInput
            label={{ add_document_type: "Document name", rename_field_label: "New name", add_select_option: "New choice" }[kind]}
            required
            maxLength={200}
            value={label}
            onChange={(e) => setLabel(e.currentTarget.value)}
          />
        )}
        <Textarea label="Note for the reviewer" autosize minRows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button type="submit" loading={pending}>
            Send request
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

export function ChangeRequests() {
  const queries = useQueryClient();
  const [requesting, setRequesting] = useState(false);
  const requests = useQuery({ queryKey: ["change-requests"], queryFn: () => must(api.GET("/app/admin/change-requests")) });
  const targets = useQuery({ queryKey: ["change-targets"], queryFn: () => must(api.GET("/app/admin/change-requests/targets")) });
  const done = () => {
    setRequesting(false);
    void queries.invalidateQueries({ queryKey: ["change-requests"] });
  };

  return (
    <>
      <PageHeader
        title="Change requests"
        description="Ask for new fields, document types and choices. We review every request and ship it in a new version."
        actions={<Button onClick={() => setRequesting(true)}>Request a change</Button>}
      />
      <DataTable
        caption="Change requests"
        rows={requests.data ?? []}
        rowKey={(r) => r.id}
        loading={requests.isPending}
        error={requests.isError ? "Couldn't load the change requests." : null}
        onRetry={() => void requests.refetch()}
        emptyText="No requests yet"
        columns={[
          { key: "payload", header: "Request", render: (r) => describe(r.payload, targets.data) },
          { key: "status", header: "Status", render: (r) => <StatusPill tone={STATUS[r.status][0]}>{STATUS[r.status][1]}</StatusPill> },
          { key: "review_note", header: "Review note", render: (r) => r.review_note ?? "—" },
          { key: "created_at", header: "Sent", render: (r) => new Date(r.created_at).toLocaleDateString(), sortValue: (r) => r.created_at },
        ]}
      />
      <Modal opened={requesting} onClose={() => setRequesting(false)} title="Request a change">
        {targets.data ? (
          <RequestForm targets={targets.data} onDone={done} />
        ) : targets.isError ? (
          <Stack align="flex-start">
            <Text size="sm">Couldn't load your forms.</Text>
            <Button variant="default" onClick={() => void targets.refetch()}>
              Retry
            </Button>
          </Stack>
        ) : (
          <Loader aria-label="Loading your forms" />
        )}
      </Modal>
    </>
  );
}
