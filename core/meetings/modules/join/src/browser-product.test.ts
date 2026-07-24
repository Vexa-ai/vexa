/**
 * #938 browser-product contract — the decision is explicit before any browser
 * is launched or a live meeting is contacted.
 *
 * Run:
 *   pnpm --filter @vexa/join exec tsx src/browser-product.test.ts
 */

import assert from "assert/strict";
import { readFileSync } from "fs";
import { join } from "path";

type Contract = {
  schema: string;
  unsignedZoomStockBrowser: {
    driver: { package: string; version: string };
    browser: {
      engine: string;
      version: string;
      playwrightRevision: string;
      artifactPath: string;
    };
    profilePolicy: string;
    stockEvidenceEligible: boolean;
  };
  selectionMatrix: Array<{
    case: string;
    input: Record<string, unknown>;
    expected: Record<string, unknown>;
  }>;
  artifactCustody: {
    copyOnly: string;
    requiredNativeEvidence: string[];
    requiredNotices: string[];
    requiredNestedComponents: string[];
    forbiddenArtifacts: Array<{
      product: string;
      playwrightRevision: string;
      path: string;
    }>;
  };
};

type OfficialTestPage = {
  schema: string;
  url: string;
  purpose: string;
  participantTraffic: string;
  profilePolicy: string;
  doesNotProve: string[];
};

function readFixture<T>(...segments: string[]): T {
  return JSON.parse(readFileSync(join(__dirname, ...segments), "utf8")) as T;
}

async function main(): Promise<void> {
  const contract = readFixture<Contract>(
    "__fixtures__",
    "browser-product.contract.v1.json",
  );
  const officialPage = readFixture<OfficialTestPage>(
    "zoom",
    "__fixtures__",
    "official-test-page.v1.json",
  );

  assert.equal(contract.schema, "zoom-browser-product-contract.v1");
  assert.deepEqual(contract.unsignedZoomStockBrowser, {
    driver: { package: "playwright", version: "1.61.1" },
    browser: {
      engine: "firefox",
      version: "151.0",
      playwrightRevision: "1532",
      artifactPath: "/ms-playwright/firefox-1532",
    },
    profilePolicy: "fresh_ephemeral",
    stockEvidenceEligible: true,
  });
  assert.deepEqual(contract.artifactCustody.requiredNativeEvidence, [
    "bot_linux_amd64",
    "lite_linux_amd64",
    "lite_linux_arm64",
  ]);
  assert(contract.artifactCustody.requiredNotices.includes("MPL-2.0"));
  assert(contract.artifactCustody.requiredNestedComponents.includes("liblgpllibs.so"));
  assert.deepEqual(
    contract.artifactCustody.forbiddenArtifacts.map(({ playwrightRevision, path }) => ({
      playwrightRevision,
      path,
    })),
    [
      {
        playwrightRevision: "1228",
        path: "/ms-playwright/chromium-1228",
      },
      {
        playwrightRevision: "1228",
        path: "/ms-playwright/chromium_headless_shell-1228",
      },
    ],
  );

  assert.equal(officialPage.schema, "zoom-official-test-page.v1");
  assert.equal(officialPage.url, "https://zoom.us/test");
  assert.equal(officialPage.purpose, "native_stock_image_negative_control");
  assert.equal(officialPage.participantTraffic, "forbidden");
  assert.equal(officialPage.profilePolicy, "fresh_ephemeral");
  assert(officialPage.doesNotProve.includes("protected_room_admission"));

  console.log(
    "FIXTURE_GREEN: selection, Firefox custody, no-CfT, and official-test-page contracts are coherent",
  );

  let browserProduct: {
    selectBrowserProduct: (input: Record<string, unknown>) => Record<string, unknown>;
  };
  try {
    const modulePath = "./browser-product";
    browserProduct = await import(modulePath);
  } catch (error) {
    throw new Error(
      "BROWSER_PRODUCT_RED: @vexa/join has no native browser/profile selection seam",
      { cause: error },
    );
  }

  for (const row of contract.selectionMatrix) {
    assert.deepEqual(
      browserProduct.selectBrowserProduct(row.input),
      row.expected,
      row.case,
    );
  }
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
