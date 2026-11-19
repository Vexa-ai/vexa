/** Settings → Sign-in: the admin edits who may sign in (Vexa-ai/vexa#1783).
 *
 *  The list lives in admin-api's platform settings (`signin.allow`, behind the admin-gated
 *  `/api/admin/settings/signin`); the deployment's `VEXA_SIGNIN_ALLOW` is the other half and is shown
 *  read-only. Three things the admin must be able to trust: what loads is what is stored, a save
 *  sends exactly what they typed, and a refused save says WHICH entry was wrong.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { SigninSection } from "../settings";
import { allowLines } from "../settingsApi";

let puts: string[] = [];

function stubAdminRoute(opts: { refuse?: string } = {}) {
  puts = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url) !== "/api/admin/settings/signin") return new Response("{}", { status: 404 });
    if (init?.method === "PUT") {
      const body = String(init.body);
      puts.push(body);
      if (opts.refuse) return new Response(JSON.stringify({ detail: opts.refuse }), { status: 422 });
      const allow = (JSON.parse(body).allow as string).split(/[\s,]+/).filter(Boolean).map((e) => e.toLowerCase()).join(", ");
      return new Response(JSON.stringify({ key: "signin", value: allow ? { allow } : {} }), { status: 200 });
    }
    return new Response(JSON.stringify({
      key: "signin",
      value: { allow: "alice@example.com, @bank.example" },
      env: { allow: "@seeded.example" },
      env_problems: ["'typo.example' has no @"],
    }), { status: 200 });
  }));
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Settings → Sign-in", () => {
  it("shows the stored list one per line, and the deployment's half read-only with its problems", async () => {
    stubAdminRoute();
    render(<SigninSection />);
    const box = await screen.findByLabelText("Allowed addresses and domains") as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("alice@example.com\n@bank.example"));
    expect(screen.getByText("@seeded.example")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("typo.example");
  });

  it("saves exactly what was typed, and shows the canonical list that came back", async () => {
    stubAdminRoute();
    render(<SigninSection />);
    const box = await screen.findByLabelText("Allowed addresses and domains") as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).not.toBe(""));
    fireEvent.change(box, { target: { value: "Bob@Example.net\n@bank.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Saved/);
    expect(JSON.parse(puts[0])).toEqual({ allow: "Bob@Example.net\n@bank.example" });
    expect(box.value).toBe("bob@example.net\n@bank.example");
  });

  it("a refused save names the bad entry and changes nothing", async () => {
    stubAdminRoute({ refuse: "'bank.example' has no @ — write @bank.example to allow everyone at that domain" });
    render(<SigninSection />);
    const box = await screen.findByLabelText("Allowed addresses and domains") as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).not.toBe(""));
    fireEvent.change(box, { target: { value: "bank.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("'bank.example' has no @"))).toBe(true));
    expect(screen.queryByText(/Saved/)).toBeNull();
  });

  it("allowLines turns the stored form into one entry per line", () => {
    expect(allowLines("a@b.co, @c.co")).toBe("a@b.co\n@c.co");
    expect(allowLines("")).toBe("");
  });
});
