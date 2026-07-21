/**
 * Pure label/title builder for <VersionChip /> — kept free of React and of the
 * build-time release-version.generated.json import so its two-identity honesty
 * (hosted #72) is unit testable without a DOM or a generated file.
 *
 * When `platformVersion` is provided (hosted deploys where a UI build fronts a
 * newer platform release), the chip shows the platform version PROMINENT with
 * the UI build as provenance ("v0.12.16 · UI 0.10.6.3"). When it is absent
 * (OSS self-host where UI == release), it falls back to the UI build alone,
 * unchanged from prior behavior.
 */

export type Variant = "full" | "compact" | "minimal";

export function withVPrefix(v: string): string {
  return v.startsWith("v") ? v : `v${v}`;
}

export function versionChipText({
  uiVersion,
  releaseDate,
  platformVersion = null,
  variant = "minimal",
}: {
  uiVersion: string;
  releaseDate: string;
  platformVersion?: string | null;
  variant?: Variant;
}): { label: string; title: string } {
  const platform = platformVersion ? withVPrefix(platformVersion) : null;

  let label: string;
  if (platform) {
    // Platform release prominent; UI build carried as provenance.
    switch (variant) {
      case "full":
        label = `Running ${platform} · UI ${uiVersion} · updated ${releaseDate}`;
        break;
      case "compact":
        label = `${platform} · UI ${uiVersion} · ${releaseDate}`;
        break;
      case "minimal":
      default:
        label = `${platform} · UI ${uiVersion}`;
    }
  } else {
    switch (variant) {
      case "full":
        label = `Running ${uiVersion} · updated ${releaseDate}`;
        break;
      case "compact":
        label = `${uiVersion} · ${releaseDate}`;
        break;
      case "minimal":
      default:
        label = uiVersion;
    }
  }

  const title = platform
    ? `Vexa platform ${platform} · dashboard UI build ${uiVersion} (released ${releaseDate}) · click for UI release notes`
    : `Vexa ${uiVersion} · released ${releaseDate} · click for release notes`;

  return { label, title };
}
