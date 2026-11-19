/** The socket upgrade (server.mjs) and the REST proxy (proxyAuth.ts, Next's `cookies()`) must read the
 *  same `vexa-token`. A phone that once signed in on the parent domain holds two: the parent-domain one
 *  (older, sent first) and this host's (newer, sent last). Next keeps the last; the socket must too, or
 *  REST works while every socket (the meetings multiplex and a voice note's audio) is refused. */
import { describe, expect, it } from "vitest";
import { RequestCookies } from "next/dist/compiled/@edge-runtime/cookies";
import { readCookie } from "../cookieHeader.mjs";

const nextReads = (header: string, name: string) =>
  new RequestCookies(new Headers({ cookie: header })).get(name)?.value;

describe("readCookie", () => {
  it("reads a single cookie", () => {
    expect(readCookie("a=1; vexa-token=tok; b=2", "vexa-token")).toBe("tok");
    expect(readCookie("a=1", "vexa-token")).toBeUndefined();
    expect(readCookie(undefined, "vexa-token")).toBeUndefined();
  });

  it("decodes the value", () => {
    expect(readCookie("vexa-token=a%3Db", "vexa-token")).toBe("a=b");
  });

  it("picks the same value Next's cookies() picks when the name repeats", () => {
    const header = "vexa-token=parent-domain-stale; vexa-user-info=x; vexa-token=this-host-fresh";
    expect(readCookie(header, "vexa-token")).toBe(nextReads(header, "vexa-token"));
    expect(readCookie(header, "vexa-token")).toBe("this-host-fresh");
  });
});
