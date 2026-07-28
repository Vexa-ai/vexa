/** Home — the workbench. */
import { App } from "./App";
import { isAlloySttTelemetryEnabled } from "./api/alloyTelemetryMode";

// ALLOY: Keep the server-side flag evaluation dynamic for every request.
export const dynamic = "force-dynamic";

export default function Page() {
  // ALLOY: Evaluate the opt-in for every server render; do not freeze env at import time.
  return (
    <App
      alloySttTelemetryEnabled={isAlloySttTelemetryEnabled()}
    />
  );
}
