"""Save a review workflow's written text and JSON payload to the package's review folder.

Usage: save_review.py <task_output_file> <review_dir>
Writes findings.md (the writer's text, if the agent returned it instead of writing the file) and
payload.json (every finding with its verdict, the C findings, checked-clean and not-demonstrated).
"""
import json
import sys
from pathlib import Path

raw = open(sys.argv[1], encoding="utf-8").read()
data = json.loads(raw[raw.find("{"):])
data = data.get("result", data)
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
(out / "payload.json").write_text(json.dumps(data["payload"], indent=1, ensure_ascii=False),
                                  encoding="utf-8")
md = out / "findings.md"
if not md.exists():
    text = data["written"]
    start = text.find("# ")
    md.write_text(text[start:] if start >= 0 else text, encoding="utf-8")
p = data["payload"]
sys.stdout.reconfigure(encoding="utf-8")
print(f"verified {len(p['verified'])}, unverified A/B {len(p['unverified_ab'])}, C {len(p['c_findings'])}")
for f in p["verified"]:
    v = f["verdict"]
    print(f"  real={v['real']} {v['severity']} (was {f['severity']}) [{f['group']}] {f['title'][:150]}")
for f in p["unverified_ab"]:
    print(f"  UNVERIFIED {f['severity']} [{f['group']}] {f['title'][:150]}")
