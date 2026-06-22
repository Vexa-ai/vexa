/**
 * build-dist.mjs — emit the brick's runtime dist/ via esbuild.
 *
 * The component imports its sibling brick @vexa/dash-contracts by relative `.ts` source path (the
 * dashboard_new convention), so `tsc` can only typecheck it (allowImportingTsExtensions ⇒ noEmit).
 * The shippable runtime is therefore produced by esbuild here: bundle src/index.ts → dist/index.js as
 * ESM, with react / react-dom marked external (the host app provides them, per peerDependencies). The
 * @vexa/dash-contracts imports are type-only and erase, so dist carries only the component + its inline
 * styles. A hand-written dist/index.d.ts re-exports the types from source for consumers' typechecks.
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { writeFile, mkdir } from "node:fs/promises";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

await build({
  entryPoints: [join(ROOT, "src", "index.ts")],
  bundle: true,
  format: "esm",
  target: "es2022",
  jsx: "automatic",
  outfile: join(ROOT, "dist", "index.js"),
  external: ["react", "react-dom", "react/jsx-runtime"],
  logLevel: "info",
});

// Hand-written declaration front door — the props/types live in source; consumers resolve them here.
await mkdir(join(ROOT, "dist"), { recursive: true });
await writeFile(
  join(ROOT, "dist", "index.d.ts"),
  `export { TranscriptViewer } from "./TranscriptViewer.js";\n` +
    `export type { TranscriptViewerProps } from "./TranscriptViewer.js";\n`,
);
await writeFile(
  join(ROOT, "dist", "TranscriptViewer.d.ts"),
  `import type { TranscriptSegment } from "@vexa/dash-contracts";\n` +
    `export interface TranscriptViewerProps {\n` +
    `  segments: TranscriptSegment[];\n` +
    `  isLive?: boolean;\n` +
    `  playbackTime?: number;\n` +
    `  onSegmentClick?: (startTimeSeconds: number, absoluteStartTime?: string) => void;\n` +
    `}\n` +
    `export declare function TranscriptViewer(props: TranscriptViewerProps): JSX.Element;\n`,
);

console.log("dist/ emitted: index.js + index.d.ts + TranscriptViewer.d.ts");
