/**
 * Pure label/title builder for <VersionChip /> — kept free of React and of the
 * build-time release-version.generated.json import so the honesty rules below
 * are unit testable without a DOM or a generated file.
 *
 * The chip carries TWO identities and never lets one impersonate the other:
 *
 *   - the BACKEND version, read live from the deployment's own gateway
 *     (`/api/version` → `GET {VEXA_API_URL}/version`), and
 *   - this UI's own build stamp, always labelled as such ("UI build").
 *
 * They are shown together whenever they differ, which on a hosted deploy is
 * always: a 0.10-lineage dashboard build fronts a 0.12 platform. Collapsing
 * them into one number is what produced the defect this file exists to
 * prevent — a badge reading "v0.12.18" while the cluster ran v0.12.22-rc.3,
 * because the 0.12.18 was a constant the image had been built with.
 *
 * When the backend cannot be reached the chip says "unknown". It never falls
 * back to a configured or compiled-in backend version.
 */

export type Variant = "full" | "compact" | "minimal";

/** How much we know about the backend right now. "unknown" is a real answer. */
export type BackendStatus = "loading" | "ok" | "unknown";

export function withVPrefix(v: string): string {
  return v.startsWith("v") ? v : `v${v}`;
}

export function versionChipText({
  uiVersion,
  releaseDate,
  backendVersion = null,
  backendStatus = "unknown",
  variant = "minimal",
}: {
  uiVersion: string;
  releaseDate: string;
  backendVersion?: string | null;
  backendStatus?: BackendStatus;
  variant?: Variant;
}): { label: string; title: string } {
  const backend =
    backendStatus === "ok" && backendVersion ? withVPrefix(backendVersion) : null;
  const ui = withVPrefix(uiVersion);

  // The backend half of the label — a version, an admission, or a wait.
  const backendLabel =
    backend !== null
      ? backend
      : backendStatus === "loading"
        ? "checking…"
        : "version unknown";

  // When the two agree there is one product version and no reason to spend a
  // reader's attention saying it twice.
  const sameVersion = backend !== null && backend === ui;

  let label: string;
  if (sameVersion) {
    switch (variant) {
      case "full":
        label = `Running ${backend} · updated ${releaseDate}`;
        break;
      case "compact":
        label = `${backend} · ${releaseDate}`;
        break;
      default:
        label = backend;
    }
  } else {
    switch (variant) {
      case "full":
        label = `Running ${backendLabel} · UI build ${ui} · updated ${releaseDate}`;
        break;
      case "compact":
        label = `${backendLabel} · UI build ${ui} · ${releaseDate}`;
        break;
      default:
        label = `${backendLabel} · UI ${ui}`;
    }
  }

  let title: string;
  if (sameVersion) {
    title = `Vexa ${backend} · released ${releaseDate} · click for release notes`;
  } else if (backend !== null) {
    title = `Vexa ${backend} (live from this deployment) · dashboard UI build ${ui}, released ${releaseDate} · click for release notes`;
  } else if (backendStatus === "loading") {
    title = `Asking this deployment which version it runs · dashboard UI build ${ui}, released ${releaseDate}`;
  } else {
    title = `This deployment did not report its version — showing the dashboard UI build ${ui} only, which does NOT tell you the backend release · released ${releaseDate}`;
  }

  return { label, title };
}
