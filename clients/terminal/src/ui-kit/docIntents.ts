/** docIntents — the one act a rendered document asks of the shell above it.
 *
 *  A doc's image placeholder offers **Find it** (`docImages`), which queues a same-target act on the
 *  reader's chat. The act's door is `postIntent` in `minutes/extend`, a shell module that sits above
 *  the ui-kit: reaching it from here, even by lazy `import()`, made ui-kit and minutes import each
 *  other (Vexa-ai/vexa#1783). So the ui-kit names the act and the composition root (`app/App.tsx`)
 *  supplies the door. Nothing registered means nothing is posted — a document renders the same.
 */

/** The intent a document posts: an `extend` job on the page the reader is on. */
export interface DocExtendIntent {
  kind: "extend";
  workspace?: string;
  path: string;
  instruction: string;
}

type Poster = (intent: DocExtendIntent) => unknown;
let poster: Poster | null = null;

/** Supply the door. Called once by the composition root; tests supply their own. */
export function provideDocIntentPoster(fn: Poster | null): void {
  poster = fn;
}

/** Post an intent through the supplied door; resolves once the door has taken it. */
export async function postDocIntent(intent: DocExtendIntent): Promise<void> {
  await poster?.(intent);
}
