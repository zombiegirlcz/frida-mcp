import sys
from androguard.core.dex import DEX

path = sys.argv[1]
needles = sys.argv[2].split(',')
data = open(path, 'rb').read()
d = DEX(data)
print(f"[*] {path}: classes={len(d.get_classes())} strings={len(d.get_strings())}")
found = {}
for c in d.get_classes():
    for m in c.get_methods():
        code = m.get_code()
        if code is None:
            continue
        for ins in code.get_bc().get_instructions():
            if ins.get_name() == 'const-string':
                out = ins.get_output()
                for n in needles:
                    if n in out:
                        found.setdefault(c.get_name(), set()).add((m.get_name(), out.strip('"')))
print(f"[*] TRefs: {len(found)} trid")
for cn in sorted(found):
    print("CLASS", cn)
    for mn, sv in sorted(found[cn])[:6]:
        print("    ", mn, "->", sv[:110])
