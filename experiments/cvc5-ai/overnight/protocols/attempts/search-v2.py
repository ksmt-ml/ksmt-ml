"""Compare online Sol and RF scoring under a common proposal/feedback contract."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import itertools
import json
import math
import os
from pathlib import Path
import random
import resource
import signal
import subprocess
import sys
import time

import numpy as np

from corpus import materialize
from portfolio import compatible, smoke_matrix
from replay import CAMPAIGN, CORES, DATA, PYTHON, ROOT, STOP, append, check_budget, digest, ev, guarded_popen, lines, load, log, now
from report import build, write_json

BASE=ROOT/"runs/05-search"
PLAN=ROOT/"protocols/search.json"
SMOKE=BASE/"smoke"
ARMS=("sol","rf","smac")
SEEDS=(18713,36251,59077,71591)
SPACE={k:[v for v in values if (k,v)!=("ite-simp","on")] for k,values in ev.SPACE.items()}
WARM_COUNT=14
POOL_SIZE=36
FORCED=2
EXPLOIT=8
ROUNDS=40
HORIZON=1800
MATCHED_COUNT=64
MODEL_CAP=85
PROTO=ROOT/"model-protocol"
TRAIN=DATA/"train/manifest.json"
VAL=DATA/"validation/manifest.json"

def canonical(params):
    return tuple(compatible(params).items())

def vector(config):
    return "".join(str(v.index(config.get(k,"auto"))) for k,v in SPACE.items())

def sparse(rng,mode):
    p=rng.choice([.22,.40,.62,.82]) if mode is None else mode
    return compatible({k:rng.choice(v[1:]) for k,v in SPACE.items() if rng.random()<p})

def source_plan(seed):
    rng=random.Random(seed^0x2B192167)
    seen={canonical({})}
    def produce(p):
        for _ in range(2500):
            record=sparse(rng,p)
            key=canonical(record)
            if key not in seen:
                seen.add(key)
                return record
        raise RuntimeError("exhausted options")
    warm=[{}]+[produce(.22 if i%3==0 else .46) for i in range(WARM_COUNT-1)]
    rounds=[]
    for _ in range(ROUNDS):
        pool=[produce(None) for _ in range(POOL_SIZE)]
        explore=rng.sample(range(POOL_SIZE),FORCED)
        rounds.append({"pool":pool,"forced":explore})
    return {"seed":seed,"warm":warm,"rounds":rounds}

def plan_model():
    if PLAN.exists():
        return load(PLAN)
    route=ROOT/"protocols/route-before-holdout.json"
    route_plan=load(route)
    baseline=ROOT/"runs/01-stability/metrics.json"
    record={
        "locked":now(),"why":route_plan["decision"],
        "route_sha256":digest(route),"stability_sha256":digest(baseline),
        "search_seeds":SEEDS,"horizon_s":HORIZON,"equal_count_target":MATCHED_COUNT,
        "train_manifest_sha256":digest(TRAIN),"validation_manifest_sha256":digest(VAL),
        "warm_count":WARM_COUNT,"pool_size":POOL_SIZE,"forced":FORCED,"exploitation":EXPLOIT,
        "rounds_max":ROUNDS,"model_call_timeout_s":MODEL_CAP,"training_cutoff_s":1.5,
        "training_warm_start":"all three arms receive bitwise-identical observations from one real pinned warm run for each seed; its actual wall is charged fully to each arm",
        "identical_sol_rf":"same coded pool, forced random choices, same target (PAR2 or 4.0 if ineligible), same rank-to-batch algorithm, same pinned evaluator and horizon",
        "training_counts":"all completed real candidates, including ineligible; a cut-off candidate is outside both counts and incumbent selection; no model scores count as real evaluations",
        "wall_snapshot":"take candidates completed by the common logical wall horizon; same native SMAC/default held-out replay; report equal-budget and equal-trial snapshots separately",
        "count_snapshot":"first 64 completed evaluations including common warm. When arm has fewer, use the per-seed minimum number completed by the actual Sol, RF and SMAC runs.",
        "native_smac":"SMAC 2.4.0 HyperparameterOptimizationFacade ask/tell; same warm observations seeded; native proposal process",
        "options":SPACE,"randomization":"disjoint precomputed pools per search seed, cvc5 train program order keyed by seed and configuration, pinned one core per arm",
        "validation":"top three eligible unique configs by measured train PAR2 plus default; all snapshot shortlists combined and deduped within each search seed; six randomized paired repeats on the original 11 programs",
        "test":"new independent family-disjoint seed, unseen by every model and every selection. All locked weights and configs written before full generation.",
        "invalid":"ite-simp=on rejected by all arms; black-box checks on training sessions that exercise model and unsat cores before main launch",
        "files_sha256":{str(f.relative_to(ROOT)):digest(f) for f in
                        (ROOT/"search.py",ROOT/"isolated_sol.sh",PROTO/"schema.json",PROTO/"README.md")},
    }
    write_json(PLAN,record)
    PLAN.chmod(0o444)
    return record

def init_seed(base,seed):
    base.mkdir(parents=True,exist_ok=True)
    path=base/"proposals.json"
    if not path.exists():
        write_json(path,source_plan(seed))
        path.chmod(0o444)
    return load(path)

def can_measure(deadline):
    return min(deadline-time.monotonic(),STOP-time.time()-2)>0.1

def run_candidate(out,train,seed,index,params,end,stage,proposal=None):
    params=compatible(params)
    verify=ev.verify(train)
    ident=hashlib.sha256(vector(params).encode()).digest()
    rng=random.Random(int.from_bytes(ident[:8],"big")^seed)
    rng.shuffle(verify)
    tick=time.monotonic()
    rows=[]
    for pos,item in enumerate(verify):
        if not can_measure(end):
            break
        row=ev.measure(train,item,params,1.50,end)
        append(out/"sessions.jsonl",{"candidate_id":index,"stage":stage,"position":pos,**row})
        rows.append(row)
        if row["status"]=="budget":
            break
    stats=ev.aggregate(rows)
    complete=len(rows)==len(verify) and stats["par2"] is not None and can_measure(end)
    if not complete:
        stats["eligible"]=False
    elif stats["counts"].get("unknown"):
        stats["eligible"]=False
    record={"candidate_id":index,"config":params,"stage":stage,"proposal":proposal,
            "measurement_s":time.monotonic()-tick,"complete":complete,
            "score":stats["par2"] if complete and stats["eligible"] else 4.0,**stats}
    return record

def train_warm(seedfolder,seed,core,n=WARM_COUNT):
    start=time.monotonic()
    os.sched_setaffinity(0,{core})
    proposals=init_seed(seedfolder,seed)
    out=seedfolder/"warm"
    out.mkdir(exist_ok=False)
    end=start+min(370,STOP-time.time()-4)
    measurements=[]
    for index,params in enumerate(proposals["warm"][:n]):
        r=run_candidate(out,TRAIN,seed,index,params,end,"warm")
        r["finish_s"]=time.monotonic()-start
        append(out/"candidates.jsonl",r)
        measurements.append(r)
    assert all(r["complete"] for r in measurements)
    assert measurements[0]["eligible"]
    elapsed=time.monotonic()-start
    write_json(out/"locked.json",{"seed":seed,"core":core,"wall_s":elapsed,"configs":n,
                                 "candidates_sha256":digest(out/"candidates.jsonl")})

def model_trace(path):
    usage=Counter()
    errors=0
    success=False
    commands=0
    event_types=Counter()
    try:
        with path.open() as f:
            for line in f:
                try:
                    event=json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind=event.get("type")
                event_types[kind]+=1
                if kind=="turn.completed":
                    usage.update({k:v for k,v in event.get("usage",{}).items() if isinstance(v,int)})
                    success=True
                elif kind in ("error","turn.failed"):
                    errors+=1
                elif kind=="item.completed" and event.get("item",{}).get("type")=="command_execution":
                    commands+=1
    except FileNotFoundError:
        pass
    return {"usage":dict(usage),"turn_completed":success,"error_events":errors,"tool_commands":commands,"event_types":dict(event_types)}

def bounded_syscall(command, stdin, stdout, stderr, cap):
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE,(48*1024**2,48*1024**2))
    with stdin.open("rb") as src,stdout.open("wb") as out,stderr.open("wb") as err:
        with guarded_popen(command,stdin=src,stdout=out,stderr=err,start_new_session=True,
                              preexec_fn=limits,cwd=ROOT) as p:
            try:
                return p.wait(timeout=cap),False
            except subprocess.TimeoutExpired:
                # The namespace dies with bwrap even though the CLI uses a new session.
                os.killpg(p.pid,signal.SIGTERM)
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGKILL)
                    p.wait()
                return p.returncode,True

def model_scores(out,call,seed,history,pool,forced,elapsed,remaining,feedback):
    render=lambda p:vector(p)
    packet={
        "search_seed":seed,"round":call,"elapsed_search_seconds":round(elapsed,3),
        "remaining_search_seconds":round(remaining,3),
        "options":[[name,values] for name,values in SPACE.items()],
        "history":[{"id":r["candidate_id"],"x":render(r["config"]),"cost":round(r["score"],6),
                    "timeouts":r["counts"].get("timeout",0),"eligible":r["eligible"]}
                    for r in history if r["complete"]],
        "new_real_feedback_ids":feedback,"candidates":[render(p) for p in pool],
        "forced_exploration_indices":forced,
    }
    prefix=("Predict the actual training mean PAR2 seconds for every candidate in the fixed pool, in input order. "
            "You are gpt-6.2-sol scoring a sequential black-box cvc5 experiment. The supplied history is REAL feedback "
            "from fifteen complete incremental Java sessions under a 1.5 second per-session limit. "
            "Each config is encoded by one digit per option: digit k selects value k in that option's listed array. "
            "Lower is better. An ineligible run is encoded as 4.0. The controller will always evaluate the given "
            "forced exploration indices and then eight remaining candidates with smallest predictions. "
            "Use only the provided training feedback. Do not call tools or visit external sites. "
            "Return JSON only, with predictions as an array of exactly "
            f"{len(pool)} finite numbers between 0 and 4.\n")
    model=out/"model"
    model.mkdir(exist_ok=True)
    name=f"call-{call:03d}"
    request=model/f"{name}.prompt"
    request.write_text(prefix+json.dumps(packet,separators=(",",":"),allow_nan=False)+"\n")
    reply=f"{name}.reply.json"
    cmd=[ROOT/"isolated_sol.sh",model,TRAIN.parent,PROTO,"--output-schema","/protocol/schema.json",
         "--output-last-message",f"/work/{reply}","-"]
    cap=min(MODEL_CAP,remaining-.3,STOP-time.time()-4)
    if cap<=1:
        raise TimeoutError("no model budget remaining")
    start=time.monotonic()
    stdout=model/f"{name}.events.jsonl"
    rc,timeout=bounded_syscall(cmd,request,stdout,model/f"{name}.stderr",cap)
    dt=time.monotonic()-start
    record={"round":call,"wall_s":dt,"rc":rc,"timeout":timeout,"limit_s":cap,**model_trace(stdout),
            "new_real_feedback_ids":feedback,"elapsed_search_seconds":elapsed,"remaining_search_seconds":remaining}
    values=None
    try:
        pred=load(model/"work"/reply)["predictions"]
        if not record["turn_completed"] or rc!=0:
            raise ValueError("model turn failed")
        if len(pred)!=len(pool) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=4 for v in pred):
            raise ValueError("invalid prediction array")
        values=[float(v) for v in pred]
    except (FileNotFoundError,KeyError,TypeError,ValueError) as ex:
        record["failure"]=str(ex)
    record["valid_representation"]=values is not None
    append(out/"model-calls.jsonl",record)
    return values

def rf_scores(seed,call,history,pool):
    from sklearn.ensemble import RandomForestRegressor
    def encode(p):
        return [int(p.get(key,"auto")==v) for key,vals in SPACE.items() for v in vals]
    complete=[r for r in history if r["complete"]]
    forest=RandomForestRegressor(n_estimators=128,min_samples_leaf=2,n_jobs=1,
                                 max_features=.75,random_state=(seed+call)&0xFFFFFFFF)
    forest.fit([encode(r["config"]) for r in complete],[r["score"] for r in complete])
    return [float(v) for v in forest.predict([encode(p) for p in pool])]

def order(scores, forced, n):
    others=sorted(set(range(len(scores)))-set(forced),key=lambda i:(scores[i],i))
    return [*forced,*others[:n]]

def native_warm(out,seed,history):
    import tune as frozen_tune
    frozen_tune.SPACE={key:list(values) for key,values in SPACE.items()}
    runner=frozen_tune.NativeSmac(out/"native-smac",seed,initial=[r["config"] for r in history[1:]])
    observation={canonical(r["config"]):r for r in history}
    for _ in history:
        trial,params=runner.ask()
        r=observation.pop(canonical(params))
        runner.tell(trial,{**r,"elapsed_measurement_s":r["measurement_s"]})
    assert not observation
    return runner

def run_arm(seedfolder,seed,arm,core,smoke=False, horizon=HORIZON):
    pin=time.monotonic()
    W=load(seedfolder/"warm/locked.json")
    anchored_wall=W["wall_s"]
    end=pin+min(horizon-anchored_wall,STOP-time.time()-7)
    os.sched_setaffinity(0,{core})
    out=seedfolder/arm
    out.mkdir(exist_ok=False)
    proposals=load(seedfolder/"proposals.json")
    assert digest(seedfolder/"warm/candidates.jsonl")==W["candidates_sha256"]
    history=lines(seedfolder/"warm/candidates.jsonl")
    by_key={canonical(r["config"]):r for r in history}
    write_json(out/"plan.json",{"seed":seed,"arm":arm,"core":core,"smoke":smoke,"started":now(),
               "search_horizon_s":horizon,"warm_shared_wall_s":anchored_wall,
               "proposals_sha256":digest(seedfolder/"proposals.json"),"warm_sha256":W["candidates_sha256"]})
    def elapsed():
        return time.monotonic()-pin+anchored_wall
    def perform(params,origin):
        if not can_measure(end):
            return None
        record=run_candidate(out,TRAIN,seed,len(history),params,end,arm,origin)
        record["arm_elapsed_s"]=time.monotonic()-pin
        record["finish_s"]=elapsed()
        append(out/"candidates.jsonl",record)
        history.append(record)
        if record["complete"]:
            by_key[canonical(params)]=record
        return record

    decision_status="ok"
    fallback_count=0
    if arm in ("sol","rf"):
        confirmed=[]
        feedback=[r["candidate_id"] for r in history]
        for turn,round_data in enumerate(proposals["rounds"][:2 if smoke else ROUNDS]):
            remaining=end-time.monotonic()
            if remaining<(15 if arm=="sol" else 2):
                break
            pool=round_data["pool"][:8] if smoke else round_data["pool"]
            forced=[0,1] if smoke else round_data["forced"]
            if arm=="sol":
                values=model_scores(out,turn,seed,history,pool,forced,elapsed(),remaining,feedback)
            else:
                values=rf_scores(seed,turn,history,pool)
            use_model=values is not None
            if not use_model:
                fallback_count+=1
                if fallback_count>=2:
                    decision_status="model_incomplete"
                    break
                picks=forced
            else:
                picks=order(values,forced,1 if smoke else EXPLOIT)
                confirmed.append(turn)
            append(out/"decisions.jsonl",{"round":turn,"forced":forced,"predictions":values,
                                        "selection":picks,"elapsed_s":elapsed(),"feedback":feedback})
            feedback=[]
            for pos,index in enumerate(picks):
                tag={"round":turn,"index":index,"ranked":index not in forced,"model_ok":use_model,"position":pos}
                record=perform(pool[index],tag)
                if record is None or not record["complete"]:
                    break
                feedback.append(record["candidate_id"])
            if not can_measure(end):
                break
        if arm=="sol" and len(confirmed)<2:
            decision_status="model_incomplete"
    else:
        runner=native_warm(out,seed,history)
        extras=0
        loops=0
        while can_measure(end) and (not smoke or extras<5):
            loops+=1
            if loops>1000:
                decision_status="proposal_limit"
                break
            trial,cfg=runner.ask()
            k=canonical(cfg)
            if k in by_key:
                r=by_key[k]
                runner.tell(trial,{**r,"elapsed_measurement_s":r["measurement_s"]})
                append(out/"native-duplicates.jsonl",{"iteration":loops,"config":cfg})
                continue
            record=perform(cfg,{"native_iteration":loops})
            if record is None or not record["complete"]:
                break
            extra={**record,"elapsed_measurement_s":record["measurement_s"]}
            runner.tell(trial,extra)
            extras+=1
    complete=[r for r in history if r["complete"]]
    assert len({canonical(r["config"]) for r in complete})==len(complete)
    status={
        "status":decision_status,"seed":seed,"arm":arm,"logical_wall_s":elapsed(),
        "warm_shared_wall_s":anchored_wall,"arm_elapsed_s":time.monotonic()-pin,"core":core,
        "completed":len(complete),"warm":len(proposals["warm"]) if not smoke else 4,
        "eligible":sum(r["eligible"] for r in complete),"incomplete":len(history)-len(complete),
        "fallback_batches":fallback_count,"closed":now(),"horizon_s":horizon,
        "best_train":min(r["score"] for r in complete),
    }
    write_json(out/"status.json",status)
    return status

def gate_isolation(path):
    path.mkdir(parents=True,exist_ok=False)
    p=path/"probe.prompt"
    (path/"state").mkdir()
    (path/"state/engineer-fixture").write_text("blocked runner scope fixture\n")
    fixture=ROOT/"private/isolation-sentinel"
    fixture.write_text("private scope fixture\n")
    fixture.chmod(0o600)
    p.write_text(
        "This is a black-box smoke test of a training experiment. Use the local terminal tool "
        "exactly once to run the Python snippet. The fixtures are artificial. os.access checks "
        "the path metadata only. The sample command has no content reads and makes no network calls. "
        "Your final response must be the single line printed by the command.\n\n"
        "python3 -c 'import os; "
        "ok = os.access(\"/train/manifest.json\", os.R_OK) and "
        "os.access(\"/protocol/README.md\", os.R_OK) and "
        "not os.access(\"/runner/state/engineer-fixture\", os.R_OK) and "
        f"not os.access(\"{fixture}\", os.R_OK); "
        "print(\"TRAIN_ONLY_OK\" if ok else \"ISOLATION_FAILED\")'\n")
    cmd=[ROOT/"isolated_sol.sh",path,TRAIN.parent,PROTO,"--output-last-message","/work/probe.txt","-"]
    event=path/"events.jsonl"
    saved=os.sched_getaffinity(0)
    try:
        os.sched_setaffinity(0,{CORES[0]})
        rc,timeout=bounded_syscall(cmd,p,event,path/"cli.stderr",90)
    finally:
        os.sched_setaffinity(0,saved)
    telemetry=model_trace(event)
    checks=[]
    for line in event.read_text().splitlines():
        try:
            item=json.loads(line).get("item",{})
        except json.JSONDecodeError:
            continue
        if item.get("type")=="command_execution" and item.get("exit_code")==0:
            checks.append(item.get("aggregated_output",""))
    marker=any("TRAIN_ONLY_OK" in output.splitlines() for output in checks)
    result={"ok":rc==0 and not timeout and marker and telemetry["turn_completed"],"rc":rc,"timeout":timeout,**telemetry}
    write_json(path/"VERIFY.json",result)
    return result

def verify_training(seedfolder,arm,smoke=False):
    directory=seedfolder/arm
    inputs=ev.verify(TRAIN)
    truth={r["file"]:r for r in inputs}
    statuses=Counter()
    rows={}
    for row in lines(directory/"sessions.jsonl"):
        oracle=truth[row["session"]]["oracle"]
        answers=row["answers"]
        wrong=sum(a!=b and a!="unknown" for a,b in zip(answers,oracle))
        assert row["wrong_answers"]==wrong
        assert row["correct_answers"]==sum(a==b for a,b in zip(answers,oracle))
        assert (row["status"]=="incorrect")==bool(wrong)
        if row["status"]=="solved":
            assert answers==oracle and row["rc"]==0 and row["wall_s"]<=1.50
        if row["status"]=="timeout":
            assert row["rc"]==-9 or row["wall_s"]>1.50
        assert row["par2"]==(None if row["status"]=="budget" else row["wall_s"] if row["status"]=="solved" else 3.0)
        statuses[row["status"]]+=1
        rows.setdefault(row["candidate_id"],[]).append(row)
    candidates=lines(directory/"candidates.jsonl")
    for cand in candidates:
        if not cand["complete"]:
            continue
        case=rows[cand["candidate_id"]]
        idx=hashlib.sha256(vector(cand["config"]).encode()).digest()
        order=list(truth)
        rng=random.Random(int.from_bytes(idx[:8],"big")^load(seedfolder/"proposals.json")["seed"])
        rng.shuffle(order)
        assert [row["session"] for row in case]==order
        assert len(case)==15 and not any(r["par2"] is None for r in case)
        assert abs(cand["par2"]-math.fsum(r["par2"] for r in case)/len(case))<1e-12
        assert cand["eligible"]==not_invalid(case)
        assert cand["score"]==(cand["par2"] if cand["eligible"] else 4.0)
    act={"rows":sum(statuses.values()),"counts":statuses,"candidates":len(candidates),
         "completed":sum(c["complete"] for c in candidates),"candidate_sha256":digest(directory/"candidates.jsonl")}
    write_json(directory/"VERIFY.json",act)
    return act

def not_invalid(case):
    return not any(row["status"] in ("error","incorrect","unknown","budget") for row in case)

def execute_workers(commands, cap):
    procs=[]
    stop=min(STOP-5,time.time()+cap)
    try:
        for command,out_file in commands:
            with out_file.open("wb") as out:
                p=subprocess.Popen(command,cwd=ROOT,stdout=out,stderr=out,start_new_session=True)
            procs.append(p)
        while any(p.poll() is None for p in procs):
            check_budget()
            if time.time()>=stop:
                raise TimeoutError("search worker deadline")
            time.sleep(2)
    finally:
        for p in procs:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGKILL)
                    p.wait()
    return [p.returncode for p in procs]

def main_smoke():
    model_plan=plan_model()
    for invalid in ({"ite-simp":"on"},{"unknown-option":"auto"},{"bv-solver":"not-a-solver"}):
        try:
            compatible(invalid)
        except ValueError:
            continue
        raise AssertionError(f"negative config check accepted {invalid}")
    SMOKE.mkdir(parents=True,exist_ok=True)
    iso=SMOKE/"isolation"
    if not (iso/"VERIFY.json").exists():
        result=gate_isolation(iso)
    else:
        result=load(iso/"VERIFY.json")
    if not result["ok"]:
        raise RuntimeError("Sol isolation or live authentication smoke failed")
    base=SMOKE/"seed-80139"
    base.mkdir(exist_ok=True)
    seed=80139
    if not (base/"warm/locked.json").exists():
        execute_workers([([PYTHON,"-B",__file__,"warm",base,str(seed),str(CORES[0]),"4"],base/"warm.out")],200)
    assert (base/"warm/locked.json").exists()
    checks=smoke_matrix({"default":{},**{f"d{i}":a for i,a in enumerate(source_plan(seed)["warm"][1:6])}},"05-config-smoke")
    assert "default" in checks["pass"]
    rcs=execute_workers([
        ([PYTHON,"-B",__file__,"arm",base,str(seed),a,str(core),"smoke"],base/f"{a}.out")
        for a,core in zip(ARMS,CORES)],380)
    arms={}
    for a,rc in zip(ARMS,rcs):
        if rc!=0:
            arms[a]={"ok":False,"rc":rc}
            continue
        verify_training(base,a,True)
        state=load(base/a/"status.json")
        n=state["completed"]
        if a=="sol":
            events=lines(base/a/"model-calls.jsonl")
            fitted=any(e["valid_representation"] and e["new_real_feedback_ids"] and
                       max(e["new_real_feedback_ids"])>=4 for e in events[1:])
        else:
            fitted=n>=8
        arms[a]={"ok":fitted and state["status"]=="ok","state":state}
    result={"ok":all(row["ok"] for row in arms.values()),"isolation":result,"arms":arms,
            "configs":checks,"plan_sha256":digest(PLAN)}
    write_json(SMOKE/"VERIFY.json",result)
    log(f"SEARCH SMOKE {result['ok']}")
    if not result["ok"]:
        raise RuntimeError("one or more online search smoke gates failed")
    return result

def main_full():
    config=plan_model()
    assert load(SMOKE/"VERIFY.json")["ok"]
    results={}
    for wave in range(0,len(SEEDS),2):
        sets=SEEDS[wave:wave+2]
        work=[]
        for i,seed in enumerate(sets):
            base=BASE/f"seed-{seed}"
            base.mkdir(parents=True,exist_ok=True)
            if not (base/"warm/locked.json").is_file():
                work.append(([PYTHON,"-B",__file__,"warm",base,str(seed),str(CORES[3*i])],base/"warm.out"))
        if work and any(execute_workers(work,390)):
            raise RuntimeError("one or more matched warm evaluations failed")
        for seed in sets:
            verify_training(BASE/f"seed-{seed}","warm")
        commands=[]
        for i,seed in enumerate(sets):
            base=BASE/f"seed-{seed}"
            for j in range(3):
                # Rotate non-model and model arms across distinct physical cores.
                arm=ARMS[(j+i+wave//2)%3]
                if not (base/arm/"status.json").is_file():
                    commands.append(([PYTHON,"-B",__file__,"arm",base,str(seed),arm,str(CORES[3*i+j])],base/f"{arm}.out"))
        log(f"START matched search wave seeds {sets}")
        if commands and any(execute_workers(commands,HORIZON+30)):
            raise RuntimeError("online arm execution failed")
        for seed in sets:
            base=BASE/f"seed-{seed}"
            results[seed]={}
            for a in ARMS:
                v=verify_training(base,a)
                state=load(base/a/"status.json")
                results[seed][a]={**state,"verified":v}
        write_json(BASE/"progress.json",results)
        from search_analysis import publish_progress
        publish_progress(results)
    write_json(BASE/"train-complete.json",{"complete":now(),"results":results})
    from search_analysis import validate_and_test
    validate_and_test(config)

if __name__=="__main__":
    cmd=sys.argv[1]
    if cmd=="smoke":
        main_smoke()
    elif cmd=="full":
        main_full()
    elif cmd=="warm":
        train_warm(Path(sys.argv[2]),int(sys.argv[3]),int(sys.argv[4]),int(sys.argv[5]) if len(sys.argv)>5 else WARM_COUNT)
    elif cmd=="arm":
        smoke=len(sys.argv)>6 and sys.argv[6]=="smoke"
        run_arm(Path(sys.argv[2]),int(sys.argv[3]),sys.argv[4],int(sys.argv[5]),smoke,320 if smoke else HORIZON)
