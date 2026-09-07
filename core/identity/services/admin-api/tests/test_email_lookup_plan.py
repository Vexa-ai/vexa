"""A20 — the case-folded email lookups get an ORDER BY, an index, and a folded write.

The fold that made `Anna.Smith@acme.test` and `anna.smith@acme.test` one account (R-B08) shipped
with three things missing, and each is a different kind of wrong:

  * **no ORDER BY.** On an instance that ALREADY holds case-variant duplicates — which is exactly
    the estate the fold exists for — more than one row matches and `.first()` returns whichever the
    plan reached first. The two lookups can disagree, and one lookup can disagree with itself after
    a vacuum, so "which account is this person" has no stable answer.
  * **no fold on write.** Two concurrent `POST /admin/users` in different cases both miss the
    lookup and both insert. A read-side fold cannot serialise them; only a constraint can.
  * **no index on `lower(email)`.** Both lookups are sequential scans on `users`, on the sign-in
    path.

Offline — the SQL the routes emit, the normaliser, and the index in the model metadata. No docker.
The DB-backed behaviour (duplicates, the race, existing rows untouched) is in
`test_email_case_folding.py`.
"""
from __future__ import annotations

import inspect

from admin_api.app.main import create_app, normalise_email
from admin_api.schema.models import User


def test_new_addresses_are_folded_and_trimmed():
    assert normalise_email("Anna.Smith@Acme.Test") == "anna.smith@acme.test"
    assert normalise_email("  anna@acme.test \n") == "anna@acme.test"
    assert normalise_email("") == ""
    assert normalise_email(None) == ""


def test_lower_email_is_indexed():
    """`lower(email) = …` cannot use the plain `email` index — the predicate is an expression."""
    names = {ix.name for ix in User.__table__.indexes}
    assert "ix_users_email_lower" in names
    index = next(ix for ix in User.__table__.indexes if ix.name == "ix_users_email_lower")
    assert "lower(email)" in str(index.expressions[0])


def test_the_email_index_is_not_unique_and_says_why():
    """DELIBERATE, and the reasoning has to survive in the tree rather than in a review.

    A UNIQUE `lower(email)` is the invariant we want; shipping it in the model would make every
    instance that holds duplicates REFUSE TO BOOT, because `_sync_indexes` fails closed on a unique
    index it cannot build (#1186). That trades a data defect for an outage. The upgrade is an
    operator step after reconciliation — MIGRATION-0007 carries the SQL."""
    import pathlib

    index = next(ix for ix in User.__table__.indexes if ix.name == "ix_users_email_lower")
    assert index.unique is False

    doc = (pathlib.Path(__file__).resolve().parents[1]
           / "src/admin_api/schema/MIGRATION-0007-users-email-lower.md")
    assert doc.exists(), "a non-unique index for a unique invariant needs its migration note"
    body = doc.read_text()
    assert "CREATE UNIQUE INDEX CONCURRENTLY uq_users_email_lower" in body
    assert "HAVING count(*) > 1" in body          # how an operator finds what blocks it


def _handler(app, method, path):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in (getattr(route, "methods", None) or ()):
            return route.endpoint
    raise AssertionError(f"{method} {path} is not routed")


def test_both_case_folded_lookups_order_by_id():
    """OLDEST WINS, and it must be the SAME rule in both places: two folding lookups that disagree
    about which duplicate row is the person put the desk on one account and the meetings on
    another."""
    app = create_app()
    for method, path in (("POST", "/admin/users"), ("GET", "/admin/users/email/{email}")):
        src = inspect.getsource(_handler(app, method, path))
        assert "func.lower(User.email)" in src, f"{method} {path} stopped folding case"
        assert ".order_by(User.id)" in src, (
            f"{method} {path} folds case with no ORDER BY — on an instance holding case-variant "
            "duplicates it returns whichever row the plan reached first")


def test_the_create_path_folds_on_write_and_survives_the_losing_race():
    app = create_app()
    src = inspect.getsource(_handler(app, "POST", "/admin/users"))
    assert "normalise_email(user_in.email)" in src, (
        "a new row stored in the typed case leaves two concurrent creates both inserting")
    assert "IntegrityError" in src, (
        "the fold on write turns the race into a constraint violation — unhandled, that is a 500 "
        "on somebody's sign-in instead of a 200 on the account that won")


def test_the_migration_note_is_reachable_from_the_model():
    """The index carries an explanation a reader hits before they 'fix' it to unique."""
    import pathlib

    models = (pathlib.Path(__file__).resolve().parents[1]
              / "src/admin_api/schema/models.py").read_text()
    assert "MIGRATION-0007-users-email-lower.md" in models


def test_lower_email_index_ddl_is_what_postgres_will_build():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    index = next(ix for ix in User.__table__.indexes if ix.name == "ix_users_email_lower")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "CREATE INDEX ix_users_email_lower ON users (lower(email))" in " ".join(ddl.split())
