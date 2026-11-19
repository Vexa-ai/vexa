/** WHO IS PRESENTING, IN PLAIN ENGLISH. app.dev 22 Oct: the founder shared his screen and the stage's
 *  header read «You is presenting · Screen». The viewer's own share reads "You're presenting"; anyone
 *  else's reads "<name> is presenting" — the header and the share's accessible name alike. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { CallStage } from "../callkit/Stage";
import type { CallPerson } from "../callkit/types";

afterEach(cleanup);

const people: CallPerson[] = [{ id: "me", name: "Dmitry", self: true }, { id: "v", name: "Valeriia" }];

describe("the shared screen's header", () => {
  it("your own share reads «You're presenting»", () => {
    render(<CallStage people={people} share={{ by: "me", byName: "You", title: "Screen", content: null }} />);
    expect(screen.getByRole("region", { name: "You're presenting" })).toBeTruthy();
    expect(document.querySelector(".vx-ck-share-head")!.textContent).toContain("You're presenting · Screen");
    expect(document.body.textContent).not.toContain("You is presenting");
  });

  it("someone else's share names them", () => {
    render(<CallStage people={people} share={{ by: "v", byName: "Valeriia", title: "Screen", content: null }} />);
    expect(screen.getByRole("region", { name: "Valeriia is presenting" })).toBeTruthy();
    expect(document.querySelector(".vx-ck-share-head")!.textContent).toContain("Valeriia is presenting · Screen");
  });
});
