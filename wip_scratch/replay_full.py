"""Replay the stopped Word fix-3 builder (agent a3cd77b71f08d8c03) onto a clean wt-word @ 2671947.

Replays, in the original order: every successful Write/Edit (worktree AND the builder's scratchpad
helper files, paths remapped to this session's scratchpad), the two append_file.py appends, and the
three script runs that rewrote the worktree (quote_notes.py at call 78 and 80, gen_zapf_table.py at 92).
"""
import json, os, re, subprocess

SRC = r"C:/Users/Alex/.claude/projects/C--Users-Alex/770e98db-0074-4ce5-875e-ec166cb8d0cd/subagents/workflows/wf_a756fa53-8c9/agent-a3cd77b71f08d8c03.jsonl"
OLD = "8d5f7e69-4380-443b-b3f3-3ba6b9b29bc9"
NEW = "c4101d6e-425a-4e46-b1e2-205e8e47cc10"
SP = f"C:/Users/Alex/AppData/Local/Temp/claude/C--Users-Alex/{NEW}/scratchpad"
PY = r"C:/Users/Alex/document-iq/.venv/Scripts/python.exe"
STOP_AT = int(os.environ.get("STOP_AT", "999999"))


def mp(p):
    return p.replace("\\", "/").replace(OLD, NEW)


uses, results = [], {}
for line in open(SRC, encoding="utf-8"):
    rec = json.loads(line)
    cont = (rec.get("message") or {}).get("content")
    if not isinstance(cont, list):
        continue
    for c in cont:
        if c.get("type") == "tool_use":
            uses.append(c)
        if c.get("type") == "tool_result":
            results[c["tool_use_id"]] = c


def append(src, dst):  # word_fix2/append_file.py semantics
    text = open(dst, "rb").read().decode("utf-8")
    add = open(src, "rb").read().decode("utf-8")
    assert "\r\n" not in text and "\r\n" not in add
    if not text.endswith("\n"):
        text += "\n"
    open(dst, "wb").write((text + add).encode("utf-8"))


RUNS = {78: "quote_notes.py", 80: "quote_notes.py", 92: "gen_zapf_table.py"}
ok = fail = 0
for idx, u in enumerate(uses, 1):
    if idx > STOP_AT:
        break
    inp = u["input"]
    r = results.get(u["id"], {})
    if u["name"] == "Bash":
        cmd = inp["command"]
        if idx == 80:
            q = f"{SP}/word_fix3/quote_notes.py"
            t = open(q, encoding="utf-8").read()
            open(q, "w", encoding="utf-8", newline="").write(t.replace("assert n == 17, n", "assert n == 18, n"))
        if idx in RUNS:
            rr = subprocess.run([PY, RUNS[idx]], cwd=f"{SP}/word_fix3", capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
            tail = (rr.stdout + rr.stderr)[-300:].replace("\n", " | ")
            print(f"RUN {idx} {RUNS[idx]} rc={rr.returncode} orig_is_error={r.get('is_error')} :: {tail}")
        for m in re.finditer(r"append_file\.py (\S+\.txt) (\S+)", cmd):
            append(os.path.normpath(f"{SP}/word_fix3/{m.group(1)}"),
                   os.path.normpath(f"{SP}/word_fix3/{m.group(2)}"))
            print("APPEND", idx, m.group(1), "->", m.group(2))
            ok += 1
        continue
    if u["name"] not in ("Edit", "Write"):
        continue
    if r.get("is_error"):
        continue
    p = mp(inp["file_path"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if u["name"] == "Write":
        open(p, "w", encoding="utf-8", newline="").write(inp["content"].replace(OLD, NEW))
        ok += 1
        continue
    if not os.path.exists(p) and "/wt-word/" not in p:
        continue
    s = open(p, encoding="utf-8", newline="").read()
    o, n = inp["old_string"], inp["new_string"]
    k = s.count(o)
    if k == 0 and "�" in o:
        # The source spells U+FFFD as the ASCII escape; the recorded edit carries the character.
        o, n = o.replace("�", "\\ufffd"), n.replace("�", "\\ufffd")
        k = s.count(o)
        if k:
            print("FFFD-ESCAPED", idx, p[-40:])
    if k == 0 and not p.replace("\\", "/").split("/scratchpad/")[1].startswith("wt-word"):
        continue  # a scratchpad helper that was copied in by shell (measure/, mutants): not needed
    if k == 0 or (k > 1 and not inp.get("replace_all")):
        print("FAIL", idx, k, p[-60:], repr(o[:80]))
        fail += 1
        continue
    s = s.replace(o, n) if inp.get("replace_all") else s.replace(o, n, 1)
    open(p, "w", encoding="utf-8", newline="").write(s)
    ok += 1
print("applied", ok, "failed", fail)
