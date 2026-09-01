# Debug API namespace

This namespace contains development-only, same-origin bridges used by local witness interfaces.
Every route must fail closed outside `NODE_ENV=development` and must require its own explicit
fixture/backend configuration. Nothing under this directory is a hosted product API.
