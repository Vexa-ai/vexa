# Architecture Validation: Proposed Architecture vs Industry Best Practices

**Date:** 2026-03-27
**Scope:** Validate 6 architectural decisions in `docs/architecture-proposed.md`
**Method:** Web research, competitor analysis, case studies, known vulnerabilities

---

## 1. Auth via Gateway Headers (X-User-ID injection)

**Verdict: VALIDATED — proven pattern, but requires mandatory hardening**

### Who does it well

| System | Pattern | Details |
|--------|---------|---------|
| **Envoy ext_authz** | External auth service returns headers forwarded to backends | `headersToBackend: ["x-current-user"]` — only auth-response headers reach backends, not client-supplied ones. [Envoy Gateway docs](https://gateway.envoyproxy.io/docs/tasks/security/ext-auth/) |
| **Kubernetes Gateway API** | v1.4 (Oct 2025) added native ext_authz support with configurable header forwarding | [K8s Gateway API v1.4 blog](https://kubernetes.io/blog/2025/11/06/gateway-api-v1-4/) |
| **Kong** | Request Transformer plugin strips/adds headers after auth validation | Headers set by plugins override client-supplied ones |
| **Traefik** | ForwardAuth middleware — external service returns headers injected into request | `authResponseHeaders` whitelist controls what gets forwarded |
| **AWS ALB** | OIDC auth injects `X-Amzn-Oidc-Data` (signed JWT), `X-Amzn-Oidc-Identity` (sub claim) | [AWS ALB auth docs](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html) |
| **Supabase** | Kong gateway validates API key / JWT, injects appropriate role-scoped JWT for upstream | API key → gateway looks up consumer → replaces Authorization header with pre-signed ES256 JWT. [Supabase auth architecture](https://supabase.com/docs/guides/auth/architecture) |

### The proposed pattern matches Envoy ext_authz exactly

```
Client → Gateway (validates token via admin-api) → injects X-User-ID, X-User-Scopes → backend
```

This is the standard "auth proxy" pattern used in production by every major gateway.

### Critical security concern: header spoofing

**The ALBeast vulnerability (2024)** exposed ~15,000 apps that trusted gateway-injected headers without proper validation. Attack: attacker configures their own ALB, forges JWT with arbitrary claims, sends it to victim app that only validates signature (shared JWK set across all AWS ALB customers). [Traceable.ai ALBeast writeup](https://www.traceable.ai/blog-post/albeast-a-simple-misconfiguration-to-a-complete-authentication-bypass)

**Traefik CVE (GHSA-62c8-mh53-4cqv):** Clients could remove X-Forwarded-* headers, causing apps to trust spoofed values. [Traefik security advisory](https://github.com/traefik/traefik/security/advisories/GHSA-62c8-mh53-4cqv)

### Mandatory mitigations for this architecture

1. **Gateway MUST strip incoming X-User-ID, X-User-Scopes, X-User-Limits headers** before auth validation. If the gateway doesn't strip these, a client can set `X-User-ID: 1` and bypass auth entirely. Envoy does this by design (only auth-response headers forwarded). Your gateway must do the same.

2. **Backend services MUST NOT be directly accessible** — only via gateway. Use network-level controls (Docker network isolation, K8s NetworkPolicy, security groups). AWS lesson: "Restrict your targets to only receive traffic from your Application Load Balancer."

3. **The `validate_request()` function in each package should check order carefully:**
   - If `X-User-ID` header present AND request comes from trusted network → trust it
   - If `X-API-Key` present → validate against env var (standalone mode)
   - Never trust `X-User-ID` from untrusted networks

4. **Consider a shared secret** between gateway and backends (e.g., `X-Gateway-Secret` header) as defense-in-depth. Cheap to implement, prevents accidental exposure.

### Risk if not mitigated

If someone accesses a backend directly (misconfigured port exposure, Kubernetes service without NetworkPolicy), they can set `X-User-ID: <any_user>` and impersonate anyone. This is a **critical** vulnerability class.

---

## 2. Each Service Owns Its DB Models (Same Postgres, Separate Schemas)

**Verdict: VALIDATED — well-established pattern, split Alembic works but needs care**

### Who does it

| System | Pattern | Details |
|--------|---------|---------|
| **Shopify** | 37 components in modular monolith, shared MySQL, Packwerk enforces code boundaries | Components own their domain models. Cross-component associations are violations. [Shopify monolith](https://shopify.engineering/shopify-monolith) |
| **Django** | Multi-app, single DB, FKs across apps are standard | `'app_name.ModelName'` syntax for cross-app FKs. This is Django's intended architecture. [Django multi-db docs](https://docs.djangoproject.com/en/6.0/topics/db/multi-db/) |
| **Temporal** | Multiple keyspaces in single Cassandra/Postgres, separate schemas | Each component (history, visibility, matching) has its own schema within the shared database |
| **Supabase** | Single Postgres, separate schemas (auth, storage, realtime, public) | Each service owns its schema. Cross-schema refs via simple IDs. |

### Split Alembic migrations: viable but needs careful setup

The Alembic maintainer (CaselIT) [explicitly endorses](https://github.com/sqlalchemy/alembic/discussions/1522) separate migrations for services sharing one DB, with these requirements:

1. **Each service gets its own `alembic_version` table:**
   ```python
   context.configure(
       connection=connection,
       target_metadata=target_metadata,
       version_table='alembic_version_meeting',  # unique per service
       include_object=include_object  # filter to own tables only
   )
   ```

2. **`include_object()` callback** ensures each migration context only sees its own tables — prevents service A from trying to drop service B's tables during `--autogenerate`.

3. **Separate migration directories** per service (or separate alembic.ini sections). The [DEV Community guide](https://dev.to/fadi-bck/managing-database-migrations-for-multiple-services-in-a-monorepo-with-alembic-3p5l) shows one pattern but isn't battle-tested.

4. **Alembic maintainer's caveat:** If services share the same schema and tables, consider a third dedicated migration project instead. Since the proposed architecture has separate schemas (admin, meeting, agent), this caveat doesn't apply.

### The proposed FK pattern is correct

```
FK: meetings.user_id → users.id (DB level only)
No cross-schema model imports in code
```

This matches Django's standard multi-app pattern and Supabase's architecture. The FK provides referential integrity at the DB level. The code treats `user_id` as an opaque integer.

### Risks to watch

| Risk | Mitigation |
|------|------------|
| Migration ordering — service B migration references table from service A that doesn't exist yet | Run admin schema migrations first (it owns users). Document migration order. |
| Schema drift — developer adds FK in code but not in the right migration directory | CI check: run `alembic check` per service to detect unmigrated changes |
| Shared Alembic version table collision | Use per-service version tables from day 1 |
| Cross-schema queries sneak back in | Lint rule or Packwerk-equivalent: grep for imports across package boundaries in CI |

---

## 3. Package = Independently Installable, Service = Internal

**Verdict: VALIDATED — standard monorepo pattern, naming is correct**

### Industry patterns

| System | Publishable | Internal | Convention |
|--------|-------------|----------|------------|
| **Turborepo** | `packages/` | `apps/` | Apps are deployable services; packages are importable libraries. [Turborepo docs](https://turborepo.dev/docs/crafting-your-repository/structuring-a-repository) |
| **Nx** | `libs/` (publishable) | `apps/` | `--publishable` flag on library generation |
| **Go** | `pkg/` (convention, not enforced) | `internal/` (compiler-enforced) | [Go project layout](https://github.com/golang-standards/project-layout) |
| **Backstage (Spotify)** | `plugins/` (npm-publishable) | `packages/` (app internals) | Open-source plugins published to npm; internal plugins stay in monorepo. [Backstage project structure](https://backstage.io/docs/contribute/project-structure/) |
| **Python (uv/Pants)** | `packages/` with pyproject.toml | `libs/` or `internal/` | Each publishable package has its own pyproject.toml, version, and build config |

### The proposed split is correct

```
packages/          → independently publishable (runtime-api, agent-api, meeting-api, etc.)
services/          → Vexa internal (admin-api, api-gateway, calendar-service)
libs/              → shared internal code (shared-models → admin-models)
```

This maps cleanly to Turborepo's `packages/` vs `apps/` pattern and Go's `pkg/` vs `internal/` pattern.

### One refinement to consider

Turborepo and Backstage both use **namespaced package names** for internal packages to avoid npm registry conflicts (e.g., `@vexa/shared-models`). For PyPI, this would mean namespacing like `vexa-runtime-api` vs `vexa-internal-admin-api`. Not critical for now but worth establishing the naming convention early.

---

## 4. Five Standalone Packages with Their Own docker-compose

**Verdict: VALIDATED — standard pattern, but distinguish quickstart from production**

### Who ships docker-compose inside packages

| Project | docker-compose location | Purpose | Production? |
|---------|------------------------|---------|-------------|
| **Temporal** | Separate repo: `temporalio/docker-compose` | Development/testing only. Uses auto-setup script explicitly NOT for production. Production uses Helm charts (`temporalio/helm-charts`). [Temporal deployment docs](https://docs.temporal.io/self-hosted-guide/deployment) |
| **Supabase** | `docker/docker-compose.yml` in main repo | Self-hosting quickstart. Docs say "suitable for development and small to medium production workloads." [Supabase self-hosting](https://supabase.com/docs/guides/self-hosting/docker) |
| **Appwrite** | Root `docker-compose.yml` | Single installation command. Explicitly no horizontal scaling for app server. |
| **Meilisearch** | Official `docker-compose.yml` in docs | Quickstart. Production uses single binary or cloud. |
| **MinIO** | `docker-compose.yml` in quickstart docs | Development. Production uses K8s operator. |
| **Traefik** | `docker-compose.yml` in quickstart guide | Getting started. Production uses static config + systemd or K8s. |

### Established two-tier pattern

Every major infrastructure project ships:
1. **`docker-compose.yml`** (or `docker-compose.quickstart.yml`) — single command to get running, includes all deps (Postgres, Redis, etc.), NOT for production
2. **Helm chart / K8s manifests / production guide** — separate, expects external managed DB, proper TLS, scaling config

### Recommendation for the proposed packages

```
services/runtime-api/
  docker-compose.yml          ← quickstart (includes Postgres, Redis)
  docker-compose.dev.yml      ← development overrides (hot reload, debug ports)
  deploy/
    helm/                     ← production (K8s)
    docker-compose.prod.yml   ← production (Docker, external DB)
```

Add a comment at the top of each quickstart compose:
```yaml
# WARNING: For development/testing only. Not for production use.
# See deploy/ for production configurations.
```

This matches Temporal's approach exactly: quickstart compose in one repo, helm charts for production.

---

## 5. Transcription-Collector Folding into Meeting-API

**Verdict: CAUTION — viable for simplicity, but has real backpressure risks**

### What transcription-collector does today

From the dependency audit:
- **Reads:** Redis streams (transcription segments from bots)
- **Writes:** Postgres (Transcription, Meeting tables), Redis (tc:meeting:* pub/sub, meeting:*:segments cache)
- **Depends on:** shared_models ORM, storage client (MinIO/GCS)
- **Also serves:** HTTP API (transcription queries, auth-protected)

This is a **dual-role service**: stream consumer (background) + HTTP API (request/response).

### CQRS perspective: should they be separate?

| Factor | Separate | Folded | Winner |
|--------|----------|--------|--------|
| **Failure isolation** | Stream consumer crash doesn't take down API | Consumer OOM or slow query blocks API responses | Separate |
| **Scaling** | Scale consumer independently of API | Must scale both together | Separate |
| **Backpressure** | Consumer can lag without affecting latency | Consumer and API compete for same connection pool, CPU, memory | Separate |
| **Operational simplicity** | Two deployments to manage | One deployment | Folded |
| **Codebase size** | ~15k total lines, team of ~2 | Overhead of separate service not justified | Folded |
| **Deployment coupling** | Changes to collector require separate deploy | Changes deploy together (they change together anyway) | Folded |

### What Temporal teaches

Temporal explicitly separates **server** (API/matching/history) from **workers** (execute workflows). But Temporal's workers handle unbounded, long-running tasks with complex replay semantics. A Redis stream consumer writing to Postgres is much simpler.

### What actually matters: shared resources

The real risk is **connection pool exhaustion**. If the stream consumer holds Postgres connections during a burst of transcription data, the API endpoints can't get connections and timeout. Mitigations:

1. **Separate connection pools** — consumer gets its own pool (e.g., 5 connections) distinct from the API pool (e.g., 10 connections). SQLAlchemy supports this.
2. **Bounded consumer concurrency** — limit how many segments are written in parallel
3. **Health check that covers both** — if consumer falls behind, health check should reflect this

### Recommendation

**Fold it in, but with guardrails:**
- Separate connection pools for consumer vs API
- Consumer runs as a background task (asyncio) with bounded concurrency
- If backpressure becomes a problem (metrics: consumer lag, API p99 latency), split it back out. The separate-schema architecture makes this easy.
- Do NOT fold the HTTP API endpoints — only the stream consumer logic. The transcript query endpoints should move to meeting-api as-is.

---

## 6. Risks We Might Be Missing

**Verdict: CAUTION — 5 packages from ~15k lines is on the edge; watch for premature splitting**

### Segment's cautionary tale

Segment moved to microservices during hypergrowth (2016-2017), adding ~50 destinations. Problems:
- **Shared library changes took ~1 week** due to cross-service testing overhead
- **Uniform auto-scaling** wasted resources (each service had different needs but same config)
- **Code reuse collapsed** — easier to copy-paste than update shared libs across repos

They consolidated back into "Centrifuge" — a single repo, unified deployment. [InfoQ: Segment back to monolith](https://www.infoq.com/news/2020/04/microservices-back-again/)

### Shopify's explicit choice

Shopify runs a 3M+ line Rails monolith with 37 components enforced by Packwerk. They are **very deliberate** about extracting services:

> "Splitting a single monolithic application into a distributed system of services increases the overall complexity considerably." — [Shopify Engineering](https://shopify.engineering/deconstructing-monolith-designing-software-maximizes-developer-productivity)

Shopify only extracts when:
1. The component has genuinely different scaling requirements
2. The team is large enough that the coordination cost is justified
3. The boundary is truly stable and well-understood

### Amazon Prime Video's lesson

Moved from microservices back to monolith, reducing costs by 90%. The distributed overhead (network calls, serialization, deployment complexity) wasn't justified by the scaling benefits.

### The ratio question: 5 packages from ~15k lines

| Metric | Value | Concern |
|--------|-------|---------|
| Total LOC | ~15,000 | Small codebase |
| Packages | 5 publishable + 6 services + 2 libs = 13 units | High ratio |
| Team size | ~2 developers | Every boundary = coordination cost |
| LOC per package | ~1,100 average | Very small packages |

**Industry rule of thumb** (from modular monolith literature): For teams under 100 engineers and traffic under 100M req/day, a well-structured modular monolith is the most productive architecture.

### Specific risks for this architecture

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Shared library change hell** — changing shared-models requires testing across all packages | HIGH | HIGH | Keep shared-models minimal. Per-service schemas reduce this. |
| **Docker compose sprawl** — 5 separate docker-compose files for 5 packages, each with their own Postgres/Redis | MEDIUM | MEDIUM | Shared dev compose at root; per-package compose for standalone testing only |
| **Migration coordination** — deploying schema changes across 3 Alembic migration directories | MEDIUM | HIGH | CI runs all migrations in order. Document the order. |
| **Import boundary enforcement** — without a tool like Packwerk, cross-package imports creep back | HIGH | MEDIUM | CI lint: grep for forbidden imports. Consider a Python equivalent (import-linter). |
| **Over-abstraction of auth** — each package has its own `validate_request()` that needs to work in both standalone and Vexa mode | LOW | MEDIUM | Keep it as a simple function, not a framework. The proposed approach is already minimal. |
| **Premature package extraction** — tts-service and transcription-service may not have standalone users yet | MEDIUM | LOW | Extract them last. Only publish when there's actual demand. |

### Are we over-splitting?

**Maybe.** Five publishable packages from a 15k-line codebase means ~1,100 lines per package average. Each boundary adds:
- A pyproject.toml
- A docker-compose.yml
- A README
- An Alembic migration directory
- CI configuration
- Version management

For a 2-person team, this is significant overhead. **Recommendation:** Start with the minimum viable split:

1. **runtime-api** — clear standalone value, already decoupled, different scaling needs (container management)
2. **meeting-api** — core product, most complex, needs its own domain
3. Keep agent-api, transcription-service, tts-service as **internal services** for now. Promote to packages when there's external demand.

This reduces from 5 packages to 2, while keeping the architecture clean enough to split later.

---

## Summary Matrix

| Decision | Verdict | Key Risk | Top Mitigation |
|----------|---------|----------|----------------|
| **1. Auth via gateway headers** | VALIDATED | Header spoofing if backends directly accessible | Gateway MUST strip incoming auth headers; network isolation required |
| **2. Separate schemas, shared Postgres** | VALIDATED | Migration ordering, schema drift | Per-service alembic_version tables; CI migration checks |
| **3. packages/ vs services/ split** | VALIDATED | None significant | Establish naming convention early |
| **4. docker-compose per package** | VALIDATED | Dev/prod confusion | Label quickstart vs production clearly |
| **5. Fold transcription-collector** | CAUTION | Backpressure, connection pool exhaustion | Separate connection pools; bounded consumer concurrency |
| **6. 5 packages from 15k lines** | CAUTION | Over-splitting for team size; coordination overhead | Start with 2 packages (runtime-api, meeting-api), promote others on demand |

---

## Sources

### Auth / Gateway Headers
- [Envoy Gateway ext_authz docs](https://gateway.envoyproxy.io/docs/tasks/security/ext-auth/)
- [K8s Gateway API v1.4](https://kubernetes.io/blog/2025/11/06/gateway-api-v1-4/)
- [ALBeast vulnerability writeup](https://www.traceable.ai/blog-post/albeast-a-simple-misconfiguration-to-a-complete-authentication-bypass)
- [Traefik header spoofing CVE](https://github.com/traefik/traefik/security/advisories/GHSA-62c8-mh53-4cqv)
- [AWS ALB auth docs](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html)
- [Supabase auth architecture](https://supabase.com/docs/guides/auth/architecture)

### Separate Schemas / Split Migrations
- [Alembic maintainer on split migrations](https://github.com/sqlalchemy/alembic/discussions/1522)
- [Alembic multi-schema issue #710](https://github.com/sqlalchemy/alembic/issues/710)
- [DEV Community: Alembic in monorepos](https://dev.to/fadi-bck/managing-database-migrations-for-multiple-services-in-a-monorepo-with-alembic-3p5l)
- [Django multi-database docs](https://docs.djangoproject.com/en/6.0/topics/db/multi-db/)

### Monorepo Structure
- [Turborepo: Structuring a repository](https://turborepo.dev/docs/crafting-your-repository/structuring-a-repository)
- [Backstage project structure](https://backstage.io/docs/contribute/project-structure/)
- [Go project layout](https://github.com/golang-standards/project-layout)

### Docker Compose Patterns
- [Temporal deployment guide](https://docs.temporal.io/self-hosted-guide/deployment)
- [Temporal docker-compose repo](https://github.com/temporalio/docker-compose)
- [Supabase self-hosting](https://supabase.com/docs/guides/self-hosting/docker)

### CQRS / Stream Consumer Separation
- [CQRS Pattern — Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/patterns/cqrs)
- [Martin Fowler on CQRS](https://martinfowler.com/bliki/CQRS.html)
- [Microservices.io: CQRS pattern](https://microservices.io/patterns/data/cqrs.html)

### Monolith vs Microservices
- [Segment back to monolith — InfoQ](https://www.infoq.com/news/2020/04/microservices-back-again/)
- [Shopify: Deconstructing the Monolith](https://shopify.engineering/deconstructing-monolith-designing-software-maximizes-developer-productivity)
- [Shopify monolith state](https://shopify.engineering/shopify-monolith)
- [Shopify Packwerk retrospective](https://shopify.engineering/a-packwerk-retrospective)
- [Shopify Packwerk enforcement](https://shopify.engineering/enforcing-modularity-rails-apps-packwerk)
