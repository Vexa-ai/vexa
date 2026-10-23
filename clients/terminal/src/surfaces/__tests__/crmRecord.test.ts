import { describe, expect, it } from "vitest";
import { display, mentionHref, recordTitle } from "../crmRecord";

describe("the CRM record page", () => {
  it("opens a backlink at the workspace page's canonical route", () => {
    expect(mentionHref({ workspace: "sales", page: "accounts/globex.md" })).toBe("/w/sales/accounts/globex.md");
    expect(mentionHref({ workspace: "sales", page: "../x.md" })).toBe("/w/sales");
  });
  it("titles a record by its name, a person by their names", () => {
    expect(recordTitle({ id: "1", object_type: "Company", revision: 1, fields: { Name: "Globex" }, narrative: null })).toBe("Globex");
    expect(recordTitle({ id: "1", object_type: "Person", revision: 1, fields: { FirstName: "Jane", LastName: "Roe" }, narrative: null })).toBe("Jane Roe");
  });
  it("shows an empty value as a dash", () => {
    expect(display("")).toBe("—"); expect(display(true)).toBe("Yes"); expect(display(5)).toBe("5");
  });
});
