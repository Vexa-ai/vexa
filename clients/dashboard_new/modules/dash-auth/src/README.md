# dash-auth — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> The identity/session core behind a PORT: createAuthSession({ backend, selfHostToken? }) owns token resolution (session token → selfHostToken → null) + the login/logout flows (self-host, magic-link, OAuth), all as find-or-create→mint over an AuthBackend port. createFakeAuthBackend is the in-memory double; the heavy provider wiring (admin API + JWT + OAuth) is a thin adapter. The dashboard holds one AuthSession; real vs fake is a swap.

