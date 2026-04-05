# Model Split Patterns: Per-Service Ownership with Shared PostgreSQL

Research for Step 6 of the [proposed architecture](../architecture-proposed.md) migration path: splitting `shared_models/models.py` so each service owns its tables while sharing one Postgres database with cross-service foreign keys.

## Current State

One file (`libs/shared-models/shared_models/models.py`) defines everything:
- `Base = declarative_base()` — single registry, single MetaData
- `User`, `APIToken` — auth domain (admin-api should own)
- `Meeting`, `MeetingSession`, `Transcription`, `Recording`, `MediaFile`, `CalendarEvent` — meeting domain (meeting-api should own)
- Agent tables TBD (agent-api should own)

One Alembic migration chain: 5 migrations in `libs/shared-models/alembic/versions/`, linear from `dc59a1c03d1f` → `c8d9e0f1a2b3`.

All services import `from shared_models.models import ...`.

---

## 1. How Other Frameworks Handle This

### Django

Django's approach is the most mature and directly relevant:

- **Each Django app owns its models** in its own `models.py` (or `models/` package)
- **Cross-app ForeignKeys use lazy string references**: `ForeignKey('accounts.User')` — resolved at migration time, not import time
- **Each app has its own `migrations/` directory** with independently generated migration files
- **`makemigrations` auto-detects cross-app FK dependencies** and adds them to the migration's `dependencies` list
- **Migration execution order follows the dependency graph**, not alphabetical order
- **Circular FK dependencies**: Django auto-resolves by splitting one FK creation into a separate migration (CREATE TABLE first, ALTER TABLE ADD FK second)

Key takeaway: **Django proves that per-app model ownership with cross-app FKs in a shared database works at scale.** Sentry's `src/sentry/models/` has 122+ model files organized this way.

### Rails

Rails engines achieve the same pattern:
- Each engine has its own models, namespaced (e.g., `Analytics::LogStat`)
- Cross-engine associations use `class_name: 'OtherEngine::Author'`
- Migrations can live in the engine or be copied to the host app

### Sentry (Real-World Django at Scale)

Sentry has 122+ individual model files in `src/sentry/models/` with 5 subdirectories. Their `__init__.py` uses barrel exports (`from .module import *`). Notably, Sentry is a **single Django app** with file-level organization, not multi-app — the models all share `app_label = 'sentry'`. The proprietary `getsentry` layer extends via signals and feature flags, not cross-app FKs.

---

## 2. SQLAlchemy Multi-Package Patterns

### The Two Things That Matter

SQLAlchemy has two resolution namespaces:

| Feature | Resolves Against | Uses Name | Example |
|---------|-----------------|-----------|---------|
| `ForeignKey("users.id")` | **MetaData** (table collection) | Table name (`__tablename__`) | `ForeignKey("users.id")` |
| `relationship("User")` | **Registry** (class collection) | Class name | `relationship("User")` |

`declarative_base()` bundles both: `Base.metadata` (MetaData) + `Base.registry` (registry). Every class inheriting from `Base` is registered in both.

### Pattern A: Shared Base (Recommended)

One package exports `Base`, all others import it and subclass:

```python
# Package: admin-models (or keep in shared-models)
from sqlalchemy.orm import declarative_base
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    ...

# Package: meeting-api (DIFFERENT package)
from admin_models import Base  # SAME Base!

class Meeting(Base):
    __tablename__ = "meetings"
    user_id = Column(Integer, ForeignKey("users.id"))  # ✅ works (same MetaData)
    user = relationship("User")                         # ✅ works (same registry)
```

**This is what we already do** — `shared_models.models` exports `Base` and `calendar-service` imports from it.

### Pattern B: Shared Registry, Separate Bases (Advanced)

```python
from sqlalchemy.orm import registry, DeclarativeBase

shared_registry = registry()

class BaseA(DeclarativeBase):
    registry = shared_registry

class BaseB(DeclarativeBase):
    registry = shared_registry  # same registry → same MetaData
```

SQLAlchemy 2.0+ supports this but it's fragile and rarely used outside SQLAlchemy + SQLModel integration scenarios (see sqlalchemy/sqlalchemy Discussion #7711).

### What Breaks

Two separate `Base` objects with independent MetaData/registry:

```python
Base1 = declarative_base()
Base2 = declarative_base()  # DIFFERENT MetaData, DIFFERENT registry

class User(Base1): ...
class Meeting(Base2):
    user_id = Column(Integer, ForeignKey("users.id"))  # ❌ FAILS
    user = relationship("User")                         # ❌ FAILS
```

Both `ForeignKey` and `relationship` fail because `Base2.metadata` has no table "users" and `Base2.registry` has no class "User".

### String-Based ForeignKey: Works Without Import

`ForeignKey("users.id")` uses the **table name** (string), not the Python class. Resolution is lazy — it happens when MetaData is first consulted (at `create_all()` time or first query). The referenced model does NOT need to be imported at definition time, but **must have been imported before the MetaData is used**.

Our code already uses this pattern: `models.py:38` has `ForeignKey("users.id")` on the Meeting class.

### String-Based Relationship: Needs Same Registry

`relationship("User")` uses the **class name** (string). The class must be in the same `registry`, but does NOT need to be imported in the file defining the relationship. It just needs to have been imported **somewhere** before mapper initialization.

**This is the coupling to decide on.** If meeting-api defines `meeting.user = relationship("User")`, the `User` class must be in the same registry. This means meeting-api must import `Base` from wherever `User` is defined.

---

## 3. The FK Problem: Recommendations

### ForeignKey Only (DB Integrity, No Code Coupling)

The proposed architecture says: *"FK: meetings.user_id → users.id (DB level only). No cross-schema model imports in code."*

This works if meeting-api's models use `ForeignKey("users.id")` (string) and **drop the `relationship("User")`** on Meeting:

```python
# meeting-api/models.py
from shared_base import Base  # minimal package: just Base + nothing else

class Meeting(Base):
    __tablename__ = "meetings"
    user_id = Column(Integer, ForeignKey("users.id"))  # ✅ DB constraint
    # NO relationship("User") — no ORM navigation to User
```

The `ForeignKey("users.id")` string will resolve at runtime as long as the "users" table exists in the same MetaData. Since all models share `Base`, the MetaData includes all tables that have been imported.

**But wait** — if meeting-api doesn't import `User`, then "users" won't be in the MetaData at definition time. The resolution is lazy, but the table must be registered before `create_all()` or the first query.

### Three Options for the Shared Base Problem

#### Option 1: Tiny `shared-base` Package (Simplest)

```
packages/
  shared-base/          # exports ONLY Base = declarative_base()
    shared_base/
      __init__.py       # from .base import Base
      base.py           # Base = declarative_base()
  admin-models/         # User, APIToken — imports Base from shared-base
  meeting-models/       # Meeting, Transcription, etc. — imports Base from shared-base
  agent-models/         # AgentSession, Workspace — imports Base from shared-base
```

Each service imports its own models package + shared-base. The `init_db()` function in each service must import ALL model packages to populate MetaData before calling `create_all()`.

**Pros**: Clean separation. Each package is independently publishable.
**Cons**: Extra package. Startup code must import all model packages.

#### Option 2: Keep `shared-models` as the Base + Admin Models

```
packages/
  shared-models/        # Base + User + APIToken (renamed to admin-models later)
  meeting-api/
    models.py           # imports Base from shared-models, defines Meeting etc.
  agent-api/
    models.py           # imports Base from shared-models, defines AgentSession etc.
```

This is the path of least resistance. `shared-models` already defines `Base` and the auth models. Other packages extend it.

**Pros**: Minimal change. Already works today.
**Cons**: meeting-api depends on shared-models (which includes User), even though it only needs Base.

#### Option 3: Each Package Has Its Own Base, Wire at Startup (Most Decoupled)

```python
# meeting-api/models.py
from sqlalchemy.orm import DeclarativeBase

class MeetingBase(DeclarativeBase):
    pass  # own registry, own MetaData

class Meeting(MeetingBase):
    __tablename__ = "meetings"
    user_id = Column(Integer, ForeignKey("users.id"))  # will fail unless wired
```

At startup, the orchestrator wires the MetaData objects together. This is complex and fragile — **not recommended**.

### Recommendation: Option 2 (Pragmatic) → Option 1 (Later)

Start with Option 2: keep `shared-models` as the Base provider + admin models. Meeting-api and agent-api import `Base` from it and define their own models. This is a rename and split of the current `models.py`, not a rewrite.

Later, extract `Base` into a tiny `shared-base` package if true independence is needed for standalone publishing.

### What About `relationship()` Back-References?

Current code has bidirectional relationships:
- `User.meetings = relationship("Meeting", back_populates="user")` (in User model)
- `Meeting.user = relationship("User", back_populates="meetings")` (in Meeting model)

After splitting:
- **Drop `User.meetings`** — admin-api doesn't need to navigate to meetings
- **Keep `Meeting.user_id` as ForeignKey** — DB integrity
- **Drop `Meeting.user` relationship** — meeting-api gets `user_id` from headers, never loads the User ORM object
- **Keep all intra-domain relationships** — `Meeting.transcriptions`, `Recording.media_files`, etc.

This matches the proposed architecture: *"meeting-api reads headers, never queries users table directly."*

---

## 4. Alembic Multi-Directory Migrations

### Current State

Single linear chain, 5 migrations, one `alembic.ini`, one `env.py`:
```
dc59a1c03d1f (base) → 5befe308fa8b → a1b2c3d4e5f6 → b7f3a2e91c4d → c8d9e0f1a2b3 (head)
```

### Pattern A: Shared `alembic_version` Table with Branch Labels (Recommended)

Multiple branches share a single `alembic_version` table. Each branch gets its own row.

```ini
# alembic.ini
[alembic]
script_location = alembic
version_path_separator = os
version_locations =
    %(here)s/libs/shared-models/alembic/versions
    %(here)s/services/meeting-api/alembic/versions
    %(here)s/services/agent-api/alembic/versions
```

Create independent branches:
```bash
# Existing chain gets a branch label
# Edit dc59a1c03d1f to add: branch_labels = ('shared_models',)

# New branch for meeting-api
alembic revision -m "meeting-api base" \
  --head=base \
  --branch-label=meeting_api \
  --version-path=services/meeting-api/alembic/versions

# New branch for agent-api
alembic revision -m "agent-api base" \
  --head=base \
  --branch-label=agent_api \
  --version-path=services/agent-api/alembic/versions
```

Cross-branch FK dependencies:
```python
# meeting-api migration that needs users table
revision = 'meeting_001'
down_revision = 'meeting_base'
depends_on = ('c8d9e0f1a2b3',)  # shared-models migration that creates users
```

### Pattern B: Separate `alembic_version` Tables

Each service tracks its own version:
```python
# env.py per service
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    version_table="alembic_version_meeting_api"
)
```

Alembic maintainer (CaselIT, Discussion #1522) recommends this when services have truly separate schemas. But warns: "If the two services share both the schema and the table I would suggest you to rethink your schema."

### Transition Steps (From Current → Branched)

1. **Add branch label to existing base migration** (`dc59a1c03d1f`):
   ```python
   branch_labels = ('shared_models',)
   ```

2. **Update `alembic.ini`** with `version_locations` pointing to all migration directories

3. **Create new branch bases** with `--head=base --branch-label=<name>`

4. **Stamp existing databases** — they already have `c8d9e0f1a2b3` in `alembic_version`. After adding branches, stamp the new branch bases:
   ```bash
   alembic stamp meeting_api@head  # mark as "already applied"
   ```
   Now `alembic_version` has two rows.

5. **`alembic upgrade heads`** (plural) to apply all branches going forward

### Critical: `include_object` Filtering

Without filtering, each branch's `--autogenerate` tries to DROP tables it doesn't own:

```python
# Shared env.py or per-branch env.py
SHARED_TABLES = {"users", "api_tokens", "alembic_version"}
MEETING_TABLES = {"meetings", "meeting_sessions", "transcriptions", "recordings", "media_files", "calendar_events"}
AGENT_TABLES = {"agent_sessions", "workspaces"}

def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        if reflected and name not in MY_TABLES:
            return False  # don't touch tables I don't own
    return True

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    include_object=include_object
)
```

### Ordering and Concurrency

- `alembic upgrade heads` applies all pending migrations across all branches
- Within a branch, order is guaranteed. Between independent branches, order is non-deterministic
- Use `depends_on` for explicit cross-branch ordering (e.g., meetings depends on users)
- **Never run `alembic upgrade` concurrently** — Alembic doesn't lock `alembic_version`. Add PostgreSQL advisory locks if services run migrations at startup:

```python
def run_migrations_online():
    with connectable.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(12345)"))
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(12345)"))
```

### Real-World: OpenStack Neutron Expand/Contract

Neutron splits migrations into two branches:
- **expand**: additive-only (CREATE TABLE, ADD COLUMN) — safe while service runs
- **contract**: destructive (DROP COLUMN, data migration) — requires maintenance window

This is worth considering if we ever need zero-downtime migrations.

### Recommendation for Vexa

**Use Pattern A** (shared `alembic_version` table with branch labels):
- Services share FKs, so `depends_on` is valuable
- Single `alembic upgrade heads` deploys everything
- All migrations visible in one place for debugging
- `include_object` filtering prevents cross-contamination in autogenerate

---

## 5. Concrete Split Plan

### File-Level Changes

**Before** (one file):
```
libs/shared-models/shared_models/
  models.py     # Base + User + APIToken + Meeting + Transcription + ...
  database.py   # init_db(), session factory
```

**After** (split by domain):
```
libs/shared-models/shared_models/
  models.py     # Base + User + APIToken only
  database.py   # init_db(), session factory (imports all model packages for MetaData)

services/meeting-api/meeting_api/
  models.py     # Meeting, MeetingSession, Transcription, Recording, MediaFile, CalendarEvent
                # imports Base from shared_models.models

services/agent-api/agent_api/
  models.py     # AgentSession, Workspace (new tables)
                # imports Base from shared_models.models
```

### Model Changes

**User (stays in shared-models):**
```python
class User(Base):
    __tablename__ = "users"
    # ... all columns stay ...

    # REMOVE these cross-domain relationships:
    # meetings = relationship("Meeting", back_populates="user")  ← DELETE
    api_tokens = relationship("APIToken", back_populates="user")  # keep (same domain)
```

**Meeting (moves to meeting-api):**
```python
from shared_models.models import Base  # import Base only

class Meeting(Base):
    __tablename__ = "meetings"
    user_id = Column(Integer, ForeignKey("users.id"))  # keep FK (string-based)
    # REMOVE: user = relationship("User", back_populates="meetings")  ← DELETE
    transcriptions = relationship("Transcription", back_populates="meeting")  # keep (same domain)
    sessions = relationship("MeetingSession", back_populates="meeting")       # keep
    recordings = relationship("Recording", back_populates="meeting")          # keep
```

**Recording (moves to meeting-api):**
```python
class Recording(Base):
    __tablename__ = "recordings"
    user_id = Column(Integer, ForeignKey("users.id"))  # keep FK (string-based)
    meeting_id = Column(Integer, ForeignKey("meetings.id"))  # keep FK (same domain)
    # REMOVE: user = relationship("User")  ← DELETE
    meeting = relationship("Meeting", back_populates="recordings")  # keep (same domain)
```

**CalendarEvent (moves to meeting-api or stays):**
```python
class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    user_id = Column(Integer, ForeignKey("users.id"))  # keep FK
    meeting_id = Column(Integer, ForeignKey("meetings.id"))  # keep FK
    # REMOVE: user = relationship("User")  ← DELETE
    # REMOVE: meeting = relationship("Meeting")  ← DELETE (or keep if same domain)
```

### Code That Needs Updating

Any service code that navigates `meeting.user` or `recording.user` must change to use `user_id` integer directly. Since the proposed architecture has the gateway injecting `X-User-ID` headers, meeting-api never needs to load the User object — it already has the `user_id` from the request.

### Migration Transition

No schema changes needed — the database tables and FKs don't change. This is purely a code reorganization. The Alembic branching (section 4) is about future migrations, not retroactive changes.

---

## 6. Gotchas and Dead Ends

### Gotcha: Model Import Order at Startup

All model modules must be imported before `Base.metadata.create_all()` or the first query. If meeting-api starts up and only imports its own `models.py` but not `shared_models.models`, the MetaData will be missing the "users" table and `ForeignKey("users.id")` will fail to resolve.

**Fix**: Each service's `init_db()` must import all model packages:
```python
async def init_db():
    import shared_models.models   # registers User, APIToken
    import meeting_api.models     # registers Meeting, Transcription, etc.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
```

### Gotcha: `create_all` Creates Everything

`Base.metadata.create_all()` creates ALL tables registered in that MetaData, not just the ones from the importing package. If meeting-api imports `Base` from shared-models, and shared-models defines User, then meeting-api's `create_all()` will also create the users table.

This is usually fine (it's idempotent with `checkfirst=True`). But if you want strict separation, skip `create_all()` and rely on Alembic only.

### Gotcha: Circular Imports

If admin-models has `User.meetings = relationship("Meeting")` and meeting-models has `Meeting.user = relationship("User")`, you get circular imports. The fix is the recommendation above: **drop cross-domain relationships entirely**.

### Dead End: Separate Databases

Don't split into separate PostgreSQL databases per service. Cross-database FKs don't work in Postgres. You'd need application-level consistency, which is far more complex than the current pattern. The Alembic maintainer explicitly warns against this for shared-FK scenarios.

### Dead End: SQLAlchemy `automap_base()`

Some guides suggest using `automap_base()` to reflect tables at runtime, avoiding model imports. This works but loses all column type hints, custom properties, and hybrid attributes. Not worth it.

### Dead End: Separate MetaData Objects + Manual Wiring

You could create separate MetaData per package and merge them at startup with `MetaData.reflect()`. This is fragile, poorly documented, and breaks autogenerate. Stick with shared Base.

---

## 7. Summary Decision Matrix

| Question | Answer |
|----------|--------|
| Shared Base or separate? | **Shared Base** — one `declarative_base()`, all packages import it |
| Where does Base live? | **shared-models** (later: extract to shared-base if publishing standalone) |
| ForeignKey to other domains? | **String-based**: `ForeignKey("users.id")` — no model import |
| relationship() to other domains? | **Remove them** — use `user_id` integer, not ORM navigation |
| Intra-domain relationships? | **Keep them** — `Meeting.transcriptions`, `Recording.media_files`, etc. |
| Alembic approach? | **Branched** — one `alembic_version` table, branch labels, `depends_on` |
| Migration transition? | **Stamp + branch** — no schema changes, just reorganize migration directories |
| `create_all` vs Alembic-only? | **Alembic-only** in production, `create_all` acceptable for dev/test |
| Advisory locks? | **Yes** — add `pg_advisory_lock` if multiple services run migrations at startup |

## Sources

- [SQLAlchemy 2.1: Declarative Mapping Styles](https://docs.sqlalchemy.org/en/21/orm/declarative_styles.html)
- [SQLAlchemy 2.1: Defining Constraints and Indexes](https://docs.sqlalchemy.org/en/21/core/constraints.html)
- [SQLAlchemy Discussion #7711: Share class registry among multiple declarative base](https://github.com/sqlalchemy/sqlalchemy/discussions/7711)
- [SQLAlchemy Discussion #10761: ORM mapping registry relationship to metadata](https://github.com/sqlalchemy/sqlalchemy/discussions/10761)
- [SQLAlchemy Issue #5042: ForeignKey documentation — table name vs class name](https://github.com/sqlalchemy/sqlalchemy/issues/5042)
- [Alembic: Working with Branches](https://alembic.sqlalchemy.org/en/latest/branches.html)
- [Alembic Discussion #1522: Separate migrations for shared DB](https://github.com/sqlalchemy/alembic/discussions/1522)
- [Alembic Issue #777: Modular system with multiple bases](https://github.com/sqlalchemy/alembic/issues/777)
- [Django Migrations Documentation](https://docs.djangoproject.com/en/6.0/topics/migrations/)
- [Sentry Models Directory](https://github.com/getsentry/sentry/tree/master/src/sentry/models)
- [Sharing SQLAlchemy models between Python projects (Jacob Salway)](https://www.jacobsalway.com/blog/sharing-sqlalchemy-models-between-python-projects)
- [Caveat With Model Modules and SQLAlchemy 2+](https://iamjeremie.me/post/2024-07/caveat-with-model-modules-and-sqlalchemy-20/)
- [SQLModel: Code Structure](https://sqlmodel.tiangolo.com/tutorial/code-structure/)
- [OpenStack Neutron: Alembic Migrations (Expand/Contract)](https://docs.openstack.org/neutron/latest/contributor/alembic_migrations.html)
- [Modular Alembic Migrations (Medium)](https://medium.com/@karuhanga/of-modular-alembic-migrations-e94aee9113cd)
- [Rails Engines Guide](https://edgeguides.rubyonrails.org/engines.html)
