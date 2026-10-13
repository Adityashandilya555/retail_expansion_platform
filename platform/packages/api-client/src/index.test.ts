import { expect, test } from "vitest";
import { PACKAGE } from "./index";

test("exports its package name", () => {
  expect(PACKAGE).toBe("@zm/api-client");
});
