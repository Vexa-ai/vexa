# app/api/vexa/[...path] 

The catch-all REST proxy handler: forwards GET/POST/PUT/DELETE/PATCH to the gateway with the auth token, maps `/meetings`→`/bots`, and streams recording media with seek headers.
