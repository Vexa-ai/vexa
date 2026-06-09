# Mensaje para pegar en nueva sesión de Claude Code

Copia desde aquí hacia abajo y pega tal cual en la nueva sesión:

---

Vamos a retomar un proyecto que dejé a medias. Antes de hacer nada, recupera el contexto:

```bash
# Lee handoff guardado en mis memorias
ruflo memory retrieve -k "vexa-snapshots-feature-handoff-2026-05-23"
```

Y mira también auto-memory: `~/.claude/projects/-home-aleix/memory/project_vexa.md`.

## Lo que hay que hacer

Iniciar `/gsd-new-project` para planificar la feature **"Vexa Meeting Snapshots"** — añadir a Vexa (open-source meeting transcription) la capacidad de capturar imágenes/snapshots de meetings tipo Fathom u Otter.

## Contexto crítico (no inventes nada)

- **Repo de trabajo:** fork mío en `github.com/Allevat-ORX/vexa` (upstream: `Vexa-ai/vexa`).
- **Local:** `/home/aleix/Proyectos-Claude/Apps-Locales/vexa`.
- **Branch activa:** `feature/meeting-snapshots` (último commit `1454658` ya pushed al fork).
- **Vexa está corriendo** en docker compose con imagen `vexaai/*:0.10.6.2.1-260522-1105` + un bind-mount local con parche para 3 bugs del endpoint `/transcribe` (issue upstream `Vexa-ai/vexa#355` abierto).
- **Fase 1 ya hecha:** `CAPTURE_MODES=audio,video` en `.env` — los próximos meetings grabarán video además de audio.
- **Meeting 2 ya transcrito y visible** en `http://localhost:3001/meetings/2` (sirve como testbed).

## Plan que ya acordamos (4 fases pendientes)

| Fase | Qué |
|---|---|
| 2 | Worker Python `frame-extractor`: descarga master.webm de MinIO, `ffmpeg -vf fps=1/30` extrae 1 frame cada 30s, sube a MinIO `recordings/{mid}/{sid}/frames/` |
| 3 | Schema migration tabla `recording_frames(id, recording_id, timestamp_s, storage_path)` + endpoint `GET /recordings/{id}/frames` que devuelve presigned URLs |
| 4 | Componente React `<SnapshotsGallery>` en `services/dashboard/src/app/meetings/[id]/`: grid de thumbnails, click → seek video al timestamp |
| 5 | Tests + PR upstream a `Vexa-ai/vexa` |

## Cómo ejecutar el GSD

**Decisión pendiente:** dónde vive `.planning/` para no chocar con el `CLAUDE.md` upstream de Vexa. Yo voto por (A); tú decides:

- **(A)** `.planning/` en directorio separado `~/Proyectos-Claude/vexa-snapshots-planning/`, código en fork. Más limpio para el PR upstream.
- **(B)** `.planning/` dentro del fork, preservando CLAUDE.md upstream como `CLAUDE.vexa-upstream.md`.

Pregúntame antes de elegir.

## Setup para arrancar

```bash
# Si elijo opción A:
mkdir -p ~/Proyectos-Claude/vexa-snapshots-planning
cd ~/Proyectos-Claude/vexa-snapshots-planning
git init

# Si elijo opción B:
cd ~/Proyectos-Claude/Apps-Locales/vexa
git checkout feature/meeting-snapshots
mv CLAUDE.md CLAUDE.vexa-upstream.md
git add CLAUDE.vexa-upstream.md && git commit -m "chore: preserve upstream CLAUDE.md before GSD init"
```

Luego en cualquier caso:

```
/gsd-new-project
```

Cuando el GSD pregunte:
- **"Research first?"** → Sí (Vexa es ecosistema complejo, hay 4 servicios que conocer)
- **"Granularity?"** → Standard
- **"Execution parallel?"** → Yes
- **"Git tracking?"** → Si opción A: Yes. Si opción B: No (el `.planning/` no debe contaminar el PR upstream)
- **"Mode?"** → YOLO

## Cosas a NO olvidar

- El `CLAUDE.md` del repo Vexa exige `python3 tests3/lib/stage.py probe` antes de tocar código. Como estamos en una branch propia, podemos saltarnos su state machine, pero si vas a hacer PR upstream conviene seguirlo.
- El `.env` del repo Vexa **no está commiteado** (gitignored) y contiene la API key de Groq. No la commitees nunca.
- Si recreas containers (`docker compose down/up`), el bind-mount del parche `local-patches/meetings.py` se mantiene (está en docker-compose.yml).
- Cuando Vexa mergee mi issue #355, hay que quitar la línea del bind-mount.

Empieza por leer la memoria del handoff y luego pregúntame qué opción (A o B) elijo antes de tocar nada.
