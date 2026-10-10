"""routers/admin.py — The operator's surface: the hidden admin panel, the organisation tier, and the two
credential self-tests. Internal-tier gated, not a user surface.

Extracted from `api.py`'s `create_app` VERBATIM: the handler bodies below are the same
bytes, with `@app.` rewritten to `@router.` and nothing else. Everything they close over
is handed in by `build()` and rebound to the name it already had, so no body needed a
single identifier changed.
"""
from __future__ import annotations

from typing import Optional

from control_plane import dispatch as dispatch_mod
from control_plane import global_layer, model_providers, system_mounts
from control_plane.bodies import GlobalReadyBody, SessionId
from control_plane.ceiling import require_in_ceiling
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from shared import units
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
    sess = d['sess']
    settings = d['settings']
    subject_of = d['subject_of']

    @router.get("/api/models")
    def models(request: Request):
        """The ONE model this product runs: the agent's. There is no second, "streaming"/"meeting"
        model any more — PRD decision 34 removed the in-product inference pipeline that had one."""
        subject_of(request)  # identity gate (P20)
        chat_model = settings.agent_model or "default"
        return {"chat_model": chat_model, "agent_model": chat_model}
    @router.get("/api/models/catalog")
    def models_catalog(request: Request, session: Optional[SessionId] = None):
        """The models you may pick on this deployment (``models.v1`` ModelList): each one's name,
        provider, harness and capabilities — never an endpoint or a credential — plus the model you
        run on before you pick (``default``). Name a chat (``session``) to also get that chat's own
        pick (``selected``; null when it follows your default) and its effort pick
        (``selected_effort``; null when it runs at the model's ``default_effort``). Each model lists
        the effort levels you may pick for it (``capabilities.reasoning_efforts``, empty when it has
        no effort control). An empty list means the deployment
        declares no catalog, and its model is the operator's to set."""
        subject = subject_of(request)
        catalog = dispatcher.catalog
        with_selected = session is not None
        selected = selected_effort = None
        if with_selected:
            name = str(session).strip() or units.DEFAULT_CHAT_SESSION
            try:
                selected = sess.model(subject, name)
                selected_effort = sess.effort(subject, name)
            except Exception:  # noqa: BLE001 — an unreadable pick is the default
                selected = selected_effort = None
        if catalog.empty:
            return {"models": [], "default": None,
                    **({"selected": None, "selected_effort": None} if with_selected else {})}
        ctx = dispatch_mod.route_context(dispatcher.resolve_model_config(subject) or {},
                                         allowlist=settings.model_allowlist)
        return catalog.listing(ctx, admin=lambda: dispatcher.is_admin(subject),
                               selected=selected, with_selected=with_selected,
                               selected_effort=selected_effort)
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
            overview["workloads"] = admin_panel.fetch_workloads(
                settings.runtime_api_url, token=settings.runtime_api_token.get_secret_value())
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
            workloads = admin_panel.fetch_workloads(
                settings.runtime_api_url, token=settings.runtime_api_token.get_secret_value())
        except Exception:  # noqa: BLE001
            workloads = None
        return admin_panel.run_probe(settings, r, live.list(), relay_health=_txw.relay_health(),
                                     workloads=workloads)
    @router.post("/api/global/ready")
    def global_ready(request: Request, body: GlobalReadyBody = Body(default_factory=GlobalReadyBody)):
        """ACCEPT an admin-written company layer: verify the files and commit them as the admin.
        Call it at the END of the company-setup conversation, once the administrator agrees the five
        files are right; telling them it is done before this has accepted it is always wrong.

        OPTIONAL. Nothing waits for this any more (founder ruling 2026-10-08: "let's remove global
        setup at all so that there is no need to setup global at all - let it be empty with no data -
        it's fine"); `_global` may stay empty for good. It used to also lift the instance gate in
        admin-api — that gate is gone, so this verb only verifies and commits.

        NOTHING MAY MARK ITSELF ACCEPTED: the verb goes and looks — the five files present and
        written, and a README that opens with the company's name and one sentence of what it does,
        because those two lines are read out loud to strangers. Admin-only, idempotent, and it
        reports WHY it refused — the caller is an agent mid-conversation with the one person who
        can fix it."""
        require_in_ceiling(request, system_mounts.GLOBAL_SLUG)
        subject = subject_of(request)
        if not global_layer.is_admin(settings, str(subject)):
            raise HTTPException(status_code=403,
                                detail="only the instance admin may accept the company layer")
        root = _global_store()
        # Top-ups before the commit, so anything added rides into the admin's own acceptance commit
        # instead of sitting untracked. Additive, never overwriting, and never raising.
        from control_plane import global_seed, preset_library
        preset_library.top_up(root)
        global_seed.top_up(root)
        st = global_layer.state(root)
        if not st["ready"]:
            return JSONResponse(status_code=409, content={
                "accepted": False,
                "missing_files": st["missing_files"],
                "reasons": st["reasons"],
                "next": "write the missing files into /workspaces/_global, then call this again",
            })
        # THE CALLER IS THE AUTHOR. The identity the call arrived with carries the admin's address,
        # so an agent accepting on their behalf names them without having to know it.
        caller = (request.headers.get("x-user-email") or "").strip()
        email = (body.author_email or "").strip() or caller or f"admin-{subject}@vexa.local"
        name = ((body.author_name or "").strip()
                or (email.split("@", 1)[0] if caller or body.author_email else "")
                or f"vexa admin {subject}")
        try:
            sha = global_layer.commit(root, author_email=email, author_name=name,
                                      message=f"company layer: {st['company']}")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not commit the company layer: {e}")
        return {"accepted": True, "company": st["company"], "service": st["service"],
                "commit": sha, "files": st["present"]}
    @router.get("/api/models/test")
    def models_test(request: Request, model: Optional[str] = None):
        """Test the effective model credentials NOW: custom mode = a real 1-token completion
        against the endpoint; subscription = mounted-credentials expiry check (the recurring
        stale-Keychain 401 surfaces here with its remedy instead of at the next chat turn).

        With a model catalog, the model to test is a catalog id (``model``; your default when
        absent), resolved through the same provider port a turn's dispatch uses and probed with
        exactly the credential that route carries."""
        from control_plane import config_test as _ct
        subject = subject_of(request)
        if not dispatcher.catalog.empty:
            ctx = dispatch_mod.route_context(dispatcher.resolve_model_config(subject) or {},
                                             allowlist=settings.model_allowlist)
            try:
                route = dispatcher.catalog.route(str(model or "").strip(), ctx,
                                                 admin=lambda: dispatcher.is_admin(subject))
            except model_providers.ModelChoiceFault as fault:
                return {"ok": False, "summary": fault.sentence(), "mode": "catalog",
                        "route": "catalog", "fault": fault.as_dict()}
            return _ct.run_route_test(route)
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
        configured: dict = {}
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
                configured = body.get("transcription") or {}
            except Exception:
                pass  # fall through to env — the probe result still says what was tested
        # ONE URL, ITS OWN TOKEN: the pair a bot spawned now would use. A person's URL never gets
        # the deployment's token, and a person's token never goes to the deployment's URL.
        url, token, source, provider = _ct.transcription_route(configured, {
            "TRANSCRIPTION_SERVICE_URL": os.environ.get("TRANSCRIPTION_SERVICE_URL", ""),
            "TRANSCRIPTION_SERVICE_TOKEN": os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "")})
        if provider == "customer":     # the person's own endpoint: held to the outbound URL guard
            return _ct.run_customer_transcription_test(url, token, source)
        return _ct.run_transcription_test(url, token, source)

    return router
