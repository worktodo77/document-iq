"""Rebuild the two lost session scratchpads (8d5f7e69, 770e98db) into this session's scratchpad.

Replays every Write/Edit whose target was a scratchpad file outside a wt-* worktree, across the main
transcripts and every subagent/workflow transcript of both sessions, in timestamp order. Simple
`cp $S/a $S/b` copies inside a scratchpad are replayed too. Prints what could not be rebuilt.
"""
import glob, json, os, re, shutil

BASE = "C:/Users/Alex/.claude/projects/"
OLDS = ["8d5f7e69-4380-443b-b3f3-3ba6b9b29bc9", "770e98db-0074-4ce5-875e-ec166cb8d0cd"]
NEW = "c4101d6e-425a-4e46-b1e2-205e8e47cc10"
ROOT = f"C:/Users/Alex/AppData/Local/Temp/claude/C--Users-Alex/{NEW}/scratchpad/recovered"

files = []
for old in OLDS:
    files += glob.glob(f"{BASE}*/{old}.jsonl") + glob.glob(f"{BASE}*/{old}/**/*.jsonl", recursive=True)
files = sorted(set(os.path.normpath(f) for f in files))
print(len(files), "transcripts")


def mp(p):
    p = p.replace("\\", "/")
    for old in OLDS:
        tag = f"C--Users-Alex/{old}/scratchpad/"
        if tag in p:
            rest = p.split(tag, 1)[1]
            if rest.startswith("wt-"):
                return None
            return f"{ROOT}/{rest}"
    return None


ops = []
for f in files:
    uses, results = [], {}
    for line in open(f, encoding="utf-8", errors="replace"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        cont = (rec.get("message") or {}).get("content")
        if not isinstance(cont, list):
            continue
        for c in cont:
            if c.get("type") == "tool_use":
                uses.append((rec.get("timestamp", ""), c))
            elif c.get("type") == "tool_result":
                results[c["tool_use_id"]] = c
    for ts, u in uses:
        if results.get(u["id"], {}).get("is_error"):
            continue
        ops.append((ts, f, u))
ops.sort(key=lambda t: t[0])

done = failed = copies = 0
fails = []
for ts, f, u in ops:
    inp = u["input"]
    if u["name"] in ("Write", "Edit"):
        p = mp(inp.get("file_path", ""))
        if not p:
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if u["name"] == "Write":
            c = inp["content"]
            for old in OLDS:
                c = c.replace(old, NEW + "/scratchpad/recovered" if False else old)
            open(p, "w", encoding="utf-8", newline="").write(inp["content"])
            done += 1
            continue
        if not os.path.exists(p):
            failed += 1; fails.append(("missing", p)); continue
        s = open(p, encoding="utf-8", newline="").read()
        o, n = inp["old_string"], inp["new_string"]
        if s.count(o) == 0 and "\ufffd" in o:
            o, n = o.replace("\ufffd", "\\ufffd"), n.replace("\ufffd", "\\ufffd")
        k = s.count(o)
        if k == 0 or (k > 1 and not inp.get("replace_all")):
            failed += 1; fails.append(("nomatch", p)); continue
        s = s.replace(o, n) if inp.get("replace_all") else s.replace(o, n, 1)
        open(p, "w", encoding="utf-8", newline="").write(s)
        done += 1
    elif u["name"] == "Bash":
        cmd = inp.get("command", "")
        m = re.search(r"S=(\S+?/scratchpad);", cmd)
        svar = m.group(1) if m else None
        for cm in re.finditer(r"\bcp (?:-r )?(\S+) (\S+)", cmd):
            a, b = cm.group(1), cm.group(2)
            if svar:
                a, b = a.replace("$S", svar), b.replace("$S", svar)
            pa, pb = mp(a), mp(b)
            if pa and pb and os.path.exists(pa):
                if os.path.isdir(pa):
                    shutil.copytree(pa, pb, dirs_exist_ok=True)
                else:
                    if pb.endswith("/") or os.path.isdir(pb):
                        pb = os.path.join(pb, os.path.basename(pa))
                    os.makedirs(os.path.dirname(pb), exist_ok=True)
                    shutil.copyfile(pa, pb)
                copies += 1
print("applied", done, "copies", copies, "failed", failed)
seen = set()
for kind, p in fails:
    if p not in seen:
        seen.add(p); print(" ", kind, p.split("/recovered/")[1])
