"""Common evaluator for tuning, baselines and locked comparisons."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import time

from common import ROOT, CVC, BMC, Z3, DAFNY, CPUS, environment, now, ordered, read, run, sha, write

MENU = {
    "hw": {
        "0": [], "AA": [], "extend": ["--bv-rw-extend-eq"], "gauss": ["--bv-gauss-elim"],
        "bool": ["--bv-to-bool"], "prop": ["--no-bv-propagate"], "minisat": ["--sat-solver=minisat"],
        "static": ["--no-static-learning"], "repeat": ["--repeat-simp"], "eager": ["--bitblast=eager"],
        "internal": ["--bv-solver=bitblast-internal"], "ite": ["--ite-simp"],
        "eager-eval": ["--bv-eager-eval"], "pow2": ["--bv-intro-pow2"],
        "abstraction": ["--bv-abstraction", "--bv-abstraction-size=5"],
        "simpl-none": ["--simplification=none"], "no-eq": ["--no-bv-eq-engine"],
    },
    "opt": {
        "0": [], "AA": [], "none": ["--arith-prop=none"], "both": ["--arith-prop=both"],
        "equal": ["--arith-rewrite-equalities"], "dio": ["--no-dio-solver"], "minisat": ["--sat-solver=minisat"],
        "static": ["--no-static-learning"], "repeat": ["--repeat-simp"], "decision": ["--decision=justification"],
        "ite": ["--ite-simp"],
    },
    "dafny": {"0": [], "AA": [], "static": ["--no-static-learning"], "ite": ["--ite-simp"], "repeat": ["--repeat-simp"]},
}
ANSWER = re.compile(r"^(sat|unsat|unknown)$",re.MULTILINE)
ERROR = re.compile(r"(?im)^\s*(?:\(error|error:|fatal error|Traceback|##.*Solver Error:)")


def existing_text(path):
    return path.read_text(errors="replace") if path.is_file() else ""


def manifest(part,producer,profile):
    return read(ROOT/"inputs"/profile/part/producer/"manifest.json")["items"]


def predict_hw(where,item,flags,engine):
    if engine=="replay-z3":
        stream=ROOT/item["replay"]
        return [Z3,"-smt2",stream],environment(),where/"stdout",stream
    path=ROOT/item["path"]
    solver_name="z3" if engine=="z3" else "cvc5"
    tool_path=ROOT/"deps/shims" if engine=="z3" else CVC.parent
    env=environment(PATH=str(tool_path)+":/usr/bin:/bin", PYTHONPATH=str(ROOT/"deps/yosys/usr/share/yosys"))
    cmd=[BMC,"-s",solver_name,"--noprogress","--presat","--dump-smt2",where/"stream.smt2",
         "--dummy",where/"replies.txt","-t",str(item["depth"])]
    if item["expected"]=="sat":
        cmd.extend(["--dump-vcd",where/"witness.vcd"])
    for flag in flags:
        cmd.extend(["-S",flag])
    return cmd+[path],env,where/"replies.txt",where/"stream.smt2"


def evaluate(where,item,flags,cpu,cap,engine="cvc5"):
    if (where/"result.json").exists():
        row=read(where/"result.json")
        assert (row["id"],row["flags"],row["engine"],row["cap"],row["cpu"]) == (item["id"],flags,engine,cap,cpu)
        assert sha(ROOT/item["path"])==item["sha256"]==row["input_sha256"]
        for filename, digest in row["raw_sha256"].items():
            assert sha(where/filename)==digest
        return row
    path=ROOT/item["path"]
    assert sha(path)==item["sha256"]
    producer=item["producer"]
    output_path=where/"stdout"
    stream=path
    env=environment()
    if producer=="hw":
        cmd,env,output_path,stream=predict_hw(where,item,flags,engine)
    elif producer=="dafny":
        cmd=[DAFNY,"verify","--cores","1","--verification-time-limit","0","--solver-log",where/"stream.smt2",path]
        if engine=="cvc5":
            boogie="-proverOpt:SOLVER=cvc5" + "".join(f" -proverOpt:C:{flag}" for flag in flags)
            cmd += ["--solver-path", CVC, "--boogie", boogie]
        stream=where/"stream.smt2"
    elif engine=="z3":
        cmd=[Z3,"-smt2",path]
    else:
        cmd=[CVC,"--lang=smt2",*flags,path]
    process=run(where,cmd,cpu,cap,env)
    stdout=existing_text(where/"stdout")
    stderr=existing_text(where/"stderr")
    answer_stream=existing_text(output_path)
    answers=ANSWER.findall(answer_stream)
    expected=item["expected"]
    partial=0
    wrong=False
    okay=False
    observed=None
    witness_audit=None
    if producer=="hw":
        text=existing_text(stream)
        queries=len(re.findall(r"(?m)^\s*\(check-sat(?:-assuming)?\b",text))
        if engine=="replay-z3":
            expected_answers=item["expected_answers"]
            wrong=any(a in ("sat","unsat") and a!=b for a,b in zip(answers,expected_answers))
            partial=sum(a==b for a,b in zip(answers,expected_answers))
            okay=answers==expected_answers and len(answers)==queries and process["ret"]==0
        else:
            prefix=["sat","unsat"] * item["depth"]
            expected_answers=prefix if expected=="unsat" else ["sat","sat"]
            wrong=any(a in ("sat","unsat") and a!=b for a,b in zip(answers,expected_answers))
            partial=sum(a==b for a,b in zip(answers,expected_answers))
            if expected=="unsat":
                okay=process["ret"]==0 and "Status: PASSED" in stdout and answers==prefix and queries==len(prefix)
                observed="unsat" if "Status: PASSED" in stdout else "sat" if "Status: FAILED" in stdout else None
            else:
                okay=(process["ret"]==1 and "Status: FAILED" in stdout and answers[:2]==["sat","sat"]
                      and all(x=="sat" for x in answers) and queries==len(answers)
                      and (where/"witness.vcd").is_file())
                if okay:
                    from hw_witness import check
                    try:
                        witness_audit=check(where/"witness.vcd",item["family"],item["width"],item.get("mutation_trigger"))
                        wrong=wrong or not witness_audit["ok"]
                    except (ValueError, KeyError) as error:
                        witness_audit={"ok":False,"reason":repr(error)}
                        wrong=True
                observed="sat" if "Status: FAILED" in stdout else "unsat" if "Status: PASSED" in stdout else None
            wrong=wrong or (not process["timeout"] and observed in ("sat","unsat") and observed!=expected)
    elif producer=="dafny":
        verdict=re.findall(r"finished with (\d+) verified, (\d+) errors?",stdout)
        observed=list(map(int,verdict[-1])) if verdict else None
        queries=None
        if observed:
            wrong=observed[0]>expected[0] or (observed[0]==expected[0] and observed[1]<expected[1]
                and "Prover error" not in stdout+stderr and "BadExprFromProver" not in stdout+stderr)
        okay=(observed==expected and "Prover error" not in stdout+stderr and "BadExprFromProver" not in stdout+stderr
              and process["ret"] in ([0] if expected[1]==0 else [0,4]))
        partial=sum(observed) if okay else 0
    else:
        queries=len(re.findall(r"(?m)^\s*\(check-sat\b",existing_text(path)))
        observed=answers[0] if len(answers)==1 else None
        wrong=observed in ("sat","unsat") and observed!=expected
        okay=observed==expected and len(answers)==queries==1 and process["ret"]==0
        partial=int(okay)
    errors=(ERROR.search(stdout+"\n"+stderr) is not None) if producer!="dafny" else False
    if wrong:
        result="wrong"
    elif process["timeout"]:
        result="timeout"
    elif okay and not errors:
        result="solved"
    elif errors or process["error"] or process["ret"] not in (0,1) or producer=="dafny":
        result="error"
    elif "unknown" in answers:
        result="unknown"
    else:
        result="incomplete"
    raw={k:sha(where/k) for k in ("stdout","stderr","stream.smt2","replies.txt","witness.vcd") if (where/k).is_file()}
    row=dict(id=item["id"],family=item["family"],producer=producer,engine=engine,flags=flags,cpu=cpu,cap=cap,
             start=process["start"],stop=process["stop"],seconds=process["seconds"],
             status=result,par2=process["seconds"] if result=="solved" else 2*cap,
             observed=observed,expected=expected,answers=answers,queries=queries,verified_checks=partial,input_sha256=item["sha256"],
             stream_sha256=sha(stream) if stream.is_file() else None,raw_sha256=raw,witness=witness_audit)
    write(where/"result.json",row)
    return row


def blocked(where,items,arms,cap,reps,seed,engine="cvc5",cpus=CPUS):
    where.mkdir(parents=True,exist_ok=True)
    job_file=where/"assignment.json"
    jobs=[]
    items=[i for i in items if "error" not in i]
    for rep in range(reps):
        for idx,item in enumerate(ordered(items,seed,rep,"items")):
            jobs.append(dict(id=item["id"],rep=rep,cpu=cpus[(idx+rep)%len(cpus)],
                             order=ordered(arms,seed,rep,item["id"])))
    plan=dict(jobs=jobs,arms=arms,cap=cap,engine=engine)
    if job_file.exists():
        assert read(job_file)==plan
    else:
        write(job_file,plan)
    lookup={i["id"]:i for i in items}
    def run_core(cpu):
        rows=[]
        for job in (x for x in jobs if x["cpu"]==cpu):
            for arm in job["order"]:
                saved=where/f"r{job['rep']}"/job["id"]/arm
                row=evaluate(saved,lookup[job["id"]],arms[arm],cpu,cap,engine)
                rows.append(dict(row,arm=arm,rep=job["rep"],location=str(saved.relative_to(ROOT))))
        return rows
    with ThreadPoolExecutor(max_workers=len(cpus)) as pool:
        rows=[r for group in pool.map(run_core,cpus) for r in group]
    write(where/"rows.json",rows)
    return rows


def summarize(rows):
    by={}
    for row in rows:
        by.setdefault(row["arm"],[]).append(row)
    return {key:dict(par2=round(sum(r["par2"] for r in group)/len(group),5),n=len(group),
                    solved=sum(r["status"]=="solved" for r in group),
                    wrong=sum(r["status"]=="wrong" for r in group),
                    error=sum(r["status"]=="error" for r in group),
                    checks=sum(r["verified_checks"] for r in group)) for key,group in by.items()}


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("task",choices=("oracle","screen"))
    p.add_argument("producer",choices=("hw","opt","dafny","public"))
    p.add_argument("--part",default="train")
    p.add_argument("--profile",default="base")
    p.add_argument("--cap",type=float,default=8.)
    p.add_argument("--reps",type=int,default=1)
    p.add_argument("--cpus",type=int,nargs="+",default=CPUS)
    p.add_argument("--arms",nargs="+")
    p.add_argument("--label")
    a=p.parse_args()
    rows=manifest(a.part,a.producer,a.profile)
    if a.task=="screen":
        menu=MENU[a.producer]
        arms={k:menu[k] for k in a.arms} if a.arms else menu
        eng="cvc5"
    else:
        arms={"Z3":[]}
        eng="z3"
    where=ROOT/"raw"/f"{a.task}-{a.producer}-{a.profile}-{a.part}"
    if a.arms:
        where=where.with_name(where.name+"-"+"-".join(a.arms))
    if a.label:
        where=where.with_name(where.name+"-"+a.label)
    print(now(),summarize(blocked(where,rows,arms,a.cap,a.reps,3311,eng,a.cpus)),flush=True)
