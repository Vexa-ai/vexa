"""The account view every `/admin/users*` route answers with carries no stored credential, and a
masked read-back written to a credential field is no write. No database: the shapes themselves."""
from admin_api.app.main import UserResponse
from admin_api.app.platform_settings import validate_config_fields


def test_the_account_view_masks_every_stored_credential():
    data = {"webhook_secret": "whsec-0123456789", "webhook_url": "https://h.example.com/x",
            "model_prefs": {"api_key": "sk-abcdefgh1234", "model": "m"},
            "transcription_prefs": {"token": "t-xyz123456789", "url": "https://s.example.com"},
            "calendar_connections": [{"id": "c1", "ics_url": "https://cal.example.com/private-abc/f.ics"}],
            "calendar_ics_url": "https://x.example.com/private-def/f.ics"}
    view = UserResponse(id=1, email="a@b.c", max_concurrent_bots=1, data=data).model_dump()["data"]
    text = repr(view)
    for secret in ("whsec-0123456789", "sk-abcdefgh1234", "t-xyz123456789", "private-abc", "private-def"):
        assert secret not in text, secret
    assert view["model_prefs"] == {"model": "m", "api_key_set": True, "api_key": "********1234"}
    assert view["transcription_prefs"] == {"url": "https://s.example.com", "token_set": True, "token": "********6789"}
    assert view["calendar_connections"] == [{"id": "c1", "ics_url_set": True, "ics_url_masked": "cal.example.com/….ics"}]
    assert view["calendar_ics_url_set"] is True and view["webhook_url"] == "https://h.example.com/x"
    assert data["model_prefs"]["api_key"] == "sk-abcdefgh1234"              # the stored value is untouched


def test_a_masked_read_back_is_no_write():
    assert validate_config_fields({"api_key": "********1234", "model": "m"}) == {"model": "m"}
    assert validate_config_fields({"token": "********6789"}) == {}
    assert validate_config_fields({"api_key": "sk-new"}) == {"api_key": "sk-new"}
    assert validate_config_fields({"api_key": ""}) == {"api_key": ""}        # an empty write still clears
