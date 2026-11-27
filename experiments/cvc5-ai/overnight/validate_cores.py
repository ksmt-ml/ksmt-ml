"""Check returned cvc5 assumption cores against an independent Z3 context."""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time

from corpus import USVM
from portfolio import MENU
from replay import CAMPAIGN, CORES, ROOT, STOP, append, check_budget, digest, ev, guarded_popen, lines, load, log, now
from report import build, write_json
from stability_metrics import card_table

BASE=ROOT/"runs/14-core-audit"
SCOPE=ROOT/"protocols/returned-core-audit.json"
MARKER=re.compile(r'(?m)^"?night_core_(\d+)_([ab])"?\s*$')
QUERY=("check-sat","check-sat-assuming")
CAP=4.50

sys.path.insert(0,str(USVM))
from convert import terms,render

def samples():
    sources=[
        (ROOT/"runs/12-new-libraries/portable-corpus/manifest.json",True,"new libraries"),
        (ROOT/"runs/12-new-libraries/corpus/manifest.json",False,"new libraries"),
        (ROOT/"runs/11b-array-bridge/tuple/paired-manifest.json",True,"previous libraries"),
        (ROOT/"runs/02-portfolio/dev-corpus/manifest.json",False,"USVM Java development"),
    ]
    buckets={}
    for source,arrays,kind in sources:
        if not source.exists() or kind in buckets:
            continue
        pool=[r for r in load(source)["sessions"] if r.get("operations",{}).get("unsatCore",0)>0]
        chosen=sorted(pool,key=lambda r:r["sha256"])[:16]
        buckets[kind]=[(source,arrays,kind,r) for r in chosen]
    return [row for group in buckets.values() for row in group]

def freeze():
    BASE.mkdir(exist_ok=True)
    if SCOPE.exists():
        return load(SCOPE)
    menu=load(MENU)
    selected=samples()
    assert selected
    config={"default":{},"global":menu["arms"][menu["global_arm"]]}
    data={"locked":now(),"corpus_kind":"previously measured USVM traces, correctness diagnostic on reused cases",
          "scope":"Select up to 16 class/method records with actual core requests per provenance; independently revalidate returned subsets and unsatisfiability under reconstructed assertion scopes.",
          "configs":config,"case_smt_timeout":CAP,"independent_z3_timeout_s":3.0,
          "core_check_sample_per_program":3,"repeats":1,"selection":"hash order before checking any returned cvc5 cores",
          "conditions":"This verifies requested unsat assumption cores. Counterexamples, coverages and arbitrary printed model interpretations remain separate.",
          "inputs":[{"manifest":os.path.relpath(m,ROOT),"family":k,"arrays_exp":a,"session":r} for m,a,k,r in selected]}
    write_json(SCOPE,data)
    SCOPE.chmod(0o444)
    return data

def prepare_one(record,number):
    row=record["session"]
    manifest=ROOT/record["manifest"]
    src=manifest.parent/row["file"]
    assert digest(src)==row["sha256"]
    text=src.read_text()
    assert "night_core_" not in text
    ast=terms(text)
    indices=[i for i,t in enumerate(ast) if t[0]=="get-unsat-assumptions"]
    indices=list(sorted(random.Random(98117+number).sample(indices,min(len(indices),3))))
    assert indices
    chosen={i:j for j,i in enumerate(indices)}
    assert out_of_order(ast,indices)==0
    prelude=[]
    output=[]
    checks={}
    seen_queries=0
    for i,t in enumerate(ast):
        op=t[0]
        if i in chosen:
            p=chosen[i]
            assumptions=ast[i-1][1]
            assert row["oracle"][seen_queries-1]=="unsat"
            checks[p]={"context":[*prelude],"selected_assumptions":[render(o) for o in assumptions],"query_no":seen_queries}
            output.extend((f'(echo "night_core_{p}_a")',render(t),f'(echo "night_core_{p}_b")'))
        else:
            output.append(render(t))
        if op in QUERY:
            seen_queries+=1
        if op not in QUERY and not op.startswith("get-") and op!="exit" and not (
           op=="set-option" and t[1].startswith(":produce-")):
            prelude.append(render(t))
    assert seen_queries==len(row["oracle"])
    path=BASE/"instrumented"
    path.mkdir(exist_ok=True)
    file=path/f"program-{number:03d}.smt2"
    file.write_text("\n".join(output)+"\n")
    return checks,file

def out_of_order(ast,indices):
    return sum(i<1 or ast[i-1][0]!="check-sat-assuming" for i in indices)

def cvc_capture(path,opts,arrays):
    flags=ev.flags(opts)+(["--arrays-exp"] if arrays else [])
    cmd=["/usr/bin/prlimit",f"--as={ev.LIMIT_AS}","--core=0","--",ev.SOLVER,
         "--lang=smt2","--incremental","--quiet",f"--seed={ev.SOLVER_SEED}",*flags,path]
    start=time.monotonic()
    cap=min(CAP,max(.01,STOP-time.time()-8))
    with guarded_popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True) as p:
        try:
            stdout,stderr=p.communicate(timeout=cap)
            status="solved" if p.returncode==0 and b'(error "' not in stdout else "error"
        except subprocess.TimeoutExpired:
            p.kill()
            stdout,stderr=p.communicate()
            status="timeout"
    return {"status":status,"stdout":stdout.decode(errors="replace"),
            "stderr":stderr.decode(errors="replace"),"wall_s":time.monotonic()-start}

def independently_unsat(prefix,subset):
    z3=CAMPAIGN/".venv/bin/z3"
    body="\n".join([*prefix,f"(check-sat-assuming {render(subset)})","(exit)"])+"\n"
    cmd=["/usr/bin/prlimit","--as=2147483648","--core=0","--",z3,"-smt2","-in","-T:2"]
    cap=min(3.0,max(.01,STOP-time.time()-5))
    with guarded_popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True) as p:
        try:
            ans,err=p.communicate(body.encode(),timeout=cap)
        except subprocess.TimeoutExpired:
            p.kill()
            ans,err=p.communicate()
            return "timeout"
        if p.returncode or b"(error " in ans:
            return "error"
        if ans.strip() in (b"sat",b"unsat",b"unknown"):
            return ans.strip().decode()
        if b"timeout" in ans:
            return "timeout"
        return "unrecognized"

def scan(text,expected):
    found={}
    every=list(MARKER.finditer(text))
    for begin,end in zip(every,every[1:]):
        if begin[2]=="a" and end[2]=="b" and begin[1]==end[1]:
            index=int(begin[1])
            if index in expected:
                found[index]=text[begin.end():end.start()].strip()
    return found

def run_slot(slot,scope):
    os.sched_setaffinity(0,{CORES[slot]})
    out=BASE/f"worker-{slot}.jsonl"
    if out.exists():
        raise FileExistsError(out)
    for number in range(slot,len(scope["inputs"]),len(CORES)):
        rec=scope["inputs"][number]
        row=rec["session"]
        case,program=prepare_one(rec,number)
        rng=random.Random(3319+number)
        order=list(scope["configs"])
        rng.shuffle(order)
        for position,arm in enumerate(order):
            check_budget()
            measured=cvc_capture(program,scope["configs"][arm],rec["arrays_exp"])
            actual=ev.ANSWERS.findall(measured["stdout"])
            correct=actual==row["oracle"][:len(actual)]
            if measured["status"]=="solved" and len(actual)!=len(row["oracle"]):
                correct=False
            marked=scan(measured["stdout"],case)
            outputs=[]
            for index,proof in case.items():
                if index not in marked:
                    results={"status":"unobserved"}
                else:
                    try:
                        values=terms(marked[index])
                        assert len(values)==1
                        selected=Counter(render(t) for t in values[0])
                        want=Counter(proof["selected_assumptions"])
                        is_subset=not (selected-want)
                        fresh=independently_unsat(proof["context"],values[0])
                        results={"status":fresh,"exact_subset":is_subset,"size":sum(selected.values()),
                                 "response":marked[index],"supplied_size":sum(want.values())}
                    except (ValueError,AssertionError) as e:
                        results={"status":"malformed","message":str(e),"response":marked[index][:1000]}
                outputs.append({"query":proof["query_no"],"index":index,**results})
            raw_capture=BASE/f"stdout-{number:03d}-{arm}.txt"
            raw_capture.write_text(measured["stdout"])
            append(out,{"group":row["group"],"family":rec["family"],"core":CORES[slot],"position":position,
                        "arm":arm,"session_status":measured["status"],"wall_s":measured["wall_s"],"answers":actual,
                        "answers_match_prefix":correct,"source_sha256":row["sha256"],"command_sha256":digest(program),
                        "stdout_sha256":digest(raw_capture),"checks":outputs,"stderr":measured["stderr"][:2000]})

def audit(scope):
    rows=[r for path in sorted(BASE.glob("worker-*.jsonl")) for r in lines(path)]
    expected={}
    for i,f in enumerate(scope["inputs"]):
        a=f["session"]["group"]
        rng=random.Random(3319+i)
        order=list(scope["configs"])
        rng.shuffle(order)
        expected[a]=(f,order,i)
    assert len(rows)==len(expected)*len(scope["configs"])
    for r in rows:
        ref,order,i=expected[r["group"]]
        assert order[r["position"]]==r["arm"] and r["core"]==CORES[i%len(CORES)]
        assert r["source_sha256"]==ref["session"]["sha256"]
        assert r["stdout_sha256"]==digest(BASE/f'stdout-{i:03d}-{r["arm"]}.txt')
        possible=ref["session"]["oracle"]
        prefix=r["answers"]==possible[:len(r["answers"])]
        if r["session_status"]=="solved":
            prefix=prefix and len(r["answers"])==len(possible)
        assert r["answers_match_prefix"]==prefix
        assert all(possible[target["query"]-1]=="unsat" for target in r["checks"])
    keys={(r["group"],r["arm"]) for r in rows}
    assert len(keys)==len(rows)
    results={}
    for arm in scope["configs"]:
        runs=[r for r in rows if r["arm"]==arm]
        checks=[c for r in runs for c in r["checks"]]
        proof=Counter(x["status"] for x in checks)
        known=sum(c["status"] not in ("unobserved","malformed") for c in checks)
        exact=sum(c.get("exact_subset",False) for c in checks)
        results[arm]={"programs":len(runs),"targets":len(checks),"certified_by_z3":proof["unsat"],
                      "returned_sets":known,"lexically_identical_subset":exact,
                      "statuses":proof,"observed_sat_counterexamples":proof["sat"],
                      "source_sessions":Counter(r["session_status"] for r in runs),
                      "cvc5_response_mismatches":sum(not r["answers_match_prefix"] for r in runs)}
    R={"programs":len(expected),"repeats":1,"rows":len(rows),"per_arm":results,
       "scope_sha256":digest(SCOPE),"files_verified":len(rows),
       "source_counts":dict(Counter(row["family"] for row in scope["inputs"]))}
    write_json(BASE/"VERIFY.json",R)
    ref=results["default"]
    headline=f"Независимый Z3 подтвердил {ref['certified_by_z3']} из {ref['returned_sets']} возвращённых ядер предположений default."
    write_json(ROOT/"evidence/15-cores.json",{
        "title":"15 · Независимая проверка возвращённых ядер",
        "done":f"Проверены ответы и ядра предположений в {len(rows)} наборах оценок.",
        "summary":headline,
        "paragraphs":[
            f"Использованы {len(expected)} ранее записанных программ, {len(R['source_counts'])} групп источников и один полный повтор на режим. В каждом методе до трёх фиксированных запросов ядра. Для каждого полученного ядра восстановлен контекст до соответствующей проверки и отдельно проверен в Z3 с исходным стеком.",
            "Дословное подмножество указано отдельно от подтверждённой невыполнимости. Решатель может печатать синтаксически нормализованные выражения. Не полученные до ограничения ответы отмечены как непроверенные. В эксперименте проверяется свойство ядра, а модель задаётся отдельным объектом."
        ],
        "tables":[card_table(f"Ответы на запросы ядра · {len(expected)} программ, 1 повтор на режим",[
                    "Режим","Целей","Получено","Подмножество","Z3 UNSAT","Z3 SAT","Z3 unknown/таймаут/ошибка","Ядра без ответа","Несовпадения cvc5"],
                    [[k,v["targets"],v["returned_sets"],v["lexically_identical_subset"],v["certified_by_z3"],
                      v["observed_sat_counterexamples"],sum(v["statuses"].get(x,0) for x in ("unknown","timeout","error","unrecognized")),
                      v["statuses"].get("unobserved",0),v["cvc5_response_mismatches"]] for k,v in results.items()])],
        "paths":["protocols/returned-core-audit.json","runs/14-core-audit/VERIFY.json",
                 "runs/14-core-audit/instrumented/","runs/14-core-audit/worker-{0..5}.jsonl"],
        "recommendation":"Включать независимую проверку невыполнимости возвращённых предположений при изменении SMT-LIB формата или настройки решателя.",
        "next":"Проверить стоимость чтения и преобразования моделей непосредственно в живом клиенте при том же формате массивов."
    })
    build()

def main():
    scope=freeze()
    if not (BASE/"VERIFY.json").exists():
        with ProcessPoolExecutor(max_workers=len(CORES)) as worker:
            futures=[worker.submit(run_slot,i,scope) for i in range(len(CORES))]
            for task in futures:
                task.result(timeout=1500)
    audit(scope)
if __name__=="__main__":
    from recovery import attempt
    from search_retest import measure
    if (ROOT/"runs/05-search/train-complete.json").exists():
        attempt("search-retiming",measure)
    main()
