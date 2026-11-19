/** The git provider port, terminal half: which configured provider a URL is on, its ssh form, how deep
 *  a path may go, and where `owner/repo` expands to. */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BUILTIN_GIT_PROVIDER, listGitProviders, maxPathSegments, providerForUrl, setKnownGitProviders,
  shorthandUrl, sshFormOf, type GitProvider, type GitProviderList,
} from "../gitProviders";

const GITLAB: GitProvider = {
  name: "corp", kind: "gitlab", label: "Corp GitLab", base_url: "https://git.example.com:8443/gitlab",
  host: "git.example.com:8443", nested_groups: true, can_create: true,
};
const LIST: GitProviderList = { default: "github", providers: [BUILTIN_GIT_PROVIDER, GITLAB] };

afterEach(() => { setKnownGitProviders(null); vi.unstubAllGlobals(); });

describe("providerForUrl", () => {
  it("matches the built-in provider", () => {
    expect(providerForUrl("https://github.com/acme/kg.git", LIST)?.name).toBe("github");
    expect(providerForUrl("git@github.com:acme/kg.git", LIST)?.name).toBe("github");
  });
  it("matches a GitLab on a port and under a base path", () => {
    expect(providerForUrl("https://git.example.com:8443/gitlab/it/ai/vexa.git", LIST)?.name).toBe("corp");
  });
  it("does not match the wrong port or outside the base path", () => {
    expect(providerForUrl("https://git.example.com/gitlab/it/vexa.git", LIST)).toBeNull();
    expect(providerForUrl("https://git.example.com:8443/other/it/vexa.git", LIST)).toBeNull();
    expect(providerForUrl("https://elsewhere.example.org/a/b", LIST)).toBeNull();
    expect(providerForUrl("", LIST)).toBeNull();
  });
  it("matches an ssh URL by bare host", () => {
    expect(providerForUrl("git@git.example.com:it/ai/vexa.git", LIST)?.name).toBe("corp");
  });
});

describe("sshFormOf", () => {
  it("rewrites https on a provider to git@host:path.git", () => {
    expect(sshFormOf("https://github.com/acme/kg.git", LIST)).toBe("git@github.com:acme/kg.git");
    expect(sshFormOf("https://git.example.com:8443/gitlab/it/ai/vexa", LIST)).toBe("git@git.example.com:it/ai/vexa.git");
  });
  it("is null off any provider, or for a non-https form", () => {
    expect(sshFormOf("https://elsewhere.example.org/a/b.git", LIST)).toBeNull();
    expect(sshFormOf("git@github.com:acme/kg.git", LIST)).toBeNull();
  });
});

describe("maxPathSegments + shorthandUrl", () => {
  it("2 on a GitHub-kind host, 22 on GitLab or an unknown host", () => {
    expect(maxPathSegments("github.com", LIST)).toBe(2);
    expect(maxPathSegments("git.example.com:8443", LIST)).toBe(22);
    expect(maxPathSegments("unknown.example.org", LIST)).toBe(22);
  });
  it("expands owner/repo on the default provider", () => {
    expect(shorthandUrl("acme", "kg", LIST)).toBe("https://github.com/acme/kg.git");
    expect(shorthandUrl("it", "vexa", { default: "corp", providers: LIST.providers })).toBe("https://git.example.com:8443/gitlab/it/vexa.git");
  });
  it("uses the known list when none is passed, built-in before it loads", () => {
    expect(shorthandUrl("acme", "kg")).toBe("https://github.com/acme/kg.git");
    setKnownGitProviders({ default: "corp", providers: LIST.providers });
    expect(shorthandUrl("acme", "kg")).toBe("https://git.example.com:8443/gitlab/acme/kg.git");
  });
});

describe("listGitProviders", () => {
  it("reads the route and keeps the built-in present", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ default: "corp", providers: [GITLAB] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const got = await listGitProviders();
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toContain("/api/workspace/git-providers");
    expect(got.default).toBe("corp");
    expect(got.providers.map((p) => p.name)).toEqual(["github", "corp"]);
  });
});
