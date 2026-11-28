import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expectNoAxeViolations, renderWithZm } from "@zm/ui/test";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FileApiError, FileSlot, type FileApi, type FileSlotProps, type FileVersion } from "./FileSlot";

const MB = 1024 * 1024;
const NOC = { accept: ["pdf", "jpg", "png"], max_mb: 25, keep_versions: true };
const VALUE = { key: "cases/MX-0001/legal_dd_checklist/dd_report/1-dd.pdf", sha256: "a".repeat(64), name: "dd.pdf" };
const V1: FileVersion = {
  id: "f1",
  field: "dd_report",
  version: 1,
  name: "dd-v1.pdf",
  size_bytes: 2 * MB,
  content_type: "application/pdf",
  uploaded_by: "u1",
  uploaded_by_name: "Legal Executive",
  uploaded_at: "2026-11-20T10:00:00Z",
};
const V2: FileVersion = { ...V1, id: "f2", version: 2, name: "site.png", content_type: "image/png" };

/** XMLHttpRequest as Storage answers the signed PUT; the test drives progress and the answer. */
class FakeXhr {
  static last: FakeXhr | undefined;
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown;
  status = 0;
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  send(body: unknown) {
    this.body = body;
    FakeXhr.last = this;
  }
  progress(fraction: number) {
    act(() => this.upload.onprogress?.({ lengthComputable: true, loaded: fraction * 100, total: 100 }));
  }
  answer(status: number) {
    this.status = status;
    act(() => this.onload?.());
  }
}

/** The PUT the slot sent to Storage. */
function sent(): FakeXhr {
  if (!FakeXhr.last) throw new Error("nothing was PUT");
  return FakeXhr.last;
}

beforeEach(() => {
  FakeXhr.last = undefined;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function api(overrides: Partial<FileApi> = {}): FileApi {
  return {
    upload: vi.fn(async () => ({ file_id: "f9", upload_url: "http://127.0.0.1:54321/storage/v1/object/upload/sign/files/k?token=t" })),
    confirm: vi.fn(async () => VALUE),
    url: vi.fn(async (id: string, attachment: boolean) => `http://storage.test/${id}?token=t${attachment ? "&download=x" : ""}`),
    ...overrides,
  };
}

function renderSlot(props: Partial<FileSlotProps> = {}, scheme?: "light" | "dark") {
  const onChange = vi.fn();
  const view = renderWithZm(<FileSlot name="dd_report" label="DD report" rule={NOC} value={undefined} onChange={onChange} {...props} />, { scheme });
  // the hidden input the Choose file button opens
  const input = () => view.container.querySelector("input[type=file]") as HTMLInputElement;
  return { ...view, onChange, input, user: userEvent.setup({ applyAccept: false }) };
}

function pdf(name = "dd.pdf", size = 2 * MB) {
  const file = new File(["%PDF-1.7"], name, { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

test("a file of the wrong type or over max_mb is refused before anything is sent", async () => {
  const files = api();
  const { user, input } = renderSlot({ files });
  expect(screen.getByText("PDF, JPG, PNG · up to 25 MB")).toBeDefined();
  expect(input().accept).toBe(".pdf,.jpg,.png");

  await user.upload(input(), new File(["MZ"], "noc.exe", { type: "application/x-msdownload" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Only PDF, JPG, PNG files go here");
  await user.upload(input(), pdf("big.pdf", 30 * MB));
  expect((await screen.findByRole("alert")).textContent).toBe("The file is larger than 25 MB");
  expect(files.upload).not.toHaveBeenCalled();
  expect(FakeXhr.last).toBeUndefined();
});

test("an upload goes to the signed URL with progress announced, then is confirmed into the form", async () => {
  const files = api({ versions: vi.fn(async () => [] as FileVersion[]) });
  const { user, input, onChange } = renderSlot({ files });
  const file = pdf();
  await user.upload(input(), file);

  await waitFor(() => expect(FakeXhr.last).toBeDefined());
  expect(files.upload).toHaveBeenCalledWith("dd_report", file);
  const xhr = sent();
  expect([xhr.method, xhr.url, xhr.headers["Content-Type"], xhr.body]).toEqual(["PUT", expect.stringContaining("/upload/sign/"), "application/pdf", file]);
  xhr.progress(0.5);
  const bar = screen.getByRole("progressbar", { name: "Uploading dd.pdf" });
  expect(bar.getAttribute("aria-valuenow")).toBe("50");
  expect(screen.getByRole("status").textContent).toBe("Uploading dd.pdf: 50%");
  expect(screen.getByRole("button", { name: "Choose file" }).hasAttribute("disabled")).toBe(true);

  xhr.answer(200);
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(VALUE));
  expect(files.confirm).toHaveBeenCalledWith("f9");
  expect(screen.getByRole("status").textContent).toBe("Uploaded dd.pdf");
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(files.versions).toHaveBeenCalledTimes(2); // on mount, and again after the confirm
});

test("a file the API refuses on confirm shows the reason and fills nothing", async () => {
  const files = api({ confirm: vi.fn(async () => Promise.reject(new FileApiError(422, "This isn't a real PDF file"))) });
  const { user, input, onChange } = renderSlot({ files });
  await user.upload(input(), pdf("noc.pdf"));
  await waitFor(() => expect(FakeXhr.last).toBeDefined());
  sent().answer(200);
  expect((await screen.findByRole("alert")).textContent).toBe("This isn't a real PDF file");
  expect(onChange).not.toHaveBeenCalled();
});

test("storage refusing the PUT is an error, and the slot can try again", async () => {
  const files = api();
  const { user, input } = renderSlot({ files });
  await user.upload(input(), pdf());
  await waitFor(() => expect(FakeXhr.last).toBeDefined());
  sent().answer(413);
  expect((await screen.findByRole("alert")).textContent).toBe("The file is larger than storage allows");
  expect(files.confirm).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Choose file" }).hasAttribute("disabled")).toBe(false);
});

test.each([403, 503])("a %i from Storage on the PUT is an upload error, never 'off in preview', and can be retried", async (status) => {
  const files = api();
  const { user, input } = renderSlot({ files });
  await user.upload(input(), pdf());
  await waitFor(() => expect(FakeXhr.last).toBeDefined());
  sent().answer(status); // e.g. the signed URL ran out, or Storage was briefly down
  expect((await screen.findByRole("alert")).textContent).toBe("The upload didn't go through; try again");
  expect(screen.queryByText("Uploads are off in preview")).toBeNull();
  expect(screen.queryByText("Uploads aren't available in this workspace")).toBeNull();
  expect(screen.getByRole("button", { name: "Choose file" }).hasAttribute("disabled")).toBe(false);

  FakeXhr.last = undefined; // the retry goes through
  await user.upload(input(), pdf());
  await waitFor(() => expect(FakeXhr.last).toBeDefined());
  sent().answer(200);
  await waitFor(() => expect(files.confirm).toHaveBeenCalledTimes(1));
  expect(files.upload).toHaveBeenCalledTimes(2);
});

test("a pdf / png / jpg is PUT as its own type, whatever the OS reported", async () => {
  const { user, input } = renderSlot({ files: api() });
  await user.upload(input(), new File(["\u0089PNG"], "Plan.PNG", { type: "" }));
  await waitFor(() => expect(FakeXhr.last).toBeDefined());
  expect(sent().headers["Content-Type"]).toBe("image/png");
});

test("two files dropped at once start one upload", async () => {
  const files = api();
  const { container } = renderSlot({ files });
  const zone = container.querySelector("[data-zm-drop]") as HTMLElement;
  act(() => {
    fireEvent.drop(zone, { dataTransfer: { files: [pdf("a.pdf")] } });
    fireEvent.drop(zone, { dataTransfer: { files: [pdf("b.pdf")] } });
  });
  await waitFor(() => expect(FakeXhr.last).toBeDefined());
  expect(files.upload).toHaveBeenCalledTimes(1);
  expect(files.upload).toHaveBeenCalledWith("dd_report", expect.objectContaining({ name: "a.pdf" }));
});

/** The signed URL's bytes, as Storage serves them, whatever type it says. */
function stubPreviewFetch() {
  const objectUrls = { made: [] as Blob[], revoked: [] as string[] };
  vi.stubGlobal("fetch", vi.fn(async () => new Response("<script>alert(1)</script>", { headers: { "content-type": "text/html" } })));
  // jsdom has no object URLs
  Object.assign(URL, {
    createObjectURL: (blob: Blob) => `blob:preview-${objectUrls.made.push(blob)}`,
    revokeObjectURL: (url: string) => void objectUrls.revoked.push(url),
  });
  return objectUrls;
}

test("versions list newest first; each downloads, and previews from a blob of its own type", async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const objectUrls = stubPreviewFetch();
  const files = api({ versions: vi.fn(async () => [V2, V1]) });
  const { user } = renderSlot({ files, value: VALUE });
  const list = await screen.findByRole("list", { name: "DD report versions" });
  expect(within(list).getAllByRole("listitem").map((li) => li.textContent?.slice(0, 2))).toEqual(["v2", "v1"]);
  expect(screen.getByText("Attached: dd.pdf")).toBeDefined();
  expect(screen.getByRole("button", { name: "Replace file" })).toBeDefined();

  await user.click(screen.getByRole("button", { name: "Download dd-v1.pdf v1" }));
  await waitFor(() => expect(click).toHaveBeenCalled());
  expect(files.url).toHaveBeenCalledWith("f1", true);
  expect((click.mock.contexts[0] as HTMLAnchorElement).href).toBe("http://storage.test/f1?token=t&download=x");

  // the preview is a local blob typed as the file's own type: the browser renders it only as a
  // PDF (or an image) whatever Storage says, and it outlives the 60-second signed URL
  await user.click(screen.getByRole("button", { name: "Preview dd-v1.pdf v1" }));
  const frame = await screen.findByTitle("Preview of dd-v1.pdf");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("src")).toBe("blob:preview-1");
  expect(fetch).toHaveBeenCalledWith("http://storage.test/f1?token=t");
  expect(objectUrls.made[0]?.type).toBe("application/pdf");
  await user.click(screen.getByRole("button", { name: "Preview site.png v2" }));
  expect((await screen.findByRole("img", { name: "site.png" })).getAttribute("src")).toBe("blob:preview-2");
  expect(objectUrls.made[1]?.type).toBe("image/png");
  expect(screen.queryByTitle("Preview of dd-v1.pdf")).toBeNull();
  expect(objectUrls.revoked).toEqual(["blob:preview-1"]);
  await user.click(screen.getByRole("button", { name: "Close preview" }));
  expect(screen.queryByRole("img", { name: "site.png" })).toBeNull();
  expect(objectUrls.revoked).toEqual(["blob:preview-1", "blob:preview-2"]);
});

test("read-only: the versions show, nothing can be uploaded", async () => {
  renderSlot({ readOnly: true, files: api({ versions: async () => [V1] }) });
  expect(await screen.findByRole("button", { name: "Download dd-v1.pdf v1" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "Choose file" })).toBeNull();
});

test.each([
  [503, "Uploads aren't available in this workspace"],
  [403, "Uploads are off in preview"],
])("a %i on the versions says why and offers no upload", async (status, message) => {
  renderSlot({ files: api({ versions: async () => Promise.reject(new FileApiError(status, "x")) }) });
  expect(await screen.findByText(message)).toBeDefined();
  expect(screen.queryByRole("button", { name: "Choose file" })).toBeNull();
});

test("without a file API (the /dev/ui previews) the slot says uploads aren't available here", () => {
  renderSlot({ files: undefined });
  expect(screen.getByText("Uploads aren't available here")).toBeDefined();
});

test("the form's own error and required mark show on the slot", () => {
  renderSlot({ files: api(), error: "Required", withAsterisk: true });
  const group = screen.getByRole("group", { name: /DD report/ });
  expect(within(group).getByText("Required")).toBeDefined();
  expect(group.getAttribute("aria-describedby")).toContain(screen.getByText("Required").id);
});

test.each(["light", "dark"] as const)("axe passes with versions and an upload in progress (%s)", async (scheme) => {
  const { user, input, container } = renderSlot({ files: api({ versions: async () => [V2, V1] }), value: VALUE }, scheme);
  await screen.findByRole("list", { name: "DD report versions" });
  await user.upload(input(), pdf());
  await waitFor(() => expect(FakeXhr.last).toBeDefined());
  sent().progress(0.25);
  await expectNoAxeViolations(container);
});
