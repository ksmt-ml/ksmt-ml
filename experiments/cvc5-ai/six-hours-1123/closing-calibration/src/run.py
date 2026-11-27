"""Repeat only the original core calibration in the closing host epoch."""
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from statistics import median
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
sys.path.insert(0,str(TOP/"recovery/scripts"))
import base
from recover import host_sample,manifest

CUTOFF=datetime.fromisoformat("2026-11-23T09:20:00+03:00").timestamp()


def main():
    base.setup_limits()
    source=TOP/"recovery/scripts/recover.py"
    code=next(x.value.value for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=="calibrate"
              for x in n.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="code" for t in x.targets))
    prior=base.read(TOP/"recovery/evidence/post-boot-calibration.json")
    expected={r["result"]["digest"] for rows in prior["standalone"].values() for r in rows}
    assert len(expected)==1
    start=host_sample()

    def probe(mode,core,rep):
        where=ROOT/"raw"/f"{mode}-{rep+1}-{core}"
        if where.exists() and not (where/"status.json").is_file():
            where.rename(where.with_name(where.name+f"-interrupted-{int(time.time())}"))
        if not where.is_dir():
            if time.time()+6>=CUTOFF:
                return None
            where.mkdir()
            cmd=["taskset","-c",str(core),sys.executable,"-c",code]
            base.write(where/"invocation.json",dict(cmd=cmd,mode=mode,rep=rep,core=core))
            base.run_process(where,cmd,base.public_env(where),5)
        st=base.read(where/"status.json")
        done=st["returncode"]==0 and not st["watchdog"]
        result=base.read(where/"stdout.log") if done else None
        if done:
            assert result["digest"] in expected
        return dict(status=st,result=result,mode=mode,core=core,files=manifest(where),path=str(where.relative_to(ROOT)))

    rows=[probe("single",core,i) for core in base.CPUS for i in range(3)]
    with ThreadPoolExecutor(4) as pool:
        for i in range(6):
            rows.extend(pool.map(lambda core:probe("parallel",core,i),base.CPUS))
    rows=[r for r in rows if r is not None]
    if not rows:
        return
    summary={}
    for core in base.CPUS:
        key=str(core)
        summary[key]={}
        for label,history in (("single",prior["standalone"][key]),("parallel",[j[key] for j in prior["concurrent"]])):
            current=[r["result"] for r in rows if r["mode"]==label and r["core"]==core and r["result"] is not None]
            if current:
                summary[key][label]=dict(n=len(current),old_n=len(history),**{k:dict(old=median(r["result"][k] for r in history),
                                  new=median(r[k] for r in current)) for k in ("cpu_ms","wall_ms")})
    clocks=[r["status"] for r in rows]
    base.write(ROOT/"evidence/summary.json",dict(start=min(r["start_msk"] for r in clocks),end=max(r["end_msk"] for r in clocks),
      measured=len(rows),failed=sum(r["result"] is None for r in rows),planned=36,host_before=start,host_after=host_sample(),
      previous=prior["before"]["at"],source_sha=base.sha(source),summary=summary,raw=rows))
    print("closing calibration",len(rows),base.now(),flush=True)


if __name__=="__main__":
    main()
