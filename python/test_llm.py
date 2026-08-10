"""Uji LLMClient terhadap server OpenAI-compatible palsu (tanpa biaya API)."""
import json, sys, os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm import LLMClient, LLMError

class Handler(BaseHTTPRequestHandler):
    mode = "json"
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        req = json.loads(body)
        msgs = req.get("messages", [])
        user = msgs[-1]["content"] if msgs else ""
        if Handler.mode == "fenced":
            content = '```json\n{"action":"OPEN","bias":"LONG","confidence":0.8,"entry":1.12345,"sl":1.12000,"tp":1.12800,"lot_fraction":0.01,"regime_label":"TREND_UP","reasoning":"tes"}\n```'
        elif Handler.mode == "nojsonformat":
            if "response_format" in req:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Unsupported parameter: response_format"}}')
                return
            content = '{"action":"HOLD","bias":"FLAT","confidence":0.4,"lot_fraction":0.01,"regime_label":"MIXED","reasoning":"no format ok"}'
        else:
            content = '{"action":"HOLD","bias":"FLAT","confidence":0.3,"lot_fraction":0.01,"regime_label":"RANGING","reasoning":"ok"}'
        resp = {"choices": [{"message": {"role": "assistant", "content": content}}]}
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a): pass

srv = HTTPServer(("127.0.0.1", 18111), Handler)
t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()

cfg = {"base_url": "http://127.0.0.1:18111/v1", "api_key": "test",
       "model": "ocg/deepseek-v4-flash", "json_mode": True, "timeout_s": 5,
       "retries": 2, "min_llm_confidence": 0.55}
c = LLMClient(cfg)

# 1) json normal
d = c.decide("system", "user")
assert d["action"] == "HOLD" and d["bias"] == "FLAT", d
print("PASS json normal:", d["action"], d["bias"])

# 2) markdown fence
Handler.mode = "fenced"
d = c.decide("system", "user")
assert d["action"] == "OPEN" and d["bias"] == "LONG" and d["entry"] == 1.12345, d
print("PASS fenced JSON parse:", d["action"], d["bias"], d["entry"])

# 3) provider tanpa response_format -> auto fallback ke non-json
Handler.mode = "nojsonformat"
d = c.decide("system", "user")
assert d["action"] == "HOLD", d
print("PASS json_mode fallback (json_mode sekarang", c.json_mode, ")")

# 4) parse invalid -> ValidationError -> LLMError setelah retries
Handler.mode = "fenced"
from llm import ValidationError
class BadHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        data = b'{"choices":[{"message":{"role":"assistant","content":"not json at all"}}]}'
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a): pass
srv2 = HTTPServer(("127.0.0.1", 18112), BadHandler)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
c2 = LLMClient({**cfg, "base_url": "http://127.0.0.1:18112/v1", "retries": 1})
try:
    c2.decide("s", "u")
    print("FAIL: expected LLMError")
except LLMError as e:
    print("PASS invalid JSON rejected:", str(e)[:60])

srv.shutdown(); srv2.shutdown()
print("ALL LLM TESTS PASS")
