import contract from "./__fixtures__/browser-product.contract.v1.json";

export type JoinPlatform = "google_meet" | "teams" | "zoom" | "jitsi";
export type BrowserEngine = "chromium" | "firefox" | "operator";
export type BrowserRuntime = "existing" | "unsigned_zoom_stock" | "operator_override";
export type BrowserProfilePolicy =
  | "existing"
  | "fresh_ephemeral"
  | "authenticated_persistent";

export interface BrowserProductInput {
  platform: JoinPlatform;
  authenticated?: boolean;
  executablePath?: string;
}

export interface BrowserProductSelection {
  engine: BrowserEngine;
  runtime: BrowserRuntime;
  profilePolicy: BrowserProfilePolicy;
  stockEvidenceEligible: boolean;
  executablePath?: string;
}

export const browserProductContract = contract;

export function selectBrowserProduct(
  input: BrowserProductInput,
): BrowserProductSelection {
  if (input.executablePath) {
    return {
      engine: "operator",
      runtime: "operator_override",
      profilePolicy: input.authenticated
        ? "authenticated_persistent"
        : "fresh_ephemeral",
      stockEvidenceEligible: false,
      executablePath: input.executablePath,
    };
  }

  if (input.platform === "zoom" && !input.authenticated) {
    return {
      engine: "firefox",
      runtime: "unsigned_zoom_stock",
      profilePolicy: "fresh_ephemeral",
      stockEvidenceEligible: true,
    };
  }

  return {
    engine: "chromium",
    runtime: "existing",
    profilePolicy: input.authenticated
      ? "authenticated_persistent"
      : "existing",
    stockEvidenceEligible: false,
  };
}
