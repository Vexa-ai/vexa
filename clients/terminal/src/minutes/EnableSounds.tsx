"use client";
/** "Enable sounds" (0.13.4 S6) — browsers keep audio locked until a gesture, so the first
 *  interruption that would have made a sound raises this small one-time prompt instead. One click
 *  unlocks (so does any click on the page); then it is gone for the session. */
import { Volume2 } from "lucide-react";
import { Button } from "../ui-kit/primitives/Button";
import { enableSounds, useNoticeSounds } from "./noticeSounds";
import "./notifySettings.css";

export function EnableSoundsPrompt() {
  const { needsEnable } = useNoticeSounds();
  if (!needsEnable) return null;
  return (
    <div className="vx-enable-sounds" data-enable-sounds="">
      <Button variant="secondary" icon={<Volume2 size={14} aria-hidden />} onClick={enableSounds}>Enable sounds</Button>
    </div>
  );
}
