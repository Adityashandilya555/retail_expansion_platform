import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expectNoAxeViolations, renderWithZm } from "@zm/ui/test";
import { describe, expect, test, vi } from "vitest";
import caEntry from "../../schema/tests/fixtures/fin_ca_entry.schema.json";
import { sampleApproval as APPR, sampleDdChecklist as DD } from "./samples";
import type { FormSchema } from "./validate";
import { ZmForm, type ZmFormProps } from "./ZmForm";

const CA = caEntry as FormSchema;
const OUTCOME = "qsr_licences__appr_legal_L3_outcome";
const COMMENT = "qsr_licences__appr_legal_L3_comment";

function renderForm(props: Partial<ZmFormProps> & { schema: FormSchema }, scheme?: "light" | "dark") {
  const onSubmit = props.onSubmit ?? vi.fn<ZmFormProps["onSubmit"]>(async () => undefined);
  const view = renderWithZm(<ZmForm layout={props.schema["x-zm-layout"] ?? []} {...props} onSubmit={onSubmit} />, { scheme });
  return { ...view, onSubmit, user: userEvent.setup() };
}

/** The CSS Mantine generated for the column holding `label`'s field, base rule and `sm` rule. */
function columnCss(label: string): { base: string; sm: string } {
  const col = screen.getByText(new RegExp(`^${label}`), { selector: ".mantine-InputWrapper-label" }).closest<HTMLElement>(".mantine-Grid-col");
  const cls = [...(col?.classList ?? [])].find((c) => c.startsWith("__m__"));
  const css = [...document.querySelectorAll("style")].map((s) => s.textContent).join("\n");
  const rules = [...css.matchAll(new RegExp(`(@media[^{]*\\{)?\\s*\\.${cls}\\{([^}]*)\\}`, "g"))];
  return {
    base: rules.find((r) => !r[1])?.[2] ?? "",
    sm: rules.find((r) => r[1]?.includes("48em"))?.[2] ?? "",
  };
}

describe("legal_dd_checklist", () => {
  test("renders 6 yes/no fields, the textarea and the disabled document note in the grid", () => {
    renderForm({ schema: DD });
    const section = screen.getByRole("group", { name: "Fill DD checklist" });
    expect(within(section).getAllByRole("radiogroup")).toHaveLength(6);
    for (const label of ["Title document clear", "Sanctioned plan", "OC / CC", "Commercial use permitted", "Property tax paid", "Electricity load OK"]) {
      const group = within(section).getByRole("radiogroup", { name: label });
      expect(within(group).getAllByRole("radio").map((r) => r.getAttribute("value"))).toEqual(["yes", "no"]);
      expect(columnCss(label)).toEqual({ base: expect.stringContaining("100%"), sm: expect.stringContaining("100%") });
    }
    expect(within(section).getByLabelText("Notes").tagName).toBe("TEXTAREA");
    expect((within(section).getByLabelText("DD report") as HTMLInputElement).disabled).toBe(true);
    expect(within(section).getByText("Uploads arrive with Files")).toBeDefined();
  });

  test("shows the case data panel from x-zm-show, read-only", () => {
    renderForm({
      schema: DD,
      caseLabel: "Site",
      caseData: { loi_file: { label: "Signed LOI", value: { key: "sites/MX-0001/bd/loi/1-loi.pdf", sha256: "0".repeat(64), name: "loi.pdf" } } },
    });
    const panel = screen.getByRole("region", { name: "Site data" });
    expect(within(panel).getByText("Signed LOI")).toBeDefined();
    expect(within(panel).getByText("loi.pdf")).toBeDefined();
    expect(within(panel).queryByRole("textbox")).toBeNull();
  });

  test("a yes/no answer submits as a boolean and an unanswered one is left out", async () => {
    const { user, onSubmit } = renderForm({ schema: DD, values: { dd_oc_cc: false } });
    expect((within(screen.getByRole("radiogroup", { name: "OC / CC" })).getByRole("radio", { name: "No" }) as HTMLInputElement).checked).toBe(true);
    await user.click(within(screen.getByRole("radiogroup", { name: "Title document clear" })).getByRole("radio", { name: "Yes" }));
    await user.click(screen.getByRole("button", { name: "Submit" }));
    expect(onSubmit).toHaveBeenCalledWith({ dd_oc_cc: false, dd_title_doc: true });
  });

  test.each(["light", "dark"] as const)("has no axe violations (%s)", async (scheme) => {
    const { container } = renderForm({ schema: DD, caseLabel: "Site", caseData: { loi_file: { label: "Signed LOI", value: null } } }, scheme);
    await expectNoAxeViolations(container);
  });
});

describe("validation", () => {
  test("a missing required field shows the error and sends no request", async () => {
    const { user, onSubmit } = renderForm({ schema: CA });
    await user.type(screen.getByLabelText(/CA code/), "CA-17");
    await user.click(screen.getByRole("button", { name: "Submit" }));
    expect(onSubmit).not.toHaveBeenCalled();
    const amount = screen.getByLabelText(/Amount \(INR\)/);
    expect(amount.getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(amount);
    expect(screen.getAllByText("Required")).toHaveLength(1);
  });

  test("a cleared text field counts as missing", async () => {
    const { user, onSubmit } = renderForm({ schema: CA, values: { ca_code: "CA-17", ca_amount: 10 } });
    await user.clear(screen.getByLabelText(/CA code/));
    await user.click(screen.getByRole("button", { name: "Submit" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/CA code/).getAttribute("aria-invalid")).toBe("true");
  });

  test("a server 422 appears under the right field; unknown paths go to the form", async () => {
    const onSubmit = vi.fn(async () => [
      { path: "/ca_code", message: "CA code already used" },
      { path: "", message: "Release changed, reload" },
    ]);
    const { user } = renderForm({ schema: CA, values: { ca_code: "CA-17", ca_amount: 10 }, onSubmit });
    await user.click(screen.getByRole("button", { name: "Submit" }));
    expect(onSubmit).toHaveBeenCalledWith({ ca_code: "CA-17", ca_amount: 10 });
    const code = screen.getByLabelText(/CA code/);
    expect(code.getAttribute("aria-invalid")).toBe("true");
    const described = (code.getAttribute("aria-describedby") ?? "").split(" ").map((id) => document.getElementById(id)?.textContent);
    expect(described).toContain("CA code already used");
    expect(screen.getByRole("alert").textContent).toContain("Release changed, reload");
  });

  test("submit is disabled while the request is pending", async () => {
    let resolve: () => void = () => {};
    const onSubmit = vi.fn(() => new Promise<void>((r) => (resolve = r)));
    const { user } = renderForm({ schema: CA, values: { ca_code: "CA-17", ca_amount: 10 }, onSubmit });
    await user.click(screen.getByRole("button", { name: "Submit" }));
    expect((screen.getByRole("button", { name: "Submit" }) as HTMLInputElement).disabled).toBe(true);
    await act(async () => resolve());
    expect((screen.getByRole("button", { name: "Submit" }) as HTMLButtonElement).disabled).toBe(false);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});

describe("approval task", () => {
  test("outcomes are buttons: the first is primary, reject is red", () => {
    renderForm({ schema: APPR });
    const approve = screen.getByRole("button", { name: "Approve" });
    const sendBack = screen.getByRole("button", { name: "Send back" });
    const reject = screen.getByRole("button", { name: "Reject" });
    expect(approve.getAttribute("data-variant")).toBe("filled");
    expect(sendBack.getAttribute("data-variant")).not.toBe("filled");
    expect(reject.getAttribute("style")).toContain("red");
    expect(approve.getAttribute("style") ?? "").not.toContain("red");
    expect(screen.queryByRole("button", { name: "Submit" })).toBeNull();
  });

  test("send back without a comment is rejected and focuses the comment until it has text", async () => {
    const { user, onSubmit } = renderForm({ schema: APPR });
    await user.click(screen.getByRole("button", { name: "Send back" }));
    expect(onSubmit).not.toHaveBeenCalled();
    const comment = screen.getByLabelText("Comment");
    expect(document.activeElement).toBe(comment);
    expect(comment.getAttribute("aria-invalid")).toBe("true");
    await user.type(comment, "Fire NOC is missing.");
    expect(comment.hasAttribute("aria-invalid")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Send back" }));
    expect(onSubmit).toHaveBeenCalledWith({ [OUTCOME]: "send_back", [COMMENT]: "Fire NOC is missing." });
  });

  test("approve needs no comment", async () => {
    const { user, onSubmit } = renderForm({ schema: APPR });
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(onSubmit).toHaveBeenCalledWith({ [OUTCOME]: "approve" });
  });

  test.each(["light", "dark"] as const)("has no axe violations (%s)", async (scheme) => {
    const { container } = renderForm({ schema: APPR, caseLabel: "Store", caseData: { store_name: { label: "Store name", value: "Indiranagar" } } }, scheme);
    await expectNoAxeViolations(container);
  });
});

describe("field types", () => {
  const ALL: FormSchema = {
    type: "object",
    additionalProperties: false,
    required: ["name"],
    properties: {
      name: { type: "string", title: "Name", "x-zm-type": "text" },
      area: { type: "number", title: "Area", "x-zm-type": "number" },
      rent: { type: "number", title: "Rent", "x-zm-type": "currency" },
      opened: { type: "string", format: "date", title: "Opened", "x-zm-type": "date" },
      model: { type: "string", enum: ["kiosk", "cafe"], title: "Model", "x-zm-type": "select", "x-zm-options": [{ id: "kiosk", label: "Kiosk" }, { id: "cafe", label: "Café" }] },
      checks: { type: "object", title: "Checks", "x-zm-type": "checklist" },
    },
  };
  const layout = [
    { key: "a", title: "Basics", rows: [[{ field: "name", span: 6 }, { field: "area", span: 6 }], [{ field: "rent", span: 4 }, { field: "opened", span: 4 }, { field: "model", span: 4 }], [{ field: "checks" }]] },
  ];

  test("each type gets its Mantine input and the configured span from sm up", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { user, onSubmit } = renderForm({ schema: ALL, layout, values: { rent: 250000 } });
    expect((screen.getByLabelText(/Rent/) as HTMLInputElement).value).toBe("₹ 2,50,000");
    expect(screen.getByLabelText(/Opened/).getAttribute("type")).toBe("date");
    expect(screen.getByText("checklist fields are unsupported in v1")).toBeDefined();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("checklist"));
    expect(columnCss("Name")).toEqual({ base: expect.stringContaining("100%"), sm: expect.stringContaining("50%") });
    expect(columnCss("Rent").sm).toContain("33.3");

    await user.type(screen.getByLabelText(/Name/), "Indiranagar");
    await user.type(screen.getByLabelText(/Area/), "1200");
    await user.click(screen.getByLabelText(/Model/, { selector: "input" }));
    await user.click(await screen.findByRole("option", { name: "Café" }));
    await user.type(screen.getByLabelText(/Opened/), "2026-11-01");
    await user.click(screen.getByRole("button", { name: "Submit" }));
    expect(onSubmit).toHaveBeenCalledWith({ name: "Indiranagar", area: 1200, rent: 250000, opened: "2026-11-01", model: "cafe" });
    warn.mockRestore();
  });

  test("read-only shows values without actions", () => {
    renderForm({ schema: APPR, readOnly: true, values: { [OUTCOME]: "approve", [COMMENT]: "ok" } });
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByLabelText("Comment").hasAttribute("readonly")).toBe(true);
  });
});
