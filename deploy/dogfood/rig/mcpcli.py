import json, urllib.request, urllib.error, sys, pathlib
GW = "http://localhost:18456"
TOK = pathlib.Path.home().joinpath(".storm/tok").read_text().strip()

class MCP:
    def __init__(self, tok=TOK, url=GW+"/mcp"):
        self.tok, self.url, self.sid, self.n = tok, url, None, 0
    def _post(self, body):
        self.n += 1
        h = {"content-type":"application/json","Accept":"application/json, text/event-stream",
             "X-API-Key": self.tok}
        if self.sid: h["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(self.url, method="POST",
                                     data=json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                sid = r.headers.get("Mcp-Session-Id")
                if sid: self.sid = sid
                raw = r.read().decode()
                return r.status, self._parse(raw)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:500]
    @staticmethod
    def _parse(raw):
        if "data: " in raw:
            raw = [l[6:] for l in raw.splitlines() if l.startswith("data: ")][-1]
        try: return json.loads(raw)
        except Exception: return raw[:500]
    def init(self):
        st, r = self._post({"jsonrpc":"2.0","id":self.n+1,"method":"initialize",
            "params":{"protocolVersion":"2025-06-18","capabilities":{},
                      "clientInfo":{"name":"storm","version":"0"}}})
        # notifications/initialized
        h={"content-type":"application/json","Accept":"application/json, text/event-stream","X-API-Key":self.tok}
        if self.sid: h["Mcp-Session-Id"]=self.sid
        try:
            urllib.request.urlopen(urllib.request.Request(self.url, method="POST",
                data=json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}).encode(),
                headers=h), timeout=15).read()
        except Exception: pass
        return st, r
    def tools(self):
        return self._post({"jsonrpc":"2.0","id":self.n+1,"method":"tools/list","params":{}})
    def call(self, name, args):
        return self._post({"jsonrpc":"2.0","id":self.n+1,"method":"tools/call",
                           "params":{"name":name,"arguments":args}})
