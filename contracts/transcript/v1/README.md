# transcript.v1 — attributed transcript segments (to formalize at MVP2)

Producer: `delivery` bricks (`segment-publisher.ts`). Consumer: `collector`
(`meeting-api/collector/`). Today this exists as the de facto Redis segment
schema; MVP2 freezes it as JSON schema + goldens recorded from production
replay (`make play-replay` FULL mode already publishes through it).
