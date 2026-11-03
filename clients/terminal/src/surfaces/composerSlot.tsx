"use client";
/** THE COMPOSER, IN PLACE (founder, UI lab 2026-10-14: "the void in the middle"). A new chat's home
 *  draws the chat's own composer directly under its greeting instead of pinned far below: the chat
 *  offers its composer here, a surface that wants it claims it, and the chat then leaves its bottom
 *  bar empty. One composer, one draft, wherever it is drawn. */
import { createContext, useContext, useEffect, type ReactNode } from "react";

export type ComposerSlot = { composer: ReactNode; claim: (on: boolean) => void };
const Ctx = createContext<ComposerSlot | null>(null);
export const ComposerSlotProvider = Ctx.Provider;

/** The chat's composer, claimed for as long as the caller is mounted; null outside a chat. */
export function useComposerSlot(): ReactNode | null {
  const slot = useContext(Ctx);
  const claim = slot?.claim;
  useEffect(() => {
    if (!claim) return;
    claim(true);
    return () => claim(false);
  }, [claim]);
  return slot ? slot.composer : null;
}
