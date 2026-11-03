/** `/p/<handle>` (D35): the public profile shows what the server answered (the fields the person
 *  marked public) and says plainly when a profile is not public. Synthetic data. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import PublicProfile from "../p/[handle]/page";
import { publicProfilePath } from "../../surfaces/personApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function answer(status: number, body: unknown) {
  vi.stubGlobal("fetch", () => Promise.resolve({ ok: status === 200, status, json: () => Promise.resolve(body) } as Response));
}

describe("the public profile page", () => {
  it("draws the public fields and links", async () => {
    answer(200, { handle: "h".repeat(22), fields: { display_name: "Val Example", headline: "Builds things",
      links: { github: "https://github.com/val-example" } } });
    render(<PublicProfile params={Promise.resolve({ handle: "h".repeat(22) })} />);
    expect(await screen.findByText("Val Example")).toBeTruthy();
    expect(screen.getByText("Builds things")).toBeTruthy();
    expect(screen.getByText("GitHub").getAttribute("href")).toBe("https://github.com/val-example");
  });

  it("says a profile is not public when the server has none", async () => {
    answer(404, {});
    render(<PublicProfile params={Promise.resolve({ handle: "x".repeat(22) })} />);
    expect(await screen.findByText("This profile is not public.")).toBeTruthy();
    expect(publicProfilePath("abc")).toBe("/p/abc");
  });
});
