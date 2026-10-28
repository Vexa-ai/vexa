/** PHOTOS FOR FACES DRAWN BY NAME (UI 15g; feature `shell2`): the people the person knows
 *  (`GET /api/people/known`, the same set the @ picker and the People strip read) carry their photo
 *  route; registering them lets every AvatarStack show the photo, with the monogram as the fallback.
 *  Read once per page load; a failed read leaves the monograms. Only a Vexa user's own photo is used
 *  — a CRM contact who is not a user is never given a face (15g). */
import { useEffect } from "react";
import { registerPersonPhotos } from "../../ui-kit";
import { media } from "../../platform/identity";
import { listKnownPeople } from "../../surfaces/sessionsApi";

let asked = false;
export function usePeoplePhotos(): void {
  useEffect(() => {
    if (asked) return;
    asked = true;
    void listKnownPeople().then(({ people }) => {
      const m: Record<string, string> = {};
      for (const p of people) {
        if (!p.vexa) continue;
        const src = p.person.photo || (p.person.avatar ? media(p.person.avatar) : "");
        if (src) m[p.name] = src;
      }
      registerPersonPhotos(m);
    }).catch(() => { asked = false; });
  }, []);
}
