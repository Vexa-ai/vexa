"""flows-api's mail-design routes, as its OpenAPI publishes them — the admin account's
`mail_templates_list` · `mail_template_edit` · `mail_preview` · `mail_test_send` · `mail_kind_set`,
and every person's `mail_address` (global mail, 2026-10-14). One copy, merged into
each suite's stand-in flows OpenAPI."""


def _body(name, props):
    return {"requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "title": name,
        "properties": {k: {"title": k, **v} for k, v in props.items()}}}}}}


_KIND = {"name": "kind", "in": "path", "required": True, "schema": {"type": "string"}}

MAIL_PATHS = {
    "/mail/kinds": {"get": {"summary": "Every kind of mail this deployment can send", "parameters": []}},
    "/mail/templates/{kind}": {"put": {"summary": "Rewrite one mail kind's template",
                                       "parameters": [_KIND],
                                       **_body("_MailTemplate", {"content": {"type": "string"}})}},
    "/mail/preview/{kind}": {"post": {"summary": "Render one mail kind with sample values",
                                      "parameters": [_KIND],
                                      **_body("_MailPreview", {"sample": {"type": "object"},
                                                               "draft": {"type": "string"}})}},
    "/mail/test-send/{kind}": {"post": {"summary": "Send one mail kind to your own address",
                                        "parameters": [_KIND],
                                        **_body("_MailTest", {"sample": {"type": "object"}})}},
    "/mail/kinds/{kind}": {"put": {"summary": "Switch one mail kind on, off or admins-only",
                                   "parameters": [_KIND],
                                   **_body("_MailSwitch", {"state": {"type": "string"}})}},
    "/mail/address": {"get": {"summary": "The address to invite Vexa at", "parameters": []}},
}

MAIL_TOOLS = ("mail_templates_list", "mail_template_edit", "mail_preview", "mail_test_send",
              "mail_kind_set", "mail_address")
