"""Paired factorial: distinguish a real timing effect from session-cap censoring."""
from collections import Counter
from pathlib import Path
import sys

import numpy as np

from corpus import materialize
from portfolio import MENU, LOCK, compatible, frac, group_draws, smoke_matrix
from replay import ROOT, digest, launch, load, log, now
from report import build, write_json
from stability_metrics import B, card_table, fmt_ci, pct, replay_cube

BASE=ROOT/"runs/06-cap-calibration"
LOCK_FILE=ROOT/"protocols/cap-calibration.json"
FACTORIAL={"default":{},"sat":{"bv-sat-solver":"cadical"},
           "simpl":{"simplification":"none"},
           "joint":{"bv-sat-solver":"cadical","simplification":"none"},
           "default_alias":{}}
CAPS=(1.5,4.5)
REPEATS=12

def scope():
    if LOCK_FILE.exists():
        return load(LOCK_FILE)
    data=load(MENU)
    configs=FACTORIAL.copy()
    arm=data["global_arm"]
    cfg=data["arms"][arm]
    if cfg not in configs.values():
        configs["portfolio_global"]=cfg
    for v in configs.values():
        compatible(v)
    checked=smoke_matrix(configs,"06-cap-smoke")
    assert all(arm in checked["pass"] for arm in configs)
    write_json(LOCK_FILE,{
        "locked":now(),"hypothesis":"The apparent effect of the CDCL backend and simplification is modified by time-censoring and their interaction. Detect whether it persists with longer cap on the same full incremental sessions.",
        "menu_reference_sha256":digest(MENU),"selector_reference_sha256":digest(LOCK),
        "smoke_sha256":digest(ROOT/"runs/06-cap-smoke/VERIFY.json"),
        "configs":configs,"limits_s":CAPS,"repeats":REPEATS,
        "primary":"ratio of joint to default by timeout cap; additive interaction of PAR2 (joint minus sat minus simpl plus default), all program-paired and bootstrapped at family level",
        "secondary":"conditional raw wall/solver CPU on jointly solved program × repeat pairs, separately count rescue/loss timeouts, compare actual 1.5 s observations with synthetic recensoring of matches at 4.5 s",
        "model_scope":"no model read of this independent materialization",
    })
    LOCK_FILE.chmod(0o444)
    return load(LOCK_FILE)

def read_calibration(directory,manifest,output):
    plan,groups,arms,scores,status,wall=replay_cube(directory)
    verify=load(directory/"VERIFY.json")
    m=load(manifest)
    family_by={r["group"]:r["family"] for r in m["sessions"]}
    families=[family_by[g] for g in groups]
    settings=load(LOCK_FILE)
    rng=np.random.default_rng(71272)
    draw=group_draws(families,rng)
    aliases=list(settings["configs"])
    n,a,r=scores.shape
    arms_ix={arm:i for i,arm in enumerate(arms)}
    by_budget={}
    interaction_program_effect={}
    for cap in CAPS:
        short=str(cap)
        ref=arms_ix[f"default@{short}"]
        alias=arms_ix[f"default_alias@{short}"]
        keys={k:arms_ix[f"{k}@{short}"] for k in aliases}
        s=scores[:, :, :].mean(axis=2)
        den=s[:,ref]
        offsets=np.sum(draw,axis=1)
        ci=lambda q: np.percentile((draw@q)/offsets,[2.5,97.5]).tolist()
        stats={}
        for key,i in keys.items():
            both=(status[:,ref]=="solved")&(status[:,i]=="solved")
            diff=(wall[:,i]-wall[:,ref])[both]
            stats[key]={"par2":float(s[:,i].mean()),"ratio":frac(s[:,i],den,draw),
                        "counts":dict(Counter(status[:,i].reshape(-1))),
                        "paired_both_solved":int(diff.size),
                        "paired_wall_delta_median_ms":float(np.median(diff)*1000),
                        "rescued_default_timeouts":int(np.sum((status[:,ref]=="timeout")&(status[:,i]=="solved"))),
                        "new_timeouts":int(np.sum((status[:,ref]=="solved")&(status[:,i]=="timeout")))}
        interaction=s[:,keys["joint"]]-s[:,keys["sat"]]-s[:,keys["simpl"]]+den
        interaction_program_effect[short]=interaction
        by_budget[short]={"arms":stats,"interaction_par2_s":float(interaction.mean()),
                          "interaction_ci":ci(interaction),
                          "absolute_alias_median_ms":float(np.median(np.abs(
                              (wall[:,alias]-wall[:,ref])[(status[:,ref]=="solved")&(status[:,alias]=="solved")]))*1000)}
    cap_effects={}
    for key in aliases:
        low,high=(arms_ix[f"{key}@{cap}"] for cap in CAPS)
        # Alternate long time penalty back to the short contract. This diagnostic
        # shares the exact program/cpu/repeat but uses a fresh separate replay.
        emulated=np.where((status[:,high]=="solved")&(wall[:,high]<=CAPS[0]),wall[:,high],2*CAPS[0])
        low_achieved=status[:,low]=="solved"
        em_achieved=emulated<2*CAPS[0]
        cap_effects[key]={"low_observed_par2_s":float(scores[:,low].mean()),
                          "long_recensored_par2_s":float(emulated.mean()),
                          "short_long_category_flips":int(np.sum(low_achieved!=em_achieved)),
                          "long_rescue_count":int(np.sum((~low_achieved)&(status[:,high]=="solved")))}
    across=interaction_program_effect["4.5"]-interaction_program_effect["1.5"]
    contrast={"delta_s":float(across.mean()),"ci":np.percentile((draw@across)/np.sum(draw,axis=1),[2.5,97.5]).tolist()}
    result={"difference_in_interaction":contrast,"program_count":n,"families":len(set(families)),"repeats":r,"rows":verify["rows"],
            "rejected":len(m["rejected"]),"by_budget":by_budget,"cap_diagnostics":cap_effects,
            "family_bootstrap_B":B,"family_bootstrap_seed":71272,
            "source_scope":m["scope"],"per_program":{g:{arm:float(scores[i,ia].mean()) for ia,arm in enumerate(arms)} for i,g in enumerate(groups)}}
    write_json(output,result)
    return result

def publish(result,directory,manifest,title="07 · Взаимодействие опций и предел времени",
            card="07-calibration",real=False):
    n,f,r=result["program_count"],result["families"],result["repeats"]
    m=result["by_budget"]
    lower,higher=[m[str(cap)] for cap in CAPS]
    label="библиотечной записи" if real else "новом закрытом генераторном наборе"
    intro=f"На {label} взаимодействие флагов при лимитах 1,50 и 4,50 с составило {lower['interaction_par2_s']:+.3f} и {higher['interaction_par2_s']:+.3f} с PAR2."
    texts=[
        f"Парный факторный эксперимент. {n} Java-программ, {f} семейств, {r} повторов на ячейку, {result['rows']} воспроизведений. Оба лимита и все настройки перемешивались внутри каждого блока «программа × повтор». Отдельно измерялся дубль default. Каждому таймауту соответствует штраф, равный удвоенному лимиту.",
        "A задаёт --bv-sat-solver=cadical, B задаёт --simplification=none, совместный запуск задаёт оба флага. Аддитивное взаимодействие равно PAR2(A+B) − PAR2(A) − PAR2(B) + PAR2(default). Отрицательная величина означает уменьшение PAR2 сверх суммы отдельных эффектов. Это величина при данном лимите, который меняет цену таймаута.",
        f"Взаимодействие при 1,50 с: {lower['interaction_par2_s']:+.3f} с, 95% ДИ [{lower['interaction_ci'][0]:+.3f}; {lower['interaction_ci'][1]:+.3f}]. При 4,50 с: {higher['interaction_par2_s']:+.3f} с, 95% ДИ [{higher['interaction_ci'][0]:+.3f}; {higher['interaction_ci'][1]:+.3f}]. Парный bootstrap по семействам.",
        (f"Сопоставление двух взаимодействий на тех же программах: 4,50 минус 1,50 с даёт "
         f"{result['difference_in_interaction']['delta_s']:+.3f} с PAR2, 95% ДИ "
         f"[{result['difference_in_interaction']['ci'][0]:+.3f}; {result['difference_in_interaction']['ci'][1]:+.3f}]."),
        f"У default медиана абсолютной разницы решений с дублем {lower['absolute_alias_median_ms']:.1f} мс при коротком лимите и {higher['absolute_alias_median_ms']:.1f} мс при длинном. Таблица пересечения лимитов показывает, насколько воспроизводится решение раньше 1,50 с по независимому прогону с длинным лимитом.",
    ]
    main_rows=[]
    for cap,key in ((cap,k) for cap in CAPS for k in m[str(cap)]["arms"]):
        s=m[str(cap)]["arms"][key]
        main_rows.append([cap,key,f'{s["par2"]:.3f}',fmt_ci(s["ratio"]),s["counts"].get("timeout",0),
                         s["rescued_default_timeouts"],s["new_timeouts"],s["paired_both_solved"],
                         f'{s["paired_wall_delta_median_ms"]:+.1f}',
                         sum(s["counts"].get(k,0) for k in ("unknown","error","incorrect"))])
    tables=[card_table(f"Воспроизведение с двумя лимитами · по {n*r} сеансов на строку",["Лимит, с","Флаги","PAR2, с",
       "К default [95% ДИ]","Таймауты","Таймауты default, решённые опцией","Новые таймауты","Полные пары",
       "Медиана Δ wall, мс","Ошибки + unknown + несовпадения"],main_rows),
       card_table(f"Длинные прогоны, мысленно ограниченные 1,50 с · {n*r} пар на настройку",
          ["Флаги","Фактический короткий PAR2, с","Переоценка длинного PAR2, с","Расхождения на границе","Решилось с большим лимитом"],
          [[k,f'{v["low_observed_par2_s"]:.3f}',f'{v["long_recensored_par2_s"]:.3f}',
            v["short_long_category_flips"],v["long_rescue_count"]] for k,v in result["cap_diagnostics"].items()])]
    write_json(ROOT/f"evidence/{card}.json",{
        "title":title,"done":f"Проверены {result['rows']} парных измерений двух лимитов.",
        "summary":intro,"paragraphs":texts,"tables":tables,
        "paths":["protocols/cap-calibration.json",
                 str(manifest.relative_to(ROOT)),str((directory/"plan.json").relative_to(ROOT)),
                 str((directory/"worker-{0..5}.jsonl").relative_to(ROOT)),str((directory/"metrics.json").relative_to(ROOT)),
                 str((directory/"VERIFY.json").relative_to(ROOT))],
        "recommendation":"Выбирать настройку под рабочий лимит. Проверять совместное действие флагов и число возвращённых из таймаута сеансов на целевом распределении.",
        "next":"Проверить устойчивость к внутреннему seed решателя и повторить сравнение на новых семействах с зафиксированными контрастами."
    })
    build()

def main():
    locked=scope()
    build("Проверяю влияние лимита сеанса и взаимодействие двух опций на новых семействах.")
    capture=BASE/"corpus"
    manifest=capture/"manifest.json"
    if not manifest.exists():
        manifest=materialize("calibration-test",LOCK_FILE,capture,
            [ROOT/"runs/02-portfolio/dev-corpus",ROOT/"runs/02-portfolio/test-corpus",ROOT/"runs/05-search/heldout"],core=8)
    order_conf={f"{key}@{cap}":value for cap in CAPS for key,value in locked["configs"].items()}
    limits={arm:float(arm.split("@")[1]) for arm in order_conf}
    run=BASE/"replay"
    if not (run/"VERIFY.json").exists():
        launch(run,manifest,order_conf,REPEATS,981034,3700,timeout_by_arm=limits)
    result=read_calibration(run,manifest,run/"metrics.json")
    publish(result,run,manifest)

if __name__=="__main__":
    failure=ROOT/"runs/04-transfer/corpus/manifest.json"
    if failure.exists() and load(failure)["group_count"]==0:
        import transfer_recover
        try:
            transfer_recover.main()
        except Exception as ex:
            import traceback
            with (ROOT/"runs/04-transfer/recovery-failure.log").open("w") as out:
                traceback.print_exc(file=out)
            log(f"Library recovery failure: {type(ex).__name__}")
    main()
