"use client";
/** `/decide/<card>` — the nudge landing. The page is the route; the behaviour is `../DecideLanding`. */
import { use } from "react";
import { DecideLanding } from "../DecideLanding";

export default function DecidePage({ params }: { params: Promise<{ card: string }> }) {
  const { card } = use(params);
  let id = card;
  try { id = decodeURIComponent(card); } catch { /* keep it as given */ }
  return <DecideLanding cardId={id} />;
}
