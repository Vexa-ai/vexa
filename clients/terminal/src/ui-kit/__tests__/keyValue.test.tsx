/** KeyValue, Truncate and DateText: metadata never breaks mid-word (guidelines §3.4, §4.16).
 *
 *  The defect: `EntityProperties` set `overflowWrap: "anywhere"` on both columns, so in a 240px
 *  panel a domain broke as "car eers" and a date as "2026-10- 09". jsdom cannot lay text out, so
 *  these tests hold the RULES that make that impossible: no `anywhere` on the table, dates and long
 *  tokens rendered as single non-wrapping units, the stacked form below 360px of pane width. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { DateText, KeyValue, Truncate, formatDate, humanizeKey, kvValue, splitForMiddle } from "..";
import { EntityProperties } from "../EntityProperties";

afterEach(cleanup);
const CSS = readFileSync(join(__dirname, "..", "primitives", "primitives.css"), "utf8");

describe("no mid-word breaks", () => {
  it("the table never uses overflow-wrap:anywhere or word-break:break-all", () => {
    const kv = CSS.slice(CSS.indexOf("KeyValue"), CSS.indexOf("Fold:"));
    expect(kv).not.toMatch(/overflow-wrap:\s*anywhere|word-break:\s*break-all/);
    expect(kv).toMatch(/overflow-wrap:\s*break-word/);
    const src = readFileSync(join(__dirname, "..", "EntityProperties.tsx"), "utf8");
    expect(src).not.toMatch(/overflowWrap:\s*"anywhere"/);
  });

  it("stacks below 360px of PANE width (a container query, not the viewport)", () => {
    expect(CSS).toMatch(/\.vx-kv\s*\{[^}]*container-type:\s*inline-size/);
    expect(CSS).toMatch(/@container \(max-width: 359px\)\s*\{\s*\.vx-kv-dl\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\)/);
  });

  it("acceptance row: an email renders as ONE truncating unit with the full value on hover", () => {
    const html = renderToStaticMarkup(<>{kvValue("name@example-domain.com")}</>);
    expect(html).toContain('class="vx-trunc"');
    expect(html).toContain('title="name@example-domain.com"');
    expect(html).toContain('href="mailto:name@example-domain.com"');
  });

  it("a date is a nowrap <time>, never split at its hyphen", () => {
    const html = renderToStaticMarkup(<>{kvValue("2026-10-09")}</>);
    expect(html).toMatch(/<time class="vx-nowrap vx-tabular" dateTime="2026-10-09"/);
    expect(CSS).toMatch(/\.vx-nowrap\s*\{\s*white-space:\s*nowrap/);
  });

  it("a long path or id truncates in the MIDDLE, keeping its end", () => {
    expect(splitForMiddle("acme/meetings/2026-10-08.md")).toEqual(["acme/meetings", "/2026-10-08.md"]);
    const html = renderToStaticMarkup(<>{kvValue("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")}</>);
    expect(html).toContain('data-mode="middle"');
  });

  it("a URL is a safe link shown without its scheme", () => {
    const html = renderToStaticMarkup(<>{kvValue("https://careers.example.com/jobs/")}</>);
    expect(html).toContain('href="https://careers.example.com/jobs/"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain(">careers.example.com/jobs<");
  });

  it("an unsafe scheme is text, never a link", () => {
    expect(renderToStaticMarkup(<Truncate text="javascript:alert(1)" href="javascript:alert(1)" />)).not.toContain("href=");
  });
});

describe("values by type", () => {
  it("booleans read Yes/No; objects nest as a table, never JSON", () => {
    expect(kvValue(true)).toBe("Yes");
    expect(kvValue(false)).toBe("No");
    const html = renderToStaticMarkup(<>{kvValue({ city: "Example City", zip: "00000" })}</>);
    expect(html).toContain("vx-kv");
    expect(html).not.toContain("{");
  });

  it("labels are humanised from keys", () => {
    expect(humanizeKey("first_seen_at")).toBe("First seen at");
    expect(humanizeKey("lastContact")).toBe("Last contact");
  });

  it("over 8 rows shows 6 and a 'Show all' control", () => {
    const items = Array.from({ length: 10 }, (_, i) => ({ key: `k${i}`, value: `v${i}` }));
    render(<KeyValue items={items} />);
    expect(screen.getAllByRole("term")).toHaveLength(6);
    fireEvent.click(screen.getByRole("button", { name: "Show all 10" }));
    expect(screen.getAllByRole("term")).toHaveLength(10);
  });

  it("the page property table renders through KeyValue", () => {
    const html = renderToStaticMarkup(<EntityProperties source={"---\ntype: company\nsite: https://example.test\nfounded: 2026-10-09\n---\n"} />);
    expect(html).toContain("vx-kv");
    expect(html).toContain('dateTime="2026-10-09"');
  });
});

describe("the one date formatter", () => {
  const now = new Date("2026-10-10T15:00:00Z");
  const o = { now, locale: "en-US", timeZone: "UTC" };
  it("relative within a week, absolute beyond, year only when it differs", () => {
    expect(formatDate("2026-10-10T09:05:00Z", o)).toBe("9:05 AM");
    expect(formatDate("2026-10-09T18:29:00Z", o)).toBe("Yesterday 6:29 PM");
    expect(formatDate("2026-10-06T10:00:00Z", o)).toBe("Tue");
    expect(formatDate("2026-09-28T10:00:00Z", o)).toBe("Sep 28");
    expect(formatDate("2025-10-08T10:00:00Z", o)).toBe("Oct 8, 2025");
  });
  it("a date-only value is a calendar day in every time zone", () => {
    expect(formatDate("2026-10-09", { ...o, timeZone: "Pacific/Kiritimati" })).toBe("Oct 9");
    expect(formatDate("2026-10-09", { ...o, timeZone: "Pacific/Pago_Pago" })).toBe("Oct 9");
  });
  it("DateText carries full precision in its tooltip", () => {
    const html = renderToStaticMarkup(<DateText value="2026-10-08T13:00:00Z" opts={o} />);
    expect(html).toMatch(/title="[^"]*2026[^"]*"/);
  });
});
