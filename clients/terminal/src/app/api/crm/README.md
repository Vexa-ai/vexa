# CRM proxy

Only the six fixed operation names are accepted. The caller's auth cookie travels
to CRM for identity resolution; no environment fallback key is used. Disabled CRM
returns 404 and an unreachable CRM returns 503.
