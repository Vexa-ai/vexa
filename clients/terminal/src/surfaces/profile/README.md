# profile/

Public profiles and the directory (R4). These are behind the `public_profiles` switch, which is off by default and not included in `all`. The design is the approved mock in proto-on-product `lab/desk/Flows.tsx`, flows 13–14.

- `PublicProfile.tsx`:
  - `PublicProfileSettings` (Settings → Public profile): a person publishes their own profile field by field and can withdraw it. "Linked by N orgs" shows how many organisations link it, never which. This is also where a company domain is verified.
  - `LinkedProfile` (on a CRM record page): the person's public fields, live, next to the organisation's own notes. Shows the organisation's data only after a withdrawal. Link a profile from the directory, or unlink it.
  - `DirectorySearch` (account menu → Directory): searches published profiles. A query needs at least 3 letters, there are at most 10 answers, and profiles are reached by handle only.
- `profileApi.ts` holds the calls:
  - identity's `/api/user/profile*`, `/api/user/directory/*` and `/api/user/company-verification*`;
  - crm-api's `profile/read` and `profile/link` through `/api/crm`.

**Retiring (PRD 0.13.4 D35, 2026-10-17).** The person's own profile is now their claimed CRM record,
with one writer (crm-api `/person/profile`); `profileApi.ts` maps this editor onto it, and its public
page is `/p/<handle>`. Identity's profile accepts no writes; its directory search still reads it for
one release.
