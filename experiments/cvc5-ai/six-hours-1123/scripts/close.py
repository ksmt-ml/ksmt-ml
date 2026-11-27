"""After acquisition closes, seal the final report and verify local evidence."""
from datetime import datetime
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys

from status import ROOT, atomic, now, status


def main():
    session=json.loads((ROOT/"evidence/session.json").read_text())
    started=datetime.fromisoformat(now())
    cutoff=datetime.fromisoformat(session["acquisition_cutoff"])
    end=datetime.fromisoformat(session["report_deadline"])
    assert cutoff<=started<end,(started,cutoff,end)
    with (ROOT/"evidence/acquisition.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=ROOT/"evidence/acquisition-stop.json"
        if not path.is_file():
            raw=[]
            for f in ROOT.glob("*/raw/**/status.json"):
                data=json.loads(f.read_text())
                time=data.get("end_msk")
                if time and f.parts[len(ROOT.parts)] not in {"recovery"}:
                    raw.append((time,str(f.relative_to(ROOT))))
            if raw:
                last=max(raw,key=lambda a:datetime.fromisoformat(a[0]))
                assert datetime.fromisoformat(last[0])<cutoff,last
            else:
                last=(None,None)
            atomic(path,json.dumps(dict(observed=now(),last_native_or_jvm=last[0],last_status=last[1],
                   free_bytes=shutil.disk_usage(ROOT).free,lock_acquired=True),ensure_ascii=False,indent=2)+"\n")
        subprocess.run([sys.executable,"scripts/verify_campaign.py","--full"],cwd=ROOT,check=True,
                       stdout=subprocess.DEVNULL)
        status("final","Сбор завершён. Проверенные отчёты и журналы готовы.",
               "Закрыто окно сбора, сверены SHA-256, интервалы времени, CPU и дисковый бюджет",[])
        subprocess.run([sys.executable,"scripts/render.py"],cwd=ROOT,check=True)
        result=subprocess.check_output([sys.executable,"scripts/verify_campaign.py","--final"],cwd=ROOT,text=True)
        atomic(ROOT/"evidence/final-verification.log",result)
        print(result,flush=True)
        assert datetime.fromisoformat(now())<=end


if __name__=="__main__":
    main()
