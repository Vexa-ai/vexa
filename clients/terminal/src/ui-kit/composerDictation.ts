/** composerDictation — what the composer's text box shows while the mic streams.
 *
 *  The box reads `base + dictated`: `base` is what the person had (or edited) and the dictation
 *  engine supplies the rest. Two moments hand the shown text over and call the engine's `commit()`:
 *    - `sent()`: Enter sent the message — the box empties, listening goes on, and only speech after
 *      the send appears next (late answers for sent audio are dropped by the engine);
 *    - `edited(text)`: the person typed in the box — their text becomes the base, and dictation
 *      continues after it instead of overwriting it.
 */
export interface CommitTarget { commit(): void }

export interface ComposerDictation {
  /** Recording starts with the box holding `current`. */
  start(current: string): void;
  /** The engine (or streaming dictation) whose shown text this composer owns. */
  attach(target: CommitTarget | null): void;
  /** Engine update: final text so far + the live draft. */
  update: (finalText: string, draft: string) => void;
  /** The person changed the box to `text`. */
  edited(text: string): void;
  /** The box's text was sent. */
  sent(): void;
  /** Recording stopped with `finalText` (text since the last commit). */
  finish(finalText: string): void;
}

const joinText = (a: string, b: string) => (!a ? b : !b ? a : /\s$/.test(a) ? `${a}${b}` : `${a} ${b}`);

export function createComposerDictation(o: { setValue: (v: string) => void }): ComposerDictation {
  let base = "";
  let target: CommitTarget | null = null;
  return {
    start(current) { base = current.trim(); target = null; },
    attach(t) { target = t; },
    update: (finalText, draft) => o.setValue(joinText(base, joinText(finalText, draft))),
    edited(text) { if (!target) return; base = text; target.commit(); },
    sent() { if (!target) return; base = ""; target.commit(); },
    finish(finalText) { target = null; o.setValue(joinText(base, finalText)); },
  };
}
