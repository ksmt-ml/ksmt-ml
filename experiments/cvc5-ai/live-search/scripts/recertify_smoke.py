"""Correctly normalize Java file URLs and recheck the unchanged synthetic evidence."""
from base import *
from smoke import config

source=ROOT/"inputs/space.json"
first=ROOT/"evidence/space-attempt-01.json"
assert source.is_file() and not first.exists()
shutil.copyfile(source,first)
prior=read(first)
data={}
for k in prior["combinations"]:
    path=ROOT/"raw/smoke"/k
    problems=[]
    for mode,marker in (("contract","CONTRACT_PASS"),("smoke","LIVE_SMOKE_PASS")):
        p=path/mode
        try:
            st=read(p/"status.json")
            assert st["returncode"]==0 and not st["watchdog"],st
            assert marker in (p/"stdout.log").read_text()
            validate_backend(p,read(p/"backend.json"),read(p/"invocation.json"),read(EXPECTED))
        except Exception as e:
            problems.append(f"{mode}: {e!r}")
    data[k]=dict(ok=not problems,reasons=problems,options={} if k=="z3" else config(int(k)))
write(source,dict(start_msk=prior["start_msk"],end_msk=prior["end_msk"],checked_msk=now(),
                  previous=sha(first),combinations=data))
print("admitted",sum(v["ok"] for v in data.values()),"out of",len(data))
assert data["0"]["ok"] and data["z3"]["ok"]
