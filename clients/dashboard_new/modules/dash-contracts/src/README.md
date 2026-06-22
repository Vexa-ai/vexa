# dash-contracts — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> The single consumed-contract brick: the seam between the dashboard and the backend. TS types for the WS frames (0.10.6 ws.v1 truth) + REST shapes (sealed api.v1) the dashboard reads, plus ajv validators that load the on-disk sealed schemas and pin every golden. Conforms to the contracts; never redefines them.

