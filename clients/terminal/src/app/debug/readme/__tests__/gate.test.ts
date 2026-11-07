/** The README lab is never served by a production build (404 there). */
import { describe, expect, it } from "vitest";
import { labEnabled } from "../gate";

describe("README lab gate", () => {
  it("is off in production and on in development", () => {
    expect(labEnabled({ NODE_ENV: "production" })).toBe(false);
    expect(labEnabled({ NODE_ENV: "development" })).toBe(true);
  });
});
