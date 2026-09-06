"""routers/admin.py — The operator's surface: the hidden admin panel, the organisation tier, and the two
credential self-tests. Internal-tier gated, not a user surface.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from control_plane import global_layer
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from shared.git_redaction import redact as redact_secrets
import hmac
import json
import os


def build(**d) -> APIRouter:
    """The admin routes, bound to one app's dependencies."""
    router = APIRouter()
    _global_store = d['_global_store']
    dispatcher = d['dispatcher']
    live = d['live']
    redis_url = d['redis_url']
    settings = d['settings']
    subject_of = d['subject_of']

    @router.get("/api/models")
    def models(request: Request):
        """The ONE model this product runs: the agent's. There is no second, "streaming"/"meeting"
        model any more — PRD decision 34 removed the in-product inference pipeline that had one."""
        subject_of(request)  # identity gate (P20)
        chat_model = settings.agent_model or "default"
        return {"chat_model": chat_model, "agent_model": chat_model}
    @router.get("/api/admin/overview")
    def admin_overview(request: Request):
        """Read-only infra + pipeline introspection for the terminal's hidden admin panel: every
        runtime.v1 workload (agent workers + meeting bots, classified) plus the per-meeting redis
        pipeline carriers (proc/tc streams, opt-in flag, cursor, active_meetings membership).

        INTERNAL-TIER ONLY (fail-closed): the caller must present ``X-Internal-Secret`` matching
        ``VEXA_INTERNAL_API_SECRET`` — the terminal's Next server holds it and fronts this with its
        own email-allowlist gate; an unconfigured secret means NOBODY gets in (403), and the check
        holds regardless of ingress (direct or via the gateway's /agent/* proxy)."""
        from control_plane import admin_panel

        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        provided = request.headers.get("x-internal-secret", "")
        if not secret or not hmac.compare_digest(provided, secret):
            raise HTTPException(status_code=403, detail="internal secret required")

        overview: dict = {"workloads": [], "meetings": []}
        try:
            overview["workloads"] = admin_panel.fetch_workloads(settings.runtime_api_url)
        except Exception as e:  # noqa: BLE001 — typed partial failure (P18): the panel shows the section error
            # SCRUBBED, like every other error this service returns (R-E11). Both of these come off
            # a client built from a URL that routinely carries a credential — `redis://:password@host`
            # here, an api key in the runtime URL above — and an exception's text is not ours to
            # predict. `redact` exists two routes away and was not applied here.
            overview["workloads_error"] = redact_secrets(f"{type(e).__name__}: {e}")
        if redis_url:
            import redis as _redis

            try:
                r = _redis.from_url(redis_url, decode_responses=True)
                overview["meetings"] = admin_panel.pipeline_snapshot(r, live.list())
            except Exception as e:  # noqa: BLE001
                overview["meetings_error"] = redact_secrets(f"{type(e).__name__}: {e}", redis_url)
        else:
            overview["meetings_error"] = "no redis_url configured"
        return overview
    @router.post("/api/admin/probe")
    def admin_probe(request: Request):
        """Run the transcription-pipeline golden smoke probe (gateway → meeting-api → runtime →
        redis carriers → transcript relay). Same internal-tier gate as the overview; POST because
        it actively exercises the path (a redis write/read round-trip on scratch keys)."""
        from control_plane import admin_panel
        from control_plane import transcription_watcher as _txw

        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        provided = request.headers.get("x-internal-secret", "")
        if not secret or not hmac.compare_digest(provided, secret):
            raise HTTPException(status_code=403, detail="internal secret required")

        r = None
        if redis_url:
            import redis as _redis

            try:
                r = _redis.from_url(redis_url, decode_responses=True)
            except Exception:  # noqa: BLE001 — the probe's redis stage reports the fault
                r = None
        # Workloads cross-check the in-memory live registry (a stale "live" entry must not turn
        # relay quiet into a false FAIL). Unknown (kernel unreachable) → None = trust the registry.
        try:
            workloads = admin_panel.fetch_workloads(settings.runtime_api_url)
        except Exception:  # noqa: BLE001
            workloads = None
        return admin_panel.run_probe(settings, r, live.list(), relay_health=_txw.relay_health(),
                                     workloads=workloads)
    @router.get("/api/global/state")
    def global_state(request: Request):
        """WHAT THE COMPANY LAYER HOLDS — the wizard's poll, and the honest answer to "why is this
        instance still refusing people".

        Readable by any authenticated subject on purpose: a non-admin who has just been refused at
        the door deserves to be told the instance is mid-setup rather than that they are broken.
        The company NAME is only returned once the gate is down — before that it is a half-written
        answer to a question about somebody's employer."""
        subject = subject_of(request)
        gate = global_layer.instance_state(settings)
        try:
            st = global_layer.state(_global_store())
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not read the organisation tier: {e}")
        down = gate.get("global_setup") == global_layer.COMPLETED
        return {
            "global_setup": gate.get("global_setup", global_layer.MISSING),
            "company": (gate.get("company") or st["company"]) if down else None,
            "present": st["present"],
            "missing_files": st["missing_files"],
            "reasons": st["reasons"],
            "is_repo": st["is_repo"],
            "commits": st["commits"],
            "ready_to_accept": st["ready"],
            "you_are_admin": global_layer.is_admin(settings, str(subject)),
            "gate_sentence": global_layer.GATE_SENTENCE,
        }
    @router.post("/api/global/ready")
    def global_ready(request: Request, body: dict = Body(default={})):
        """ACCEPT the company layer: verify the files, commit them as the admin, lift the gate.

        NOTHING MAY MARK ITSELF READY. The agent that wrote the layer asks for this verb and the
        verb goes and looks — the five files present and non-empty, and a README that opens with
        the company's name and one sentence of what it does. That last rule is the founder's:
        *"the first chat needs to present itself knowing about itself — which company it's from and
        what's their service."* An agent can only say which company it belongs to if a human wrote
        the name down, so the gate does not lift on a README that does not carry one.

        Admin-only, idempotent, and it reports WHY it refused rather than just refusing — the caller
        is an agent mid-conversation with the one person who can fix it."""
        subject = subject_of(request)
        if not global_layer.is_admin(settings, str(subject)):
            raise HTTPException(status_code=403,
                                detail="only the instance admin may accept the company layer")
        root = _global_store()
        # The SECOND top-up point. Start is the one that matters for a running instance; this one
        # catches the instance that was started before its store existed, or whose `_global` became
        # writable later — and it runs BEFORE the commit below, so anything added rides into the
        # admin's own acceptance commit instead of sitting untracked. Additive, never overwriting,
        # and never raising (it logs what it could not write) — so it cannot fail an acceptance.
        from control_plane import global_seed, preset_library
        preset_library.top_up(root)
        # The rest of the tier on the same terms — the layer files, `POLICIES.md`, the flow pages
        # and the mail templates. It cannot lift the gate on its own: every seeded layer file
        # carries `global_layer.UNWRITTEN_MARKER`, and `state()` below counts a file that still
        # carries it as not yet written.
        global_seed.top_up(root)
        st = global_layer.state(root)
        if not st["ready"]:
            return JSONResponse(status_code=409, content={
                "accepted": False,
                "global_setup": global_layer.MISSING,
                "missing_files": st["missing_files"],
                "reasons": st["reasons"],
                "next": "write the missing files into /workspaces/_global, then call this again",
            })
        email = str(body.get("author_email") or "").strip() or f"admin-{subject}@vexa.local"
        name = str(body.get("author_name") or "").strip() or f"vexa admin {subject}"
        try:
            sha = global_layer.commit(root, author_email=email, author_name=name,
                                      message=f"company layer: {st['company']}")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not commit the company layer: {e}")
        try:
            global_layer.mark_ready(settings, company=st["company"])
        except Exception as e:  # noqa: BLE001
            # The commit stands and the files are on disk; only the MARKER failed. Say exactly that
            # — an agent told "failed" would rewrite files that are already correct.
            raise HTTPException(status_code=502, detail=(
                f"the company layer is committed ({sha}) but the instance gate could not be "
                f"recorded: {e}"))
        return {"accepted": True, "global_setup": global_layer.COMPLETED,
                "company": st["company"], "service": st["service"], "commit": sha,
                "files": st["present"]}
    @router.get("/api/models/test")
    def models_test(request: Request):
        """Test the effective model credentials NOW: custom mode = a real 1-token completion
        against the endpoint; subscription = mounted-credentials expiry check (the recurring
        stale-Keychain 401 surfaces here with its remedy instead of at the next chat turn)."""
        from control_plane import config_test as _ct
        subject = subject_of(request)
        cfg: dict = {}
        mc = getattr(dispatcher, "_model_config", None)
        if mc is not None:
            try:
                cfg = mc.resolve(subject) or {}
            except Exception as exc:  # resolver down → still test the env floor, but SAY so
                out = _ct.run_models_test({})
                out["summary"] += f" (settings resolver unavailable: {exc} — tested env defaults)"
                return out
        return _ct.run_models_test(cfg)
    @router.get("/api/transcription/test")
    def transcription_test(request: Request):
        """Probe the effective STT backend with its token (GET /balance): catches dead URLs,
        rejected tokens, and the zero-balance-external-account case that 402s every segment."""
        from control_plane import config_test as _ct
        subject = subject_of(request)
        url, token, source = "", "", "env"
        settings = dispatcher.settings
        admin = (settings.admin_api_url or "").rstrip("/")
        if admin:  # same internal edge bot_spawn uses (bot-context carries the resolved override)
            import urllib.request as _ur
            try:
                req = _ur.Request(f"{admin}/internal/users/{subject}/bot-context",
                                  headers={"X-Internal-Secret":
                                           settings.internal_api_secret.get_secret_value()})
                with _ur.urlopen(req, timeout=5) as r:
                    body = json.loads(r.read())
                t = body.get("transcription") or {}
                if t.get("url") or t.get("token"):
                    url, token, source = t.get("url") or "", t.get("token") or "", "settings"
            except Exception:
                pass  # fall through to env — the probe result still says what was tested
        if not url:
            url = os.environ.get("TRANSCRIPTION_SERVICE_URL", "")
            token = token or os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "")
        elif not token:
            token = os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "")
        return _ct.run_transcription_test(url, token, source)

    return router
