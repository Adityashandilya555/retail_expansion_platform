import { Button, CloseButton, FileButton, Group, Input, Paper, Progress, Stack, Text, VisuallyHidden } from "@mantine/core";
import { useEffect, useId, useState, type DragEvent, type ReactNode } from "react";

/** A `document` field's value: what `POST /app/files/{id}/confirm` answers (#51). */
export type DocumentValue = { key: string; sha256: string; name: string };
/** The slot's rule, `x-zm-document` in the compiled schema. */
export type DocumentRule = { accept: string[]; max_mb: number; keep_versions?: boolean };
/** One confirmed version, as `GET /app/cases/{code}/files` lists it. */
export type FileVersion = {
  id: string;
  field: string;
  version: number;
  name: string;
  size_bytes: number;
  content_type: string | null;
  uploaded_by: string;
  uploaded_by_name: string | null;
  uploaded_at: string;
};

/** What a file call rejects with: the API's status and its message. */
export class FileApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** How a form's document slots reach the API; the shell binds it to a task or a start form. Each call rejects with a FileApiError. */
export type FileApi = {
  /** `POST /app/files/upload`: a pending upload and the signed URL to PUT the file to. */
  upload: (field: string, file: File) => Promise<{ file_id: string; upload_url: string }>;
  /** `POST /app/files/{id}/confirm`: the field's value, once the API has checked the stored file. */
  confirm: (fileId: string) => Promise<DocumentValue>;
  /** `GET /app/cases/{code}/files?field=`: the slot's versions, newest first; absent before the case exists. */
  versions?: (field: string) => Promise<FileVersion[]>;
  /** `GET /app/files/{id}/download`: a URL that works for 60 seconds, as a download or to show inline. */
  url: (fileId: string, attachment: boolean) => Promise<string>;
};

export type FileSlotProps = {
  name: string;
  label: string;
  rule: DocumentRule;
  value: unknown;
  onChange: (value: DocumentValue) => void;
  error?: ReactNode;
  withAsterisk?: boolean;
  readOnly?: boolean;
  files?: FileApi;
};

const MB = 1024 * 1024;
const extension = (name: string) => (name.includes(".") ? (name.split(".").pop() ?? "").toLowerCase() : "");
const accepted = (rule: DocumentRule) => rule.accept.map((a) => a.toLowerCase().replace(/^\./, ""));
const isDocument = (v: unknown): v is DocumentValue => typeof v === "object" && v !== null && "name" in v && "key" in v;
const PREVIEWS: Record<string, "pdf" | "image"> = { "application/pdf": "pdf", "image/png": "image", "image/jpeg": "image" };
const AGAIN = "Something went wrong. Check your connection and try again.";
const messageOf = (e: unknown) => (e instanceof FileApiError ? e.message : AGAIN);

/** Why a file breaks the slot's rule, or null; the API checks the same (`zm_api.files.rule_error`). */
export function ruleError(rule: DocumentRule, file: File): string | null {
  const accept = accepted(rule);
  if (!accept.includes(extension(file.name))) return `Only ${accept.map((a) => a.toUpperCase()).join(", ")} files go here`;
  if (file.size > rule.max_mb * MB) return `The file is larger than ${rule.max_mb} MB`;
  if (file.size === 0) return "The file is empty";
  return null;
}

/** Why the slot can't upload at all, from an API answer: a workspace without Storage, or a preview (#42). */
function unavailableFor(e: unknown): string | null {
  if (!(e instanceof FileApiError)) return null;
  if (e.status === 503) return "Uploads aren't available in this workspace";
  if (e.status === 403) return "Uploads are off in preview";
  return null;
}

/** PUT the file to Storage's signed URL: XMLHttpRequest, since fetch reports no upload progress. */
function put(url: string, file: File, onProgress: (fraction: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () =>
      xhr.status < 300
        ? resolve()
        : reject(new FileApiError(xhr.status, xhr.status === 413 ? "The file is larger than storage allows" : "The upload didn't go through; try again"));
    xhr.onerror = () => reject(new FileApiError(0, AGAIN));
    xhr.send(file);
  });
}

/** A `document` field (#51): pick or drop a file, checked against the rule here and again by the API, uploaded straight to the
 * client's bucket with its progress announced, then confirmed into the form; the case's versions with download and preview. */
export function FileSlot({ name, label, rule, value, onChange, error, withAsterisk, readOnly = false, files }: FileSlotProps) {
  const id = useId();
  const [versions, setVersions] = useState<FileVersion[]>([]);
  const [unavailable, setUnavailable] = useState<string | null>(files ? null : "Uploads aren't available here");
  const [progress, setProgress] = useState<number | null>(null);
  const [status, setStatus] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ id: string; url: string; name: string; kind: "pdf" | "image" } | null>(null);
  const [uploads, setUploads] = useState(0); // each confirmed upload lists the versions again

  useEffect(() => {
    if (!files?.versions) return;
    let live = true;
    files.versions(name).then(
      (found) => live && setVersions(found),
      (e: unknown) => {
        if (!live) return;
        const why = unavailableFor(e);
        setUnavailable(why);
        if (!why) setProblem(messageOf(e));
      },
    );
    return () => {
      live = false;
    };
  }, [files, name, uploads]);

  async function pick(file: File | null) {
    if (!file || !files || progress !== null) return;
    setProblem(null);
    const refused = ruleError(rule, file);
    if (refused) return setProblem(refused);
    setProgress(0);
    setStatus(`Uploading ${file.name}`);
    try {
      const started = await files.upload(name, file);
      await put(started.upload_url, file, (fraction) => {
        setProgress(fraction);
        setStatus(`Uploading ${file.name}: ${Math.floor(fraction * 4) * 25}%`);
      });
      onChange(await files.confirm(started.file_id));
      setStatus(`Uploaded ${file.name}`);
      setUploads((n) => n + 1);
    } catch (e) {
      setStatus("");
      setUnavailable(unavailableFor(e));
      setProblem(messageOf(e));
    } finally {
      setProgress(null);
    }
  }

  async function open(v: FileVersion, attachment: boolean) {
    if (!files) return;
    try {
      const url = await files.url(v.id, attachment);
      const kind = PREVIEWS[v.content_type ?? ""];
      if (!attachment && kind) return setPreview({ id: v.id, url, name: v.name, kind });
      // Storage answers with `Content-Disposition: attachment`, so this downloads and the page stays
      const a = document.createElement("a");
      a.href = url;
      a.rel = "noopener";
      a.click();
    } catch (e) {
      setProblem(messageOf(e));
    }
  }

  function drop(e: DragEvent) {
    e.preventDefault();
    void pick(e.dataTransfer.files[0] ?? null);
  }

  const shown = problem ?? error;
  const current = isDocument(value) ? value : null;
  const describedBy = [`${id}-rule`, shown ? `${id}-error` : null].filter(Boolean).join(" ");
  const types = accepted(rule);
  return (
    <Stack gap={6} role="group" aria-labelledby={`${id}-label`} aria-describedby={describedBy}>
      <div>
        <Input.Label id={`${id}-label`} required={withAsterisk}>
          {label}
        </Input.Label>
        <Input.Description id={`${id}-rule`}>
          {types.map((a) => a.toUpperCase()).join(", ")} · up to {rule.max_mb} MB
        </Input.Description>
      </div>
      {current && <Text size="sm">Attached: {current.name}</Text>}
      {unavailable ? (
        <Text size="sm" c="dimmed">
          {unavailable}
        </Text>
      ) : (
        !readOnly && (
          <Paper withBorder p="sm" style={{ borderStyle: "dashed" }} onDragOver={(e) => e.preventDefault()} onDrop={drop}>
            <Group gap="sm">
              <FileButton onChange={(f) => void pick(f)} accept={types.map((a) => `.${a}`).join(",")} disabled={progress !== null}>
                {(props) => (
                  <Button variant="default" size="xs" {...props} disabled={progress !== null}>
                    {current ? "Replace file" : "Choose file"}
                  </Button>
                )}
              </FileButton>
              <Text size="sm" c="dimmed">
                or drop it here
              </Text>
            </Group>
          </Paper>
        )
      )}
      {progress !== null && (
        <Progress.Root>
          <Progress.Section value={Math.round(progress * 100)} aria-label={status.split(":")[0]} />
        </Progress.Root>
      )}
      <VisuallyHidden role="status">{status}</VisuallyHidden>
      {shown && (
        <Input.Error id={`${id}-error`} role={problem ? "alert" : undefined}>
          {shown}
        </Input.Error>
      )}
      {versions.length > 0 && (
        <Stack component="ul" gap={4} m={0} p={0} style={{ listStyle: "none" }} aria-label={`${label} versions`}>
          {versions.map((v) => (
            <Group component="li" key={v.id} gap="xs" wrap="wrap">
              <Text size="sm">
                v{v.version} · {v.name}
              </Text>
              <Text size="xs" c="dimmed">
                {new Date(v.uploaded_at).toLocaleString()}
                {v.uploaded_by_name ? ` · ${v.uploaded_by_name}` : ""}
              </Text>
              {PREVIEWS[v.content_type ?? ""] && (
                <Button variant="subtle" size="compact-xs" aria-label={`Preview ${v.name} v${v.version}`} onClick={() => void open(v, false)}>
                  Preview
                </Button>
              )}
              <Button variant="subtle" size="compact-xs" aria-label={`Download ${v.name} v${v.version}`} onClick={() => void open(v, true)}>
                Download
              </Button>
            </Group>
          ))}
        </Stack>
      )}
      {preview && (
        <Paper withBorder p="xs">
          <Group justify="space-between" mb={4}>
            <Text size="sm">{preview.name}</Text>
            <CloseButton aria-label="Close preview" onClick={() => setPreview(null)} />
          </Group>
          {preview.kind === "pdf" ? (
            <iframe src={preview.url} title={`Preview of ${preview.name}`} style={{ width: "100%", height: 480, border: 0 }} />
          ) : (
            <img src={preview.url} alt={preview.name} style={{ maxWidth: "100%" }} />
          )}
        </Paper>
      )}
    </Stack>
  );
}
