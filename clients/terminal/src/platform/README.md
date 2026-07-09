# platform

The client substrate: dependency injection (`createServiceId`/`useService`), observable `createStore`/`useStore`, and the `Command`/`ContextKey` services. Views consume injected services and stores — never reaching across surfaces (VSCode-style DI + service layer).
