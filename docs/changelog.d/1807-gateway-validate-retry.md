- **An admin-api restart no longer fails requests with 503 (#1807).** When the gateway cannot connect
  to admin-api to check an API key, it now tries again after 0.5 s and then 1.5 s before answering
  `503 Authentication temporarily unavailable, retry`, so a short restart costs a request about two
  seconds instead of an error. Only a request admin-api never received is repeated; a slow admin-api
  is answered `503` at once, as before, and a key is still admitted only on admin-api's answer. See
  [Troubleshooting](/troubleshooting#503-authentication-temporarily-unavailable).
