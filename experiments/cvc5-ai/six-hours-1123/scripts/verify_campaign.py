"""Verify report structure, schedules, raw digests, and final resource bounds."""
from datetime import datetime
from hashlib import file_digest
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys

from status import ROOT, atomic, now

class Report(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headings=[]
        self.title=False
        self.language=None
        self.tally={}
        self.errors=[]
        self.ids=set()
        self.anchors=set()

    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        self.tally[tag]=self.tally.get(tag,0)+1
        if tag=="html":
            self.language=a.get("lang")
        if tag in ("script","iframe","object","embed","link"):
            self.errors.append(tag)
        if "src" in a and a["src"].startswith(("http:","https:","//")):
            self.errors.append(a["src"])
        if "id" in a:
            if a["id"] in self.ids:
                self.errors.append(a["id"])
            self.ids.add(a["id"])
        if tag=="a" and a.get("href","").startswith("#"):
            self.anchors.add(a["href"][1:])
        if tag=="title":
            self.title=True

def read(path):
    return json.loads((ROOT/path).read_text())

def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream,"sha256").hexdigest()

def verify_report(name):
    text=(ROOT/name).read_text()
    parser=Report()
    parser.feed(text)
    assert text.lower().startswith("<!doctype html>"),name
    assert parser.title and parser.language=="ru" and parser.tally.get("main")==1
    assert "МСК" in text and "butvinm-work" in text and not parser.errors,name
    assert parser.anchors<=parser.ids,name
    return dict(bytes=(ROOT/name).stat().st_size,tables=parser.tally.get("table",0),sha256=digest(ROOT/name))

def verify_map(base, mapping):
    for name,expected in mapping.items():
        p=base/name
        assert p.is_file() and digest(p)==expected,str(p)
    return len(mapping)

def check_blind():
    calls=[]
    for run_root in ("blind/evidence","search/raw","search/cold/raw","native-search/raw","array-tuning/raw/search","prompt-study/raw"):
        top=ROOT/run_root
        if not top.exists():
            continue
        for staged in top.rglob("staged-train/training.json"):
            outer=staged.parent.parent
            invocation=outer/"invocation.json"
            final=outer/"result.json"
            text=staged.read_text()
            data=json.loads(text)
            assert top.is_relative_to(ROOT)
            assert set(data)<= {"allowed","option_values","baseline","trials","objective","nonce","fixed_steps","repeats"}
            assert "/home/" not in text and "/test/" not in text
            if not invocation.is_file():
                assert not final.is_file()
                calls.append(dict(path=str(outer.relative_to(ROOT)),complete=False,accepted=False))
                continue
            inv=json.loads(invocation.read_text())
            assert digest(staged)==inv["data_sha"]
            assert digest(outer/"prompt.txt")==inv["prompt_sha"]
            assert inv["model"]=="gpt-6.2-sol"
            assert inv["sandbox_sha"]==digest(ROOT/"blind/src/run.sh")
            assert inv["schema_sha"]==digest(ROOT/"blind/protocol/schema.json")
            call=dict(path=str(outer.relative_to(ROOT)),complete=final.is_file(),accepted=False)
            if final.is_file():
                measurement=json.loads(final.read_text())
                assert measurement["input_sha"]==inv["data_sha"]
                status=measurement["status"]
                if measurement["answer"] is not None:
                    assert status["returncode"]==0 and not status["watchdog"] and measurement["tool_events"]==0
                    call["accepted"]=True
                call.update(seconds=status["wall_ms"]/1000,tool_events=measurement["tool_events"],
                            start=status["start_msk"],end=status["end_msk"])
            calls.append(call)
    return calls

def verify_measurement_hashes():
    amounts={}
    for name,summaries,rawroot,key in (
        ("power","followup/evidence/power","followup/raw/power","digest"),
        ("native","native/evidence/pairs","native/raw/paired","digest"),
        ("bv_backend","bv-study/evidence/pairs","bv-study/raw/paired","digest"),
        ("interactions","interactions/evidence/pairs","interactions/raw/paired","digest"),
        ("width_study","width-study/evidence/pairs","width-study/raw/paired","digest"),
    ):
        count=0
        folder=ROOT/summaries
        for p in folder.glob("*.json"):
            if p.name in ("launch.json","end.json"):
                continue
            data=json.loads(p.read_text())
            d=ROOT/rawroot/data["job"]["id"]
            for a,r in data["arms"].items():
                count+=verify_map(d/a,r[key])
        amounts[name]=count
    for name,folder,rawroot in (
        ("warm","screening/evidence/warm","screening/raw/warm"),
        ("warm_compact","screening/evidence/warm-compact","screening/raw/warm-compact"),
    ):
        total=0
        for p in (ROOT/folder).glob("*-r*.json"):
            data=json.loads(p.read_text())
            total+=verify_map(ROOT/rawroot/data["job"]["id"],data["hashes"])
        amounts[name]=total
    for name,where,record,archive_key in (
        ("fp","diagnostics","runs","files"),("oracle_live","oracle-live","runs","files"),
        ("kernels","kernels","runs","files"),("fp_encoding","fp-encoding","runs","files"),
        ("array_semantics","array-semantics","runs","files"),
        ("exact_live","stable","arms","check"),("cpu_dose","cpu-dose","arms","check"),
        ("trace_cause","trace-cause","arms","attestation"),
        ("assertion_order","order-trace","arms","attestation"),
        ("ast_shadow","ast-shadow","arms","attestation"),
        ("array_transfer","array-transfer","arms","digest"),
        ("pure_resource","pure-resource","results","files"),
        ("logic_study","logic-study","results","files"),
        ("logic_complement","logic-complement","results","files"),
        ("logic_unused_uf","logic-unused-uf","results","files"),
        ("resource_live_mini","resource-live-mini","arms","digest"),
        ("resource_live_hi","resource-live-hi","arms","digest"),
        ("trace_overhead","trace-overhead","arms","digest"),
    ):
        total=0
        if name=="fp":
            summary=ROOT/where/"evidence/fp.json"
            if summary.exists():
                for row in json.loads(summary.read_text())[record]:
                    total+=verify_map(ROOT/where/"raw/fp"/row["id"],row[archive_key])
        else:
            for p in (ROOT/where/"evidence").glob("*-r*.json"):
                if not re.search(r"-r\d+\.json$",p.name):
                    continue
                data=json.loads(p.read_text())
                path=ROOT/where/"raw"/data["job"]["id"]
                for a,row in data[record].items():
                    mapping=row[archive_key]
                    if archive_key not in {"files","digest"}:
                        mapping=mapping["digest"]
                    total+=verify_map(path/a,mapping)
        amounts[name]=total
    for p in (ROOT/"library-panel/evidence").glob("*-r*.json"):
        item=json.loads(p.read_text())
        trial=ROOT/"library-panel/raw"/item["job"]["id"]
        for label,case in item["results"].items():
            amounts["library_panel"]=amounts.get("library_panel",0)+verify_map(trial/label,case["digest"])
    for p in (ROOT/"budget/evidence/test").glob("*.json"):
        item=json.loads(p.read_text())
        trial=ROOT/"budget/raw"/item["stratum"]/item["job"]["id"]
        for label,case in item["results"].items():
            amounts["native_budget"]=amounts.get("native_budget",0)+verify_map(trial/label,case["files"])
    for group in ("calibration","test"):
        for p in (ROOT/"live-resource/evidence"/group).glob("*.json"):
            item=json.loads(p.read_text())
            trial=ROOT/"live-resource/raw"/group/item["job"]["id"]
            for label,case in item["arms"].items():
                amounts["live_resource"]=amounts.get("live_resource",0)+verify_map(trial/label,case["digest"])
    for root in ("array-tuning","smac-recovery"):
        for p in (ROOT/root/"raw").rglob("result.json"):
            if not p.with_name("assignment.json").is_file():
                continue
            data=json.loads(p.read_text())
            for row in data["rows"]:
                for arm,case in row["arms"].items():
                    n=verify_map(p.parent/row["job"]["id"]/arm,case["files"])
                    amounts[root]=amounts.get(root,0)+n
                    if data["split"]=="test":
                        lock=read(root+"/evidence/test-lock.json")["at"]
                        assert datetime.fromisoformat(case["state"]["start_msk"])>=datetime.fromisoformat(lock)
    cal=ROOT/"closing-calibration/evidence/summary.json"
    if cal.is_file():
        for row in json.loads(cal.read_text())["raw"]:
            n=verify_map(ROOT/"closing-calibration"/row["path"],row["files"])
            amounts["closing_calibration"]=amounts.get("closing_calibration",0)+n
    return amounts

def verify_logic_inputs():
    original=read("native/inputs/manifest.json")
    sources={p["id"]:p for p in original["programs"]}
    confirmed={p["id"] for p in read("native/evidence/oracle.json") if p["verified"]}
    primary=set()
    for folder in ("logic-study","logic-complement","logic-unused-uf"):
        seal=ROOT/folder/"inputs/manifest.json"
        if not seal.is_file():
            continue
        plan=json.loads(seal.read_text())
        selected=set(plan["programs"])
        assert selected<=confirmed
        if folder=="logic-study":
            primary=selected
        elif folder=="logic-complement":
            assert selected==confirmed-primary
        else:
            assert selected==primary
        for key,program in plan["programs"].items():
            path=ROOT/"native"/sources[key]["path"]
            alternate=ROOT/folder/program["all"]
            assert digest(path)==sources[key]["sha256"]==program["sha256"]
            assert digest(alternate)==program["all_sha"]
            logic,sep,body=path.read_bytes().partition(b"\n")
            assert sep and logic.decode()==program["logic"]
            assert alternate.read_bytes()==b"(set-logic ALL)\n"+body
            if "uf" in program:
                with_uf=ROOT/folder/program["uf"]
                assert digest(with_uf)==program["uf_sha"]
                expected={b"(set-logic QF_BV)":"(set-logic QF_UFBV)",
                          b"(set-logic QF_ABV)":"(set-logic QF_AUFBV)"}[logic]
                assert program["uf_logic"]==expected
                assert with_uf.read_bytes()==expected.encode()+b"\n"+body
        for job in plan["jobs"]:
            receipt=ROOT/folder/"evidence"/(job["id"]+".json")
            if not receipt.is_file():
                continue
            p=plan["programs"][job["program"]]
            for arm in job["order"]:
                invocation=read(f"{folder}/raw/{job['id']}/{arm}/invocation.json")
                assert invocation["arm"]==arm and invocation["core"]==job["core"]
                input_key,logic_value={"C":("all_sha","ALL"),"U":("uf_sha",p.get("uf_logic"))}.get(arm,("sha256",p["logic"]))
                assert invocation["input_hash"]==p[input_key] and invocation["logic"]==logic_value

def main():
    session=read("evidence/session.json")
    clock=datetime.fromisoformat(now())
    stop=datetime.fromisoformat(session["acquisition_cutoff"])
    final="--final" in sys.argv
    status=read("current.json")
    assert set(status)=={"current","done","next","updated"}
    assert (ROOT/"CHECKPOINT.md").is_file()
    old=read("recovery/evidence/summary.json")
    assert old["acquisition"]["counts"]=={"pre-restart":8,"post-restart":8,"combined":16}
    assert all(old["combined"]["arm_totals"][a]["n"]==128 for a in ("default","sol","smac","z3"))
    hist=verify_map(ROOT/"recovery",read("recovery/evidence/verified-sha256.json"))
    sel=verify_map(ROOT/"recovery",read("recovery/evidence/selection-sha256.json"))
    sizes=int(subprocess.check_output(["du","-s","-B1",str(ROOT)],timeout=15).split()[0])
    assert sizes<=session["disk_limit_bytes"],sizes
    topo=set()
    for cpu in session["logical_cpus"]:
        path=Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        assert (path/"thread_siblings_list").read_text().strip()==str(cpu)
        topo.add(((path/"physical_package_id").read_text(),(path/"core_id").read_text()))
    assert len(topo)==4
    measurements={}
    summary_paths={
        "power":"followup/evidence/power-summary.json",
        "fp":"diagnostics/evidence/fp.json",
        "native":"native/evidence/summary.json",
        "warm":"screening/evidence/warm-summary.json",
        "warm_compact":"screening/evidence/warm-compact-summary.json",
        "oracle_live":"oracle-live/evidence/summary.json",
        "bv_backend":"bv-study/evidence/summary.json",
        "native_budget":"budget/evidence/summary.json",
        "exact_live":"stable/evidence/summary.json",
        "live_search":"search/evidence/live-summary.json",
        "native_search":"native-search/evidence/summary.json",
        "trace_cause":"trace-cause/evidence/summary.json",
        "assertion_order":"order-trace/evidence/summary.json",
        "ast_shadow":"ast-shadow/evidence/summary.json",
        "java_kernels":"kernels/evidence/summary.json",
        "array_semantics":"array-semantics/evidence/summary.json",
        "interactions":"interactions/evidence/summary.json",
        "library_panel":"library-panel/evidence/summary.json",
        "fp_encoding":"fp-encoding/evidence/summary.json",
        "cpu_dose":"cpu-dose/evidence/summary.json",
        "live_search_cold":"search/cold/evidence/live-summary.json",
        "live_resource":"live-resource/evidence/summary.json",
        "live_resource_calibration":"live-resource/evidence/calibration-summary.json",
        "pure_resource":"pure-resource/evidence/summary.json",
        "logic_study":"logic-study/evidence/summary.json",
        "logic_complement":"logic-complement/evidence/summary.json",
        "logic_unused_uf":"logic-unused-uf/evidence/summary.json",
        "resource_live_mini":"resource-live-mini/evidence/summary.json",
        "resource_live_hi":"resource-live-hi/evidence/summary.json",
        "trace_overhead":"trace-overhead/evidence/summary.json",
        "closing_calibration":"closing-calibration/evidence/summary.json",
        "width_study":"width-study/evidence/summary.json",
        "array_tuning":"array-tuning/evidence/summary.json",
        "array_transfer":"array-transfer/evidence/summary.json",
        "option_prompt_comparison":"prompt-study/evidence/summary.json",
        "smac_recovery":"smac-recovery/evidence/summary.json",
    }
    for name,path in summary_paths.items():
        if (ROOT/path).is_file():
            x=read(path)
            assert datetime.fromisoformat(x["start"]) <= datetime.fromisoformat(x["end"]) < stop,name
            measurements[name]=dict(start=x["start"],end=x["end"],sha256=digest(ROOT/path))
    if final:
        deadline=datetime.fromisoformat(session["report_deadline"])
        assert stop<=clock<=deadline, "Final verification must run in the report window"
        closure=read("evidence/acquisition-stop.json")
        assert closure["lock_acquired"] and stop<=datetime.fromisoformat(closure["observed"])<=clock
        assert datetime.fromisoformat(closure["last_native_or_jvm"])<stop
        assert not status["next"] and stop<=datetime.fromisoformat(status["updated"])<=clock
    calls=check_blind()
    for call in calls:
        if call["complete"]:
            assert datetime.fromisoformat(call["start"])<=datetime.fromisoformat(call["end"])<stop,call["path"]
    result=dict(at=now(),mode="final" if final else "intermediate",recovery_raw_files=hist,selection_raw_files=sel,
        reports={n:verify_report(n) for n in ("report.html","recovery/report.html")},
        measurements=measurements,model_calls=calls,disk_bytes=sizes,free_bytes=shutil.disk_usage(ROOT).free)
    if final or "--full" in sys.argv:
        result["measurement_files"]=verify_measurement_hashes()
        verify_logic_inputs()
    if final:
        result["acquisition_stop"]=closure
        result["physical_cpu_ids"]=session["logical_cpus"]
    atomic(ROOT/"evidence/verification.json",json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
