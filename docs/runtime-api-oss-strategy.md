# Runtime API — Open Source Strategy

## The Opportunity

Runtime API is a generic Container-as-a-Service. We validated that **no open-source project does this** (30+ evaluated). The closest are Fly Machines (proprietary), Selenium Grid (browser-specific), and Coder (AGPL, overkill). This is a real gap in the infrastructure ecosystem.

**Positioning: "Open-source Fly Machines — self-hosted container lifecycle API"**

This follows the Supabase playbook ("open-source Firebase") — framing against a well-known proprietary product drives immediate understanding and adoption.

---

## Business Model: COSS (Commercial Open-Source Software)

```
┌─────────────────────────────────────────┐
│          Open Source (FSL)               │
│                                         │
│  Runtime API                            │
│  • Container CRUD API                   │
│  • Docker + K8s + Process backends      │
│  • Profile system (YAML config)         │
│  • Idle management, lifecycle callbacks  │
│  • Per-tenant concurrency               │
│  • CLI + Python/Node SDKs              │
│                                         │
├─────────────────────────────────────────┤
│          Commercial (Proprietary)       │
│                                         │
│  Vexa Cloud                             │
│  • Managed Runtime API hosting          │
│  • Meeting intelligence (speaker ID,    │
│    transcript quality, platform hacks)  │
│  • Meeting API + Agent API              │
│  • Enterprise: SSO, RBAC, audit logs    │
│  • Support SLAs                         │
│                                         │
└─────────────────────────────────────────┘
```

**Revenue model:** Managed cloud (primary) + enterprise support. Same pattern as Temporal, Grafana, Supabase.

**COSS financial evidence (Linux Foundation 2025):**
- COSS companies reach Series A 20% faster, Series B 34% faster
- 7x greater valuations at IPO, 14x at M&A
- 90% of COSS companies are in infrastructure software

---

## License: Apache-2.0

Vexa itself depends on Runtime API. A restrictive license (FSL, AGPL) would create friction for Vexa's own licensing and downstream users. Apache-2.0 is the right choice:

- **Zero friction** — no copyleft, no non-compete, enterprise pre-approved
- **CNCF-compatible** — required for Sandbox/Incubating/Graduated
- **Patent grant** — explicit protection that MIT lacks
- **Maximum adoption** — no legal department will block it
- **Proven at scale** — Kubernetes, Temporal, Supabase all Apache-2.0

The moat is not in 764 lines of container orchestration. It's in the meeting intelligence, transcription pipeline, voice agent, and managed cloud built on top. Temporal proved this: MIT license, $5B valuation — operational complexity is the real moat.

---

## Naming

Lead candidates (container/shipping metaphors, continuing Docker's nautical theme):

| Name | Metaphor | Available? |
|------|----------|-----------|
| **Drydock** | Containers go to drydock when idle, launch when needed | Check GitHub/PyPI/domain |
| **Berth** | Where containers dock and rest | Check |
| **Cradle** | Lifecycle from creation to termination | Check |

**Checklist before deciding:**
- [ ] GitHub org/repo available
- [ ] PyPI package name available
- [ ] `.dev` or `.io` domain available
- [ ] No trademark conflicts
- [ ] Pronounceable across languages

---

## Lead Use Cases

### #1: AI Agent Sandboxes (lead with this)
E2B raised $21M, grew to 15M monthly sessions. ~50% of Fortune 500 running agent workloads. No self-hosted open-source alternative exists.

> "Give your AI agents their own containers — self-hosted, open-source, API-first"

### #2: Browser Automation Farms
Browserbase at $300M valuation. Web scraping market $754M → $2.87B by 2034. No general-purpose container API for browser farms.

> "Manage browser container pools with REST — idle management, tenant limits, lifecycle hooks"

### Secondary (mention, don't lead)
- Meeting bot platforms
- Dev environments
- Code execution platforms
- CI/CD ephemeral runners

---

## Extraction Plan

### Strategy: Fork + Replace (3-5 days)

Runtime API is 764 lines. No need for sync tooling or clean-room rewrite.

### Step 1: Create separate repo

```bash
# Extract from Vexa repo
git clone vexa.git runtime-api-oss
cd runtime-api-oss
git filter-repo --subdirectory-filter services/runtime-api
```

### Step 2: Strip Vexa-specific code

| Remove | Why |
|--------|-----|
| Internal hostnames (`meeting-api:8080`, `agent-api:8100`) | Internal coupling |
| `shared_models` imports (User, APIToken) | DB coupling |
| Claude/Anthropic credential mounting | Vexa-specific |
| `VEXA_USER_ID`, `BOT_CONFIG`, MinIO env injection | Domain leakage |
| Logger naming (`meeting_api.auth`) | Leaked internal names |
| Hardcoded profiles (meeting, agent, browser) | Move to config file |

### Step 3: Make pluggable

| Component | Current | Target |
|-----------|---------|--------|
| Auth | SQLAlchemy User/APIToken query | `typing.Protocol` interface, default: API key from env |
| Profiles | Hardcoded Python dict | `profiles.yaml` config file, hot-reloadable |
| Callbacks | Internal webhook URLs | `callback_url` parameter at creation time |
| State store | Redis (hardcoded) | Redis (default), keep as-is — standard enough |

### Step 4: Add distribution

```
pyproject.toml          # pip install runtime-api
Dockerfile              # docker run ghcr.io/vexa-ai/runtime-api
docker-compose.yml      # docker compose up (with Redis)
profiles.example.yaml   # example profiles
```

### Step 5: Vexa product consumes it

```yaml
# vexa/docker-compose.yml
runtime-api:
  image: ghcr.io/vexa-ai/runtime-api:v1.2.3
  volumes:
    - ./config/profiles.yaml:/app/profiles.yaml  # Vexa-specific profiles
meeting-api:
  environment:
    - RUNTIME_API_URL=http://runtime-api:8080
```

Vexa owns the profiles (meeting, agent, browser) as config. Runtime API is a generic service.

---

## Launch Playbook

### Timeline

| When | What |
|------|------|
| Week -4 | "Building in public" posts about the problem space |
| Week -3 | Blog: "Why there's no open-source Fly Machines" |
| Week -2 | README polish, record 30s terminal GIF, 5-10 early testers |
| Week -1 | Draft HN post, ProductHunt listing, Reddit posts |
| **Launch** | **Show HN (Sunday 11am ET)** |
| +1 day | ProductHunt (Tuesday) |
| +2 days | Reddit (r/selfhosted, r/devops, r/kubernetes) |
| +1 week | dev.to post, submit to awesome-* lists |
| +2 weeks | Full docs site (Starlight), first SDK |
| +2 months | CNCF Sandbox application |
| +6 months | Target: 1000 stars, 50 contributors, 3 production users |

### HN Launch

Title: `Show HN: [Name] – Open-source container lifecycle API (like Fly Machines, but self-hosted)`

Best timing: Sunday 11am ET (less competition) or weekday 8am ET.

Rules: Talk like a builder, no superlatives, respond to every comment in first 4 hours, have pristine GitHub repo (HN crowd reads code).

### Docs (minimum viable for launch)

1. **README** — one-line description, 30s GIF, quickstart (3 curl commands), features, comparison matrix
2. **Quickstart** — zero to running container in 60 seconds
3. **API Reference** — auto-generated from OpenAPI, every endpoint with curl example
4. **CONTRIBUTING.md**

### Community

- **GitHub Discussions** for structured conversation (features, RFCs, Q&A)
- **Discord** for real-time chat
- Respond to every issue/PR within 24 hours (the #1 adoption factor)
- 10-15 "good first issue" labels on real issues

---

## Competitive Positioning

| | Runtime API | Fly Machines | K8s Jobs | Docker Compose |
|---|---|---|---|---|
| REST API | Yes | Yes | Via kubectl | No |
| Container profiles | Yes | No | No | No |
| Idle management | Yes | Yes (auto-stop) | No | No |
| Lifecycle callbacks | Yes | No | Limited | No |
| Per-tenant concurrency | Yes | No | No | No |
| Self-hosted | Yes | No | Yes | Yes |
| Open source | Yes | No | Yes | Yes |
| No K8s required | Yes | Yes | No | Yes |
| Multi-backend | Yes | No | K8s only | Docker only |

---

## Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|-----------|
| AWS/GCP wrapping as managed service | Low (too niche for cloud providers) | Moat is in domain intelligence, not container CRUD |
| Recall.ai free-riding | Low | Moat is in meeting intelligence, not container orchestration |
| Fork risk | Zero | FSL from day 1, no license rug-pull |
| Maintenance burden | Medium | Community is a force multiplier, not a replacement for engineering |
| Internal/OSS roadmap divergence | High (killed Netflix OSS) | Upstream-first development — all changes go to OSS repo first |

### The Netflix Warning

When internal and OSS versions diverge, the OSS project dies (Hystrix, Conductor, Zuul). **Upstream-first development** prevents this: all Runtime API changes go to the OSS repo, Vexa product pins to published versions. Zero internal fork.

---

## Governance

| Stage | Model |
|-------|-------|
| 0-100 stars | BDFL (you). No committee. |
| 100-1000 stars | Company-controlled. GOVERNANCE.md. |
| 1000+ stars | Consider CNCF Sandbox if multi-company interest. |

Day 1 requirements:
- LICENSE (Apache-2.0)
- README.md
- CONTRIBUTING.md
- CODE_OF_CONDUCT.md (Contributor Covenant 2.1)
- DCO bot (not CLA)
- Issue templates
- 2+ people with admin access

---

## Decision Summary

| Question | Answer |
|----------|--------|
| Open-source Runtime API? | Yes — validated gap, proven COSS economics |
| When? | Before PMF (every successful COSS company did) |
| License? | Apache-2.0 (max adoption, Vexa depends on it) |
| Repo structure? | Separate repo from day 1 |
| Lead use case? | AI agent sandboxes |
| Name? | TBD — Drydock, Berth, or Cradle (check availability) |
| Cloud offering? | Within 12-18 months |
| Dependency direction? | Vexa pins to published Docker image, upstream-first development |
