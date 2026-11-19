"""The outbound-mail credential (PRD 0.13.4 S2): flows, the deployment's one mail sender, reads the
relay password the install names (`VEXA_MAIL_SMTP_PASSWORD_REF=broker:mail/<name>`) — only
`service/mail/<name>`, only with the flows broker key (the sensors role), audited, never a person's
connection — and the operator stores it from stdin."""
import io

from credential_broker import mail_secret
from credential_broker.routes_mail import record_path
from credential_broker.routes_signals import record_path as signals_path

PATH = "/api/internal/mail/smtp-credential"


def test_the_flows_key_reads_the_named_mail_credential(signed, store):
    store.put(record_path("smtp"), {"owner": "mail", "value": "APP-PASSWORD"})
    r = signed("sensors", "POST", PATH, {"name": "smtp"}, actor="flows-mail")
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "smtp", "value": "APP-PASSWORD"}


def test_a_missing_credential_is_404(signed):
    assert signed("sensors", "POST", PATH, {"name": "nothing"}, actor="flows-mail").status_code == 404


def test_every_other_role_is_refused(signed, store):
    store.put(record_path("smtp"), {"owner": "mail", "value": "APP-PASSWORD"})
    for role in ("agent", "human", "git", "bots", "signals"):
        r = signed(role, "POST", PATH, {"name": "smtp"}, actor="flows-mail")
        assert r.status_code in (401, 403), role
        assert "APP-PASSWORD" not in r.text


def test_a_name_cannot_reach_outside_the_mail_namespace(signed, store):
    store.put(signals_path("openrouter"), {"owner": "signals", "value": "MODEL-SECRET"})
    for name in ("../signals/openrouter", "pat/u1", "Smtp", "", "a" * 70):
        r = signed("sensors", "POST", PATH, {"name": name}, actor="flows-mail")
        assert r.status_code == 422, name
    # and the signal model's key is not a mail credential, whatever its name
    r = signed("sensors", "POST", PATH, {"name": "openrouter"}, actor="flows-mail")
    assert r.status_code == 404 and "MODEL-SECRET" not in r.text


def test_a_record_sealed_to_another_owner_is_not_served(signed, store):
    store.put(record_path("smtp"), {"owner": "someone", "value": "APP-PASSWORD"})
    r = signed("sensors", "POST", PATH, {"name": "smtp"}, actor="flows-mail")
    assert r.status_code == 503 and "APP-PASSWORD" not in r.text


def test_the_operator_command_stores_from_stdin_and_never_prints_it(store, capsys):
    assert mail_secret.main(["put", "smtp"], stdin=io.StringIO("APP-PASSWORD\n"), store=store) == 0
    assert store.get(record_path("smtp")).data == {"value": "APP-PASSWORD", "owner": "mail"}
    assert "APP-PASSWORD" not in capsys.readouterr().out
    assert mail_secret.main(["put", "smtp"], stdin=io.StringIO(""), store=store) == 2
    assert mail_secret.main(["put", "Bad/Name"], stdin=io.StringIO("x"), store=store) == 2
    assert mail_secret.main(["delete", "smtp"], store=store) == 0
