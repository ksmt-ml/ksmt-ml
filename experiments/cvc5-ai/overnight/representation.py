"""Controlled transfer study with equivalent branch and indexed-table expressions."""
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
import random
import re

import numpy as np

from corpus import (FAMILIES, PRIVATE, PRIORS, USVM, FamilyRng, audited_manifest,
                    commands_log, commitment, make_group, normalized_source, seal, skeleton, trace)
from portfolio import MENU, compatible, frac, group_draws, smoke_matrix
from replay import CORES, PYTHON, ROOT, digest, launch, load, log, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

BASE=ROOT/"runs/10-representation"
SCOPE=ROOT/"protocols/representation.json"
REPEATS=8
SPLIT="representation-test"
WIDTHS=(7,12)
MODES=("branch","array")
TABLE={
    "branch":"""    int key = (x ^ y) & 3;
    int s;
    if (key == 0) s = x;
    else if (key == 1) s = y;
    else if (key == 2) s = x + y;
    else s = x - y;""",
    "array":"""    int key = (x ^ y) & 3;
    int[] table = {x, y, x + y, x - y};
    int s = table[key];""",
}

def scope():
    commit=seal(SPLIT,22,4,48,method_ms=7500,solver_ms=2200)
    if SCOPE.exists():
        return load(SCOPE)
    menu=load(MENU)
    configs={"default":{},"default_alias":{},
             "global":menu["arms"][menu["global_arm"]],
             "joint":{"bv-sat-solver":"cadical","simplification":"none"}}
    c=ROOT/"protocols/restart.json"
    if c.exists():
        policy=load(c)
        vector=policy["config_vectors"][policy["best_single_development"]]
        if vector not in configs.values():
            configs["other_development"]=vector
    check=smoke_matrix(configs,"10-representation-smoke")
    assert len(check["pass"])==len(configs)
    s={
        "locked":now(),"seed_sha256":commit["seed_commitment_sha256"],
        "question":"Do flag effects survive changing a branch to a four-cell symbolic array lookup that computes the same Java result?",
        "study":"One base per skeleton, four paired variants crossing a Java branch/indexed array and 7/12 input mask bits. Same base operations, constants and variants. Measurement paired by base program family.",
        "widths":WIDTHS,"representations":TABLE,"independent_target_families":commit["families"],
        "configs":configs,"repeats":REPEATS,"minimum_complete_families":12,
        "primary":"Difference of solver-config minus default PAR2 between array and branch, across matched families, separately by width and pooled.",
        "assignment":"exclude all other known skeletons and source hash duplicates; analysis retains complete four-cell families, quality based only on ordered trace/oracle/Z3",
        "version_hash":digest(Path(__file__)),"menu_sha256":digest(MENU),
    }
    write_json(SCOPE,s)
    SCOPE.chmod(0o444)
    return s

def priors():
    paths=sorted((ROOT/"runs").glob("**/programs.json"))
    return [*PRIORS,*(p.parent for p in paths if BASE not in p.parents)]

def generate(target,s):
    plan=load(ROOT/"protocols"/f"{SPLIT}-seed.json")
    raw=bytes.fromhex((PRIVATE/f"{SPLIT}.seed").read_text().strip())
    assert commitment(SPLIT,raw)==plan["seed_commitment_sha256"]==s["seed_sha256"]
    assert plan["created"]<=s["locked"]
    disjoint=priors()
    known=[p for d in disjoint if (d/"programs.json").exists()
           for p in load(d/"programs.json")["programs"] if p.get("shape_tokens")]
    known_skeleton={skeleton(r) for r in known}
    other=[set(p["shape_tokens"]) for p in known]
    rng=random.Random(int.from_bytes(raw,"big"))
    target.mkdir(parents=True,exist_ok=False)
    folder=target/"source/pilot"
    folder.mkdir(parents=True)
    programs=[]
    for fid in range(plan["families"]):
        for _ in range(5000):
            family=tuple(rng.choice(FAMILIES) for _ in range(rng.randrange(3,7)))
            if family in known_skeleton:
                continue
            _,java,shape=make_group(FamilyRng(rng.getrandbits(64),family),0,12)
            v=set(shape)
            similarity=max((len(v&prior)/len(v|prior) for prior in other),default=0)
            if similarity<.72:
                known_skeleton.add(family)
                other.append(v)
                break
        else:
            raise RuntimeError("no new paired operation skeleton")
        for width in WIDTHS:
            for mode in MODES:
                cls=f"Program{len(programs):03d}"
                assert java.count(" = input & 4095;")==1 and java.count(" = other & 4095;")==1
                assert java.count("    int s = 0;")==1
                mask=(1<<width)-1
                content=java.replace("Program000",cls,1).replace(" = input & 4095;",f" = input & {mask};")
                content=content.replace(" = other & 4095;",f" = other & {mask};").replace("    int s = 0;",TABLE[mode],1)
                path=folder/f"{cls}.java"
                path.write_text(content)
                programs.append({"class":f"pilot.{cls}","group":f"{SPLIT}-{cls}",
                                 "family":"-".join(family),"width":width,"representation":mode,
                                 "sha256":digest(path),"normalized_sha256":normalized_source(path.read_bytes()),
                                 "source":str(path.relative_to(target)),"shape_tokens":shape,
                                 "max_similarity_to_other_family":similarity})
    assert len({a["normalized_sha256"] for a in programs})==len(programs)
    excluded={str(Path(d/"programs.json").relative_to(ROOT)) if ROOT in (d/"programs.json").parents
              else str(d/"programs.json"):digest(d/"programs.json") for d in disjoint if (d/"programs.json").exists()}
    write_json(target/"programs.json",{"split":SPLIT,"seed":raw.hex(),"programs":programs,"exclusions":excluded})
    write_json(target/"release.json",{"unlocked":now(),"scope_sha256":digest(SCOPE),
                                     "scope":str(SCOPE.relative_to(ROOT)),
                                     "seed_commitment":s["seed_sha256"],"seed":raw.hex()})
    trace(target,CORES[0],plan)
    commands_log(target/"materialization.log",
                 ["taskset","-c",str(CORES[0]),PYTHON,"-B",USVM/"convert.py",target],160,cwd=target)
    manifest=target/"manifest.json"
    m=load(manifest)
    pmap={p["sha256"]:p for p in programs}
    by=defaultdict(list)
    for row in m["sessions"]:
        props=pmap[row["source_sha256"]]
        row["family"]=props["family"]
        row["representation"]=props["representation"]
        row["width"]=props["width"]
    write_json(manifest,m)
    m=audited_manifest(target,CORES[0],disjoint,0)
    for row in m["sessions"]:
        by[row["family"]].append(row)
    cases={(w,r) for w in WIDTHS for r in MODES}
    admitted=[]
    for fam,rows in by.items():
        if {(r["width"],r["representation"]) for r in rows}==cases and len(rows)==len(cases):
            admitted.extend(rows)
        else:
            for r in rows:
                m["rejected"].append({"file":r["file"],"reason":"incomplete controlled family","sha256":r["sha256"]})
    m["sessions"]=admitted
    m["group_count"]=len(admitted)
    m["planned_programs"]=len(programs)
    write_json(manifest,m)
    manifest.chmod(0o444)
    assert len(admitted)//4>=s["minimum_complete_families"]
    return manifest

def analyse(directory,m,s):
    verified=load(directory/"VERIFY.json")
    plan,groups,arms,score,stat,wall=replay_cube(directory)
    by_group={r["group"]:r for r in m["sessions"]}
    fam=sorted({r["family"] for r in by_group.values()})
    ne=len(fam)
    idx={(by_group[g]["family"],by_group[g]["width"],by_group[g]["representation"]):i
         for i,g in enumerate(groups)}
    index={(w,r):[idx[(f,w,r)] for f in fam] for w in WIDTHS for r in MODES}
    n,a,repeats=score.shape
    assert ne*4==n
    means=score.mean(axis=2)
    base=arms.index("default")
    bootstrap=np.random.default_rng(214549).integers(ne,size=(B,ne))
    differences={}
    configs={}
    ratio_rows=[]
    for j,arm in enumerate(arms):
        per=[]
        for width in WIDTHS:
            branch=index[(width,"branch")]
            array=index[(width,"array")]
            difference=(means[array,j]-means[array,base])-(means[branch,j]-means[branch,base])
            per.append(difference)
            differences.setdefault(arm,{})[str(width)]={
                "delta_s":float(difference.mean()),
                "interval":np.percentile(difference[bootstrap].mean(axis=1),[2.5,97.5]).tolist(),
            }
            for kind,ids in (("branch",branch),("array",array)):
                draws=group_draws(fam,np.random.default_rng(55217))
                values=means[ids,j]
                r=frac(values,means[ids,base],draws)
                statuses=Counter(stat[ids,j,:].reshape(-1))
                cell={"par2":float(values.mean()),"ratio":r,"counts":statuses,"programs":len(ids)}
                configs.setdefault(arm,{})[f"{width}-{kind}"]=cell
                ratio_rows.append([arm,width,kind,f'{cell["par2"]:.3f}',fmt_ci(r),statuses["timeout"],
                                   statuses["error"]+statuses["incorrect"]+statuses["unknown"]])
        overall=np.mean(per,axis=0)
        differences[arm]["pooled"]={"delta_s":float(overall.mean()),
                                   "interval":np.percentile(overall[bootstrap].mean(axis=1),[2.5,97.5]).tolist()}
    result={"programs":n,"complete_families":ne,"planned_programs":m["planned_programs"],
            "rejected":len(m["rejected"]),"repeats":repeats,"rows":verified["rows"],
            "cells":configs,"contrasts":differences,"groups":groups,
            "program_par2":{g:{arm:float(means[i,j]) for j,arm in enumerate(arms)} for i,g in enumerate(groups)}}
    write_json(directory/"metrics.json",result)
    k=differences["global"]["pooled"]
    summ=(f"На {ne} новых совпадающих семействах замена ветвления на массив изменила "
          f"эффект глобальной настройки на {k['delta_s']*1000:+.1f} мс PAR2.")
    write_json(ROOT/"evidence/11-representation.json",{
        "title":"11 · Перенос на другую форму той же Java-операции",
        "done":f"Проверено взаимодействие флагов, формы выражения и разрядности в {ne} новых семействах.",
        "summary":summ,
        "paragraphs":[
            f"Из {m['planned_programs']} запланированных вариантов допущено {n} программ в {ne} полных семействах. В каждом базовом семействе четыре программы с одинаковыми последующими операциями и константами: ветвление или чтение из массива, 7 или 12 входных битов. {repeats} повтора, {verified['rows']} воспроизведений.",
            "Индекс (x XOR y) AND 3 является целым от 0 до 3. Ветви и четырёхэлементный массив выбирают одинаковое значение из x, y, суммы и разности. Сеансы символического решения записываются отдельно, поскольку форма исходника меняет пути и взаимодействие с решателем.",
            f"Главный контраст измеряет изменение эффекта настройки между массивом и ветвлением. Оценка глобального флага, усреднённая по ширинам, {k['delta_s']*1000:+.1f} мс, 95% ДИ [{k['interval'][0]*1000:+.1f}; {k['interval'][1]*1000:+.1f}]. Bootstrap по {ne} базовым семействам. В анализ допускаются только полные четвёрки, поэтому вывод относится к записям, прошедшим аудит во всех четырёх режимах."
        ],
        "tables":[card_table(f"Совпадающие Java-варианты · {ne} программ на ячейку, {ne*repeats} запусков",
               ["Флаги","Входных битов","Представление","PAR2, с","К default [95% ДИ]","Таймауты",
                "Ошибки + unknown + несовпадения"],ratio_rows),
          card_table("Разница эффекта между массивом и ветвлением · 95% ДИ по семействам",
                     ["Флаги","Входных битов","Δ эффекта, мс","95% ДИ, мс"],
                     [[a,w,f'{q["delta_s"]*1000:+.1f}',f'{q["interval"][0]*1000:+.1f}…{q["interval"][1]*1000:+.1f}']
                      for a,d in differences.items() for w,q in d.items()])],
        "paths":["protocols/representation.json","runs/10-representation/corpus/programs.json",
                 "runs/10-representation/corpus/manifest.json","runs/10-representation/corpus/z3-audit.jsonl",
                 "runs/10-representation/replay/metrics.json","runs/10-representation/replay/VERIFY.json"],
        "recommendation":"Включать в приёмку настройки разные представления эквивалентных операций и разрядности, если целевой клиент использует их вместе.",
        "next":"На полных сеансах разложить задержку между проверкой, построением модели и остальными операциями."
    })
    build()

def main():
    s=scope()
    target=BASE/"corpus"
    m=target/"manifest.json"
    if not m.exists():
        m=generate(target,s)
    out=BASE/"replay"
    if not (out/"VERIFY.json").exists():
        launch(out,m,s["configs"],REPEATS,55791,2500)
    analyse(out,load(m),s)
if __name__=="__main__":
    main()
