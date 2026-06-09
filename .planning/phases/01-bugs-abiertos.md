# Phase 1: Bugs Abiertos — Cierre bloqueado

**Status:** In progress
**Bloquea:** Cierre del proyecto hasta resolver

## BUG-1: Transcripción falla — `timestamp_granularities` (Critical)

- **Síntoma:** Bot envía `timestamp_granularities` a Groq Whisper API, que lo rechaza con HTTP 400
- **Root cause:** Código del bot contenedor (upstream vexa-bot #355), NO en meeting-api
- **Impacto:** Sin transcripción en tiempo real durante meetings
- **Workaround:** Transcripción diferida (POST `/meetings/{id}/transcribe`) funciona
- **Fix posible:** Custom bot image quitando el parámetro, o esperar fix upstream

## BUG-5: Bot no se sale al terminar la reunión (Low)

- **Síntoma:** Bot permanece en la reunión después de que el usuario la termina
- **Root cause:** Desconocido — posible problema de señalización del bot
- **Impacto:** Bajo — el bot eventualmente se sale por timeout

---

## Bugs resueltos esta sesión

- **BUG-2** (video no grababa) → `CAPTURE_MODES=audio,video` en .env
- **BUG-3** (race condition frame extractor) → re-run manual funciona
- **BUG-4** (int32 overflow snowflake IDs) → BigInteger en 4 columnas
- **BUG-6** (SNAPSHOTS_ENABLED=false) → .env configurado

## Resultados verificados (Meeting #4)

| Componente | Estado | Detalle |
|-----------|--------|---------|
| Audio | OK | 912KB master.webm |
| Video | OK | 2.8MB master.webm (764s) |
| Frames | OK | 25 frames (30s interval, 320x180 WebP) |
| Transcripción RT | ROTO | BUG-1 |
| Transcripción diferida | Sin probar | Workaround disponible |