import { beforeEach, describe, expect, it } from "vitest";
import { loadProjects, projectMountStack, saveProjects } from "../projects";

const PROJECTS_KEY = "vexa.minutes.projects";
const SEEDED_KEY = "vexa.minutes.orgSeeded";

describe("minutes project stacks", () => {
  beforeEach(() => localStorage.clear());

  it("puts _global first and _system last for every rendered stack", () => {
    expect(projectMountStack(["personal"])).toEqual(["_global", "personal", "_system"]);
    expect(projectMountStack(["_system", "team", "_global", "team"])).toEqual(["_global", "team", "_system"]);
  });

  it("migrates and persists a legacy Personal project without _global", () => {
    localStorage.setItem(SEEDED_KEY, "1");
    localStorage.setItem(PROJECTS_KEY, JSON.stringify([
      { id: "personal", name: "Personal", set: ["personal"], builtin: "personal", chats: [] },
    ]));

    expect(loadProjects()[0].set).toEqual(["_global", "personal"]);
    expect(JSON.parse(localStorage.getItem(PROJECTS_KEY) || "[]")[0].set).toEqual(["_global", "personal"]);
  });

  it("normalizes new project writes so _global cannot be omitted", () => {
    saveProjects([{ id: "x", name: "X", set: ["team"], chats: [] }]);
    expect(JSON.parse(localStorage.getItem(PROJECTS_KEY) || "[]")[0].set).toEqual(["_global", "team"]);
  });
});
