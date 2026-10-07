"""Shared private OpenBao transport. No credential or provider bodies in errors."""
import httpx

class VaultUnavailable(Exception):
    pass

def request(root, address, method, path, body=None, *, allow_missing=False):
    try:
        token=(root/'broker.token').read_text().strip()
        with httpx.Client(timeout=5,follow_redirects=False) as client:
            response=client.request(method,address+'/v1/'+path,headers={'X-Vault-Token':token},json=body)
            if allow_missing and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json() if response.content else {}
    except (OSError,httpx.HTTPError,ValueError):
        raise VaultUnavailable('Credential store unavailable') from None
