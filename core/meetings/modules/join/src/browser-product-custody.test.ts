/**
 * #938 P2 — the static browser artifact contract must fail closed when its
 * selected Firefox identity, copy-only path, CfT exclusions, or provenance
 * contradict each other.
 *
 * Run:
 *   pnpm --filter @vexa/join exec tsx src/browser-product-custody.test.ts
 */

import assert from "assert/strict";
import contractFixture from "./__fixtures__/browser-product.contract.v1.json";
import * as browserProduct from "./browser-product";

type MutableContract = typeof contractFixture;
type ContractValidator = (value: unknown) => MutableContract;

const validate = (
  browserProduct as typeof browserProduct & {
    validateBrowserProductContract?: ContractValidator;
  }
).validateBrowserProductContract;

assert.equal(
  typeof validate,
  "function",
  "CUSTODY_VALIDATOR_RED: browser-product has no fail-closed artifact contract validator",
);

const validateContract = validate as ContractValidator;

function mutated(change: (contract: MutableContract) => void): MutableContract {
  const copy = structuredClone(contractFixture);
  change(copy);
  return copy;
}

function rejects(label: string, change: (contract: MutableContract) => void): void {
  assert.throws(
    () => validateContract(mutated(change)),
    /BROWSER_PRODUCT_CONTRACT_INVALID:/,
    label,
  );
}

assert.doesNotThrow(
  () => validateContract(structuredClone(contractFixture)),
  "prepared Firefox contract is valid",
);

rejects("copyOnly must equal the selected Firefox artifact path", (contract) => {
  contract.artifactCustody.copyOnly = "/ms-playwright/chromium-1228";
});

const forbiddenFields = [
  ["product", "firefox"],
  ["version", "151.0"],
  ["playwrightRevision", "1532"],
  ["path", "/ms-playwright/firefox-1532"],
] as const;

for (const index of [0, 1] as const) {
  for (const [field, replacement] of forbiddenFields) {
    rejects(`forbidden artifact ${index} binds ${field}`, (contract) => {
      contract.artifactCustody.forbiddenArtifacts[index][field] = replacement;
    });
  }
}

rejects("arm64 source-image provenance binds the researched digest", (contract) => {
  contract.artifactCustody.researchArm64SourceImage =
    "mcr.microsoft.com/playwright@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
});

console.log(
  "CUSTODY_GREEN: copyOnly, full CfT exclusions, and source-image provenance fail closed",
);
