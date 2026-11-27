// InviteLink (S3-01): the link, until when it works, the copy button, and axe in light and dark.
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { InviteLink } from "./InviteLink";
import { expectNoAxeViolations, renderWithZm } from "./test";

const LINK = "https://app.zm.test/invite/pilot.abc123";
const EXPIRES = "2026-12-04T10:00:00Z";

test("InviteLink shows the link read-only with until when it works, and copies it", async () => {
  const user = userEvent.setup();
  renderWithZm(<InviteLink url={LINK} expiresAt={EXPIRES} />);
  const until = new Date(EXPIRES).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  const field = screen.getByRole<HTMLInputElement>("textbox", {
    name: "Invite link",
    description: `Works once, until ${until}. Also emailed if email is set up.`,
  });
  expect(field.value).toBe(LINK);
  expect(field.readOnly).toBe(true);
  await user.click(screen.getByRole("button", { name: "Copy link" }));
  expect(await navigator.clipboard.readText()).toBe(LINK);
  expect(screen.getByRole("button", { name: "Copied" })).toBeDefined();
});

test.each(["light", "dark"] as const)("InviteLink passes axe (%s)", async (scheme) => {
  renderWithZm(<InviteLink url={LINK} expiresAt={EXPIRES} />, { scheme });
  await expectNoAxeViolations();
});
