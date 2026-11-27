"""Read the archived live events and reconstruct the old coverage denominators."""
import collections
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT.parent / "live-client-141"
ARMS = ("cvc5-default", "cvc5-none", "z3")
FIELDS = ("completed_paths", "returned_paths", "steps", "checks", "unknown")

def step_logs(text):
    active = None
    waiting = False
    result = collections.defaultdict(collections.Counter)
    for line in text.splitlines():
        if line.startswith("STOCK_METHOD_START "):
            active = line.split(" ", 1)[1]
            waiting = False
        elif line.startswith("STOCK_METHOD_END "):
            active = None
            waiting = False
        elif waiting:
            detail = line.strip()
            word = detail.split(": ", 1)[0]
            if "fp.to_ieee_bv" in detail:
                word += " / fp.to_ieee_bv"
            elif "couldn't be performed" in detail or "is not allowed" in detail:
                word += " / StepScope"
            elif "cannot be cast" in detail:
                word += " / cast"
            result[active][word] += 1
            waiting = False
        elif active and re.search(r"\bStep failed\s*$", line):
            waiting = True
    return result

def summarize(rows):
    returned = [r for r in rows if r["status"] == "ok"]
    stops, errors, drops, reasons = (collections.Counter() for _ in range(4))
    for r in rows:
        if r["status"] == "ok":
            stops[r["stats"]["stop_reason"]] += 1
        else:
            errors[f'{r["status"]}: {r["message"][:125]}'] += 1
        drops.update(r["drops"])
        reasons.update(r["stats"]["unknown_reasons"])
    return dict(intended=len(rows), returned=len(returned), clean=sum(not r["drops"] and
        r["stats"]["stop_reason"] != "method_timeout" for r in returned),
        affected=sum(bool(r["drops"]) for r in rows), stops=stops, errors=errors,
        drops=drops, unknown_reasons=reasons,
        **{k:sum(r["stats"][k] for r in (rows if k in ("checks","unknown","steps") else returned)) for k in FIELDS})

def main():
    results = {}
    for series in ("primary","stock"):
        directory = OLD / "runs" / series
        recorded = collections.defaultdict(dict)
        projects = collections.defaultdict(lambda: collections.defaultdict(list))
        methods = collections.defaultdict(list)
        for file in sorted(directory.glob("*/*/results.jsonl")):
            arm, block = file.parent.name, file.parent.parent.name
            if arm not in ARMS:
                continue
            drops = step_logs((file.parent / "stdout.log").read_text(errors="replace"))
            for line in file.read_text().splitlines():
                r = json.loads(line)
                p = r["tag"].split("/", 1)[0]
                r["drops"] = drops[r["tag"]]
                recorded[block,r["tag"]][arm] = r
                projects[p][arm].append(r)
                methods[p,r["tag"],arm].append(r)
        comparisons = {}
        for arm in ARMS[:2]:
            matched = [(tag,rs[arm],rs["z3"]) for (_,tag),rs in recorded.items()
                       if all(a in rs and rs[a]["status"]=="ok" for a in (arm,"z3"))]
            by = {}
            for p in projects:
                part = [(tag,a,b) for tag,a,b in matched if tag.startswith(p+"/")]
                by[p] = [len(part), sum(a["stats"]["completed_paths"] for _,a,b in part),
                         sum(b["stats"]["completed_paths"] for _,a,b in part)]
            all_path = [sum(x[i] for x in by.values()) for i in range(3)]
            gaps = collections.Counter()
            context = collections.Counter()
            for tag,a,b in matched:
                s,t = a["stats"], b["stats"]
                diff = s["completed_paths"] - t["completed_paths"]
                gaps[tag] += diff
                if diff:
                    context["exploration_timeout"] += "method_timeout" in (s["stop_reason"],t["stop_reason"])
                    context["unknown"] += bool(s["unknown"] or t["unknown"])
                    context["step_drop"] += bool(a["drops"] or b["drops"])
                    context["all_discordant"] += 1
            comparisons[arm] = dict(counts=all_path, by_project=by,
                context=context, worst=sorted(gaps.items(), key=lambda x:x[1])[:8],
                best=sorted(gaps.items(), key=lambda x:-x[1])[:5])
        all_return = sum(all(arm in rs and rs[arm]["status"]=="ok" for arm in ARMS) for rs in recorded.values())
        all_error = sum(all(arm in rs and rs[arm]["status"]!="ok" for arm in ARMS) for rs in recorded.values())
        common_clean = sum(all(arm in rs and rs[arm]["status"]=="ok" and not rs[arm]["drops"]
                               and rs[arm]["stats"]["stop_reason"]!="method_timeout" for arm in ARMS)
                           for rs in recorded.values())
        results[series] = dict(intended_blocks=len(recorded), all_return=all_return,
            all_error=all_error, all_clean=common_clean, comparisons=comparisons,
            interval=[json.loads((directory/"metrics.json").read_text())[k] for k in ("start","end")],
            by_project={p:{a:summarize(rs) for a,rs in ar.items()} for p,ar in projects.items()},
            arms={a:summarize([r for p in projects.values() for r in p[a]]) for a in ARMS})
        with (ROOT/"evidence"/f"history-{series}-methods.tsv").open("w", newline="") as out:
            writer=csv.writer(out, delimiter="\t")
            writer.writerow(("project","method","arm","observed","returned","clean","step_affected",
                             "step_drops","stops","errors","completed_paths","returned_paths","steps","checks","unknown","unknown_reasons"))
            for (p,tag,arm), rows in sorted(methods.items()):
                s=summarize(rows)
                writer.writerow([p,tag,arm,*[s[k] if not isinstance(s[k],dict) else json.dumps(s[k],ensure_ascii=False)
                    for k in ("intended","returned","clean","affected","drops","stops","errors",*FIELDS,"unknown_reasons")]])
    path=ROOT/"evidence"/"historical.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2)+"\n")
    for name,result in results.items():
        print(name,{k:result[k] for k in ("intended_blocks","all_return","all_error","all_clean")})
        for a,data in result["arms"].items():
            print(a,{k:data[k] for k in ("returned","clean","affected","stops","drops","unknown_reasons")})
        for a,c in result["comparisons"].items():
            print(a,c["counts"],c["context"],c["worst"][:3],c["best"][:2])

if __name__=="__main__":
    main()
