"""Resume successfully recorded corpora and retry the narrow live CLI smoke gate."""
import os
from pathlib import Path
import subprocess

from corpus import PRIORS, audited_manifest
from replay import CORES, PYTHON, ROOT, STOP, digest, load, log, now
from report import build, write_json

def generated_calibration():
    import calibration
    original=calibration.BASE/"corpus"
    if not (original/"manifest.json").exists() or load(original/"manifest.json")["group_count"]:
        return original/"manifest.json"
    damaged=load(original/"manifest.json")
    assert any("unclosed" in row["reason"] for row in damaged["rejected"])
    recovered=calibration.BASE/"corpus-recovered"
    dest=recovered/"manifest.json"
    if dest.exists() and load(dest)["group_count"]:
        return dest
    recovered.mkdir()
    for n in ("sessions","traces","source","programs.json","release.json"):
        (recovered/n).symlink_to(original/n,target_is_directory=(original/n).is_dir())
    p=original/"pre-audit-manifest.json"
    fixed=load(p)
    write_json(dest,fixed)
    config=load(ROOT/"protocols/calibration-test-seed.json")
    earlier=[*PRIORS,ROOT/"runs/02-portfolio/dev-corpus",ROOT/"runs/02-portfolio/test-corpus"]
    revised=audited_manifest(recovered,CORES[0],earlier,config["minimum_programs"])
    dest.chmod(0o444)
    write_json(recovered/"recovery.json",{"at":now(),"original_rejection_manifest_sha256":digest(original/"manifest.json"),
               "unaltered_pre_audit_sha256":digest(p),"original_release_sha256":digest(original/"release.json"),
               "restored":"successful driver method end and balanced operations; optional close as recorded by the frozen tracer",
               "programs":revised["group_count"],"raw_event_replay_repeated":False})
    return dest

def attempt(name,call):
    log(f"RECOVERY START {name}")
    try:
        result=call()
    except Exception as exc:
        import traceback
        filename=f"runs/{name}-recovery-failure.log"
        with (ROOT/filename).open("w") as out:
            traceback.print_exc(file=out)
        log(f"RECOVERY FAIL {name}: {type(exc).__name__}")
        return {"ok":False,"failure":type(exc).__name__,"log":filename}
    log(f"RECOVERY OK {name}")
    return {"ok":True}

def restore_prerequisites():
    marker=ROOT/"protocols/recovery-status.json"
    if marker.exists():
        return
    import calibration
    import transfer_recover
    from replay import launch
    status={}
    status["transfer"]=attempt("transfer",transfer_recover.main)
    def generate_and_replay():
        manifest=generated_calibration()
        locked=calibration.scope()
        configs={f"{key}@{cap}":value for cap in calibration.CAPS for key,value in locked["configs"].items()}
        limits={k:float(k.split("@")[1]) for k in configs}
        stage=calibration.BASE/"replay"
        if not (stage/"VERIFY.json").exists():
            launch(stage,manifest,configs,calibration.REPEATS,981034,3200,timeout_by_arm=limits)
        result=calibration.read_calibration(stage,manifest,stage/"metrics.json")
        calibration.publish(result,stage,manifest)
    status["calibration"]=attempt("calibration",generate_and_replay)
    def model_smoke():
        dst=ROOT/"runs/05-sol-smoke-retry.out"
        from corpus import commands_log
        commands_log(dst,[PYTHON,"-B",ROOT/"search.py","smoke"],670)
        assert load(ROOT/"runs/05-search/smoke/VERIFY.json")["ok"]
    status["sol_smoke_retry"]=attempt("sol-smoke",model_smoke)
    write_json(marker,status)
    p=ROOT/"evidence/04z-operational.json"
    card=load(p)
    card["paragraphs"].append(
        "Первая проба модели завершила обращение к Sol. Модель пропустила диагностическую команду, "
        "которая запрашивала запрещённый /proc. Новая проба проверила доступность только обучающих файлов "
        "и недоступность контрольных путей, исключив /proc из запроса.")
    if status["calibration"]["ok"]:
        card["paragraphs"].append("После исправления допуска также восстановлена исходная запись калибровки. Решения по флагам и предварительный seed остались зафиксированными.")
    if status["sol_smoke_retry"]["ok"]:
        card["paragraphs"].append("Повторная проба Sol, RF и SMAC с реальными измерениями обучения успешно прошла.")
    else:
        card["paragraphs"].append("Повторная проба Sol не прошла, подробности сохранены в журнале.")
    card["paths"].extend(["protocols/recovery-status.json","runs/05-search/smoke-attempt1/isolation/VERIFY.json",
                          "runs/05-sol-smoke-retry.out"])
    write_json(p,card)
    build()

def restore_measurement_start():
    from corpus import commands_log
    import solver_seed
    marker=ROOT/"protocols/warm-recovery-status.json"
    if marker.exists():
        return
    def smoke():
        dst=ROOT/"runs/05-sol-smoke-warm-retry.out"
        commands_log(dst,[PYTHON,"-B",ROOT/"search.py","smoke"],660)
        assert load(ROOT/"runs/05-search/smoke/VERIFY.json")["ok"]
    failed=ROOT/"runs/05-search/smoke/VERIFY.json"
    if not failed.exists():
        s=attempt("sol-warm",smoke)
    else:
        s={"ok":load(failed)["ok"]}
    needed=not (ROOT/"evidence/08-solver-seeds.json").exists()
    v=attempt("solver-seeds",solver_seed.main) if needed else {"ok":True}
    record={"sol_smoke":s,"solver_seed":v}
    write_json(marker,record)
    note=ROOT/"evidence/04z-operational.json"
    card=load(note)
    card["paragraphs"].append("Вторая проба Sol выполнила команду и подтвердила изоляцию. Разминка остановилась до измерений из-за отсутствующего импорта hashlib. Отдельный контроллер seed также потребовал импорт списка ядер. Оба исправления проверены статически. Первые логи сохранены.")
    if s["ok"]:
        card["paragraphs"].append("Проверка реальных ответов, обратной связи Sol, RF и SMAC завершилась после восстановления разминки.")
    card["paths"].extend(["protocols/warm-recovery-status.json","runs/05-search/smoke/seed-80139-attempt2/warm.out",
                          "runs/05-sol-smoke-warm-retry.out"])
    write_json(note,card)
    build()
