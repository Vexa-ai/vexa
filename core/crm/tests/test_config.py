"""crm-api's config.v1 declaration and its boot preflight (``python -m crm`` runs it before serving)."""
import pytest

from crm import config_preflight as cp

REQUIRED = {'CRM_DATABASE_URL': 'postgresql+pg8000://vexa_crm:pw@db:5432/vexa', 'CRM_ORG_ID': 'acme',
            'ADMIN_API_URL': 'http://admin-api:8001', 'INTERNAL_API_SECRET': 'a' * 64}


def test_boot_refuses_naming_every_missing_required_key():
    with pytest.raises(cp.ConfigError) as refused:
        cp.preflight({})
    for key in REQUIRED:
        assert key in str(refused.value)


def test_boot_refuses_a_published_internal_secret_without_echoing_it():
    with pytest.raises(cp.ConfigError) as refused:
        cp.preflight({**REQUIRED, 'INTERNAL_API_SECRET': 'vexa-internal-secret'})
    assert 'INTERNAL_API_SECRET' in str(refused.value)
    assert 'vexa-internal-secret' not in str(refused.value)


def test_crm_api_boots_without_the_outbox_keys_and_reports_the_seal():
    """The flows door, its key and the seal belong to the outbox: crm-api boots without them."""
    rows = cp.preflight(dict(REQUIRED))['capabilities']
    assert rows['fact_seal_key']['state'] == cp.NOT_CONFIGURED
    rows = cp.preflight({**REQUIRED, 'VEXA_FACT_SEAL_KEY_FILE': '/run/vexa-fact-seal/key.pem'})['capabilities']
    assert rows['fact_seal_key']['state'] == cp.CONFIGURED


def test_the_outbox_keys_are_declared_publish_edges_and_secret_where_they_are_credentials():
    keys = {k['key']: k for k in cp.load_declaration()['keys']}
    assert keys['FLOWS_API_URL']['class'] == 'publish-edge'
    assert keys['FLOWS_API_URL']['publishes_events'] == ['crm.record.changed']
    assert keys['FLOWS_PUBLISH_KEY']['secret'] is True
    assert 'FLOWS_API_KEY' not in keys, 'the outbox never holds flows\' operator key'
    assert not keys['VEXA_FACT_SEAL_KEY_FILE'].get('secret')   # a path; the key is the file it names
    assert 'VEXA_FACT_SEAL_SECRET' not in keys, 'no shared seal secret: each producer holds its own key'
