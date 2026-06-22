# dash-api-client — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> The api.v1 REST client behind a PORT: getMeetings/getMeeting/getTranscripts/getRecordingMaster/postBot/deleteBot. createHttpApiClient (injected fetchImpl, validates responses via @vexa/dash-contracts) + createFakeApiClient (in-memory api.v1 golden shapes). The dashboard talks to one ApiClient interface; HTTP vs fake is a swap.

