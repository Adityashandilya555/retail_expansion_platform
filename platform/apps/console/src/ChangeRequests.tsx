// Change requests (#46, S2-24b): every client's requests in one queue, filtered by status and client,
// and one request's review: its versioning-impact dry run, Reject with a note the client admin sees,
// Apply with agent (the client's dock, linked to the request) and Approve once its edits are in the
// draft. The next publish ships approved ones (S2-09).
import { Button, Code, Group, List, NativeSelect, Stack, Text, Textarea, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@zm/api-client";
import { DataTable, Notice, notifyError, notifyInfo, PageHeader, StatusPill, type StatusTone } from "@zm/ui";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { api, must } from "./session";

type ReviewOut = components["schemas"]["ReviewOut"];
type Status = ReviewOut["status"];

// as the client app shows them (apps/web ChangeRequests.tsx)
export const REQUEST_STATUS: Record<Status, [StatusTone, string]> = {
  submitted: ["queued", "Submitted"],
  in_review: ["open", "In review"],
  approved: ["done", "Approved"],
  rejected: ["closed", "Rejected"],
  shipped: ["done", "Shipped"],
};
const OPEN: Status[] = ["submitted", "in_review"];

function Pill({ status }: { status: Status }) {
  const [tone, label] = REQUEST_STATUS[status];
  return <StatusPill tone={tone}>{label}</StatusPill>;
}

export function ReviewQueue() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<Status | "">("");
  const [client, setClient] = useState("");
  const clients = useQuery({ queryKey: ["clients"], queryFn: () => must(api.GET("/owner/clients")) });
  const query = { ...(status ? { status } : {}), ...(client ? { client_id: client } : {}) };
  const requests = useQuery({
    queryKey: ["change-requests", status, client],
    queryFn: () => must(api.GET("/owner/change-requests", { params: { query } })),
  });

  return (
    <Stack gap="lg">
      <PageHeader title="Change requests" description="What client admins asked for. Apply one with the agent, approve it, then publish." />
      <Group>
        <NativeSelect
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.currentTarget.value as Status | "")}
          data={[{ value: "", label: "All" }, ...Object.entries(REQUEST_STATUS).map(([value, [, label]]) => ({ value, label }))]}
        />
        <NativeSelect
          label="Client"
          value={client}
          onChange={(e) => setClient(e.currentTarget.value)}
          data={[{ value: "", label: "All clients" }, ...(clients.data ?? []).map((c) => ({ value: c.id, label: c.name }))]}
        />
      </Group>
      <DataTable<ReviewOut>
        caption="Change requests"
        rows={requests.data ?? []}
        rowKey={(r) => r.id}
        loading={requests.isPending}
        error={requests.isError ? "Couldn't load the change requests." : null}
        onRetry={() => void requests.refetch()}
        onRowClick={(r) => navigate(`/change-requests/${r.id}`)}
        emptyText="No change requests"
        columns={[
          { key: "summary", header: "Request", render: (r) => r.summary },
          { key: "client_name", header: "Client" },
          { key: "status", header: "Status", render: (r) => <Pill status={r.status} /> },
          { key: "created_at", header: "Sent", render: (r) => new Date(r.created_at).toLocaleString() },
        ]}
      />
    </Stack>
  );
}

export function ReviewPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queries = useQueryClient();
  const path = { params: { path: { change_request_id: id } } };
  const review = useQuery({ queryKey: ["review", id], queryFn: () => must(api.GET("/owner/change-requests/{change_request_id}", path)) });
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  if (review.isPending) return null;
  if (!review.data) {
    return (
      <Notice tone="error" title="Couldn't load this change request.">
        <Button mt="sm" variant="default" onClick={() => void review.refetch()}>
          Retry
        </Button>
      </Notice>
    );
  }
  const r = review.data;
  const open = OPEN.includes(r.status);

  async function decide(action: "reject" | "approve") {
    setBusy(true);
    try {
      const body = { note: note.trim() || null };
      const decided =
        action === "reject"
          ? await must(api.POST("/owner/change-requests/{change_request_id}/reject", { ...path, body: { note: note.trim() } }))
          : await must(api.POST("/owner/change-requests/{change_request_id}/approve", { ...path, body }));
      queries.setQueryData(["review", id], { ...r, ...decided });
      void queries.invalidateQueries({ queryKey: ["change-requests"] });
      notifyInfo(action === "reject" ? "Request rejected" : "Request approved", action === "reject" ? "The client admin sees your note." : "Publish to ship it to the client.");
      setNote("");
    } catch (e) {
      notifyError(e instanceof Error && e.message !== "Request failed" ? e.message : "Couldn't save the decision.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap="xl">
      <PageHeader
        title={r.summary}
        description={
          <>
            {r.client_name} · <Code>{r.kind}</Code> · sent {new Date(r.created_at).toLocaleString()}
          </>
        }
        actions={<Pill status={r.status} />}
        onBack={() => navigate("/change-requests")}
        backLabel="Back to change requests"
      />

      <Stack gap="xs">
        <Title order={2} size="h4">
          From the client admin
        </Title>
        <Text size="sm">{r.note ?? "No note."}</Text>
        {r.review_note && (
          <Text size="sm">
            Your note: <span>{r.review_note}</span>
          </Text>
        )}
        {r.status === "approved" && <Text size="sm">The next publish ships it.</Text>}
        {r.status === "shipped" && r.shipped_in_version !== null && <Text size="sm">Shipped in v{r.shipped_in_version}.</Text>}
      </Stack>

      <Stack gap="xs">
        <Title order={2} size="h4">
          Versioning impact
        </Title>
        {r.impact === null ? (
          <Text size="sm">The client has no draft yet, so there is nothing to check against.</Text>
        ) : (
          <>
            {r.impact.notes.length > 0 && (
              <List size="sm" aria-label="What it does">
                {r.impact.notes.map((n) => (
                  <List.Item key={n}>{n}</List.Item>
                ))}
              </List>
            )}
            {r.impact.problems.length > 0 ? (
              <Notice tone="error" title="The draft wouldn't publish with it">
                <List size="sm" aria-label="Problems">
                  {r.impact.problems.map((p) => (
                    <List.Item key={`${p.path} ${p.message}`}>{`${p.path}: ${p.message}`}</List.Item>
                  ))}
                </List>
              </Notice>
            ) : (
              <Text size="sm">No versioning problems: the draft would still publish.</Text>
            )}
          </>
        )}
      </Stack>

      {(open || r.status === "approved") && (
        <Stack gap="sm">
          <Title order={2} size="h4">
            Decision
          </Title>
          <Text size="sm">
            {r.linked_changes > 0
              ? `${r.linked_changes} ${r.linked_changes === 1 ? "edit in the draft is" : "edits in the draft are"} linked to it.`
              : "Apply it with the agent first: no edit in the draft is linked to it yet."}
          </Text>
          {open && (
            <>
              <Textarea label="Note for the client admin" value={note} onChange={(e) => setNote(e.currentTarget.value)} autosize minRows={2} maxLength={2000} />
              <Group>
                <Button variant="default" onClick={() => navigate(`/clients/${r.client_id}?apply=${r.id}`)}>
                  Apply with agent
                </Button>
                <Button disabled={r.status !== "in_review" || r.linked_changes === 0} loading={busy} onClick={() => void decide("approve")}>
                  Approve
                </Button>
                <Button variant="default" disabled={!note.trim()} loading={busy} onClick={() => void decide("reject")}>
                  Reject
                </Button>
              </Group>
            </>
          )}
        </Stack>
      )}
    </Stack>
  );
}
