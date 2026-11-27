"""Record fresh ordered KSMT sessions from installed public Java library JARs."""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from zipfile import ZipFile

import numpy as np

from corpus import USVM, audited_manifest, commands_log, trace
from portfolio import MENU, LOCK, compatible, frac, group_draws
from replay import CAMPAIGN, CORES, PYTHON, ROOT, STOP, check_budget, digest, ev, launch, load, log, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci, pct, replay_cube

BASE=ROOT/"runs/04-transfer"
SCOPE=ROOT/"protocols/transfer-scope.json"
# Public artifact identity and package are fixed without any solver timing data.
# The old client replay is only provenance and has no inputs to this experiment.
LIBRARIES=(
    ("commons-codec","commons-codec-1.17.1.jar","org.apache.commons.codec.",15),
    ("commons-lang3","commons-lang3-3.16.0.jar","org.apache.commons.lang3.math.",10),
    ("commons-io","commons-io-2.17.0.jar","org.apache.commons.io.",14),
    ("gson","gson-2.11.0.jar","com.google.gson.",14),
    ("guava","guava-31.1-jre.jar","com.google.common.primitives.",16),
    ("commons-compress","commons-compress-1.21.jar","org.apache.commons.compress.",14),
)
CACHE_ROOTS=(CAMPAIGN/"setup/gradle/caches/modules-2/files-2.1",Path.home()/".gradle/caches/modules-2/files-2.1")

def locate(name):
    for location in CACHE_ROOTS:
        paths=sorted(location.glob(f"*/*/*/*/{name}"))
        if paths:
            return paths[0].resolve()
    raise FileNotFoundError(name)

def scope():
    if SCOPE.exists():
        return load(SCOPE)
    menu=load(MENU)
    assert LOCK.is_file()
    artifacts=[]
    for lib,name,package,limit in LIBRARIES:
        jar=locate(name)
        artifacts.append({"project":lib,"jar":str(jar),"sha256":digest(jar),"package":package,"max_classes":limit,
                          "method_ms":7500,"solver_ms":2000})
    plan={
        "locked":now(),"menu_sha256":digest(MENU),"selector_lock_sha256":digest(LOCK),
        "hypothesis":"Whether the frozen global setting improves over default on independently captured genuine Java library methods, and how much independent-repeat oracle headroom exists.",
        "selection":"immutable DatasetDriver seed 668921, at most one method per public concrete class, operation order preserved. Package and class limits fixed before generation. Admission by trace and independent Z3 audit only.",
        "provenance":"local installed Maven artifacts; older clients used some of these project families, but previous individual SMT queries and timings are not reused here",
        "library_historical_family_overlap":["commons-codec","commons-lang3","gson"],
        "repeats":12,"timeout_s":1.50,"library_split":"each JAR is a family; these libraries are never used to fit the deployed menu or selector",
        "method_minimum_checks":2,"minimum_admitted_total":12,"artifacts":artifacts,"configs":menu["arms"],"global":menu["global_arm"],
        "primary":"paired ratio of frozen global to default, class/method-level equal weights; cluster by program and separately by whole library",
    }
    write_json(SCOPE,plan)
    SCOPE.chmod(0o444)
    return plan

def convert_library(target,artifact):
    sys.path.insert(0,str(USVM))
    from convert import convert
    traces=target/"traces"
    methods={r["tag"]:r for r in (json.loads(line) for line in (traces/"methods.jsonl").read_text().splitlines())}
    (target/"sessions").mkdir()
    considered,admitted,rejected=set(),[],[]
    with ZipFile(artifact["jar"]) as jar:
        for file in sorted(traces.glob("*.events")):
            try:
                session,tag,oracle,operations=convert(file)
                considered.add(tag)
                if methods[tag]["status"]!="ok":
                    raise ValueError(f'driver {methods[tag]["status"]}')
                if len(oracle)<2:
                    raise ValueError("below minimum two ordered checks")
                selector=tag.split("/",1)[1].split("(",1)[0]
                name,method=selector.rsplit(".",1)
                blob=jar.read(name.replace(".","/")+".class")
            except (ValueError,KeyError,IndexError,UnicodeError) as ex:
                rejected.append({"file":file.name,"reason":str(ex),"sha256":digest(file)})
                continue
            session_path=target/"sessions"/f'{artifact["project"]}-{file.stem}.smt2'
            session_path.write_text(session)
            admitted.append({"family":artifact["project"],"group":f'{artifact["project"]}:{name}',
                             "method":method,"file":str(session_path.relative_to(target)),"tag":tag,
                             "trace":str(file.relative_to(target)),"trace_sha256":digest(file),
                             "sha256":digest(session_path),"source_sha256":hashlib.sha256(blob).hexdigest(),
                             "oracle":oracle,"operations":operations})
    # Report methods that never made an admitted trace separately.
    data={"format":1,"split":artifact["project"],"scope":"fresh USVM/KSMT ordered incremental Java library traces",
          "group_count":len(admitted),"sessions":admitted,"rejected":rejected,
          "driver_methods":len(methods),"unparsed_or_missing_method_traces":sorted(set(methods)-considered)}
    write_json(target/"manifest.json",data)

def capture_one(path,slot,scope_path=SCOPE):
    plan=load(scope_path)
    artifact=plan["artifacts"][slot]
    core=CORES[slot]
    target=path/artifact["project"]
    target.mkdir(parents=True,exist_ok=False)
    assert digest(artifact["jar"])==artifact["sha256"]
    trace(target,core,artifact,jars=(artifact["jar"],),package=artifact["package"],
          project=artifact["project"],shard_seed=668921)
    convert_library(target,artifact)
    # Some library groups can be unscorable. Report them and let the locked
    # cohort threshold determine eligibility for the combined comparison.
    result=audited_manifest(target,core,[],0)
    write_json(target/"counts.json",{"core":core,"driver_methods":result["driver_methods"],
                "programs":result["group_count"],"rejected":len(result["rejected"]),
                "reasons":dict(Counter(r["reason"] for r in result["rejected"])),
                "missing_or_unparsed_methods":len(result["unparsed_or_missing_method_traces"])})

def capture(base, plan, scope_path=SCOPE):
    base.mkdir(parents=True,exist_ok=False)
    workers=[]
    cap=min(STOP,time.time()+2000)
    try:
        for slot,artifact in enumerate(plan["artifacts"]):
            with (base/f'{artifact["project"]}.out').open("wb") as out:
                workers.append(subprocess.Popen(["taskset","-c",str(CORES[slot]),PYTHON,"-B",__file__,"capture",base,str(slot),scope_path],
                                                stdout=out,stderr=out,start_new_session=True))
        while any(p.poll() is None for p in workers):
            check_budget()
            if time.time()>cap:
                raise TimeoutError("library trace batch")
            time.sleep(3)
    finally:
        import signal
        for p in workers:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:
                    p.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGKILL)
                    p.wait()
    combined,seen,failures=[],set(),{}
    capture_counts={}
    for p,artifact in zip(workers,plan["artifacts"],strict=True):
        name=artifact["project"]
        folder=base/name
        if p.returncode:
            failures[name]=p.returncode
            continue
        data=load(folder/"manifest.json")
        counts=load(folder/"counts.json")
        capture_counts[name]=counts
        for a in data["sessions"]:
            if a["sha256"] in seen:
                counts.setdefault("combined_duplicate",0)
                counts["combined_duplicate"]+=1
                continue
            seen.add(a["sha256"])
            combined.append({**a,"file":name+"/"+a["file"],"trace":name+"/"+a["trace"]})
    manifest=base/"manifest.json"
    write_json(manifest,{"format":1,"group_count":len(combined),"sessions":combined,"counts":capture_counts,
                        "failed_projects":failures,"plan_sha256":digest(scope_path),"created":now()})
    ev.verify(manifest)
    if len(combined)<plan["minimum_admitted_total"]:
        raise RuntimeError(f"only {len(combined)} admitted class sessions on real libraries")
    manifest.chmod(0o444)
    log(f"VERIFY {base.relative_to(ROOT)}: {len(combined)} sessions of {len(capture_counts)} captured libraries")
    return manifest

def analyze(path,manifest,scope):
    check=load(path/"VERIFY.json")
    raw=load(manifest)
    _,groups,arms,score,status,wall=replay_cube(path)
    n,a,r=score.shape
    counters={c:dict(Counter(status[:,i].reshape(-1))) for i,c in enumerate(arms)}
    ss=score.mean(axis=2)
    glob,default,alias=(arms.index(k) for k in (scope["global"],"default","default_alias"))
    families=[g.split(":",1)[0] for g in groups]
    mat=group_draws(families,np.random.default_rng(520751))
    program_mat=group_draws(groups,np.random.default_rng(122751))
    valid=np.array([i for i in range(a) if i!=alias])
    etc_part_1, etc_part_2=score[:,:,:r//2].mean(axis=2),score[:,:,r//2:].mean(axis=2)
    selected1=valid[np.argmin(etc_part_1[:,valid],axis=1)]
    selected2=valid[np.argmin(etc_part_2[:,valid],axis=1)]
    row=np.arange(n)
    oracle=(etc_part_2[row,selected1]+etc_part_1[row,selected2])/2
    oracle_naive=(etc_part_1[row,selected1]+etc_part_2[row,selected2])/2
    both=(status[:,default]=="solved")&(status[:,alias]=="solved")
    aliases=np.abs(wall[:,default]-wall[:,alias])[both]
    global_effect=frac(ss[:,glob],ss[:,default],mat)
    oracle_effect=frac(oracle,ss[:,glob],mat)
    first_rows=[]
    for family in sorted(set(families)):
        j=np.array([f==family for f in families])
        first_rows.append([family,int(j.sum()),f"{ss[j,default].mean():.3f}",
                           f"{ss[j,glob].mean():.3f}",f"{oracle[j].mean():.3f}",
                           int(np.sum(status[j,default]=="timeout")),int(np.sum(status[j,glob]=="timeout"))])
    matrix={g:{arm:{"par2":float(ss[j,i]),"counts":dict(Counter(status[j,i]))}
               for i,arm in enumerate(arms)} for j,g in enumerate(groups)}
    relative=path.relative_to(ROOT)
    corpus_relative=manifest.parent.relative_to(ROOT)
    result={"programs":n,"families":len(set(families)),"repeats":r,"verify":check,"global":scope["global"],
            "capture":raw["counts"],"failed_projects":raw["failed_projects"],"effect_global_family_ci":global_effect,
            "effect_global_program_ci":frac(ss[:,glob],ss[:,default],program_mat),
            "oracle_cross_par2":float(oracle.mean()),"oracle_same_par2":float(oracle_naive.mean()),"oracle_to_global":oracle_effect,
            "alias_both_solved":int(aliases.size),"alias_median_abs_ms":float(np.median(aliases)*1000),
            "alias_timeout_discordance":int(np.sum((status[:,alias]=="timeout")!=(status[:,default]=="timeout"))),
            "per_program":matrix}
    write_json(path/"metrics.json",result)
    total=Counter()
    for c in counters.values():
        total.update(c)
    summary=(f"На полных сеансах библиотек, {n} классов из {len(set(families))} проектов, "
             f"PAR2 у default {ss[:,default].mean():.3f} с, у глобального {scope['global']} {ss[:,glob].mean():.3f} с.")
    tables=[
        card_table(f"По проектам · {r} повторов на класс и режим",["Проект","Классы","default PAR2, с","Глобальный PAR2, с",
                    "Оракул PAR2, с","Таймауты default","Таймауты глобального"],first_rows),
        card_table("Запись и допуск полных сеансов · одна попытка записи на метод",
                   ["Проект","Методы драйвера","Допущено до проверки дубликатов между JAR",
                    "Отклонённых файлов трасс","Без разбираемой метки"],
                   [[f,c["driver_methods"],c["programs"],c["rejected"],c["missing_or_unparsed_methods"]] for f,c in raw["counts"].items()])
    ]
    texts=[
        f"Свежие записи USVM/KSMT на установленных JAR, {n} классов, один метод и один полный сеанс на класс. {len(set(families))} проектов, {r} повтора на ячейку, всего {check['rows']} запусков. Используется прежний драйвер и независимый аудит ответов Z3. Пути, версии, SHA-256 и пределы записаны до захвата.",
        "В истории работы уже встречались commons-codec, commons-lang3 и Gson. Здесь для них заново записаны упорядоченные сеансы. Процесс cvc5 1.4.1 воспроизводит утверждения, push/pop, проверки с предположениями и запросы моделей и ядер. Старые одиночные запросы в измерение не входят. Абсолютное время этого воспроизведения не включает весь живой JVM-анализ и detach модели.",
        f"Отношение глобального к default {fmt_ci(global_effect)} при кластеризации по проектам и {fmt_ci(result['effect_global_program_ci'])} при пересэмплировании классов. Оракул с независимой половиной повторов к глобальному {fmt_ci(oracle_effect)}. Малое число разных проектов ограничивает переносимость интервалов.",
        f"Полный набор данных включает {total['timeout']} таймаутов, {total['error']} ошибок, {total['unknown']} unknown и {total['incorrect']} несовпадений. Медиана |default − дубль| {result['alias_median_abs_ms']:.1f} мс по {result['alias_both_solved']} полным парам. Смена таймаута в {result['alias_timeout_discordance']} из {n*r} пар.",
        f"Завершение захвата: {len(raw['failed_projects'])} неудачных проектов. Журналы содержат ошибки драйвера и причины каждого отклонения. Методы подбирались без таймингов cvc5."
    ]
    write_json(ROOT/"evidence/04-transfer.json",{
        "title":"04 · Перенос на Java-библиотеки","done":f"Проверено {check['rows']} воспроизведений библиотечных сеансов.",
        "summary":summary,"paragraphs":texts,"tables":tables,
        "paths":["protocols/transfer-scope.json",str(corpus_relative/"manifest.json"),
                 "runs/04-transfer/corpus/<проект>/usvm-driver.log",str(corpus_relative/"<проект>/z3-audit.jsonl"),
                 str(relative/"worker-{0..5}.jsonl"),str(relative/"metrics.json"),str(relative/"VERIFY.json"),
                 "~/Dev/ksmt-ml-experiments/experiments-usvm-client/README.md"],
        "recommendation":"Считать перенос портфеля отдельной задачей по распределению библиотечных методов. Выбирать глобальные флаги после проверки общей стоимости и ошибок на целевой нагрузке.",
        "next":"Сравнить Sol, RF и native SMAC на одинаковом историческом обучении и новом закрытом наборе. Проверить чувствительность к пределу времени отдельно."
    })
    build()
    return result

def main():
    plan=scope()
    target=BASE/"corpus"
    manifest=target/"manifest.json"
    if not manifest.exists():
        manifest=capture(target,plan)
    folder=BASE/"replay"
    if not (folder/"VERIFY.json").exists():
        launch(folder,manifest,plan["configs"],plan["repeats"],688953,2400)
    analyze(folder,manifest,plan)

if __name__=="__main__":
    if len(sys.argv)>1:
        capture_one(Path(sys.argv[2]),int(sys.argv[3]),Path(sys.argv[4]) if len(sys.argv)>4 else SCOPE)
    else:
        main()
