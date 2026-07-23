/**
 * Pure label/title builder for <VersionChip /> — kept free of React and of the
 * build-time release-version.generated.json import so the hosted product
 * release identity is unit testable without a DOM or a generated file.
 *
 * When `platformVersion` is provided (hosted deploys where a UI build fronts a
 * platform release), the chip presents that product version alone. When it is
 * absent (OSS self-host where UI == release), it falls back to the build-time
 * release identity.
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
    switch (variant) {
      case "full":
        label = `Running ${platform}`;
        break;
      case "compact":
      case "minimal":
      default:
        label = platform;
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
    ? `Vexa ${platform} · click for release notes`
    : `Vexa ${uiVersion} · released ${releaseDate} · click for release notes`;

  return { label, title };
}
