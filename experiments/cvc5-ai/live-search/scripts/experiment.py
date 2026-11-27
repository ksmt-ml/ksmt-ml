import math
import random
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from base import *
from metrics import aggregate,comparison,observe_block
from smoke import config

SEARCH_S=995
ARM_S=1650
MODEL_S=80
PACKAGE_MAX=2*CAP+4
SMAC_SEED=290141
EVID=ROOT/"evidence"
RUN=ROOT/"raw/live"
SEAL=ROOT/"inputs/seal.json"
SCHEDULE=ROOT/"inputs/schedule.json"
MANIFEST=ROOT/"inputs/manifest.json"
SPACE=ROOT/"inputs/space.json"
SPLITS=read(MANIFEST)["splits"]
JOBS=read(SCHEDULE)
MODELS=ROOT/"model"
LIVE_TIMEOUTS=(datetime.fromisoformat((EVID/"session-start-msk.txt").read_text().strip()).timestamp()+4*3600
               -time.time()+time.monotonic())
ASSETS={p["project"]:os.pathsep.join(a["path"] for a in [p["jar"],*p["dependencies"]])
        for ps in SPLITS.values() for p in ps}

def progress(name, start, **kw):
    path=RUN/"timeline.jsonl"
    with path.open("a") as out:
        print(json.dumps(dict(event=name,at=now(),start=start,**kw),ensure_ascii=False),file=out,flush=True)
    print(name,start,now(),json.dumps(kw,ensure_ascii=False),flush=True)

def package(directory,jobs,arms,deadline):
    ensure_disk()
    directory.mkdir(parents=True)
    write(directory/"assignment.json",dict(jobs=jobs,arms=arms,at=now()))
    def lane(jobs,lane_cpu):
        for j in jobs:
            if j["core"]!=lane_cpu:
                continue
            for arm in j["order"]:
                if time.monotonic()+CAP+1>deadline:
                    return
                where=directory/j["id"]/arm
                if time.monotonic()>LIVE_TIMEOUTS-600:
                    return
                case(where,j["core"],arm,arms[arm],j["seed"],j["tags"],ASSETS[j["project"]])
    with ThreadPoolExecutor(len(CPUS)) as pool:
        pending=[pool.submit(lane,jobs,core) for core in CPUS]
        for task in pending:
            task.result()
    output={a:[observe_block(directory/j["id"]/a,j) for j in jobs] for a in arms}
    write(directory/"summary.json",{a:aggregate(b) for a,b in output.items()})
    return output

def train_package(directory, options, deadline):
    jobs=[dict(j,order=["train"]) for j in JOBS["train"]]
    return package(directory,jobs,{"train":options},deadline)["train"]

def proposal_snapshot(train,allowed,trials,baseline):
    # Public view for the model. No absolute paths, sources, test or historical outputs.
    aliases={n:f"P{i+1}" for i,n in enumerate(sorted({b["project"] for b in baseline}))}
    def compact(s):
        return {k:round(s[k],3) for k in ("n","par2_ms","paths","edges","coverage","steps","checks","check_ms",
                "solver_timeouts","other_unknown","step_errors","failed","jvm_failed","missing","wall_ms")}
    aa=aggregate(baseline)
    zones={aliases[p]:compact(aggregate([b for b in baseline if b["project"]==p])) for p in aliases}
    result=[]
    for t in trials:
        if "result" not in t:
            continue
        r=t["result"]
        result.append(dict(options=list(config(t["mask"])),train=compact(r["totals"]),cost=r["cost"],
                           feasible=r["feasible"],admissible=r["admissible"],
                           projects={aliases[p]:compact(values[0]) for p,values in r["projects"].items()}))
    write(train/"observations.json",dict(default=compact(aa),default_by_project=zones,
         admitted_switch_sets=[list(config(i)) for i in allowed],trials=result))

def model_suggest(arm_dir, call_no, train, seconds):
    p=arm_dir/"model"/f"call-{call_no:02d}"
    p.mkdir(parents=True)
    prompt="""Choose one previously untried switch set for the live USVM cvc5 experiment.
Read /train/observations.json. It contains the current live training baseline, all completed training results with anonymized projects, and the allowed switch sets.
Return JSON with options (an exact subset listed under admitted_switch_sets) and a brief reason using only this training record and your solver knowledge. Choose a nonempty set you have not evaluated. Option names imply only the non-default value in /protocol/options.json. You are the proposer, so choose the next experiment yourself.
Optimize total PAR2 (lower better). Keep >=98% total paths, branches and steps, >=95% paths in each project, at most one percentage point mean instruction coverage loss, no additional failed methods, JVM failures, interpreter step errors or solver UNKNOWN; aim for >=2% PAR2 gain. For coverage, divide the recorded coverage sum by n. A result can be faster because exploration differed, so prefer same coverage and work. Training always uses identical paired methods and seeds. Files in /train and /protocol are sufficient. Do not use external tools or data. Output only the specified JSON.
"""
    (p/"prompt.txt").write_text(prompt)
    cmd=["taskset","-c",str(CPUS[0]),"prlimit","--as=2300000000","--",
         str(ROOT/"scripts/isolated_sol.sh"),str(p),str(train),str(MODELS/"protocol"),
         "--output-schema","/protocol/schema.json","--output-last-message","/work/answer.json","-"]
    write(p/"invocation.json",dict(command=cmd,snapshot_sha=sha(train/"observations.json"),start=now()))
    shutil.copyfile(train/"observations.json",p/"observations.json")
    st=run_process(p,cmd,os.environ,min(MODEL_S,seconds),stdin=prompt.encode(),rss_limit=0)
    mask=None
    why=st["watchdog"] or f"exit={st['returncode']}"
    if st["returncode"]==0 and (p/"work/answer.json").is_file():
        try:
            response=read(p/"work/answer.json")
            opts=response["options"]
            assert len(opts)==len(set(opts))
            assert set(opts)<=SWITCH.keys()
            mask=sum(1<<i for i,k in enumerate(SWITCH) if k in opts)
            why=response["reason"]
        except (AssertionError,KeyError,TypeError,ValueError) as error:
            why=repr(error)
    write(p/"proposal.json",dict(mask=mask,reason=why,at=now()))
    return mask,why

def setup_smac(path, allowed):
    from ConfigSpace import CategoricalHyperparameter, ConfigurationSpace, ForbiddenAndConjunction, ForbiddenEqualsClause
    from smac import HyperparameterOptimizationFacade,Scenario
    from smac.runhistory import TrialValue
    cs=ConfigurationSpace(seed=SMAC_SEED)
    variables=[CategoricalHyperparameter(k,[0,1],default_value=0) for k in SWITCH]
    cs.add(variables)
    for i in sorted(set(range(32))-set(allowed)):
        cs.add(ForbiddenAndConjunction(*(ForbiddenEqualsClause(v,(i>>j)&1) for j,v in enumerate(variables))))
    scenario=Scenario(cs,deterministic=True,n_trials=32,seed=SMAC_SEED,n_workers=1,
                      output_directory=path/"smac-output",walltime_limit=SEARCH_S)
    def never_called(cfg,seed=0):
        raise RuntimeError("Live target is evaluated exclusively through ask/tell")
    from smac.initial_design import RandomInitialDesign
    design=RandomInitialDesign(scenario,n_configs=2,additional_configs=[cs.get_default_configuration()])
    return HyperparameterOptimizationFacade(scenario,never_called,initial_design=design,overwrite=False),TrialValue

def validation_jobs(labels,seed):
    rng=random.Random(seed)
    out=[]
    for j in JOBS["validation"]:
        order=list(labels)
        rng.shuffle(order)
        out.append(dict(j,order=order))
    return out

def run_arm(arm,allowed):
    directory=RUN/arm
    directory.mkdir()
    started=now()
    t0=time.monotonic()
    deadline=min(t0+ARM_S,LIVE_TIMEOUTS-600)
    search_end=min(t0+SEARCH_S,deadline)
    progress("arm_start",started,arm=arm)
    baseline=train_package(directory/"train-default",{},search_end)
    seen={0}
    trials=[]
    asks=[]
    if arm=="smac":
        smac,TrialValue=setup_smac(directory,allowed)
        for _ in range(32):
            if time.monotonic()+PACKAGE_MAX>search_end or len(seen)==len(allowed):
                break
            try:
                info=smac.ask()
            except (StopIteration,RuntimeError) as error:
                asks.append(dict(error=repr(error),at=now()))
                break
            mask=sum(int(info.config[k])<<i for i,k in enumerate(SWITCH))
            assert mask in allowed
            start=now()
            if mask==0:
                cost=1.0
            elif mask in seen:
                cost=next(t["result"]["cost"] for t in trials if t["mask"]==mask)
            else:
                b=train_package(directory/f"train-{mask:02d}",config(mask),search_end)
                result=comparison(b,baseline)
                trials.append(dict(mask=mask,result=result,start=start,end=now()))
                seen.add(mask)
                cost=result["cost"]
            smac.tell(info,TrialValue(cost=cost,time=(datetime.fromisoformat(now())-datetime.fromisoformat(start)).total_seconds()))
            asks.append(dict(mask=mask,cost=cost,start=start,end=now(),seed=info.seed))
            write(directory/"ask-tell.json",asks)
            write(directory/"training.json",trials)
            progress("train_smac",start,mask=mask,cost=cost)
    else:
        for number in range(1,40):
            left=search_end-time.monotonic()-PACKAGE_MAX
            if left<12 or len(seen)==len(allowed):
                break
            start=now()
            snapshot=directory/"model-train"
            snapshot.mkdir(exist_ok=True)
            proposal_snapshot(snapshot,allowed,trials,baseline)
            mask,reason=model_suggest(directory,number,snapshot,left)
            asks.append(dict(mask=mask,reason=reason,start=start,end=now(),new=mask in allowed and mask not in seen))
            write(directory/"proposals.json",asks)
            if mask not in allowed or mask in seen:
                progress("sol_rejected",start,mask=mask)
                continue
            b=train_package(directory/f"train-{mask:02d}",config(mask),search_end)
            result=comparison(b,baseline)
            trials.append(dict(mask=mask,result=result,start=start,end=now()))
            seen.add(mask)
            write(directory/"training.json",trials)
            progress("train_sol",start,mask=mask,cost=result["cost"])
    search_finished=now()
    feasible=[t for t in trials if t["result"]["feasible"]]
    advanced=sorted((t for t in feasible if t["result"]["admissible"]),key=lambda t:(t["result"]["par2_ratio"],t["mask"]))[:2]
    n=max(0,min(len(advanced),math.floor((deadline-time.monotonic()-4)/PACKAGE_MAX)-1))
    advanced=advanced[:n]
    candidates={"default":{},**{f"candidate{i+1}":config(t["mask"]) for i,t in enumerate(advanced)}}
    val_results=[]
    if time.monotonic()+PACKAGE_MAX<=deadline:
        assignments=validation_jobs(candidates,JOBS["seed"]+10)
        valid=package(directory/"validation",assignments,candidates,deadline)
        for i,t in enumerate(advanced):
            val_results.append(dict(mask=t["mask"],result=comparison(valid[f"candidate{i+1}"],valid["default"])))
    best=min((t for t in val_results if t["result"]["admissible"]),
             key=lambda t:(t["result"]["par2_ratio"],t["mask"]),default=None)
    chosen=best["mask"] if best else 0
    result=dict(arm=arm,mask=chosen,options=config(chosen),start_msk=started,search_end_msk=search_finished,
          end_msk=now(),wall_s=time.monotonic()-t0,max_wall_s=ARM_S,new_trials=len(trials),
          complete_trials=sum(t["result"]["complete"] for t in trials),feasible_train=len(feasible),
          admissible_train=sum(t["result"]["admissible"] for t in trials),proposals=len(asks),
          validation=val_results,advanced=[t["mask"] for t in advanced],validation_baseline=(directory/"validation/summary.json").is_file(),
          feasible_validation=sum(t["result"]["feasible"] for t in val_results),
          admissible_validation=sum(t["result"]["admissible"] for t in val_results))
    write(directory/"choice.json",result)
    progress("arm_end",started,arm=arm,chosen=chosen,evaluated=len(trials))
    return result

def preflight():
    seal=read(SEAL)
    assert sha(ROOT/"PREREG.md")==seal["PREREG.md"] and sha(SCHEDULE)==seal["schedule.json"]
    assert sha(MANIFEST)==seal["manifest.json"] and sha(EVID/"historical.json")==seal["historical.json"]
    assert read(ROOT/"raw/sol-preflight/work/answer.json")["reason"]=="TRAIN_ONLY_SMOKE"
    assert version("smac")=="2.4.0"
    release=read(SPACE)["combinations"]
    allowed=[i for i in range(32) if release[str(i)]["ok"]]
    assert 0 in allowed and release["z3"]["ok"] and len(allowed)>=3
    write(MODELS/"protocol/options.json",dict(non_default_values=SWITCH,native_default_values=DEFAULTS))
    return allowed

def main():
    setup_limits()
    allowed=preflight()
    RUN.mkdir()
    write(RUN/"launch.json",dict(start=now(),smac=version("smac"),configspace=version("ConfigSpace"),
         allowed=allowed,space_sha=sha(SPACE),pkgs={str(p):sha(p) for p in sorted((ROOT/"scripts").glob("*")) if p.is_file()},
         protocol=sha(SEAL),cpus=CPUS,heap=HEAP,rss_limit=RSS_KIB))
    done=["Исторические знаменатели восстановлены из сырых логов",
          "Зафиксированы разделение проектов, критерии, лимиты и расписание",
          f"Контрактный и live smoke пройден для {len(allowed)} конфигураций cvc5 и Z3"]
    selected={}
    for arm in JOBS["arm_order"]:
        status(f"Идёт поиск и проверка на validation для {arm}",done,["Заморозить настройки","Живой тест и независимая сверка"])
        selected[arm]=run_arm(arm,allowed)
        done.append(f"{arm}: выбор заморожен, измерено новых конфигураций {selected[arm]['new_trials']}")
    options={"default":{},"smac":selected["smac"]["options"],"sol":selected["sol"]["options"],"z3":{}}
    write(RUN/"test-lock.json",dict(locked=now(),configs=options,schedule_sha=sha(SCHEDULE),
          choices={a:sha(RUN/a/"choice.json") for a in selected}))
    status("Идут парные живые прогоны на закрытом тесте",done,["Сверить сырые логи","Опубликовать отчёт"])
    start=now()
    progress("test_start",start,configs=options)
    result=package(RUN/"test",JOBS["test"],options,LIVE_TIMEOUTS-600)
    progress("test_end",start,verified={a:aggregate(b)["verified"] for a,b in result.items()})
    write(RUN/"test-contrasts.json",{a:comparison(b,result["default"]) for a,b in result.items() if a!="default"})
    status("Измерения завершены, идёт независимая сверка",done+["Получены тестовые JVM"],["Опубликовать отчёт"])

if __name__=="__main__":
    main()
