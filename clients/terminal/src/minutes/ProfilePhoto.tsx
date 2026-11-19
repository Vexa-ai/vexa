"use client";
/** SETTINGS → PROFILE: the person's own photo (UI decision 15g). Their upload wins; without one,
 *  their sign-in provider's photo or their Gravatar shows; without any, initials. Only they change
 *  it (identity refuses a worker acting for them). The same change is on their own avatar elsewhere
 *  ("Change photo", `<Identity onChange={changeOwnPhoto}>`). */
import { useEffect, useState } from "react";
import { Identity } from "../ui-kit/Identity";
import {
  PHOTO_CHANGED_EVENT, photoUrl, pickPhotoFile, readOwnPhoto, removeOwnPhoto, uploadOwnPhoto, type OwnPhoto,
} from "../surfaces/personPhotoApi";

const PROVIDER_NAMES: Record<string, string> = { google: "Google", microsoft: "Microsoft", oidc: "sign-in" };

function whereFrom(p: OwnPhoto["photo"]): string {
  if (!p) return "Initials until you add one.";
  if (p.source === "upload") return "Your own photo.";
  if (p.source === "provider") return `From your ${PROVIDER_NAMES[p.provider ?? ""] ?? "sign-in"} account.`;
  return "From your Gravatar.";
}

export function ProfilePhoto() {
  const [own, setOwn] = useState<OwnPhoto | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let live = true;
    void readOwnPhoto().then((p) => { if (live && p && typeof p.subject === "string") setOwn(p); });
    void fetch("/api/auth/me", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then((me) => {
      const u = me?.user;
      if (live && u) setName(String(u.name || (u.email ? String(u.email).split("@")[0] : "") || ""));
    }).catch(() => {});
    const onChanged = (e: Event) => { const p = (e as CustomEvent<OwnPhoto>).detail; if (p?.subject) setOwn(p); };
    window.addEventListener(PHOTO_CHANGED_EVENT, onChanged);
    return () => { live = false; window.removeEventListener(PHOTO_CHANGED_EVENT, onChanged); };
  }, []);

  const act = async (run: () => Promise<{ ok: boolean; error?: string; photo?: OwnPhoto } | null>) => {
    setBusy(true);
    setErr("");
    const r = await run();
    setBusy(false);
    if (!r) return;
    if (r.ok && r.photo) setOwn(r.photo);
    else if (!r.ok) setErr(r.error || "The photo could not be saved just now.");
  };
  const change = () => act(async () => { const f = await pickPhotoFile(); return f ? uploadOwnPhoto(f) : null; });

  const subject = own?.subject ?? "";
  const src = own?.photo && subject ? photoUrl(subject, own.photo.version) : undefined;
  return (
    <div className="vx-settings-photo" data-profile-photo="">
      <div className="vx-settings-h">Your photo</div>
      <div className="vx-settings-row">
        <Identity kind="person" user id={subject || "me"} name={name || "Me"} size={48} src={src} />
        <div className="vx-settings-photo-side">
          <span className="vx-settings-note" data-photo-source={own?.photo?.source ?? "none"}>{whereFrom(own?.photo ?? null)}</span>
          <span className="vx-settings-photo-acts">
            <button type="button" className="vx-settings-link" disabled={busy} onClick={() => void change()}>Change photo</button>
            {own?.photo?.source === "upload" && (
              <button type="button" className="vx-settings-link" disabled={busy} onClick={() => void act(removeOwnPhoto)}>Remove photo</button>
            )}
          </span>
          {err && <span className="vx-settings-note" role="status">{err}</span>}
        </div>
      </div>
    </div>
  );
}
