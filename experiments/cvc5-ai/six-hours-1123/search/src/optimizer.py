"""Real sequential proposals on measured objectives. No evaluator or held-out path is exposed to Sol."""
from importlib.metadata import version
from pathlib import Path
import random
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"recovery/scripts"))
sys.path.insert(0,str(ROOT/"blind/src"))
import base
import request as blind

def options(mask,values):
    return {k:v for i,(k,v) in enumerate(values.items()) if mask & (1<<i)}

class SmacProposer:
    def __init__(self,path,values,allowed,seed,budget):
        from ConfigSpace import CategoricalHyperparameter, ConfigurationSpace
        from ConfigSpace import ForbiddenAndConjunction, ForbiddenEqualsClause
        from smac import HyperparameterOptimizationFacade, Scenario
        from smac.initial_design import RandomInitialDesign
        from smac.runhistory import TrialValue
        assert version("smac")=="2.4.0"
        self.keys=list(values)
        model=ConfigurationSpace(seed=seed)
        params=[CategoricalHyperparameter(key,[0,1],default_value=0) for key in self.keys]
        model.add(params)
        for mask in set(range(1<<len(self.keys)))-set(allowed):
            model.add(ForbiddenAndConjunction(*(ForbiddenEqualsClause(p,(mask>>i)&1) for i,p in enumerate(params))))
        scenario=Scenario(model,deterministic=True,seed=seed,n_trials=len(allowed),n_workers=1,
             output_directory=path/"smac-output",walltime_limit=budget)
        class AllowedRandomInitialDesign(RandomInitialDesign):
            def _select_configurations(self):
                # The SMAC 2.4.0 uniform copy drops forbidden clauses.
                configurations=([self._configspace.sample_configuration()] if self._n_configs==1 else
                                self._configspace.sample_configuration(size=self._n_configs))
                for config in configurations:
                    config.origin="Initial Design: Random in original ConfigSpace"
                return configurations
        design=AllowedRandomInitialDesign(scenario,n_configs=3,additional_configs=[model.get_default_configuration()])
        def unused(config,seed=0):
            raise RuntimeError("Strict ask/tell. Use the live paired evaluator.")
        self.smac=HyperparameterOptimizationFacade(scenario,unused,initial_design=design,overwrite=False)
        self.value=TrialValue
        self.pending=None

    def ask(self):
        assert self.pending is None
        self.pending=self.smac.ask()
        return sum(int(self.pending.config[k]) << i for i,k in enumerate(self.keys))

    def tell(self,cost,seconds):
        assert self.pending is not None
        self.smac.tell(self.pending,self.value(cost=cost,time=seconds))
        self.pending=None

def ask_sol(where,allowed,values,baseline,trials,timeout,objective):
    documented=[dict(options=list(options(t["mask"],values)),cost=t["result"]["cost"],
          admissible=t["result"]["feasible"],train=t["result"]["train"]) for t in trials if "result" in t]
    handoff=dict(allowed=[list(options(i,values)) for i in allowed],option_values=values,
                 baseline=baseline,trials=documented,objective=objective)
    existing=where/"staged-train/training.json"
    finished=where/"result.json"
    if where.exists():
        if not existing.is_file() or not finished.is_file():
            return None,"interrupted inference"
        assert existing.read_bytes()==blind.safe_snapshot(handoff)+b"\n"
        answer=base.read(finished)["answer"]
    else:
        answer=blind.invoke(where,handoff,budget=timeout)
    if not answer:
        return None,"missing or rejected inference"
    picked=answer["options"]
    if not all(isinstance(k,str) and k in values for k in picked) or len(picked)!=len(set(picked)):
        return None,"invalid option subset"
    return sum(1<<index for index,k in enumerate(values) if k in picked),answer["reason"]

def window(path,method,values,allowed,seed,duration,evaluate,baseline,estimate_s,objective,max_sol=16,sol_limit=None):
    """Search elapsed wall time includes the paired trial acquisition and every model call."""
    path.mkdir(parents=True,exist_ok=True)
    finished=path/"search.json"
    if finished.exists():
        return base.read(finished)
    origin_file=path/"window-origin.json"
    if not origin_file.is_file():
        base.write(origin_file,dict(start=base.now(),clock=time.time(),maximum=duration,method=method,seed=seed))
    origin=base.read(origin_file)
    assert (origin["method"],origin["seed"])==(method,seed)
    spent=max(0.0,time.time()-origin["clock"])
    end=time.monotonic()+max(0.0,min(duration,origin["maximum"]-spent))
    partial=path/"partial.json"
    rows=base.read(partial) if partial.is_file() else []
    seen={0:1.0,**{r["mask"]:r["result"]["cost"] for r in rows if "result" in r}}
    algorithm=None
    if method=="smac" and not set(allowed)<=seen.keys() and end-time.monotonic()>estimate_s:
        cfg=path
        if (path/"smac-output").is_dir():
            cfg=path/"smac-restarts"/str(time.time_ns())
            cfg.mkdir(parents=True)
        algorithm=SmacProposer(cfg,values,allowed,seed,origin["maximum"])
        for row in rows:
            if row.get("mask") is None:
                break
            assert algorithm.ask()==row["mask"],row
            if "result" in row:
                algorithm.tell(row["result"]["cost"],row["seconds"])
            elif row.get("reused"):
                algorithm.tell(seen[row["mask"]],0)
            else:
                break
    draws=list(x for x in allowed if x)
    random.Random(seed).shuffle(draws)
    draws=[x for x in draws if x not in {r.get("mask") for r in rows}]
    inference=sum(r.get("inference_s",0.0) for r in rows)
    rejected=sum("result" not in r and (method=="sol" or not r.get("reused")) for r in rows)
    for attempt in range(len(rows), max_sol if method=="sol" else max(2*len(allowed),1)):
        remaining=end-time.monotonic()
        if remaining<=estimate_s or set(allowed)<=seen.keys():
            break
        tic=time.monotonic()
        record=dict(at=base.now(),attempt=attempt+1)
        reason=""
        if method=="sol":
            terminal=min(sol_limit or blind.LIMIT,remaining-estimate_s)
            if terminal<8:
                break
            call=path/"model"/f"call-{attempt+1:02d}"
            mask,reason=ask_sol(call,allowed,values,baseline,rows,terminal,objective)
            measured=call/"status.json"
            record["inference_s"]=base.read(measured)["wall_ms"]/1000 if measured.is_file() else time.monotonic()-tic
            inference+=record["inference_s"]
        elif algorithm:
            try:
                mask=algorithm.ask()
            except (RuntimeError,StopIteration) as error:
                rejected+=1
                rows.append(dict(record,exception=repr(error),end=base.now()))
                base.write(partial,rows)
                break
        else:
            if not draws:
                break
            mask=draws.pop()
        record.update(mask=mask,reason=reason)
        if mask not in allowed or mask in seen or end-time.monotonic()<=estimate_s:
            if algorithm and mask in seen:
                algorithm.tell(seen[mask],0)
            rejected+=int(mask not in seen or method=="sol")
            rows.append(dict(record,reused=mask in seen,end=base.now()))
            base.write(partial,rows)
            if algorithm and mask not in seen:
                break
            continue
        opts=options(mask,values)
        checked=evaluate(path/f"trial-{attempt+1:02d}",opts,end-time.monotonic())
        seconds=time.monotonic()-tic
        cost=float(checked["cost"])
        seen[mask]=cost
        if algorithm:
            algorithm.tell(cost,seconds)
        rows.append(dict(record,seconds=seconds,result=checked,end=base.now()))
        base.write(partial,rows)
    best=sorted((r for r in rows if "result" in r and r["result"]["feasible"]),
                key=lambda r:(r["result"]["cost"],r["mask"]))
    result=dict(method=method,seed=seed,assigned_wall_s=origin["maximum"],wall_s=time.time()-origin["clock"],
                start=origin["start"],end=base.now(),inference_s=inference,rejected=rejected,records=rows,
                advanced=[r["mask"] for r in best[:2]],evaluations=sum("result" in x for x in rows))
    base.write(finished,result)
    return result
