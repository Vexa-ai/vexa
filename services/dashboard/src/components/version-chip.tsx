/**
 * <VersionChip /> — small, discoverable label for the Vexa product version
 * this dashboard fronts. Hosted deployments provide that identity at runtime
 * through PLATFORM_VERSION → /api/config → `platformVersion`.
 *
 * When `platformVersion` is absent, the chip falls back to the build-time
 * release identity used by OSS self-hosted deployments.
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
  const url = releaseUrl(platformVersion || RELEASE.version);
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
