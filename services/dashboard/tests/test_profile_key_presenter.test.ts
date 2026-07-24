import { describe, expect, it } from "vitest";
import {
  profileApiErrorMessage,
  tokenMetadataPreview,
} from "@/lib/profile-api";

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Profile API-key presenter", () => {
  it("renders typed route errors as actionable text", async () => {
    const response = jsonResponse(
      {
        error: {
          code: "VALIDATION_ERROR",
          message: "body.userId: Extra inputs are not permitted",
        },
      },
      422
    );

    await expect(profileApiErrorMessage(response)).resolves.toBe(
      "body.userId: Extra inputs are not permitted"
    );
  });

  it("renders a raw FastAPI detail array without object coercion or input disclosure", async () => {
    const response = jsonResponse(
      {
        detail: [
          {
            type: "extra_forbidden",
            loc: ["body", "userId"],
            msg: "Extra inputs are not permitted",
            input: "secret-value-must-not-render",
          },
        ],
      },
      422
    );

    const message = await profileApiErrorMessage(response);
    expect(message).toBe("body.userId: Extra inputs are not permitted");
    expect(message).not.toContain("[object Object]");
    expect(message).not.toContain("secret-value-must-not-render");
  });

  it("shows only a scope-derived placeholder for listed token metadata", () => {
    expect(tokenMetadataPreview(["tx", "bot"])).toBe("vxa_tx_••••");
    expect(tokenMetadataPreview([])).toBe("Secret shown only once");
  });
});
