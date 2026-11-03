/** THE ADMIN ACCOUNT'S HOME — founder ruling 2026-10-11: the admin account is for flows & mail,
 *  templates, friction and the company workspace. Its empty chat offers those four areas, each a
 *  named ask the server mints (`/?ask=<preset>`), and no everyday account is shown them.
 *
 *  The server halves are pinned elsewhere: the asks' mounts and the admin arrival in
 *  `core/agent/tests/test_admin_account_functions.py`, the mail and friction reads in
 *  `core/flows/tests/test_admin_account_mail.py`. */
import { describe, expect, it, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { ADMIN_AREAS, AdminHome, adminAreaHref } from "../AdminHome";

afterEach(cleanup);

const REPO = join(__dirname, "..", "..", "..", "..", "..");

describe("the admin account's home", () => {
  it("offers the four areas the ruling names, in its order", () => {
    expect(ADMIN_AREAS.map((a) => a.label)).toEqual(["Flows & mail", "Templates", "Friction", "Workspace"]);
  });

  it("opens each area as a named ask, never as composed text", () => {
    const { container } = render(<AdminHome />);
    const links = [...container.querySelectorAll("a[data-admin-area]")] as HTMLAnchorElement[];
    expect(links.map((l) => l.getAttribute("href"))).toEqual(
      ["/?ask=mail-admin", "/?ask=templates", "/?ask=friction-review", "/?ask=setup-global"]);
    for (const a of ADMIN_AREAS) expect(adminAreaHref(a)).toMatch(/^\/\?ask=[a-z0-9-]+$/);
  });

  it("every area's ask ships in the preset library", () => {
    for (const a of ADMIN_AREAS) {
      expect(existsSync(join(REPO, "behavior", "asks", `${a.ask}.md`)), a.ask).toBe(true);
    }
  });

  it("is shown to the admin account only — everybody else keeps the everyday chips", () => {
    const shell = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
    expect(shell).toMatch(/emptyExtra=\{isAdminAccount \? <AdminHome \/> : (homeRead|<DesktopHome)/);  // the desktop (D1) wraps the everyday home as its fallback
    // and the flag is identity's literal `admin_account === true`, never `is_admin`
    expect(shell).toMatch(/admin_account === true\)/);
  });
});
