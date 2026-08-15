import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const serverPageSource = readFileSync(
  fileURLToPath(new URL("../src/app/meetings/page.tsx", import.meta.url)),
  "utf8",
);
const clientPageSource = readFileSync(
  fileURLToPath(new URL("../src/app/meetings/meetings-client.tsx", import.meta.url)),
  "utf8",
);
const serverLoaderSource = readFileSync(
  fileURLToPath(new URL("../src/lib/meetings-page.server.ts", import.meta.url)),
  "utf8",
);

describe("meetings page initial load", () => {
  it("server-loads the first historical page", () => {
    expect(serverPageSource).toContain("await loadInitialMeetingsPage()");
    expect(serverLoaderSource).toContain('exclude_planned: "true"');
    expect(serverLoaderSource).toContain('cache: "no-store"');
  });

  it("hydrates successful server data without a duplicate client request", () => {
    expect(clientPageSource).toContain('if (initialPage.state === "fallback")');
    expect(clientPageSource).toContain('applyFilters("", "all", "all")');
    expect(clientPageSource).toContain("hydrateMeetings(initialPage)");
    expect(clientPageSource).toContain("initialFilterPass.current = false");
  });
});
