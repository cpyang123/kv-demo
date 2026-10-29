"""Canned OpenAI-compatible endpoint for UI smoke tests: a scripted 3-step agent (ls, write a file, submit) and a
one-line memory note for the writer prompts. No model, no network."""
import json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACTOR = [
    "THOUGHT: Let me look at what is in the workspace before changing anything.\n\n```mswea_bash_command\nls -la && git log --oneline -3 2>/dev/null; echo ok\n```",
    "THOUGHT: The workspace is empty apart from git. I will add the file the task asks for and show it.\n\n```mswea_bash_command\nprintf '# UI smoke\\n\\nCreated by the canned agent.\\n' > NOTES.md && cat NOTES.md && sleep 2\n```",
    "THOUGHT: The file is in place. Submitting the change.\n\n```mswea_bash_command\necho COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git diff --cached\n```",
]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        body = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"): return self._send(200, {"object": "list", "data": [{"id": "coder-next", "object": "model", "owned_by": "fake"}]})
        self._send(404, {"error": "no"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages", []); system = (msgs[0].get("content") if msgs and msgs[0].get("role") == "system" else "") or ""
        if "mswea_bash_command" in str(system):          # only the actor's system prompt names the command fence; the writer prompt does not
            k = sum(1 for m in msgs if m.get("role") == "assistant"); text = ACTOR[min(k, len(ACTOR) - 1)]; time.sleep(1.2)
        else:
            text = "DID: added NOTES.md with a short description | WHERE: NOTES.md | VERIFY: cat NOTES.md"; time.sleep(0.5)
        ptoks = sum(len(str(m.get("content") or "")) for m in msgs) // 4
        self._send(200, {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": req.get("model", "coder-next"),
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": ptoks, "completion_tokens": len(text) // 4, "total_tokens": ptoks + len(text) // 4}})

ThreadingHTTPServer(("127.0.0.1", 8858), H).serve_forever()
