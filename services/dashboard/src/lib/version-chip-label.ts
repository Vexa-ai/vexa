/**
 * Pure label/title builder for <VersionChip /> — free of React and of the build-time
 * release-version.generated.json import, so the honesty rules are unit testable.
 *
 * The headline is the RELEASE this deployment is: what helm, the ownership lock and the GitHub
 * release all name. The dashboard's own UI build is a COMPONENT identity and lives in the
 * tooltip — a 0.10-lineage UI build fronting a 0.12 platform is normal, and putting "0.10.6.3"
 * next to the release in a headline reports a true fact about the wrong layer.
 *
 * When nothing can name the release the chip says "version unknown". It never falls back to the
 * UI build for it — that is how this badge advertised 0.12.18 against a 0.12.22-rc.3 cluster.
 */

export type Variant = "full" | "compact" | "minimal";

/** How much we know about the release right now. "unknown" is a real answer. */
export type BackendStatus = "loading" | "ok" | "unknown";

/** Where the headline came from, weakest last. */
export type VersionSource = "gateway" | "release-pin" | "none";

export function withVPrefix(v: string): string {
  return v.startsWith("v") ? v : `v${v}`;
}

export function versionChipText({
  uiVersion,
  releaseDate,
  backendVersion = null,
  backendStatus = "unknown",
  versionSource = "none",
  variant = "minimal",
}: {
  uiVersion: string;
  releaseDate: string;
  backendVersion?: string | null;
  backendStatus?: BackendStatus;
  versionSource?: VersionSource;
  variant?: Variant;
}): { label: string; title: string } {
  const release =
    backendStatus === "ok" && backendVersion ? withVPrefix(backendVersion) : null;
  const ui = withVPrefix(uiVersion);

  let label: string;
  if (!release) {
    label = backendStatus === "loading" ? "checking…" : "version unknown";
  } else {
    switch (variant) {
      case "full":
        label = `Running ${release}`;
        break;
      case "compact":
        label = `${release} · ${releaseDate}`;
        break;
      default:
        label = release;
    }
  }

  const provenance: Record<VersionSource, string> = {
    gateway: "reported live by this deployment",
    "release-pin": "the release this deployment was deployed as",
    none: "",
  };

  const detail: string[] = [];
  if (release) detail.push(`Vexa ${release} (${provenance[versionSource]})`);
  else if (backendStatus === "loading")
    detail.push("Asking this deployment which release it runs");
  else
    detail.push(
      "This deployment did not report a version and none was configured — the build below describes the UI only, not the release"
    );
  detail.push(`dashboard UI build ${ui}, released ${releaseDate}`);
  if (release) detail.push("click for release notes");

  return { label, title: detail.join(" · ") };
}
