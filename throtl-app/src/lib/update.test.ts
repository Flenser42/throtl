import { describe, expect, it } from "vitest";

import { isNewer } from "./update";

describe("isNewer", () => {
  it("orders pre-release identifiers numerically", () => {
    expect(isNewer("1.0.0-beta-1", "1.0.0-beta-2")).toBe(true);
    expect(isNewer("1.0.0-beta-2", "1.0.0-beta-1")).toBe(false);
  });

  it("treats equal versions as not newer", () => {
    expect(isNewer("1.0.0-beta-1", "1.0.0-beta-1")).toBe(false);
  });

  it("sorts a release after its pre-release", () => {
    expect(isNewer("1.0.0-rc1", "1.0.0")).toBe(true);
  });
});
