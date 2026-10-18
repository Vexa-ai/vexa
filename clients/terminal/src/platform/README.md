# platform

The client substrate: dependency injection (`createServiceId`/`useService`), observable `createStore`/`useStore`, and the `Command`/`ContextKey` services. Views consume injected services and stores — never reaching across surfaces (VSCode-style DI + service layer).

It also holds the vocabulary the surfaces share, as constants only: `events.ts` (the window events one
surface dispatches and another listens for) and `turnMarks.ts` (the marks and fixed texts a composed chat
turn carries). They live here so that a listener never imports the surface that dispatches; this folder
imports no other folder of `src/`.
