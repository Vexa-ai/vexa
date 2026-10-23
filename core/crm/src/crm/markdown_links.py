"""Canonical CRM links in Markdown descriptions. Code examples are not graph edges."""
import re
from urllib.parse import urlsplit, parse_qs
from uuid import UUID

def crm_references(markdown):
    prose=[];fence=None
    for line in (markdown or '').splitlines():
        marker=re.match(r'^ {0,3}(`{3,}|~{3,})',line)
        if fence:
            if marker and marker[1][0]==fence[0] and len(marker[1])>=len(fence):fence=None
            continue
        if marker:fence=marker[1];continue
        prose.append(line)
    prose=re.sub(r'(`+).*?\1','', '\n'.join(prose),flags=re.S)
    found=set()
    for href in re.findall(r'(?<![!\\])\[[^\]\n]*\]\((/crm\?[^\s)]+)\)',prose):
        url=urlsplit(href)
        if url.path!='/crm' or url.netloc:continue
        for value in parse_qs(url.query).get('record',[]):
            try:found.add(str(UUID(value)))
            except ValueError:pass
    return sorted(found)
