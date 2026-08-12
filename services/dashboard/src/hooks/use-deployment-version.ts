"use client";

import { useEffect, useState } from "react";

import { withBasePath } from "@/lib/base-path";
import type { BackendStatus } from "@/lib/version-chip-label";

/**
 * Ask THIS deployment what it is running, at page load.
 *
 * The dashboard image cannot know the backend release it will be deployed
 * against — a 0.10-lineage UI build fronts whatever 0.12.x the cluster
 * currently serves, and that moves without the image changing. So the answer
 * is fetched, not compiled: `/api/version` proxies the gateway's `/version`
 * server-side (no CORS, internal service URL, no key).
 *
 * On any failure the status is "unknown" and there is no version. Nothing here
 * falls back to a remembered value — a stale version reads as a fresh one.
 */

export type DeploymentVersion = {
  backendStatus: BackendStatus;
  backendVersion: string | null;
};

// One fetch per page load, shared by every chip on the page.
let cached: DeploymentVersion | null = null;
let inFlight: Promise<DeploymentVersion> | null = null;

async function fetchDeploymentVersion(): Promise<DeploymentVersion> {
  try {
    const r = await fetch(withBasePath("/api/version"), { cache: "no-store" });
    if (!r.ok) return { backendStatus: "unknown", backendVersion: null };
    const body = await r.json();
    const version: unknown = body?.backend?.version;
    if (body?.backendStatus === "ok" && typeof version === "string" && version.length > 0) {
      return { backendStatus: "ok", backendVersion: version };
    }
  } catch {
    // fall through — an unreachable backend is reported as unknown, not guessed
  }
  return { backendStatus: "unknown", backendVersion: null };
}

export function useDeploymentVersion(): DeploymentVersion {
  const [state, setState] = useState<DeploymentVersion>(
    () => cached ?? { backendStatus: "loading", backendVersion: null }
  );

  useEffect(() => {
    if (cached) return;
    if (!inFlight) inFlight = fetchDeploymentVersion();
    let alive = true;
    inFlight.then((v) => {
      cached = v;
      if (alive) setState(v);
    });
    return () => {
      alive = false;
    };
  }, []);

  return state;
}
