"use client";

/**
 * <VersionChip /> — what this deployment is running, asked of the deployment.
 *
 * The backend version comes from `/api/version` at page load (which proxies
 * the gateway's own `/version`); the UI build version is this image's stamp
 * and is always labelled "UI". The chip shows both whenever they differ, and
 * says "version unknown" — never a compiled-in or configured number — when the
 * backend does not answer.
 *
 * It used to take a `platformVersion` prop fed from a Helm value. That value
 * was expected to be hand-maintained "in lockstep with the platform release
 * staging actually serves"; it drifted to 0.12.18 against a 0.12.22-rc.3
 * cluster and nothing failed, because a constant cannot notice it is wrong.
 *
 * Mirror of services/webapp's component, intentionally kept simple so it
 * can stay in sync without sharing a package.
 */

import { useDeploymentVersion } from "@/hooks/use-deployment-version";
import { RELEASE, releaseUrl } from "@/lib/release-version";
import { versionChipText } from "@/lib/version-chip-label";

type Variant = "full" | "compact" | "minimal";
type Look = "pill" | "text";

export function VersionChip({
  variant = "minimal",
  look = "pill",
  className = "",
}: {
  variant?: Variant;
  look?: Look;
  className?: string;
}) {
  const { backendStatus, backendVersion } = useDeploymentVersion();

  // Release notes for what is RUNNING when we know it; for the UI build only
  // when we do not, since that is then the only version we can honestly name.
  const url = releaseUrl(backendVersion || RELEASE.version);
  const { label, title } = versionChipText({
    uiVersion: RELEASE.version,
    releaseDate: RELEASE.releaseDate,
    backendVersion,
    backendStatus,
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
