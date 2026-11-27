"""Identify recurring clean and failing methods in archived strict/stock data."""
import json
from collections import Counter
from historical import ARMS, OLD, ROOT, step_logs

def main():
    out={}
    for series in ("primary","stock"):
        root=OLD/"runs"/series
        by_method={}
        for folder in sorted(root.glob("*/*")):
            raw=folder/"results.jsonl"
            if not raw.exists() or folder.name not in ARMS:
                continue
            arm=folder.name
            drops=step_logs((folder/"stdout.log").read_text(errors="replace"))
            for line in raw.read_text().splitlines():
                row=json.loads(line)
                tag=row["tag"]
                r=by_method.setdefault(tag,{a:Counter() for a in ARMS})[arm]
                r["seen"]+=1
                if row["status"]!="ok":
                    r["method_error"]+=1
                    r["top_error:"+row["message"].split("\n")[0][:125]]+=1
                if drops[tag]:
                    r["step_affected"]+=1
                if row["stats"]["stop_reason"]=="method_timeout":
                    r["exploration_timeout"]+=1
                if row["stats"]["unknown"]:
                    r["solver_unknown"]+=1
                if row["status"]=="ok" and not drops[tag] and row["stats"]["stop_reason"]!="method_timeout" and not row["stats"]["unknown"]:
                    r["clean"]+=1
        def select(key):
            return [tag for tag,m in by_method.items() if all(a["seen"] and a["seen"]==a[key] for a in m.values())]
        out[series]=dict(consistently_clean=select("clean"),all_arms_method_error=select("method_error"),
                         all_arms_step_affected=select("step_affected"),all_arms_exploration_timeout=select("exploration_timeout"))
    (ROOT/"evidence/historical-details.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
    for n,d in out.items():
        print(n,*((k,len(v),v[:2]) for k,v in d.items()),sep="\n")

if __name__=="__main__":
    main()
