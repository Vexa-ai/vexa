# deploy/bank-profile — the restricted reference environment (the release's bank-ready leg)

**Concern:** prove that Vexa installs and works where a bank's platform allows only what a bank
allows. It is an operator-run release gate: **bank-ready = `run.sh all` green on the exact release
sha.** It runs on one machine (a kind cluster of its own), in about half an hour, with no internet
reach from any Vexa Pod.

## What the profile enforces

| # | Constraint | How it is enforced here |
|---|---|---|
| 1 | The app's database user is not a superuser | The bank's own Postgres 16 (`bank-db`). Variant **a**: `vexa` has `CREATEROLE` only. Variant **b**: the DB team pre-creates `vexa_agent_sessions` and `vexa_crm`; `vexa` holds no role privilege. Both run the schema Jobs. |
| 2 | Non-root under a restricted policy, like OpenShift `restricted-v2` | Pod Security Admission `restricted` (enforce) on the namespace, plus a mutating webhook (`stub.py scc`) that does what restricted-v2 does: refuses privileged, host namespaces, hostPath, added capabilities, a uid outside the namespace's random range and any seccomp but `RuntimeDefault`; then injects that uid, `runAsNonRoot`, no escalation, `drop: [ALL]`, `RuntimeDefault` and `fsGroup`. Containers the chart renders read-only get a read-only root filesystem. The chart runs with `values-openshift.yaml` (`deliver: false`). |
| 3 | No egress except declared endpoints | Calico (kindnet enforces no NetworkPolicy). A platform `default-deny` egress in the namespace, and the chart's own policies with every public address excepted (`values-bank.yaml`): DNS, the release's own Pods, the bank's services and the API server (runtime) only. |
| 4 | A corporate CA | A self-signed CA, given as `global.extraCA`. The model, STT and IdP serve certificates it issued and nothing else. |
| 5 | OIDC without `email_verified` | An ADFS-like IdP (`stub.py idp`) whose ID token has no `email_verified`; the terminal runs with `requireEmailVerified: "0"` and `signinMethods: oidc`. |
| 6 | No Anthropic endpoint | `models.runner: openai-agent` against an OpenAI-compatible model stub in the cluster; STT is a stub too. |

The deployer is a ServiceAccount with exactly the Role in
[Required permissions](../../docs/docs/deployment-kubernetes.mdx) — nothing cluster-scoped.

## The journey (each a PASS/FAIL row in the report)

1. `helm install` as the deployer, `--wait`; the schema Jobs' outcome and the resulting roles.
2. Posture: every container's uid, escalation, capabilities, seccomp, read-only root fs; a privileged
   Pod refused; the deployer's reach; egress allowed to the bank's services and refused to public
   addresses; the bank's endpoints untrusted without the corporate CA.
3. First sign-in through the IdP from a workstation Pod (cookies, PKCE, the callback), and the user
   it created in admin-api.
4. A workspace (`POST /agent/workspace/init`) and one chat turn answered by the model stub.
5. A meeting bot (the mock bot, `mock:normal`) through `active` to `completed`, with a transcript
   segment.

## Run it

```bash
docker build -f core/meetings/services/bot/Dockerfile.mock -t vexaai/mock-bot:$TAG .   # plus the release images at $TAG
IMAGE_TAG=$TAG BANK_CLUSTER=bank-profile deploy/bank-profile/run.sh all
# or: make bank-profile IMAGE_TAG=$TAG
```

`run.sh` with no argument lists the steps (`up`, `db a|b`, `install a|b`, `check`, `journey`,
`uninstall`, `down`). `KEEP=1` leaves the cluster up for debugging. It checks that its docker subnet
and the Pod/Service ranges are free before it creates anything, and `down` removes only its own
cluster and network.

## Files

| File | Role |
|---|---|
| `run.sh` | the whole gate |
| `values-bank.yaml` | the operator's values, layered after `values-openshift.yaml` |
| `manifests/platform.yaml` | what the cluster admin sets up: namespaces, PSA labels, the restricted-v2 webhook, egress default-deny, the deployer |
| `manifests/bank-ext.yaml` | the bank's services: model, STT, IdP, Postgres, and the workstation |
| `stub/stub.py` | the model, STT, IdP and restricted-v2 stand-ins (stdlib Python, TLS) |
| `stub/journey.py` | the person's side of the journey, run from the workstation |

## What it does not enforce

- **Not OpenShift.** The SCC is emulated by a webhook; SELinux MCS labels, the OpenShift router and
  `oc` are absent. A real OpenShift run is still worth doing before a bank's first install.
- **One node.** The workspace store is `ReadWriteOnce` here; on a multi-node cluster it must be RWX
  (`values-openshift.yaml`).
- **The mock bot.** No browser, no real meeting, no audio through STT: the bot's join, capture and
  transcription paths are not exercised (the STT stub is only reached for TLS and egress).
- **S3 is plain HTTP**, in the bank's namespace, with one key for both buckets.
- **Flows is off** (it needs `CREATEDB` or a pre-created database; see the install guide).
