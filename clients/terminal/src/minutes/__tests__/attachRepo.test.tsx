/** ATTACH AN EXISTING REPO — the four claims that have a plausible wrong answer.
 *
 *  Who may be attached onto: a VIEWER of a group may read its workspace and may never replace it, so
 *  offering them the target would produce a control whose only outcome is the server's 403. The desk
 *  is always offered, because it always exists.
 *
 *  What the result says: `cloned` · `restored` · `already attached` are three different facts about
 *  where the group's data now is, and "done" is none of them.
 *
 *  What a credential failure says: the server has already composed the fix — its 502 detail carries the
 *  public key and the "say `done` when added" sentence — and the key is the only part the person can
 *  act on. A paraphrase drops it, so the detail is rendered verbatim.
 *
 *  What happens to a one-off token: it is handed to the call and then it is gone. Not masked, not
 *  disabled — absent from the DOM, so there is nothing left to re-send or read back. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { AttachRepo, attachTargets } from "../AttachRepo";
import { ApiError } from "../../surfaces/apiClient";
import * as api from "../../surfaces/workspaceApi";

vi.mock("../../surfaces/workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../surfaces/workspaceApi")>()),
  listSharedMemberships: vi.fn(),
  readDeployKey: vi.fn(),
  ensureDeployKey: vi.fn(),
  attachSharedWorkspace: vi.fn(),
  swapWorkspace: vi.fn(),
}));

const MEMBERSHIPS: api.Membership[] = [
  { workspace_id: "acme-kg", role: "owner" },
  { workspace_id: "deal-room", role: "contributor" },
  { workspace_id: "read-only", role: "viewer" },
];

const PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEKEYFORTESTS vexa";
const REFUSAL =
  "git clone failed: Permission denied (publickey).\n\n" +
  "This workspace has no credential for that repository yet. Add this public key at " +
  "https://github.com/acme/kg/settings/keys as a deploy key with WRITE access, then say `done` when added:\n" +
  PUBKEY;

beforeEach(() => {
  // The module-factory mocks live for the whole FILE, so their call log outlives each test unless it
  // is cleared — and a stale calls[0] would let an assertion pass against the previous test s call.
  vi.clearAllMocks();
  vi.mocked(api.listSharedMemberships).mockResolvedValue(MEMBERSHIPS);
  vi.mocked(api.readDeployKey).mockResolvedValue({
    slug: "seed", public_key: null, fingerprint: null, add_as: "a deploy key with WRITE access",
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const open = (over: Partial<Parameters<typeof AttachRepo>[0]> = {}) =>
  render(<AttachRepo onClose={() => {}} {...over} />);

const targetSelect = () => screen.getByLabelText("Load into") as HTMLSelectElement;
const optionLabels = () => Array.from(targetSelect().options).map((o) => o.textContent);

/** Fill the form and press Attach — the shared preamble of the state/verbatim/token cases. */
async function submit(repo = "git@github.com:acme/kg.git", into?: string) {
  await screen.findByRole("option", { name: "acme-kg" });
  if (into !== undefined) fireEvent.change(targetSelect(), { target: { value: into } });
  fireEvent.change(screen.getByLabelText("Repository"), { target: { value: repo } });
  fireEvent.click(screen.getByText("Attach"));
}

describe("who may be attached onto", () => {
  it("excludes viewer memberships and always includes the desk", () => {
    expect(attachTargets(MEMBERSHIPS)).toEqual([
      { value: "", label: "Personal" },
      { value: "acme-kg", label: "acme-kg" },
      { value: "deal-room", label: "deal-room" },
    ]);
    expect(attachTargets([])).toEqual([{ value: "", label: "Personal" }]);
  });

  it("the rendered target list offers the writable groups, never the viewer one", async () => {
    open();
    await screen.findByRole("option", { name: "acme-kg" });
    expect(optionLabels()).toEqual(["Personal", "acme-kg", "deal-room"]);
    expect(screen.queryByRole("option", { name: "read-only" })).toBeNull();
  });

  it("a workspaceId prop preselects that group", async () => {
    open({ workspaceId: "deal-room" });
    await screen.findByRole("option", { name: "deal-room" });
    expect(targetSelect().value).toBe("deal-room");
  });
});

describe("the result states a state", () => {
  it("a clone into a group says which repo went where", async () => {
    vi.mocked(api.attachSharedWorkspace).mockResolvedValue({
      workspace_id: "acme-kg", active: "acme-kg", repo: "git@github.com:acme/kg.git", ref: "main",
      attached: true, cloned: true, parked: "acme-kg-prev", nested: false, state: "cloned",
    });
    open();
    await submit("git@github.com:acme/kg.git", "acme-kg");
    const card = await screen.findByText("Cloned git@github.com:acme/kg.git into acme-kg");
    expect(card).toBeTruthy();
    expect(vi.mocked(api.attachSharedWorkspace).mock.calls[0][0]).toBe("acme-kg");
  });

  it("a restore is not reported as a clone, and a no-op is neither", async () => {
    vi.mocked(api.attachSharedWorkspace).mockResolvedValue({
      workspace_id: "acme-kg", active: "acme-kg", repo: "git@github.com:acme/kg.git", ref: "main",
      attached: true, cloned: false, parked: null, nested: false, state: "restored",
    });
    open();
    await submit("git@github.com:acme/kg.git", "acme-kg");
    await screen.findByText("Restored acme-kg from the copy already here (no re-clone)");
    cleanup();

    vi.mocked(api.attachSharedWorkspace).mockResolvedValue({
      workspace_id: "acme-kg", active: "acme-kg", repo: "git@github.com:acme/kg.git", ref: "main",
      attached: false, cloned: false, parked: null, nested: false, state: "already attached",
    });
    open();
    await submit("git@github.com:acme/kg.git", "acme-kg");
    await screen.findByText("Already attached — nothing changed");
  });

  it("the desk lane goes through swapWorkspace, not the shared route", async () => {
    vi.mocked(api.swapWorkspace).mockResolvedValue({
      subject: "u1", active: "seed", repo: "git@github.com:me/kg.git", ref: "main",
      swapped: true, cloned: true, parked: "seed-prev", nested: false,
    });
    open();
    await submit("git@github.com:me/kg.git");
    await screen.findByText("Cloned git@github.com:me/kg.git into Personal");
    expect(api.attachSharedWorkspace).not.toHaveBeenCalled();
  });
});

describe("a credential refusal is shown verbatim", () => {
  it("a 502 whose detail carries the public key renders the key AND the say-`done` line", async () => {
    vi.mocked(api.attachSharedWorkspace).mockRejectedValue(
      new ApiError(502, REFUSAL, "/api/workspace/shared/acme-kg/attach"),
    );
    const { container } = open();
    await submit("git@github.com:acme/kg.git", "acme-kg");
    const pre = await waitFor(() => {
      const el = container.querySelector('[data-attach="detail"]');
      if (!el) throw new Error("no verbatim block yet");
      return el;
    });
    expect(pre.textContent).toContain(PUBKEY);
    expect(pre.textContent).toContain("say `done` when added");
    expect(pre.textContent).toBe(REFUSAL);   // verbatim — not a rewrite that keeps the gist
  });
});

describe("the one-off token", () => {
  it("is a password field, is sent with the call, and is gone from the DOM afterwards", async () => {
    vi.mocked(api.attachSharedWorkspace).mockResolvedValue({
      workspace_id: "acme-kg", active: "acme-kg", repo: "git@github.com:acme/kg.git", ref: "main",
      attached: true, cloned: true, parked: null, nested: false, state: "cloned",
    });
    const { container } = open();
    await screen.findByRole("option", { name: "acme-kg" });
    fireEvent.click(screen.getByText("Use a saved token instead"));

    const field = container.querySelector('[data-attach="token"]') as HTMLInputElement;
    expect(field.getAttribute("type")).toBe("password");
    fireEvent.change(field, { target: { value: "ghp_secret" } });

    await submit("git@github.com:acme/kg.git", "acme-kg");
    await screen.findByText("Cloned git@github.com:acme/kg.git into acme-kg");

    expect(vi.mocked(api.attachSharedWorkspace).mock.calls[0][1].token).toBe("ghp_secret");
    expect(container.querySelector('[data-attach="token"]')).toBeNull();
    expect(container.innerHTML).not.toContain("ghp_secret");
  });
});

describe("the deploy key is the primary credential", () => {
  it("shows the public half, the add_as line and the server's own add_at link — and invents no URL", async () => {
    vi.mocked(api.ensureDeployKey).mockResolvedValue({
      slug: "acme-kg", public_key: PUBKEY, fingerprint: "SHA256:abc",
      add_at: "https://github.com/acme/kg/settings/keys",
      add_as: "a deploy key with WRITE access", then: "say `done` when added",
    });
    const { container } = open();
    await screen.findByRole("option", { name: "acme-kg" });
    fireEvent.click(screen.getByText("Use this deploy key"));

    const block = await waitFor(() => {
      const el = container.querySelector('[data-attach="pubkey"]');
      if (!el) throw new Error("no key yet");
      return el;
    });
    expect(block.textContent).toBe(PUBKEY);
    expect(container.querySelector('[data-attach="addat"]')?.getAttribute("href"))
      .toBe("https://github.com/acme/kg/settings/keys");
    expect(screen.getByText(/Add as a deploy key with WRITE access/)).toBeTruthy();
    expect(screen.getByText(/say `done` when added/)).toBeTruthy();
  });

  it("no add_at from the server means no link at all", async () => {
    vi.mocked(api.ensureDeployKey).mockResolvedValue({
      slug: "seed", public_key: PUBKEY, fingerprint: "SHA256:abc", add_at: null,
      add_as: "a deploy key with WRITE access", then: "say `done` when added",
    });
    const { container } = open();
    fireEvent.click(screen.getByText("Use this deploy key"));
    await waitFor(() => {
      if (!container.querySelector('[data-attach="pubkey"]')) throw new Error("no key yet");
    });
    expect(container.querySelector('[data-attach="addat"]')).toBeNull();
  });
});
