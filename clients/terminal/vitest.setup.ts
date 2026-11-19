// Feature switches (src/platform/features.ts): existing tests render every feature ON, without a
// fetch to /api/features. A test that exercises a switch sets its own override (or `null`).
import { setFeatureOverride } from "./src/platform/features";

setFeatureOverride("all");
