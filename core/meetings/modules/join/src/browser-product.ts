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

export interface BrowserProductContract {
  schema: "zoom-browser-product-contract.v1";
  unsignedZoomStockBrowser: {
    driver: { package: "playwright"; version: "1.61.1" };
    browser: {
      engine: "firefox";
      version: "151.0";
      playwrightRevision: "1532";
      artifactPath: "/ms-playwright/firefox-1532";
    };
    profilePolicy: "fresh_ephemeral";
    stockEvidenceEligible: true;
  };
  selectionMatrix: Array<{
    case: string;
    input: BrowserProductInput;
    expected: BrowserProductSelection;
  }>;
  artifactCustody: {
    copyOnly: "/ms-playwright/firefox-1532";
    researchArm64SourceImage: string;
    requiredNativeEvidence: string[];
    requiredNotices: string[];
    requiredNestedComponents: string[];
    forbiddenArtifacts: Array<{
      product: string;
      version: string;
      playwrightRevision: string;
      path: string;
    }>;
  };
}

const RESEARCH_ARM64_SOURCE_IMAGE =
  "mcr.microsoft.com/playwright@sha256:7b86926fff94374389e8e1f4fdc5c76d050d4a06a7886bb537bf412b20e2b71e";

const FORBIDDEN_CFT_ARTIFACTS = [
  {
    product: "chrome-for-testing",
    version: "149.0.7827.55",
    playwrightRevision: "1228",
    path: "/ms-playwright/chromium-1228",
  },
  {
    product: "chrome-headless-shell-for-testing",
    version: "149.0.7827.55",
    playwrightRevision: "1228",
    path: "/ms-playwright/chromium_headless_shell-1228",
  },
] as const;

function invalidContract(reason: string): never {
  throw new Error(`BROWSER_PRODUCT_CONTRACT_INVALID: ${reason}`);
}

export function validateBrowserProductContract(
  value: unknown,
): BrowserProductContract {
  const candidate = value as Partial<BrowserProductContract>;
  const selected = candidate.unsignedZoomStockBrowser;
  const custody = candidate.artifactCustody;

  if (candidate.schema !== "zoom-browser-product-contract.v1") {
    invalidContract("schema must be zoom-browser-product-contract.v1");
  }
  if (
    selected?.driver?.package !== "playwright" ||
    selected.driver.version !== "1.61.1" ||
    selected.browser?.engine !== "firefox" ||
    selected.browser.version !== "151.0" ||
    selected.browser.playwrightRevision !== "1532" ||
    selected.browser.artifactPath !== "/ms-playwright/firefox-1532" ||
    selected.profilePolicy !== "fresh_ephemeral" ||
    selected.stockEvidenceEligible !== true
  ) {
    invalidContract("unsigned Zoom stock identity must be Playwright 1.61.1 / Firefox 151 rev 1532");
  }
  if (custody?.copyOnly !== selected.browser.artifactPath) {
    invalidContract("artifactCustody.copyOnly must equal the selected Firefox artifact path");
  }
  if (custody.researchArm64SourceImage !== RESEARCH_ARM64_SOURCE_IMAGE) {
    invalidContract("research arm64 source-image provenance does not match the prepared digest");
  }
  if (
    JSON.stringify(custody.forbiddenArtifacts) !==
    JSON.stringify(FORBIDDEN_CFT_ARTIFACTS)
  ) {
    invalidContract("forbidden artifacts must bind both complete CfT 149/rev 1228 tuples");
  }

  return value as BrowserProductContract;
}

export const browserProductContract = validateBrowserProductContract(contract);

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
