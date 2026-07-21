/**
 * <VersionChip /> — small, discoverable label disclosing what version this
 * dashboard fronts. Two identities, both truthful:
 *   - the PLATFORM release it pairs with (runtime-configured via
 *     PLATFORM_VERSION → /api/config → `platformVersion` prop); and
 *   - its own UI build (build-time truth from release-version.generated.json).
 *
 * When `platformVersion` is provided (hosted deploys where the UI build fronts
 * a newer platform), the chip shows "v0.12.16 · UI 0.10.6.3" — platform
 * prominent, UI build as provenance. When it is absent (OSS self-host where
 * UI == release), the chip falls back to the UI build alone, unchanged.
 *
 * Mirror of services/webapp's component, intentionally kept simple so it
 * can stay in sync without sharing a package.
 */

import { RELEASE, releaseUrl } from "@/lib/release-version";
import { versionChipText } from "@/lib/version-chip-label";

type Variant = "full" | "compact" | "minimal";
type Look = "pill" | "text";

export function VersionChip({
  variant = "minimal",
  look = "pill",
  className = "",
  platformVersion = null,
}: {
  variant?: Variant;
  look?: Look;
  className?: string;
  /** Runtime platform release this build fronts. null → UI-build-only. */
  platformVersion?: string | null;
}) {
  const url = releaseUrl(RELEASE.version);
  const { label, title } = versionChipText({
    uiVersion: RELEASE.version,
    releaseDate: RELEASE.releaseDate,
    platformVersion,
    variant,
  });

  const baseClasses =
    look === "pill"
      ? "inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-border bg-background/60 text-[11px] text-muted-foreground hover:border-foreground/30 hover:text-foreground transition-colors"
      : "inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground transition-colors";

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      className={baseClasses + " " + className}
    >
      <span>{label}</span>
    </a>
  );
}
