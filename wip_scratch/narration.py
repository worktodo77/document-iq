"""Dump a stopped agent's own narration (its text blocks) plus the tail of each gate command's result."""
import json, sys

src, out = sys.argv[1], sys.argv[2]
uses, lines = {}, []
n = 0
for line in open(src, encoding="utf-8"):
    rec = json.loads(line)
    msg = rec.get("message") or {}
    cont = msg.get("content")
    if not isinstance(cont, list):
        continue
    for c in cont:
        if c.get("type") == "text" and msg.get("role") == "assistant" and c["text"].strip():
            lines.append(f"\n[narration after call {n}]\n{c['text'].strip()}")
        elif c.get("type") == "tool_use":
            n += 1
            uses[c["id"]] = (n, c)
        elif c.get("type") == "tool_result" and c["tool_use_id"] in uses:
            i, u = uses[c["tool_use_id"]]
            cmd = u["input"].get("command", "")
            if u["name"] == "Bash" and any(k in cmd for k in ("pytest", "red_run", "mutants.py", "orchestrator", "selftest", "gate2")):
                t = c.get("content")
                t = t if isinstance(t, str) else " ".join(x.get("text", "") for x in t if isinstance(x, dict))
                lines.append(f"\n[call {i} result tail] $ {cmd[:200]}\n{t[-1200:]}")
open(out, "w", encoding="utf-8").write("\n".join(lines))
print(len(lines), "blocks")
