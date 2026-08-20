/** `/meetings/<id>` — the addressable meeting route.
 *
 *  Renders the SAME workbench shell as `/`; the id in the path is read by the first-view resolver
 *  (Workbench.resolveFirstView), which opens that meeting's tab. Keeping one shell means a meeting URL
 *  is a real reference — reload it, share it, open it in a fresh session — with no second app to drift. */
import { App } from "../../App";

export default function MeetingPage() {
  return <App />;
}
