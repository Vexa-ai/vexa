# join-form — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> The start-bot form VIEW brick: a presentational React component (platform select + meeting URL/native-id input + bot name) that parses the input into (platform, native id) and calls an injected onSubmit with a CreateBotRequest. Props in, DOM out — no store, no fetch, no websocket. Typed by @vexa/dash-contracts.

