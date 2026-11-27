"""Compare portable encodings for the Z3 writer's multi-index array syntax."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np

from corpus import USVM, commands_log
from portfolio import MENU, frac, group_draws
from replay import CORES, PYTHON, ROOT, STOP, append, check_budget, digest, ev, guarded_popen, launch, lines, load, log, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, replay_cube

sys.path.insert(0,str(USVM))
from convert import render, terms

BASE=ROOT/"runs/11b-array-bridge"
SCOPE=ROOT/"protocols/array-bridge.json"
ORIGINAL=ROOT/"runs/04-transfer/corpus-recovered/manifest.json"
ARRAY_TYPES=("tuple","nested")
REPEATS=8
TOKEN_PREFIX="night_idx_"

class Unsupported(RuntimeError):
    pass

def nodes(data):
    if isinstance(data,list):
        yield data
        for x in data:
            yield from nodes(x)

def array_signature(ast):
    sort_types={}
    for tok in nodes(ast):
        if len(tok)>3 and tok[0]=="Array":
            indices=tok[1:-1]
            dimensions=len(indices)
            signature=tuple(map(render,indices))
            if dimensions in sort_types and sort_types[dimensions][0]!=signature:
                raise Unsupported("ambiguous index signature at shared array arity")
            sort_types[dimensions]=(signature,indices)
    return sort_types

def symbols(source):
    return any(token in source for token in (TOKEN_PREFIX,"(_ as-array","(_ map","(lambda"))

def adapt(source,encoding):
    ast=terms(source)
    sigs=array_signature(ast)
    if not sigs:
        return source,False,{}
    if symbols(source):
        raise Unsupported("reserved adapter prefix or higher-order array operator")
    assert encoding in ARRAY_TYPES
    sorts={n:f"{TOKEN_PREFIX}sort_{n}" for n in sigs}
    constructors={n:f"{TOKEN_PREFIX}mk_{n}" for n in sigs}
    new_counts=Counter()

    def nested(indices,result):
        for q in reversed(indices):
            result=["Array",q,result]
        return result

    def const_chain(indices,result,value):
        if not indices:
            return value
        full=nested(indices,result)
        return [["as","const",full],const_chain(indices[1:],result,value)]

    def store_at(array,keys,val):
        if len(keys)==1:
            return ["store",array,keys[0],val]
        key=keys[0]
        back=store_at(["select",f"{TOKEN_PREFIX}bound",key],keys[1:],val)
        return ["let",[[f"{TOKEN_PREFIX}bound",array]],["store",f"{TOKEN_PREFIX}bound",key,back]]

    def rewrite(term):
        if not isinstance(term,list) or not term:
            return term
        head=term[0]
        if isinstance(head,list) and head[:2]==["as","const"] and len(head)==3:
            raw=head[2]
            if isinstance(raw,list) and len(raw)>3 and raw[0]=="Array":
                n=len(raw)-2
                new_counts["const"]+=1
                val=rewrite(term[1])
                if encoding=="tuple":
                    return [["as","const",["Array",sorts[n],rewrite(raw[-1])]],val]
                return const_chain([rewrite(q) for q in raw[1:-1]],rewrite(raw[-1]),val)
        if head=="Array" and len(term)>3:
            n=len(term)-2
            new_counts["sort"]+=1
            result=rewrite(term[-1])
            if encoding=="tuple":
                return ["Array",sorts[n],result]
            return nested([rewrite(q) for q in term[1:-1]],result)
        if head=="select" and len(term)>3:
            n=len(term)-2
            new_counts["select"]+=1
            x=rewrite(term[1])
            indices=[rewrite(q) for q in term[2:]]
            if encoding=="tuple":
                return ["select",x,[constructors[n],*indices]]
            for key in indices:
                x=["select",x,key]
            return x
        if head=="store" and len(term)>4:
            n=len(term)-3
            new_counts["store"]+=1
            x=rewrite(term[1])
            keys=[rewrite(q) for q in term[2:-1]]
            val=rewrite(term[-1])
            if encoding=="tuple":
                return ["store",x,[constructors[n],*keys],val]
            return store_at(x,keys,val)
        return [rewrite(x) for x in term]

    options=[]
    declarations=[]
    rest=[]
    for tok in ast:
        op=tok[0]
        if op in ("set-logic","set-option"):
            options.append(rewrite(tok))
        elif op=="declare-sort":
            declarations.append(rewrite(tok))
        else:
            rest.append(rewrite(tok))
    types=[]
    if encoding=="tuple":
        for n,(_,indices) in sorted(sigs.items()):
            fields=[[f"{TOKEN_PREFIX}{n}_{j}",rewrite(s)] for j,s in enumerate(indices)]
            types.append(["declare-datatype",sorts[n],[[constructors[n],*fields]]])
    output="\n".join(render(q) for q in [*options,*declarations,*types,*rest])+"\n"
    assert not array_signature(terms(output))
    assert new_counts["sort"]>0
    return output,True,dict(new_counts)

def stage_inputs(source,out,encoding):
    """Rewrite eligible input and keep immutable original event paths."""
    out.mkdir(parents=True,exist_ok=False)
    base=load(source)
    snapshot=out/"files"
    snapshot.mkdir()
    result=[]
    exclusions=[]
    for i,row in enumerate(base["sessions"]):
        original=source.parent/row["file"]
        assert digest(original)==row["sha256"]
        try:
            adapted,changed,operations=adapt(original.read_text(),encoding)
        except (ValueError,Unsupported,KeyError,RecursionError) as exc:
            exclusions.append({"group":row["group"],"reason":str(exc)})
            continue
        dst=snapshot/f"{i:04d}.smt2"
        if changed:
            dst.write_text(adapted)
        else:
            dst.symlink_to(original)
        record={**row,"file":str(dst.relative_to(out)),"sha256":digest(dst),
                "original_sha256":row["sha256"],"bridge_edited":changed,"bridge_operations":operations}
        if "trace" in row:
            record["trace"]=os.path.relpath(source.parent/row["trace"],out)
        result.append(record)
    write_json(out/"adapter-log.json",{"source_manifest":os.path.relpath(source,ROOT),
               "source_manifest_sha256":digest(source),"encoding":encoding,
               "records":[{k:r[k] for k in ("group","original_sha256","sha256","bridge_edited","bridge_operations")} for r in result],
               "excluded":exclusions})
    write_json(out/"audit-input.json",{**base,"sessions":[r for r in result if r["bridge_edited"]],
                                      "group_count":sum(r["bridge_edited"] for r in result)})
    return result,exclusions,base

def audit_stage(args):
    source,out,encoding,core=args
    os.sched_setaffinity(0,{core})
    rows,excluded,base=stage_inputs(source,out,encoding)
    n=sum(r["bridge_edited"] for r in rows)
    if n:
        logpath=out/"z3-audit.jsonl"
        try:
            commands_log(out/"z3-audit.log",
                         [PYTHON,"-B",USVM/"audit.py",out/"audit-input.json",logpath,"--core",str(core)],
                         18*n+10)
        except subprocess.CalledProcessError:
            if not logpath.exists() or len(lines(logpath))!=n:
                raise
        checked={r["session"]:r for r in lines(logpath)}
    else:
        checked={}
    eligible=[]
    for row in rows:
        audit=checked.get(row["file"])
        if row["bridge_edited"] and (audit["status"]!="ok" or audit["answers"]!=row["oracle"]):
            excluded.append({"group":row["group"],"reason":f'adapted Z3 audit {audit["status"]}'})
            continue
        eligible.append(row)
    out_manifest=out/"manifest.json"
    write_json(out_manifest,{**base,"bridge_encoding":encoding,
                             "source_manifest_sha256":digest(source),
                             "sessions":eligible,"group_count":len(eligible),"adapter_excluded":excluded})
    ev.verify(out_manifest)
    return out_manifest

def independent_single(source,out,encoding):
    saved=os.sched_getaffinity(0)
    try:
        return audit_stage((source,out,encoding,CORES[0]))
    finally:
        os.sched_setaffinity(0,saved)

def parse_only(source,arm,flags,core):
    os.sched_setaffinity(0,{core})
    out=BASE/f"parse-{arm}.jsonl"
    assert not out.exists()
    for p in load(source)["sessions"]:
        path=source.parent/p["file"]
        started=time.monotonic()
        cmd=["/usr/bin/prlimit",f"--as={ev.LIMIT_AS}","--core=0","--",ev.SOLVER,
             "--lang=smt2","--incremental","--quiet","--parse-only",*flags,path]
        try:
            with guarded_popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as proc:
                try:
                    stdout,stderr=proc.communicate(timeout=min(10,max(.01,STOP-time.time()-8)))
                    status="parsed" if proc.returncode==0 and b'(error "' not in stdout else "rejected"
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout,stderr=proc.communicate()
                    status="timeout"
        except OSError:
            status="process_error"
            stdout,stderr=b"",b""
        append(out,{"group":p["group"],"status":status,"wall_s":time.monotonic()-started,
                    "sha256":p["sha256"],"error":(stdout+stderr).decode(errors="replace")[:400] if status!="parsed" else ""})
    return out

def prepare():
    BASE.mkdir(exist_ok=True)
    if not SCOPE.exists():
        u=load(ORIGINAL)
        affected,unaffected=[],[]
        for p in u["sessions"]:
            text=(ORIGINAL.parent/p["file"]).read_text()
            kind="affected" if array_signature(terms(text)) else "control"
            (affected if kind=="affected" else unaffected).append({**p,"smt_array_group":kind})
        control=sorted(unaffected,key=lambda q:q["sha256"])[:8]
        cfg=load(MENU)
        c=cfg["arms"][cfg["global_arm"]]
        scope={"locked":now(),"source":"recovered library capture, exposed earlier; exploratory portability calibration",
               "question":"Can a portable nested-array or product-index encoding recover ordered sessions rejected by cvc5 for multi-index Array syntax?",
               "same_semantics":"Product index is one constructor with fields matching every original index. Nested lookup selects one index at a time. Arrays are total and extensional; both type representations have the same index/value maps.",
               "case_subset":"all input files flagged by structural Array arity, plus eight unchanged controls chosen by session hash",
               "affected":len(affected),"controls":len(control),"repeats":REPEATS,
               "source_manifest_sha256":digest(ORIGINAL),
               "dependency":"Independent Z3 audit on transformed full stream required for every changed file before paired cvc5 timing. Cvc5 parse-only recorded separately.",
               "configs":{"raw":{},"raw_exp":{},"tuple":{},"nested":{},"tuple_global":c,"nested_global":c},
               "global":cfg["global_arm"],
               "flags":"All adapted arms and raw_exp use --arrays-exp. raw uses only cvc5 defaults. Ordinary raw+flag controls array extension effects.",
               "admission_changes":[r["group"] for r in affected+control],"method_sha256":digest(Path(__file__))}
        write_json(SCOPE,scope)
        SCOPE.chmod(0o444)
        base=BASE/"source"
        base.mkdir()
        all_records=[]
        for row in affected+control:
            q=ORIGINAL.parent/row["file"]
            all_records.append({**row,"file":os.path.relpath(q,base)})
        write_json(base/"manifest.json",{**u,"sessions":all_records,"group_count":len(all_records)})
    return load(SCOPE)

def matched_manifests(raw,alternatives):
    used={"raw":raw,**alternatives}
    docs={name:load(path) for name,path in used.items()}
    shared=set.intersection(*[{r["group"] for r in data["sessions"]} for data in docs.values()])
    out={}
    for k,doc in docs.items():
        path=used[k].parent/"paired-manifest.json"
        selected=[r for r in doc["sessions"] if r["group"] in shared]
        write_json(path,{**doc,"sessions":selected,"group_count":len(selected)})
        out[k]=path
    return out

def analyze(folder,paths,scope):
    v=load(folder/"VERIFY.json")
    _,groups,arms,score,status,wall=replay_cube(folder)
    raw=load(paths["raw"])
    g={r["group"]:r for r in raw["sessions"]}
    n,a,r=score.shape
    pair_score=score.mean(axis=2)
    affected=np.array([g[group]["smt_array_group"]=="affected" for group in groups])
    parse={}
    bymode={}
    for option in ("raw","raw_exp","tuple","nested"):
        parse[option]={r["group"]:r["status"] for r in lines(BASE/f"parse-{option}.jsonl")}
    for flag,k in (("affected",affected),("unchanged",~affected),("all",np.ones(n,dtype=bool))):
        sub=[i for i,v in enumerate(k) if v]
        assert sub
        d=group_draws([g[groups[i]]["family"] for i in sub],np.random.default_rng(831900))
        baseline=pair_score[sub,arms.index("raw")]
        for i,arm in enumerate(arms):
            choice=arm.removesuffix("_global")
            values=pair_score[sub,i]
            c=Counter(status[sub,i,:].ravel())
            bymode.setdefault(flag,{})[arm]={"par2":float(values.mean()),"vs_raw":frac(values,baseline,d),"counts":c,
                                           "parsed_classes":sum(parse[choice].get(groups[j])=="parsed" for j in sub)}
    eligible=bymode["affected"]
    encoding=min(ARRAY_TYPES,key=lambda name:(-eligible[name]["parsed_classes"],
                 eligible[name]["counts"].get("error",0)+eligible[name]["counts"].get("incorrect",0),
                 eligible[name]["par2"],name))
    if not eligible[encoding]["parsed_classes"] or eligible[encoding]["counts"].get("incorrect",0):
        encoding=None
    result={"programs":n,"families":len(set(q["family"] for q in g.values())),"repeats":r,
            "affected":int(affected.sum()),"unchanged":int((~affected).sum()),
            "source_affected":scope["affected"],"total_reruns":v["rows"],
            "scope_sha256":digest(SCOPE),"metrics_by_source_kind":bymode,
            "next_encoding":encoding,"z3_audited_pairs":n,
            "excluded":{k:load(p).get("adapter_excluded",[]) for k,p in paths.items()}}
    write_json(folder/"metrics.json",result)
    inf=f"У {int(affected.sum())} сопоставимых ранее несовместимых классов разбор cvc5 прошёл в {eligible['tuple']['parsed_classes']} случаях с кортежами и {eligible['nested']['parsed_classes']} с вложенными массивами."
    write_json(ROOT/"evidence/12z-array-bridge.json",{
        "title":"12б · Перенос формата многомерных массивов",
        "done":f"Верифицированы разбор и {v['rows']} воспроизведений альтернативных записей массивов.",
        "summary":inf,
        "paragraphs":[
            f"Разведочное продолжение после 2 916 ошибок разбора в библиотечной серии. Использовано {n} ранее записанных методов, в том числе {int(affected.sum())} с многомерным синтаксисом и {int((~affected).sum())} неизменённых контролей. {r} повторов на ячейку. Набор разграничен по арности объявления Array до замеров.",
            "Один переносимый вариант использует алгебраический тип данных с конструктором из двух полей, второй представляет массив как вложение. Кортеж даёт взаимно-однозначное представление пары индексов. Вложение соответствует выбору индексов по очереди. Сверены все ответы Z3 после замены с исходной живой записью. Особенности представления извлечённых моделей остаются ограничением.",
            "Разбор без запуска поиска проверен отдельно. При парных замерах команды, запросы модели и ядра стоят в исходном порядке. В преобразованных режимах и специальном контроле исходной записи включён общий --arrays-exp.",
            f"Выбор для новых библиотечных семейств зафиксирован после этой разведки: {encoding or 'нет принятых преобразований'}. Сначала учитывались охват разбора, затем ошибки/несовпадения и PAR2. Ни один из следующих новых проектов при выборе ещё не записывался."
        ],
        "tables":[card_table(f"Библиотечные записи · {n} классов, {r} повторов",["Поднабор","Режим","Разобрано классов","PAR2, с",
                    "К исходному [95% ДИ]","Таймауты","Ошибки","Несовпадения"],
                    [[k,a,row["parsed_classes"],f'{row["par2"]:.3f}',fmt_ci(row["vs_raw"]),
                      row["counts"].get("timeout",0),row["counts"].get("error",0),row["counts"].get("incorrect",0)]
                     for k,d in bymode.items() if k!="all" for a,row in d.items()])],
        "paths":["protocols/array-bridge.json","runs/11b-array-bridge/source/manifest.json",
                 "runs/11b-array-bridge/<формат>/z3-audit.jsonl","runs/11b-array-bridge/parse-<режим>.jsonl",
                 "runs/11b-array-bridge/replay/VERIFY.json","runs/11b-array-bridge/replay/metrics.json"],
        "recommendation":"Для полного библиотечного потока сначала обеспечивать совместимый SMT-LIB и сверять порядок ответов. Проверять влияние самого представления массива на время отдельно от флагов.",
        "next":"Проверить выбранный формат и заранее зафиксированные настройки на других Java-проектах с обработкой байткода, контейнерами и сериализацией."
    })
    build()
    return result

def main():
    out=BASE/"replay"
    if (out/"metrics.json").exists():
        return load(out/"metrics.json")
    scope=prepare()
    source=BASE/"source/manifest.json"
    alternatives={}
    todo=[]
    for i,k in enumerate(ARRAY_TYPES):
        d=BASE/k/"manifest.json"
        if d.exists():
            alternatives[k]=d
        else:
            todo.append((source,d.parent,k,CORES[i]))
    if todo:
        with ProcessPoolExecutor(max_workers=len(todo)) as pool:
            for spec,value in zip(todo,pool.map(audit_stage,todo),strict=True):
                alternatives[spec[2]]=value
    paths=matched_manifests(source,alternatives)
    top=load(paths["raw"])["sessions"]
    assert len(top)>=18
    branches=[("raw",paths["raw"],[]),("raw_exp",paths["raw"],["--arrays-exp"]),
              ("tuple",paths["tuple"],["--arrays-exp"]),("nested",paths["nested"],["--arrays-exp"])]
    tasks=[(p,arm,flags,CORES[i]) for i,(arm,p,flags) in enumerate(branches) if not (BASE/f"parse-{arm}.jsonl").exists()]
    if tasks:
        with ProcessPoolExecutor(max_workers=len(tasks)) as pool:
            scheduled=[pool.submit(parse_only,*task) for task in tasks]
            for pending in scheduled:
                pending.result()
    if not (out/"VERIFY.json").exists():
        adapted={k:paths[k.removesuffix("_global")] for k in scope["configs"] if k.startswith(ARRAY_TYPES)}
        flags={k:["--arrays-exp"] for k in scope["configs"] if k!="raw"}
        launch(out,paths["raw"],scope["configs"],REPEATS,43119,2100,manifest_by_arm=adapted,extra_flags_by_arm=flags)
    return analyze(out,paths,scope)

if __name__=="__main__":
    main()
