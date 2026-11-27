"""Time-budgeted, one-worker SMAC3, Sol, random and fixed default."""
from concurrent.futures import ProcessPoolExecutor
from importlib.metadata import version
import argparse
import json
import os
from pathlib import Path
import random
import time

from common import CUTOFF, CPUS, ROOT, environment, now, ordered, read, run, sha, write
from eval import evaluate

ARMS=("default","smac","sol","random")


def flags(space, config):
    return [flag for bit,group in enumerate(space) if config & (1<<bit) for flag in group["flags"]]


def result(data, ids):
    if len(data)!=len(ids):
        return None
    families={}
    for row in data:
        families.setdefault(row["family"],[]).append(row)
    cost=sum(sum(r["par2"] for r in rows)/len(rows) for rows in families.values())/len(families)
    errors=sum(row["status"] in {"wrong","error","incomplete"} for row in data)
    wrong=sum(row["status"]=="wrong" for row in data)
    return dict(cost=cost + (1000+wrong if errors else 0),par2=cost,valid=errors==0,wrong=wrong,invalid=errors,
                solved=sum(row["status"]=="solved" for row in data),checks=sum(row["verified_checks"] for row in data))


def trial(path, items, opts, cpu, cap, stop):
    result_path=path/"aggregate.json"
    if result_path.exists():
        return read(result_path)
    t=time.monotonic()
    started=now()
    rows=[]
    for item in items:
        if time.time()+cap+.35>=min(stop,CUTOFF):
            break
        rows.append(evaluate(path/item["id"],item,opts,cpu,cap))
    aggregate=result(rows,items)
    if aggregate is None:
        write(path/"partial.json",dict(start=started,stop=now(),received=len(rows),wanted=len(items)))
        return None
    aggregate.update(start=started,stop=now(),seconds=time.monotonic()-t,flags=opts)
    write(result_path,aggregate)
    return aggregate


class NativeSmac:
    def __init__(self,path,space,seed,budget):
        from ConfigSpace import CategoricalHyperparameter, ConfigurationSpace
        from smac import HyperparameterOptimizationFacade, Scenario
        from smac.initial_design import RandomInitialDesign
        from smac.runhistory import TrialValue

        assert version("smac")=="2.4.0" and version("ConfigSpace")=="1.2.1"
        names=[f"x{i:02d}_{entry['name']}" for i,entry in enumerate(space)]
        cs=ConfigurationSpace(seed=seed)
        cs.add([CategoricalHyperparameter(name,[0,1],default_value=0) for name in names])
        scenario=Scenario(cs,deterministic=True,seed=seed,n_trials=2**len(space),n_workers=1,
                          output_directory=path,walltime_limit=budget)
        design=RandomInitialDesign(scenario,n_configs=5,additional_configs=[cs.get_default_configuration()])
        self.smac=HyperparameterOptimizationFacade(scenario,None,initial_design=design,overwrite=False)
        self.value=TrialValue
        self.names=names

    def ask(self):
        self.pending=self.smac.ask()
        return sum(int(self.pending.config[name])<<i for i,name in enumerate(self.names))

    def tell(self, summary):
        self.smac.tell(self.pending,self.value(cost=summary["cost"],time=summary["seconds"]))


def ask_sol(path, plan, history, cpu, timeout):
    space=plan["space"]
    allowed=set(range(1<<len(space)))
    seen={r["id"] for r in history}
    keys=("id","cost") if plan.get("feedback")=="scalar" else ("id","cost","solved","valid")
    payload={"options": [{"bit":i,**group} for i,group in enumerate(space)],
             "mode":plan["description"],"cap":plan["cap"],
             "objective":"Minimize average per-family session PAR2 in seconds. Timeout costs twice cap. An incorrect or invalid session disqualifies a configuration.",
             "history":[{k:r[k] for k in keys} for r in history],
             "batch":plan["sol_batch"]}
    data=json.dumps(payload,sort_keys=True,allow_nan=False,ensure_ascii=True,separators=(",",":"))
    assert len(data)<40000 and all(group["name"].replace("-","").isalnum() for group in space)
    packet=path/"train"
    packet.mkdir(parents=True,exist_ok=True)
    (packet/"training.json").write_text(data+"\n")
    prompt=("You are a cvc5 configuration proposer. Use only this measured, anonymous TRAIN packet. "
            "Do not call tools. Pick up to the stated batch size of distinct untried integer IDs. "
            "The integer ID is a bitmask, bit k enables the listed flags for bit k. "
            "ID 0 is stock and is already measured. Feedback follows each batch. "
            "Use the JSON schema. Return a concise reason.\n"+data+"\n")
    (path/"prompt.txt").write_text(prompt)
    work=path/"client"
    cmd=[ROOT/"scripts/sol-sandbox.sh",work,packet,ROOT/"protocols/sol"]
    # sandbox wrapper creates its interior paths, including its own cwd.
    proc=run(work,cmd,cpu,timeout,environment(),prompt.encode())
    events=[]
    for line in (work/"stdout").read_text(errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    forbidden_events=[e for e in events if e.get("item",{}).get("type") in
                      {"command_execution","mcp_tool_call","web_search","file_change"}]
    complete=[e for e in events if e.get("type")=="turn.completed"]
    threads=[e["thread_id"] for e in events if e.get("type")=="thread.started" and "thread_id" in e]
    response=work/"work/answer.json"
    ids=[]
    reason=""
    status="inference-failed"
    if proc["ret"]==0 and not proc["timeout"] and response.is_file() and len(complete)==len(threads)==1 and not forbidden_events:
        try:
            answer=read(response)
            proposal=answer["ids"]
            reason=answer["reason"]
            valid=(isinstance(proposal,list) and all(type(i) is int and i in allowed-seen for i in proposal)
                   and len(proposal)==len(set(proposal)) and 0<len(proposal)<=plan["sol_batch"] and isinstance(reason,str))
            if valid:
                ids=proposal
                status="ok"
            else:
                status="invalid-proposal"
        except (ValueError,KeyError,TypeError):
            status="invalid-json"
    write(path/"call.json",dict(start=proc["start"],stop=proc["stop"],seconds=proc["seconds"],model="gpt-6.2-sol",
                                 status=status,ids=ids,reason=reason,thread=threads[0] if len(threads)==1 else None,
                                 tool_events=len(forbidden_events),usage=complete[0].get("usage") if len(complete)==1 else None,
                                 packet_sha256=sha(packet/"training.json"),prompt_sha256=sha(path/"prompt.txt"),
                                 events_sha256=sha(work/"stdout")))
    return read(path/"call.json")


def worker(path, plan, method, seed, cpu, begin, end):
    os.sched_setaffinity(0,{cpu})
    os.environ.update({k:v for k,v in environment().items() if k.endswith("NUM_THREADS")})
    if (path/"search.json").exists():
        return read(path/"search.json")
    info=dict(method=method,seed=seed,cpu=cpu,planned_start=begin,deadline=end,budget=plan["budget"])
    if (path/"origin.json").exists():
        assert read(path/"origin.json")==info
    else:
        write(path/"origin.json",info)
    delay=begin-time.time()
    if delay>0:
        time.sleep(delay)
    observation_start=now()
    items=ordered(plan["items"],seed,"common-order")
    seen_file=path/"trials.json"
    calls_file=path/"calls.json"
    history=read(seen_file) if seen_file.exists() else []
    calls=read(calls_file) if calls_file.exists() else []
    seen={r["id"]:r for r in history}
    space=plan["space"]
    universe=1<<len(space)
    cap=plan["cap"]
    reserve=plan["reserve"]
    exceptions=[]
    status="budget"
    core_tic=time.monotonic()
    try:
        initial=trial(path/"evaluations/c000",items,[],cpu,cap,end)
        if initial is None:
            status="baseline-incomplete"
            return
        if not history:
            history.append(dict(id=0,**initial))
            seen[0]=history[0]
            write(seen_file,history)
        if not initial["valid"]:
            status="baseline-invalid"
            return
        if method=="default":
            for tick in range(1, int(plan["budget"]//plan["default_period"])+1):
                scheduled=begin+tick*plan["default_period"]
                while scheduled > time.time() and scheduled+reserve<end:
                    time.sleep(min(30,scheduled-time.time()))
                if scheduled+reserve>=end or end-time.time()<=reserve:
                    break
                trial(path/f"repeat/a{tick:02d}",items,[],cpu,cap,end)
            return
        if method=="smac":
            native=NativeSmac(path/"native"/f"session-{time.time_ns()}",space,seed,plan["budget"])
            if history:
                baseline=next(row for row in history if row["id"]==0)
                # Default is supplied as an observation shared by all methods.
                from smac.runhistory import TrialInfo
                from ConfigSpace import Configuration
                fixed_native=Configuration(native.smac.scenario.configspace,values={name:0 for name in native.names})
                native.smac.tell(TrialInfo(config=fixed_native,seed=seed),native.value(cost=baseline["cost"],time=baseline["seconds"]))
                for r in history[1:]:
                    proposed=native.ask()
                    while proposed==0:
                        native.tell(baseline)
                        proposed=native.ask()
                    if proposed!=r["id"]:
                        raise RuntimeError(f"SMAC deterministic replay mismatch {proposed} vs {r['id']}")
                    native.tell(r)
        if method=="random":
            rng=random.Random(seed)
            picks=list(range(1,universe))
            rng.shuffle(picks)
            random_queue=[i for i in picks if i not in seen]
        while end-time.time()>reserve and len(seen)<universe:
            if method=="random":
                ids=[random_queue.pop(0)]
            elif method=="smac":
                config=native.ask()
                if config in seen:
                    native.tell(seen[config])
                    continue
                ids=[config]
            else:
                pending=[i for r in calls if r["status"]=="ok" for i in r["ids"] if i not in seen]
                if pending:
                    ids=pending
                else:
                    timeout=min(plan["sol_timeout"],end-time.time()-reserve)
                    if timeout<8 or len(calls)>=plan["max_sol_calls"]:
                        status="call-limit" if len(calls)>=plan["max_sol_calls"] else "budget"
                        break
                    location=path/f"model/call-{len(calls)+1:03d}"
                    saved=location/"call.json"
                    feedback=read(saved) if saved.is_file() else ask_sol(location,plan,history,cpu,timeout)
                    calls.append(feedback)
                    write(calls_file,calls)
                    ids=feedback["ids"]
            for config in ids:
                if end-time.time()<=reserve:
                    break
                if config in seen:
                    continue
                data=trial(path/f"evaluations/c{config:03d}",items,flags(space,config),cpu,cap,end)
                if data is not None:
                    record=dict(id=config,**data)
                    history.append(record)
                    seen[config]=record
                    write(seen_file,history)
                    if method=="smac":
                        native.tell(record)
                else:
                    status="final-trial-partial"
                    return
        if len(seen)==universe:
            status="space-exhausted"
    except Exception as exc:
        import traceback
        status="failed"
        exceptions=[repr(exc)]
        (path/"exception.log").write_text(traceback.format_exc())
    finally:
        # Distinct replicas have independently measured stock and proposal state.
        best=sorted((r for r in history if r["valid"] and r["id"]!=0),key=lambda r:(r["cost"],r["id"]))
        summary=dict(**info,observed_start=observation_start,stop=now(),work_wall=time.monotonic()-core_tic,status=status,
                     exceptions=exceptions,seen=len(history),advancing=[r["id"] for r in best[:2]],
                     model_calls=len(calls),model_seconds=sum(c["seconds"] for c in calls),
                     model_success=sum(c["status"]=="ok" for c in calls),
                     rejected_model=sum(c["status"]!="ok" for c in calls))
        write(path/"search.json",summary)
        print(summary,flush=True)
    return read(path/"search.json")


def main(plan_path):
    plan=read(plan_path)
    assert plan_path.parent==ROOT/"protocols"
    assert len(plan["items"]) and all(row["producer"]==plan["producer"] and "/train/" in row["path"] for row in plan["items"])
    assert all(sha(ROOT/row["path"])==row["sha256"] for row in plan["items"])
    assert plan["budget"]>=60
    output=ROOT/"raw"/plan["name"]
    output.mkdir(parents=True,exist_ok=True)
    identifier={"plan":str(plan_path.relative_to(ROOT)),"sha256":sha(plan_path)}
    if (output/"plan.json").exists():
        assert read(output/"plan.json")==identifier
    else:
        write(output/"plan.json",identifier)
    summaries=[]
    for index,seed in enumerate(plan["seeds"]):
        loc=output/f"s{seed}"
        loc.mkdir(exist_ok=True)
        schedule=loc/"schedule.json"
        cpus=CPUS[index%4:]+CPUS[:index%4]
        if schedule.exists():
            times=read(schedule)
        else:
            start=time.time()+8
            end=start+plan["budget"]
            if end+60>=CUTOFF:
                break
            times=dict(scheduled_at=now(),start=start,end=end,seeds=seed,cpus=dict(zip(ARMS,cpus)))
            write(schedule,times)
        complete=loc/"round.json"
        if complete.exists():
            summaries.extend(read(complete))
            continue
        with ProcessPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(worker,loc/method,plan,method,seed,times["cpus"][method],times["start"],times["end"])
                     for method in ARMS]
            for f in futures:
                f.result()
        rows=[read(loc/m/"search.json") for m in ARMS]
        write(complete,rows)
        summaries.extend(rows)
        if any(r["status"] in ("failed","baseline-incomplete","baseline-invalid") for r in rows):
            raise RuntimeError(f"round {seed} has a failed worker; inspect {loc}")
    write(output/"search-summary.json",summaries)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("plan",type=Path)
    a=p.parse_args()
    main(a.plan.resolve())
