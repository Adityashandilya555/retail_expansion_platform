import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { AuthCard, notifyError, SignInForm } from "./SignIn";
import { expectNoAxeViolations, renderWithZm } from "./test";

function renderCard(onSubmit = vi.fn(), scheme?: "light" | "dark") {
  renderWithZm(
    <AuthCard title="Matrix Retail" description="Sign in with your work email.">
      <SignInForm onSubmit={onSubmit} />
    </AuthCard>,
    { scheme },
  );
  return onSubmit;
}

describe("SignInForm", () => {
  test("is filled and sent with the keyboard only", async () => {
    const onSubmit = renderCard();
    const user = userEvent.setup();
    expect(document.activeElement).toBe(screen.getByLabelText(/Email/)); // focused on arrival
    await user.keyboard(" legal.l1@pilot.demo ");
    await user.tab();
    expect(document.activeElement).toBe(screen.getByLabelText(/Password/));
    await user.keyboard("s3cret{Enter}");
    expect(onSubmit).toHaveBeenCalledWith({ email: "legal.l1@pilot.demo", password: "s3cret" });
  });

  test("names the client and the brand", () => {
    renderCard();
    expect(screen.getByRole("heading", { level: 1, name: "Matrix Retail" })).toBeDefined();
    expect(screen.getByText("Z-Matrix")).toBeDefined();
  });

  test("shows pending on the button", () => {
    renderWithZm(<SignInForm pending onSubmit={vi.fn()} />);
    expect((screen.getByRole("button", { name: /Sign in/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  test.each(["light", "dark"] as const)("has no axe violations (%s)", async (scheme) => {
    renderCard(vi.fn(), scheme);
    await expectNoAxeViolations();
  });
});

test("notifyError shows the message", async () => {
  renderWithZm(<button onClick={() => notifyError("We couldn't find that workspace.")}>go</button>);
  await userEvent.setup().click(screen.getByRole("button", { name: "go" }));
  expect(await screen.findByText("We couldn't find that workspace.")).toBeDefined();
});
