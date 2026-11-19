import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MdxDoc } from "../MdxDoc";
import { EntityProperties, entityProperties } from "../EntityProperties";
const source = `---
type: company
title: Example
tags:
  - prospect
  - customer
description: >-
  A multiline
  description.
resource: https://example.test
timestamp: 2026-10-08T13:00:00Z
verified: false
count: 0
---
# Example`;
describe("OKF entity properties", () => {
 it("parses YAML lists and multiline values without losing false/zero", () => {
  expect(entityProperties(source)).toMatchObject({ tags: ["prospect", "customer"], description: "A multiline description.", verified: false, count: 0 });
 });
 it("keeps metadata visible in the actual document renderer", () => {
  expect(renderToStaticMarkup(<MdxDoc>{source}</MdxDoc>)).toContain("data-entity-properties");
 });
 it("renders properties and safe resource links", () => {
  const html = renderToStaticMarkup(<EntityProperties source={source} />);
  expect(html).toContain('aria-label="Entity properties"');
  expect(html).toContain('href="https://example.test"');
  expect(html).toContain("prospect"); expect(html).toContain('dateTime="2026-10-08T13:00:00.000Z"');
 });
 it("does not execute links or markup from metadata", () => {
  const html = renderToStaticMarkup(<EntityProperties source={'---\ntype: person\nresource: "javascript:alert(1)"\ntitle: "<script>bad</script>"\n---\n'} />);
  expect(html).not.toContain("href="); expect(html).not.toContain("<script>");
 });
 it("leaves non-entity and malformed documents alone", () => {
  expect(entityProperties("# Normal page")).toBeNull();
  expect(entityProperties("---\nkind: policies\n---\n")).toBeNull();
  expect(entityProperties("---\ntype: [\n---\n")).toBeNull();
 });
});
