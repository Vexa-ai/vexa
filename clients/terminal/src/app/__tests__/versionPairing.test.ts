/** The pairing number exists in TWO places and a swap trusts the one it can read without starting
 *  a container. This test is the only thing keeping them equal.
 *
 *  F67 is the precedent: `NEXT_PUBLIC_TERMINAL_MODE` was a property of the bundle that the image
 *  did not declare, so a `-minutes` tag was a label the builder typed and two wrong-variant images
 *  passed every test that existed. The fix was to make the image state the fact — and a stated fact
 *  drifts from its source the first time someone edits one of them.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { TERMINAL_AGENT_API } from "../../version";

const dockerfile = readFileSync(resolve(process.cwd(), "Dockerfile"), "utf8");

describe("terminal image labels", () => {
  it("declares the agent-api pairing number the bundle was built against", () => {
    const m = dockerfile.match(/^LABEL ai\.vexa\.terminal\.agent_api="(\d+)"$/m);
    expect(m, "the Dockerfile no longer declares ai.vexa.terminal.agent_api — deploy.sh reads it").not.toBeNull();
    expect(Number(m![1])).toBe(TERMINAL_AGENT_API);
  });

  it("still declares the variant (F67) — the other fact the swap refuses on", () => {
    expect(dockerfile).toMatch(/^LABEL ai\.vexa\.terminal\.mode=/m);
  });

  it("bakes the build stamp the reload bar compares", () => {
    expect(dockerfile).toMatch(/ARG NEXT_PUBLIC_BUILD_ID/);
    expect(dockerfile).toMatch(/^LABEL ai\.vexa\.terminal\.build=/m);
  });
});
