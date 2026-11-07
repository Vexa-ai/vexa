/** The card-face case table (blocks.v1 golden FaceCases, held identical by the parity fact
 *  `card-face-cases`): what crm-api resolves for a viewer, the client's sanitiser keeps unchanged. */
import { describe, expect, it } from "vitest";
import cases from "../desktop/face-cases.json";
import { sanitizeFace } from "../desktop/faceBlocks";

describe("card faces: the shared case table", () => {
  for (const c of cases as { name: string; expect: unknown[] }[]) {
    it(`the client keeps the server's resolution: ${c.name}`, () => {
      expect(sanitizeFace(c.expect)).toEqual(c.expect);
    });
  }
});
