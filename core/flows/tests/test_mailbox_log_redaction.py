"""The mailbox process logs a sender as a domain plus a digest and a message as a digest of its
source id — never the address and never the subject (quarantined strangers included)."""

import ast
import re
from pathlib import Path
from types import SimpleNamespace

from flows_integrations import mailbox

SRC = Path(mailbox.__file__)


def test_log_sender_keeps_domain_drops_address():
    out = mailbox.log_sender("Someone.Private@Example.org")
    assert "someone" not in out.lower() and "private" not in out.lower()
    assert out.endswith("@example.org>")
    assert out == mailbox.log_sender("someone.private@example.org")  # stable per address


def test_log_message_never_carries_subject():
    msg = SimpleNamespace(ext_id="42", message_id="<m@x>", subject="Salary review for J. Doe")
    out = mailbox.log_message(msg)
    assert "Salary" not in out and "Doe" not in out and out.startswith("msg:")


def test_no_print_interpolates_sender_or_subject():
    tree = ast.parse(SRC.read_text())
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            for arg in node.args:
                if isinstance(arg, ast.JoinedStr):
                    for v in arg.values:
                        if isinstance(v, ast.FormattedValue):
                            text = ast.unparse(v.value)
                            if re.search(r"\.frm$|\.subject\b", text):
                                bad.append((node.lineno, text))
    assert bad == []
