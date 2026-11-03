/** OPENING A PERSON (feature `shell2`): one address for the person panel, from any row, chip, face or
 *  mention — the right panel (a sheet on a phone), never the centre (7c). */
import { openDocOnRight } from "../desktop/home2/acts";

export const PERSON_PATH = ".person/";
export const isPersonPath = (path: string | null | undefined): boolean => !!path && path.startsWith(PERSON_PATH);
/** The key a person is opened by: their stable key when known, else their account, address or name. */
export function personKey(p: { key?: string | null; subject?: string | null; email?: string | null; name?: string | null }): string {
  return p.key || (p.subject ? `u:${p.subject}` : p.email ? `e:${p.email.toLowerCase()}` : `n:${(p.name ?? "").trim()}`);
}
export const keyOfPath = (path: string): string => decodeURIComponent(path.slice(PERSON_PATH.length));
export function openPerson(p: { key?: string | null; subject?: string | null; email?: string | null; name: string }): void {
  openDocOnRight("personal", `${PERSON_PATH}${encodeURIComponent(personKey(p))}`, p.name);
}
/** Your own profile. */
export const ME_KEY = "me";
export function openMyProfile(name = "Your profile"): void { openDocOnRight("personal", `${PERSON_PATH}${ME_KEY}`, name); }
