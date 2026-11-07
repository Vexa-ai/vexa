# Connections browser boundary

The terminal's server-side half of Connections: the person's consent, credential saves,
disconnect and delete, signed for the credential broker as the `human` role
(`core/agent/contracts/credential-broker.v1`).

| File | Concern |
|---|---|
| `assertion.ts` | the ONE TypeScript signer and verifier of the broker assertion; held to the Python twin by the contract's golden vectors |
| `broker.ts` | `brokerCall`: validated sign-in → signed request; every failure a typed, logged `BrokerFault` (unauthenticated · config · transport · http_<status> · parse), never a value |
| `[...action]/route.ts` | the closed route table the panel calls; Origin must equal the declared public origin (`VEXA_CONNECTIONS_PUBLIC_ORIGIN`, else `NEXTAUTH_URL`) |
| `callback.ts` | the OAuth consent callback (`vxc_` states on `/api/auth/callback/google`); answers the popup with a status only |

Depends on the identity oracle (`currentUser`), the server-only human key file, and the broker.
The human key is mounted into this server only; agent-api and workers never hold it. No
credential or OAuth code enters a response or a log line. Configuration keys and their deploy
surfaces are declared in `__tests__/config.test.ts`.
