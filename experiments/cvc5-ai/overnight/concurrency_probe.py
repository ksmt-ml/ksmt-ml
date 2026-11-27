"""Randomized on-host concurrency calibration with fixed inference CPU and program mix."""
from collections import Counter
import itertools
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time

import numpy as np

from portfolio import MENU
from replay import CORES, DATA, PYTHON, ROOT, STOP, append, check_budget, digest, ev, launch, lines, load, log, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/13-concurrency"
SCOPE=ROOT/"protocols/concurrency.json"
BLOCKS=16
MODES=("one","six")

def freeze():
    if SCOPE.exists():
        return load(SCOPE)
    menu=load(MENU)
    extra=menu["arms"][menu["global_arm"]]
    configs={"default":{},"default_alias":{},"global":extra}
    rng=random.Random(207031)
    pairs=[]
    for i in range(BLOCKS):
        mode=rng.sample(MODES,2)
        pairs.append({"block":i,"order":mode,"paired_order_seed":rng.randrange(1,2**31)})
    s={"locked":now(),"hypothesis":"Whether a six-worker campaign can materially shift wall-time and timeout inference on a fixed physical CPU compared with one worker.",
       "motivation":"Archived default performance changed on these same inputs; the experiment isolates the locally designed concurrent solver activity using matched time blocks.",
       "measure_core":CORES[0],"load_cores":list(CORES[1:]),"paired_blocks":pairs,
       "configs":configs,"manifest_sha256":digest(DATA/"validation/manifest.json"),
       "background_manifest_sha256":digest(DATA/"train/manifest.json"),
       "design":"randomly order one-worker and six-worker treatment in each paired block, same on-core program and arm order in both halves; five pinned training replays provide calibrated background, neither power settings nor other host processes changed",
       "primary":"paired change in default PAR2 and conditional wall-CPU gap; crossed bootstrap whole programs and paired time blocks, plus paired sign randomization test over block means",
       "reused":"the original validation workload, no additional independent programs relative to stability"}
    write_json(SCOPE,s)
    SCOPE.chmod(0o444)
    return s

def load_worker(path,slot,deadline):
    core=CORES[slot]
    os.sched_setaffinity(0,{core})
    source=DATA/"train/manifest.json"
    items=ev.verify(source)
    rng=random.Random(22337+slot)
    write_json(path/f"load-{slot}.ready",{"core":core,"at":now()})
    count=0
    stop=min(STOP-12,deadline)
    while time.time()<stop-2 and not (path/"stop-load").exists():
        rng.shuffle(items)
        for row in items:
            if time.time()>=stop-2 or (path/"stop-load").exists():
                break
            record=ev.measure(source,row,{},1.5,time.monotonic()+max(.001,stop-time.time()))
            append(path/f"load-{slot}.jsonl",{"counter":count,"core":core,**record})
            count+=1
    write_json(path/f"load-{slot}.closed",{"at":now(),"replays":count})

def measure_block(scope,block,folder,mode,*,manifest=None,load_slots=None,wall_limit=120):
    result=folder/mode
    if (result/"VERIFY.json").is_file():
        return
    active=[]
    slots=list(range(1,len(CORES))) if load_slots is None and mode=="six" else list(load_slots or [])
    group=folder/("background" if load_slots is None else f"background-{mode}")
    try:
        if slots:
            group.mkdir(exist_ok=False)
            for slot in slots:
                with (group/f"load-{slot}.stderr").open("wb") as out:
                    active.append(subprocess.Popen([PYTHON,"-B",__file__,"load",group,str(slot),str(time.time()+wall_limit+30)],
                                                    stdout=out,stderr=out,start_new_session=True))
            ready=time.monotonic()+6
            while not all((group/f"load-{slot}.ready").exists() for slot in slots):
                if time.monotonic()>=ready or any(p.poll() is not None for p in active):
                    raise RuntimeError("background worker failed to initialize")
                time.sleep(.05)
            time.sleep(.35)
        launch(result,manifest or DATA/"validation/manifest.json",scope["configs"],1,block["paired_order_seed"],wall_limit,
               cores=(scope["measure_core"],))
    finally:
        if active:
            (group/"stop-load").touch()
            for p in active:
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGTERM)
                    p.wait(timeout=3)
            assert all(p.returncode==0 for p in active)

def final(scope):
    all_counters=Counter()
    by={}
    stats={}
    blocks=scope["paired_blocks"]
    for block in blocks:
        number=block["block"]
        path=BASE/f"pair-{number:02d}"
        for mode in MODES:
            result=load(path/mode/"VERIFY.json")
            for counts in result["counts"].values():
                all_counters.update(counts)
            _,groups,arms,S,T,W=replay_cube(path/mode)
            raws=lines(path/mode/"worker-0.jsonl")
            assert len(groups)==result["programs"]==11 and S.shape[2]==1
            cpu={(r["group"],r["arm"]):r["solver_cpu_s"] for r in raws}
            by[number,mode]={"scores":S[:,:,0],"status":T[:,:,0],"wall":W[:,:,0],
                             "cpu":np.array([[cpu[g,a] for a in arms] for g in groups])}
    g=len(groups); days=len(blocks)
    ref=arms.index("default"); alias=arms.index("default_alias")
    rng=np.random.default_rng(38722)
    idx_g=rng.integers(g,size=(B,g))
    idx_b=rng.integers(days,size=(B,days))
    results={}
    blocks_delta=None
    for i,a in enumerate(arms):
        vals={m:np.stack([by[b,m]["scores"][:,i] for b in range(days)],axis=0) for m in MODES}
        A,C=vals["one"],vals["six"]
        Aref=np.stack([by[b,"one"]["scores"][:,ref] for b in range(days)],axis=0)
        Cref=np.stack([by[b,"six"]["scores"][:,ref] for b in range(days)],axis=0)
        diff=C-A
        if a=="default":
            blocks_delta=diff.mean(axis=1)
        over=np.sum(C[idx_b[:,:,None],idx_g[:,None,:]],axis=(1,2))/np.sum(A[idx_b[:,:,None],idx_g[:,None,:]],axis=(1,2))
        paired_wall_diff=[]
        paired_cpu_diff=[]
        timeouts={}
        for mode in MODES:
            timeouts[mode]=sum(np.sum(by[b,mode]["status"][:,i]=="timeout") for b in range(days))
        for b in range(days):
            one,six=by[b,"one"],by[b,"six"]
            both=(one["status"][:,i]=="solved")&(six["status"][:,i]=="solved")
            paired_wall_diff.extend((six["wall"][:,i]-one["wall"][:,i])[both])
            paired_cpu_diff.extend((six["cpu"][:,i]-one["cpu"][:,i])[both])
        results[a]={"one_par2":float(A.mean()),"six_par2":float(C.mean()),
                    "paired_ratio":(float(C.mean()/A.mean()),np.percentile(over,[2.5,97.5]).tolist()),
                    "arm_to_default_one":float(A.mean()/Aref.mean()),"arm_to_default_six":float(C.mean()/Cref.mean()),
                    "median_solved_wall_difference_ms":float(1000*np.median(paired_wall_diff)),
                    "median_solved_cpu_difference_ms":float(1000*np.median(paired_cpu_diff)),
                    "solved_pairs":len(paired_wall_diff),"timeouts":{k:int(v) for k,v in timeouts.items()}}
    assert blocks_delta is not None
    flips=np.asarray(list(itertools.product((-1,1),repeat=days)),dtype=np.int8)
    null=np.abs(flips@blocks_delta/days)
    p=float(np.mean(null>=abs(blocks_delta.mean())-1e-15))
    loads=sum(len(lines(p)) for p in BASE.glob("pair-*/background/load-*.jsonl"))
    result={"programs":g,"block_pairs":days,"repeats_per_condition":days,"rows":sum(all_counters.values()),
            "background_rows":loads,"counts":all_counters,"effects":results,
            "default_block_delta_s":blocks_delta.tolist(),"randomization_p":p,"crossed_bootstrap_samples":B}
    write_json(BASE/"metrics.json",result)
    D=results["default"]
    summary=f"На одном и том же ядре default получил отношение PAR2 шести локальных рабочих процессов к одному {fmt_ci(D['paired_ratio'])}."
    write_json(ROOT/"evidence/14-concurrency.json",{
        "title":"14 · Калибровка влияния параллельных процессов",
        "done":f"Сверены {days} пар временных блоков с одним и шестью воркерами на исходной валидации.",
        "summary":summary,
        "paragraphs":[
            f"Повторно использованы 11 исходных программ. {days} временных парных блоков, по одному повтору на условие в блоке. Измеряемое ядро {scope['measure_core']} фиксировано. Во втором режиме ещё пять закреплённых процессов параллельно воспроизводили старое обучение. Порядок условий случайный внутри блока, порядок программ и настроек одинаковый в обеих половинах.",
            f"По {D['solved_pairs']} парам default с обоими полными сеансами, медиана разницы wall {D['median_solved_wall_difference_ms']:+.1f} мс, CPU {D['median_solved_cpu_difference_ms']:+.1f} мс. Точный тест случайных знаков по временным блокам p={p:.3f}. 95% ДИ также пересэмплирует целые программы и парные блоки.",
            f"Ресурсный контроль создал {loads} фоновых замеров. Целевые {sum(all_counters.values())} воспроизведений дали {all_counters['timeout']} таймаутов, {all_counters['error']} ошибок, {all_counters['unknown']} unknown, {all_counters['incorrect']} несовпадений. Сторонняя загрузка сервера могла меняться, наблюдаемые счётчики сохранены."
        ],
        "tables":[card_table(f"Измерение одного ядра · 11 программ, по {g*days} сеансов на условие",
                  ["Флаги","Один PAR2, с","Шесть PAR2, с","Шесть / один [95% ДИ]","Таймауты один",
                   "Таймауты шесть","К default один","К default шесть"],
                  [[a,f'{s["one_par2"]:.3f}',f'{s["six_par2"]:.3f}',fmt_ci(s["paired_ratio"]),
                    s["timeouts"]["one"],s["timeouts"]["six"],
                    f'{s["arm_to_default_one"]:.3f}',f'{s["arm_to_default_six"]:.3f}'] for a,s in results.items()])],
        "paths":["protocols/concurrency.json","runs/13-concurrency/pair-<номер>/<режим>/VERIFY.json",
                 "runs/13-concurrency/metrics.json","resource-samples.jsonl"],
        "recommendation":"Фиксировать состав одновременно работающих задач и повторять контроль default внутри серии. Для коротких эффектов сравнивать стенки, CPU и исходы таймаута.",
        "next":"Последним проверить контраст, который сохраняется на независимых программах и влияет на решения при рабочем лимите."
    })
    build()

def main():
    s=freeze()
    BASE.mkdir(exist_ok=True)
    for block in s["paired_blocks"]:
        check_budget()
        folder=BASE/f'pair-{block["block"]:02d}'
        folder.mkdir(exist_ok=True)
        for mode in block["order"]:
            measure_block(s,block,folder,mode)
    final(s)
if __name__=="__main__":
    if len(sys.argv)>1:
        load_worker(Path(sys.argv[2]),int(sys.argv[3]),float(sys.argv[4]))
    else:
        from recovery import attempt
        from normalized_representation import main as corrected
        attempt("arrays-exp-representation",corrected)
        main()
