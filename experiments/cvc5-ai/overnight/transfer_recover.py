"""Revalidate intact trace artifacts after correcting an erroneous close-event gate."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import os

from corpus import audited_manifest
from portfolio import MENU
from replay import CORES, ROOT, digest, ev, launch, load, log, now
from report import build, write_json
from transfer import BASE, SCOPE, analyze

SOURCE=BASE/"corpus"
RECOVERED=BASE/"corpus-recovered"
PLAN=ROOT/"protocols/transfer-admission-recovery.json"

def setup():
    if PLAN.exists():
        return load(PLAN)
    original=load(SOURCE/"manifest.json")
    assert original["group_count"]==0
    counters=Counter()
    hashes={}
    total=0
    for lib,stats in original["counts"].items():
        total+=stats["driver_methods"]
        counters.update(stats["reasons"])
        prev=SOURCE/lib/"pre-audit-manifest.json"
        hashes[lib]=digest(prev)
    info={"locked":now(),"problem":"The callback-based tracer does not usually emit close, even on previously accepted frozen Java methods. Requiring it incorrectly rejected all new library sessions.",
          "restored_boundary":"One open, balanced incremental scopes, valid known answers, successful driver end per method with matching tag, independent Z3-to-recorded check. Close is optional in this existing tracer.",
          "inputs_unchanged":hashes,"failed_driver_methods":total,"initial_rejected_traces_by_cause":dict(counters),
          "previous_cvc5_measurements":0,"original_manifest_sha256":digest(SOURCE/"manifest.json"),
          "reuse":"Reuse intact raw logs, archived Java provenance, and the previously locked menu. No live capture repeated."}
    write_json(PLAN,info)
    PLAN.chmod(0o444)
    return info

def recover_one(lib,slot,expected):
    os.sched_setaffinity(0,{CORES[slot]})
    first=SOURCE/lib
    target=RECOVERED/lib
    target.mkdir(parents=True)
    manifest=first/"pre-audit-manifest.json"
    assert digest(manifest)==expected
    # Preserve the original rejected-output manifests and all capture logs.
    for name in ("sessions","traces"):
        (target/name).symlink_to(first/name,target_is_directory=True)
    initial=load(manifest)
    write_json(target/"manifest.json",initial)
    m=audited_manifest(target,CORES[slot],[],0)
    return lib,m

def recover_corpus(info):
    base=RECOVERED
    base.mkdir(exist_ok=False)
    by=info["inputs_unchanged"]
    with ProcessPoolExecutor(max_workers=min(6,len(by))) as workers:
        results=[workers.submit(recover_one,name,i,hashval) for i,(name,hashval) in enumerate(sorted(by.items()))]
        completed=dict(f.result() for f in results)
    captured={}
    all_rows=[]
    unique=set()
    for lib,m in completed.items():
        capture={"driver_methods":m["driver_methods"],"programs":m["group_count"],
                 "rejected":len(m["rejected"]),"reasons":dict(Counter(a["reason"] for a in m["rejected"])),
                 "missing_or_unparsed_methods":len(m["unparsed_or_missing_method_traces"])}
        captured[lib]=capture
        for row in m["sessions"]:
            if row["sha256"] in unique:
                capture["combined_duplicate"]=capture.get("combined_duplicate",0)+1
                continue
            unique.add(row["sha256"])
            all_rows.append({**row,"file":lib+"/"+row["file"],"trace":lib+"/"+row["trace"]})
    assert len(all_rows)>=load(SCOPE)["minimum_admitted_total"]
    dest=base/"manifest.json"
    write_json(dest,{"format":1,"scope":"recovery of original full-session library traces",
                     "group_count":len(all_rows),"sessions":all_rows,"counts":captured,
                     "failed_projects":{},"plan_sha256":digest(SCOPE),"recovery_sha256":digest(PLAN)})
    ev.verify(dest)
    dest.chmod(0o444)
    log(f"VERIFY library recovery: {len(all_rows)} unchanged sessions admitted")
    return dest

def main():
    if (ROOT/"evidence/04-transfer.json").exists():
        return
    info=setup()
    build("Восстанавливаю допуск библиотечных трасс после исправления проверки завершения.")
    path=RECOVERED/"manifest.json"
    if not path.exists():
        path=recover_corpus(info)
    run=BASE/"replay"
    scope=load(SCOPE)
    if not (run/"VERIFY.json").exists():
        launch(run,path,scope["configs"],scope["repeats"],688953,2400)
    result=analyze(run,path,scope)
    note=ROOT/"evidence/04z-operational.json"
    card=load(note)
    header=sum(info["initial_rejected_traces_by_cause"].values())
    card["summary"]=f"Библиотечные записи восстановлены: {result['programs']} принятых сеансов после исправления проверки завершения."
    card["paragraphs"].append(
        f"Первый захват завершил {info['failed_driver_methods']} методов из шести JAR. Из {header} отклонённых трасс "
        f"{info['initial_rejected_traces_by_cause'].get('unclosed or multiply opened solver trace',0)} получили лишнее требование события close. "
        "Прежний инструмент обычно его не записывает. Допуск восстановлен по завершению метода, балансу стека и независимой проверке Z3. "
        "Повторная запись JVM не потребовалась. Меню и все исходные логи сохранены.")
    card["paths"].extend(["protocols/transfer-admission-recovery.json","runs/04-transfer/corpus/manifest.json",
                          "runs/04-transfer/corpus-recovered/manifest.json"])
    write_json(note,card)
    build()
if __name__=="__main__":
    main()
