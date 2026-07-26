"""Regression: Teams `…/v2#/meet/<digits>` links must get distinct canonical identities.

The sealed predicate (`meeting_url_matches_platform`) admits the Teams web-client form
`https://<host>/v2#/meet/<digits>`, where the meeting id lives in the URL FRAGMENT. urlparse
drops the fragment from `.path`, so before the fix every such meeting canonicalized to
`https://<host>/v2` and distinct meetings collided on one identity — and on the derived
`native_meeting_id` — within a tenant.
"""
import hashlib

from meeting_api.bot_spawn.url_validation import canonical_meeting_identity


def _native_meeting_id(tenant_id: str, identity: str) -> str:
    # Mirrors zaki_control.router: native_meeting_id = "zaki-" + sha256(f"{tenant}\0{identity}").
    return "zaki-" + hashlib.sha256(f"{tenant_id}\0{identity}".encode()).hexdigest()


def test_distinct_v2_fragment_teams_meetings_do_not_collide():
    _, ident_a = canonical_meeting_identity(
        "https://teams.microsoft.com/v2#/meet/1234567890", platform="teams"
    )
    _, ident_b = canonical_meeting_identity(
        "https://teams.microsoft.com/v2#/meet/9999999999", platform="teams"
    )
    assert ident_a != ident_b
    assert _native_meeting_id("tenant-1", ident_a) != _native_meeting_id("tenant-1", ident_b)


def test_same_v2_fragment_teams_meeting_is_stable_under_decoration():
    _, ident = canonical_meeting_identity(
        "https://teams.microsoft.com/v2#/meet/1234567890", platform="teams"
    )
    # passcode + trailing slash on the fragment, and host casing + trailing slash on the path,
    # all describe the SAME meeting and must canonicalize identically.
    for variant in (
        "https://teams.microsoft.com/v2#/meet/1234567890/?p=passcode",
        "https://Teams.Microsoft.com/v2/#/meet/1234567890",
    ):
        _, ident_variant = canonical_meeting_identity(variant, platform="teams")
        assert ident_variant == ident


def test_other_providers_canonicalization_unchanged():
    assert canonical_meeting_identity(
        "https://meet.google.com/abc-defg-hij", platform="google_meet"
    )[1] == "https://meet.google.com/abc-defg-hij"
    assert canonical_meeting_identity(
        "https://acme.zoom.us/j/98765432101?pwd=x", platform="zoom"
    )[1] == "https://acme.zoom.us/j/98765432101"
    assert canonical_meeting_identity(
        "https://meet.jit.si/ZakiRoom42", platform="jitsi"
    )[1] == "https://meet.jit.si/ZakiRoom42"
    # Teams classic short link keeps its id in the PATH — must be untouched by the fragment fold.
    assert canonical_meeting_identity(
        "https://teams.microsoft.com/meet/123456789012?p=x", platform="teams"
    )[1] == "https://teams.microsoft.com/meet/123456789012"


def test_authority_hash_binds_the_navigation_url_not_the_dedup_identity():
    """Regression: a capture grant must hash the URL the bot NAVIGATES to.

    ``canonical_meeting_identity`` returns ``(navigation_url, identity)`` where the
    navigation URL keeps provider query parameters and the identity deliberately drops
    them. ``zaki_control.router`` mints ``authority.meeting_url_sha256`` and
    ``capture.service`` re-derives it from ``validate_meeting_url(meeting_url)`` — the
    navigation URL — so hashing the IDENTITY when minting made the two disagree for any
    URL carrying a query, raising AUTHORITY_SCOPE_MISMATCH and surfacing as a generic
    422 invalid_request. That denied every Zoom "Copy Invite Link" (always ``?pwd=``)
    and every enterprise Teams ``/l/meetup-join/...?context=`` link, while query-less
    Google Meet links matched and hid the bug.
    """
    query_bearing = [
        ("zoom", "https://us05web.zoom.us/j/84335626851?pwd=abcDEF123"),
        ("teams", "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0?context=%7b%22Tid%22%3a%22x%22%7d"),
    ]
    for platform, url in query_bearing:
        navigation_url, identity = canonical_meeting_identity(url, platform=platform)
        minted = hashlib.sha256(navigation_url.encode()).hexdigest()
        verified = hashlib.sha256(navigation_url.encode()).hexdigest()
        assert minted == verified, f"{platform} authority hash must match the capture-service hash"
        # The identity is genuinely different — that is what made the old binding fail.
        assert hashlib.sha256(identity.encode()).hexdigest() != minted, (
            f"{platform} fixture lost its query string; it no longer covers the regression"
        )

    # A query-less URL is unaffected either way — this is why the bug stayed hidden.
    navigation_url, identity = canonical_meeting_identity(
        "https://meet.google.com/abc-defg-hij", platform="google_meet"
    )
    assert hashlib.sha256(navigation_url.encode()).hexdigest() == hashlib.sha256(identity.encode()).hexdigest()


def test_zoomgov_passes_the_same_host_gate_as_the_sealed_predicate():
    """The sealed zaki-control.v1 predicate admits zoomgov.com; bot_spawn must agree.

    Same gate-disagreement class the Teams host helper already documents: the sealed gate
    admitted US-gov Zoom and this one refused it, producing a generic 422.
    """
    for url in (
        "https://agency.zoomgov.com/j/98765432101",
        "https://www.zoomgov.com/j/98765432101",
        "https://us05web.zoom.us/j/84335626851",
    ):
        navigation_url, identity = canonical_meeting_identity(url, platform="zoom")
        assert navigation_url and identity
