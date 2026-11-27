"""End-to-end native Why3 proof and counterexample-handling diagnosis."""
from concurrent.futures import ThreadPoolExecutor
from statistics import mean

from common import ROOT, CPUS, environment, now, ordered, read, run, sha, write
from eval import ANSWER
from hw_audit import commands

CFG='''[prover]
name = "CVC5"
version = "1.4.1"
alternative = ""
command = "/campaign/scripts/why3-native-shim.sh {flags} %f"
driver = "cvc5"
shortcut = "cvc5"
editor = ""
in_place = false
interactive = false
'''
LOCK=ROOT/"evidence/opt-search-lock.json"
proofs=read(ROOT/"inputs/why3-generated/manifest.json")["items"]
files={r["family"]:r for r in proofs}
names=("Fuel","Balance","Triangular","Decision","Wrong")
assert set(names)==set(files)
args={"0":[],"fixed":read(LOCK)["contrast"]}
out=ROOT/"raw/why3-live"
plan=[]
for rep in range(2):
    for index,name in enumerate(ordered(names,814205,rep)):
        plan.append(dict(rep=rep,name=name,cpu=CPUS[(index+rep)%len(CPUS)],order=ordered(args,814205,rep,name)))
write(out/"assignment.json",dict(lock_sha256=sha(LOCK),arms=args,cases=plan))
configs={}
for arm,opts in args.items():
    assert all(s.startswith("--") and " " not in s and '"' not in s for s in opts)
    cfg_file=out/"configs"/arm/"why3.conf"
    cfg_file.parent.mkdir(parents=True,exist_ok=True)
    text=CFG.format(flags=" ".join(opts))
    if cfg_file.exists():
        assert cfg_file.read_text()==text
    else:
        cfg_file.write_text(text)
    configs[arm]="/campaign/"+str(cfg_file.relative_to(ROOT))

def run_core(cpu):
    data=[]
    for case in (r for r in plan if r["cpu"]==cpu):
        name=case["name"]
        expected="sat" if name in ("Decision","Wrong") else "unsat"
        for arm in case["order"]:
            where=out/f"r{case['rep']}"/name/arm
            opts=args[arm]
            source="/campaign/inputs/why3-source/"+name+".mlw"
            p=run(where,[ROOT/"scripts/why3-wrap.sh",where,"prove","-C",configs[arm],"-P","cvc5","-t","0",source],
                  cpu,8.,environment())
            decoded=(where/"stdout").read_text()
            native=where/"native.smt2"
            exported=ROOT/files[name]["path"]
            solver=where/"solver.stdout"
            statuses=ANSWER.findall(solver.read_text()) if solver.exists() else []
            same=commands(native,False)==commands(exported,False) if native.exists() else False
            verified=any(s.lstrip().startswith("Prover result is: Valid") for s in decoded.splitlines())
            app_ok=verified if expected=="unsat" else (not verified and "Prover result is:" in decoded)
            okay=statuses==[expected] and same and app_ok and not p["timeout"]
            data.append(dict(name=name,arm=arm,rep=case["rep"],cpu=cpu,flags=opts,start=p["start"],stop=p["stop"],
                             expected=expected,raw_solver=statuses,why3_verified=verified,commands_same=same,
                             outcome_contract=okay,app_ret=p["ret"],status="solved" if okay else "error",
                             par2=p["seconds"] if okay else 16.,
                             input_sha256=sha(native) if native.exists() else None,solver_stdout_sha256=sha(solver) if solver.exists() else None))
    return data

with ThreadPoolExecutor(max_workers=4) as pool:
    rows=[r for sub in pool.map(run_core,CPUS) for r in sub]
write(out/"rows.json",rows)
summary={}
for arm in args:
    rs=[r for r in rows if r["arm"]==arm]
    summary[arm]=dict(n=len(rs),solved=sum(r["outcome_contract"] for r in rs),wrong=sum(r["raw_solver"] in (["sat"],["unsat"]) and r["raw_solver"]!=[r["expected"]] for r in rs),
                      error=sum(r["status"]=="error" for r in rs),par2=mean(r["par2"] for r in rs))
write(out/"summary.json",dict(at=now(),summary=summary,excluded=[],n=len(names),source="Why3 live",original_commands=sum(r["commands_same"] for r in rows)))
print(now(),summary,flush=True)
