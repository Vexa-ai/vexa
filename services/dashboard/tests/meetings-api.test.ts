import { afterEach, describe, expect, it, vi } from "vitest";
import { vexaAPI } from "../src/lib/api";

describe("meetings history API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests run history without planned calendar rows", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ meetings: [], has_more: false }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await vexaAPI.getMeetings({ limit: 50, exclude_planned: true });

    expect(fetchMock).toHaveBeenCalledWith("/api/vexa/meetings?limit=50&exclude_planned=true");
  });

  it("leaves the calendar's explicit scheduled query unchanged", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ meetings: [], has_more: false }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await vexaAPI.getMeetings({ status: "scheduled", limit: 100 });

    expect(fetchMock).toHaveBeenCalledWith("/api/vexa/meetings?limit=100&status=scheduled");
  });
});
