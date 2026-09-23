"""Point every recovered text file at this session's scratchpad: worktrees at scratchpad/wt-*, all else at scratchpad/recovered."""
import os

NEW = "C--Users-Alex/c4101d6e-425a-4e46-b1e2-205e8e47cc10/scratchpad"
OLDS = ["8d5f7e69-4380-443b-b3f3-3ba6b9b29bc9", "770e98db-0074-4ce5-875e-ec166cb8d0cd"]
ROOT = f"C:/Users/Alex/AppData/Local/Temp/claude/{NEW}/recovered"
changed = 0
for dp, _, fns in os.walk(ROOT):
    for fn in fns:
        p = os.path.join(dp, fn)
        try:
            s = open(p, encoding="utf-8", newline="").read()
        except (UnicodeDecodeError, OSError):
            continue
        t = s
        for old in OLDS:
            for sep in ("/", "\\", "\\\\"):
                o = f"C--Users-Alex{sep}{old}{sep}scratchpad"
                n = NEW.replace("/", sep)
                t = t.replace(f"{o}{sep}wt-", f"{n}{sep}wt-")
                t = t.replace(o, f"{n}{sep}recovered")
        if t != s:
            open(p, "w", encoding="utf-8", newline="").write(t)
            changed += 1
print("rewrote", changed, "files")
