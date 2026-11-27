"""Measure the real installed USVM client with native KSMT backends on frozen methods."""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import hashlib
import os
from pathlib import Path
import random
import shutil
import subprocess
import time

import numpy as np

from corpus import JDK, commands_log
from portfolio import frac, group_draws
from replay import CAMPAIGN, CORES, ROOT, STOP, check_budget, digest, lines, load, log, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci

BASE=ROOT/"runs/16-live-client"
SCOPE=ROOT/"protocols/live-client.json"
REPEATS=3
ARMS={"z3":("z3",False),"cvc5":("cvc5",False),"cvc5-table-ite":("cvc5",True)}
METHOD_MS=6000
SOLVER_MS=1600
BATCH=3
CAP=45

def config():
    cp_path=CAMPAIGN/"setup/usvm-classpath.env"
    environment=dict(line.split("=",1) for line in cp_path.read_text().splitlines()
                 if line.startswith(("CP=","APIJAR=","APPROXJAR=")))
    assert len(environment)==3
    return environment

def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    artifacts=[]
    for root,ps in ((ROOT/"runs/04-transfer/corpus",ROOT/"protocols/transfer-scope.json"),
                    (ROOT/"runs/12-new-libraries/corpus",ROOT/"protocols/new-libraries.json")):
        if not ps.exists():
            continue
        for lib in load(ps)["artifacts"]:
            name=lib["project"]
            source=root/name/"traces/methods.jsonl"
            if not source.exists():
                continue
            methods=[r["tag"] for r in lines(source) if r["status"]=="ok"]
            methods=sorted(set(methods),key=lambda x:hashlib.sha256(x.encode()).hexdigest())[:12]
            if len(methods)<3:
                continue
            artifacts.append({**lib,"tags":methods,"tags_source":os.path.relpath(source,ROOT),
                              "tags_source_sha256":digest(source)})
    assert len(artifacts)>=3
    primary=sorted(artifacts,key=lambda a:(a["project"] not in ("commons-codec","commons-io","asm","protobuf-java","fastutil","commons-cli"),a["project"]))[:6]
    q={"locked":now(),"question":"Does portable offline replay explain end-to-end behavior of the actual incremental USVM client on recorded public library methods?",
       "source":"frozen method tags from recorded public JARs. The live symbolic paths and timings are new for each native backend. Select successful Z3 discovery by hash, never cvc5 replay performance.",
       "version_barrier":"Native backends bundled in installed KSMT 0.5.26. Not the 1.4.1 CLI and not the same fixed SMT-LIB paths. No 1.4.1 inference from this contrast.",
       "driver":"org.usvm.dataset.ClientDriverKt","classpath_sha256":digest(CAMPAIGN/"setup/usvm-classpath.env"),
       "driver_class_sha256":digest(CAMPAIGN/"setup/usvm/usvm-jvm/build/classes/kotlin/test/org/usvm/dataset/ClientDriverKt.class"),
       "arms":ARMS,"repeats":REPEATS,"method_limit_ms":METHOD_MS,"check_limit_ms":SOLVER_MS,"jvm_case_wall_s":CAP,
       "configurations":primary,"per_batch_methods":BATCH,
       "design":"Fresh bounded JVM for each paired arm/shard/repeat; random arm order, same order of methods on same physical core. Native cvc5 TABLE_ITE is an existing client transformation.",
       "outcomes":"Wall and coverage among paired successful observations, all error/timeout/missing counts separately, solver check share and native model detach share. Bootstrap by JAR family."}
    write_json(SCOPE,q)
    SCOPE.chmod(0o444)
    return q

def one_case(out,artifact,tags,repeat,core,arm,cap=CAP,*,classpath_overlay=None,extra_env=(),solver_config=None):
    check_budget()
    out.mkdir(exist_ok=False,parents=True)
    for name in ("tmp","jhome"):
        (out/name).mkdir()
    opts=config()
    classpath=opts["CP"] if classpath_overlay is None else str(classpath_overlay)+os.pathsep+opts["CP"]
    assert digest(artifact["jar"])==artifact["sha256"]
    assert all(tag.startswith(artifact["project"]+"/") for tag in tags)
    methods=out/"methods.txt"
    methods.write_text("\n".join(tags)+"\n")
    solver,rewrite=ARMS[arm] if solver_config is None else solver_config
    cmd=["taskset","-c",str(core),"env","-u","USVM_SESSION_DIR","-u","USVM_SMT_DIR",
         "USVM_CLIENT_STATS=1",f"USVM_SOLVER={solver}",f"USVM_TABLE_ITE={int(rewrite)}",
         *extra_env,
         f"usvm.jvm.api.jar.path={opts['APIJAR']}",f"usvm.jvm.approximations.jar.path={opts['APPROXJAR']}",
         "MALLOC_ARENA_MAX=2",f"TMPDIR={out/'tmp'}",JDK/"java","-Xmx2g","-XX:-UsePerfData",
         "-XX:ActiveProcessorCount=1","-XX:+UseSerialGC","-Dfile.encoding=UTF-8",
         f"-Duser.home={out/'jhome'}",f"-Djava.io.tmpdir={out/'tmp'}","-cp",classpath,
         "org.usvm.dataset.ClientDriverKt",f'project={artifact["project"]}',f'cp={artifact["jar"]}',
         f"methods={methods}",f"out={out/'results.jsonl'}",f"methodTimeoutMs={METHOD_MS}",
         f"solverTimeoutMs={SOLVER_MS}",f"totalSeconds={int(cap)-3}","rssLimitMb=3200"]
    rc=0
    timed=False
    try:
        commands_log(out/"java.log",cmd,cap,cwd=out)
    except subprocess.TimeoutExpired:
        timed=True
        import json
        rc=json.loads((out/"java.log").read_text().splitlines()[-1])["returncode"]
    except subprocess.CalledProcessError as e:
        rc=e.returncode
    # Keep every result, input tag and diagnostic log. JVM scratch contains
    # only extracted dependencies and per-process temporary databases.
    for name in ("tmp","jhome"):
        shutil.rmtree(out/name)
    files=out/"results.jsonl"
    rows=lines(files) if files.exists() else []
    assert {r["tag"] for r in rows}<=set(tags) and len({r["tag"] for r in rows})==len(rows)
    assert all(r["solver"]==solver for r in rows)
    write_json(out/"status.json",{"arm":arm,"repeat":repeat,"core":core,"rc":rc,
                               "jvm_timeout":timed,"requested":tags,"completed_at":now(),"rows":len(rows)})

def blocks(q):
    for i,artifact in enumerate(q["configurations"]):
        names=artifact["tags"]
        grouped=[names[i:i+BATCH] for i in range(0,len(names),BATCH)]
        for j,group in enumerate(grouped):
            for repeat in range(REPEATS):
                rng=random.Random(231159 + i*8191 + j*1031 + repeat*29)
                tags=list(group)
                rng.shuffle(tags)
                arm=list(ARMS)
                rng.shuffle(arm)
                core=CORES[(i+j+repeat)%len(CORES)]
                yield artifact,j,repeat,tags,arm,core

def group_runs(slot,q):
    core=CORES[slot]
    os.sched_setaffinity(0,{core})
    for artifact,shard,repeat,tags,order,cpu in blocks(q):
        if cpu!=core:
            continue
        if STOP-time.time()<145:
            raise TimeoutError("live client wall budget")
        dirname=BASE/artifact["project"]/f"shard-{shard:02d}-repeat-{repeat}"
        dirname.mkdir(parents=True,exist_ok=True)
        write_json(dirname/"order.json",{"arms":order,"tags":tags,"core":core})
        for arm in order:
            if not (dirname/arm/"status.json").exists():
                one_case(dirname/arm,artifact,tags,repeat,core,arm)

def smoke(q):
    target=BASE/"smoke"
    if (target/"VERIFY.json").exists():
        assert load(target/"VERIFY.json")["ok"]
        return
    first=q["configurations"][0]
    specs={}
    for arm in ARMS:
        where=target/arm
        one_case(where,first,first["tags"][:2],0,CORES[0],arm,50)
        state=load(where/"status.json")
        f=where/"results.jsonl"
        rows=lines(f) if f.exists() else []
        specs[arm]={"rows":len(rows),"ok":not (state["jvm_timeout"] or state["rc"]) and any(r["status"]=="ok" for r in rows),
                    "statuses":Counter(r["status"] for r in rows)}
    gate={"ok":all(q["ok"] for q in specs.values()),"programs":2,"repeats":1,"arms":specs}
    write_json(target/"VERIFY.json",gate)
    if not gate["ok"]:
        raise RuntimeError("live client black-box smoke failed")

def inspect(q):
    metrics={}
    status={k:Counter() for k in ARMS}
    stats={}
    hashes={}
    by={(arm,k):Counter() for arm in ARMS for k in ("counters","statuses")}
    group={}
    repeats={}
    tags_count=0
    jvm_timeouts=Counter()
    jvm_error=Counter()
    for artifact,shard,repeat,tags,order,cpu in blocks(q):
        folder=BASE/artifact["project"]/f"shard-{shard:02d}-repeat-{repeat}"
        assert load(folder/"order.json")=={"arms":order,"tags":tags,"core":cpu}
        for arm in order:
            p=folder/arm
            result=load(p/"status.json")
            assert result["core"]==cpu and result["repeat"]==repeat and result["requested"]==tags
            jvm_timeouts[arm]+=result["jvm_timeout"]
            jvm_error[arm]+=bool(result["rc"]) and not result["jvm_timeout"]
            what=p/"results.jsonl"
            group.update({tag:artifact["project"] for tag in tags})
            obs={z["tag"]:z for z in lines(what)} if what.exists() else {}
            assert len(obs)==result["rows"]
            if obs:
                hashes[str(what.relative_to(ROOT))]=digest(what)
            for tag in tags:
                r=obs.get(tag)
                status[arm]["no_record" if r is None else r["status"]]+=1
                if r is not None:
                    assert r["wall_ms"]>=0 and r["cpu_ms"]>=0
                    assert r["stats"]["checks"]>=r["stats"]["checks_assume"]
                    assert 0<=r["stats"]["coverage_pct"]<=100
                    stats[tag,repeat,arm]=r
    N=len(group)
    T=N*REPEATS
    table=[]
    diagnostics={}
    for arm in ARMS:
        per=defaultdict(list)
        for tag,l in group.items():
            for repeat in range(REPEATS):
                try:
                    base=stats[tag,repeat,"z3"]
                    test=stats[tag,repeat,arm]
                except KeyError:
                    continue
                if base["status"]!="ok" or test["status"]!="ok":
                    continue
                per[tag].append([base["wall_ms"],test["wall_ms"],
                                 base["stats"]["coverage_pct"],test["stats"]["coverage_pct"],
                                 base["stats"]["steps"]/(.001*base["wall_ms"]+1e-9),
                                 test["stats"]["steps"]/(.001*test["wall_ms"]+1e-9),
                                 test["stats"]["check_ms"],test["stats"]["model_ms"]])
        verified=sorted(per)
        arr=np.array([np.mean(per[tag],axis=0) for tag in verified])
        total=sum(map(len,per.values()))
        if total:
            draws=group_draws([group[tag] for tag in verified],np.random.default_rng(106086))
            ratio=frac(arr[:,1],arr[:,0],draws)
            coverage=arr[:,3]-arr[:,2]
            diffs=(draws@coverage)/draws.sum(axis=1)
            throughput=frac(arr[:,5],arr[:,4],draws) if arr[:,4].sum()>0 and np.all((draws@arr[:,4])>0) else None
            model_share=float(arr[:,7].sum()/arr[:,1].sum())
            check_share=float(arr[:,6].sum()/arr[:,1].sum())
            summary={"programs_at_least_one_paired_ok":len(verified),"paired_ok":total,
                     "wall_ratio":ratio,"coverage_pp":float(coverage.mean()),
                     "coverage_pp_ci":np.percentile(diffs,[2.5,97.5]).tolist(),"steps_per_second_ratio":throughput,
                     "model_share":model_share,"check_share":check_share,
                     "per_method":{tag:{"project":group[tag],"measurements":len(per[tag]),"averaged_components":arr[i].tolist()}
                                   for i,tag in enumerate(verified)}}
        else:
            summary={"programs_at_least_one_paired_ok":0,"paired_ok":0}
        diagnostics[arm]=summary
        table.append([arm,status[arm]["ok"],sum(n for k,n in status[arm].items() if k.startswith("error:")),
                      status[arm]["no_record"],jvm_timeouts[arm],len(verified),total,
                      fmt_ci(summary["wall_ratio"]) if total else "—",
                      f'{summary["coverage_pp"]:+.2f}' if total else "—",
                      f'{100*summary["model_share"]:.1f}%' if total else "—"])
    M={"programs":N,"libraries":len(set(group.values())),"repeats":REPEATS,"planned_per_arm":T,
       "rows":len(stats),"statuses":status,"jvm_timeouts":jvm_timeouts,
       "jvm_error":jvm_error,"metrics":diagnostics,"raw_shas":hashes,
       "version":q["version_barrier"],"methods":group}
    write_json(BASE/"VERIFY.json",M)
    cov=diagnostics["cvc5"]
    headline=(f"На живом клиенте JNI cvc5 дал время {fmt_ci(cov['wall_ratio'])} к JNI Z3 в {cov['paired_ok']} полных парах." if cov["paired_ok"] else
              "На живом клиенте JNI cvc5 не набрал совместно завершённых пар с Z3. Сохранены исходы и ошибки.")
    write_json(ROOT/"evidence/17-live-client.json",{
        "title":"17 · Живой USVM и извлечение моделей",
        "done":f"Собраны и проверены нативные запуски для {N} библиотечных методов.",
        "summary":headline,
        "paragraphs":[
            f"{N} методов, по одному на класс, из {M['libraries']} публичных JAR. {REPEATS} повтора на метод и режим, по {T} запланированных ячеек. Статистики сняты внутри реально работающего USVM. Метод и JVM имеют отдельные пределы. Сохранены результаты даже при частичном завершении JVM.",
            "Проверялись поставляемые KSMT 0.5.26 бэкенды JNI Z3 и JNI cvc5, а также существующая клиентская замена чтения таблицы на if-then-else. Эти Java-пути развиваются заново при каждом запуске. Основные матрицы отчёта используют cvc5 1.4.1 в другом режиме, воспроизводя неизменный SMT-LIB.",
            "Основное отношение оценивает wall среди совместно завершённых вызовов драйвера, с отдельными счётчиками ошибок, таймаутов JVM и отсутствующих записей. Методы статуса ok могут завершить ограниченное изучение путей. Покрытие и работа по моделям выделены отдельно. Bootstrap пересэмплирует целые JAR."
        ],
        "tables":[card_table("Живой инструмент · "+str(T)+" запланированных вызовов на режим",
              ["Режим","Статус ok","Ошибки","Без записи","Таймауты JVM","Методы в паре","Парные вызовы",
               "Wall к Z3 [95% ДИ]","Δ покрытия, п.п.","Доля model/detach"],table)],
        "paths":["protocols/live-client.json","runs/16-live-client/smoke/VERIFY.json",
                 "runs/16-live-client/VERIFY.json","runs/16-live-client/<проект>/<серия>/<режим>/java.log"],
        "recommendation":"Отделять ускорение решателя на замороженной записи от времени живого клиента и цены извлечения модели.",
        "next":"Подтвердить стабильность целевого бэкенда и формата на независимом продуктовом наборе перед развертыванием."
    })
    build()

def main():
    q=freeze()
    BASE.mkdir(exist_ok=True)
    smoke(q)
    with ProcessPoolExecutor(max_workers=len(CORES)) as jobs:
        work=[jobs.submit(group_runs,i,q) for i in range(len(CORES))]
        for w in work:
            w.result(timeout=max(1,min(3600,STOP-time.time()-45)))
    inspect(q)
if __name__=="__main__":
    from recovery import attempt
    from replay import PYTHON
    def profile():
        if (ROOT/"runs/12-new-libraries/portable-corpus/manifest.json").exists() and not (
                ROOT/"evidence/16b-portable-profile.json").exists():
            commands_log(ROOT/"runs/portable-profile.out",[PYTHON,"-B",ROOT/"real_profile.py"],1350)
    attempt("portable-profile",profile)
    main()
