/** shellProvided — what a rendered document needs from the shell above the ui-kit, supplied by the
 *  composition root (`app/App.tsx`), the way `docIntents` supplies its door.
 *
 *  A card fence (```card) renders the shell's inline card (S5, 7q) and a reference to a card opens it
 *  (S7). Both live in `minutes/`, which sits above the ui-kit: importing them from here, even lazily,
 *  made ui-kit and minutes import each other (gate:isolation, FOLDER CYCLE). Nothing supplied means a
 *  card fence renders as its source and a card reference opens nothing — a document still renders. */
import type { ComponentType, ReactNode } from "react";

type CardInlineComponent = ComponentType<{ source: string }>;
let cardInline: CardInlineComponent | null = null;
let cardOpener: ((card: string) => unknown) | null = null;

/** Supply the inline card component (once, from the composition root; tests supply their own). */
export function provideCardInline(c: CardInlineComponent | null): void { cardInline = c; }
/** The supplied inline card component, or null. */
export function suppliedCardInline(): CardInlineComponent | null { return cardInline; }

/** Supply the door that opens a card by id. */
export function provideCardOpener(fn: ((card: string) => unknown) | null): void { cardOpener = fn; }
/** Open a card through the supplied door (nothing supplied: nothing happens). */
export function openSuppliedCard(card: string): void { void cardOpener?.(card); }

/** A PERSON AS A LIVE REFERENCE (UI 7mm): the shell's person chip (`minutes/shell2/PersonChip`) — a
 *  click opens the profile, a hover shows who they are. Supplied, because surfaces sit below minutes.
 *  Nothing supplied: the name (or the face given) renders as it is. */
export type PersonRefProps = { person: { name: string; subject?: string | null; email?: string | null }; children?: ReactNode };
let personRef: ComponentType<PersonRefProps> | null = null;
export function providePersonRef(c: ComponentType<PersonRefProps> | null): void { personRef = c; }
export function suppliedPersonRef(): ComponentType<PersonRefProps> | null { return personRef; }
