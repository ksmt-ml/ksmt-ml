"""Hold out new installed projects to test frozen routing beyond generated programs."""
from collections import Counter
from pathlib import Path

import numpy as np

from portfolio import MENU, choose, frac, group_draws, predict
from prefix_probe import SCOPE as PREFIX_SCOPE, load_design
from replay import ROOT, digest, launch, load, now
from report import build, write_json
from restart_probe import SCOPE as RESTART_SCOPE
from stability_metrics import B, card_table, fmt_ci, replay_cube
from transfer import capture, locate

BASE=ROOT/"runs/12-new-libraries"
SCOPE=ROOT/"protocols/new-libraries.json"
LIBRARIES=(
    ("asm","asm-9.7.1.jar","org.objectweb.asm.",23),
    ("protobuf-java","protobuf-java-3.25.5.jar","com.google.protobuf.",24),
    ("fastutil","fastutil-8.5.12.jar","it.unimi.dsi.fastutil.ints.",22),
    ("commons-cli","commons-cli-1.5.0.jar","org.apache.commons.cli.",18),
)
REPEATS=10

def freeze(bridge_result=None):
    if SCOPE.exists():
        return load(SCOPE)
    menu=load(MENU)
    scopes={}
    for f in (PREFIX_SCOPE,RESTART_SCOPE):
        if f.exists():
            scopes[f.name]=digest(f)
    artifacts=[]
    for lib,name,package,n in LIBRARIES:
        jar=locate(name)
        artifacts.append({"project":lib,"jar":str(jar),"sha256":digest(jar),"package":package,"max_classes":n,
                          "method_ms":8500,"solver_ms":2200})
    configs=menu["arms"].copy()
    fallback={}
    if RESTART_SCOPE.is_file():
        r=load(RESTART_SCOPE)
        schedule=r["primary_schedule"]
        first,second=schedule["first"],schedule["second"]
        configs["restart"]=menu["arms"][first]
        fallback["restart"]={"first_s":schedule["first_s"],"second":menu["arms"][second]}
    bridge_encoding=bridge_result.get("next_encoding") if bridge_result else None
    data={"locked":now(),"array_bridge":bridge_encoding,
          "bridge_metrics_sha256":digest(ROOT/"runs/11b-array-bridge/replay/metrics.json") if bridge_result else None,
          "question":"Does the trained pre-check SMT prefix selector or bounded restart offer any useful improvement on entirely separate library project families?",
          "project_split":"All four separate from earlier real transfer libraries and all generated training. No cvc5 outcomes used for method selection or trace admission.",
          "development_protocol_hashes":scopes,"minimum_admitted_total":16,
          "artifacts":artifacts,"configs":configs,"fallback_by_arm":fallback,
          "repeats":REPEATS,"global":menu["global_arm"],"portfolio_menu_sha256":digest(MENU),
          "primary":"prefix-only trained rule versus default; fixed global, alias and frozen restart assessed separately; bootstrap whole projects and separately classes",
          "presence_of_models":"reuse existing trained model only if its lock exists before new recordings; library runs are never used to fit it"}
    write_json(SCOPE,data)
    SCOPE.chmod(0o444)
    return data

def analyse(directory,manifest,scope):
    m=load(manifest)
    v=load(directory/"VERIFY.json")
    _,groups,arms,score,status,wall=replay_cube(directory)
    n,a,r=score.shape
    av=score.mean(axis=2)
    original={p["group"]:p for p in m["sessions"]}
    families=[original[g]["family"] for g in groups]
    fam_n=len(set(families))
    refs={k:arms.index(k) for k in ("default","default_alias",scope["global"])}
    chosen={"default":np.full(n,refs["default"]),
            "default_alias":np.full(n,refs["default_alias"]),"global":np.full(n,refs[scope["global"]])}
    if "restart" in arms:
        chosen["restart"]=np.full(n,arms.index("restart"))
    prefix_hashes={}
    us=None
    if PREFIX_SCOPE.name in scope["development_protocol_hashes"]:
        assert digest(PREFIX_SCOPE)==scope["development_protocol_hashes"][PREFIX_SCOPE.name]
        prefix=load(PREFIX_SCOPE)
        P=prefix["config_set"]
        x,fn,prefix_hashes,us=load_design(manifest,groups,"prefix")
        assert list(fn)==families
        model=prefix["variants"]["prefix"]
        from_model=choose(predict(model["model"],x),P.index(prefix["global"]),model["gate_s"])
        chosen["prefix"]=np.array([arms.index(P[j]) for j in from_model])
    i=np.arange(n)
    data={name:av[i,indices] for name,indices in chosen.items()}
    statuses={name:Counter(status[i,indices].reshape(-1)) for name,indices in chosen.items()}
    mat=group_draws(families,np.random.default_rng(84901))
    single=group_draws(groups,np.random.default_rng(20101))
    outcomes={}
    for name,values in data.items():
        outcomes[name]={"par2":float(values.mean()),"ratio_family":frac(values,data["default"],mat),
                        "ratio_program":frac(values,data["default"],single),"counts":statuses[name],
                        "arm_distribution":dict(Counter(arms[j] for j in chosen[name]))}
    complete_profiles=(status[:,refs["default"],:]=="solved")&(status[:,refs["default_alias"],:]=="solved")
    noise=np.abs(wall[:,refs["default_alias"],:]-wall[:,refs["default"],:])[complete_profiles]
    M={"programs":n,"libraries":fam_n,"repeats":r,"checks_per_suite":sum(len(q["oracle"]) for q in m["sessions"]),
       "failed_projects":m["failed_projects"],"capture_counts":m["counts"],"rows":v["rows"],"effects":outcomes,
       "feature_micros":us,"prefix_hashes":prefix_hashes,"noise_ms":float(np.median(noise)*1000),"noise_n":int(noise.size),
       "chosen_by_program":{g:{name:arms[indices[i]] for name,indices in chosen.items()} for i,g in enumerate(groups)}}
    write_json(directory/"metrics.json",M)
    summary=f"На {n} новых методах из {fam_n} ранее неиспользованных проектов глобальная настройка дала {fmt_ci(outcomes['global']['ratio_family'])} к default."
    if "prefix" in outcomes:
        summary+=f" Префиксный селектор дал {fmt_ci(outcomes['prefix']['ratio_family'])}."
    write_json(ROOT/"evidence/13-new-libraries.json",{
        "title":"13 · Проверка переноса на других Java-проектах",
        "done":f"Проверены {v['rows']} воспроизведений новых полных библиотечных сеансов.",
        "summary":summary,
        "paragraphs":[
            f"Четыре установленных JAR заранее выделены как другие проекты. После аудита Z3 и контроля дубликатов в сравнении {n} классов, по одному методу и одной полной записи, {fam_n} проектов, {r} повторов и {M['checks_per_suite']} проверок за обход.",
            "Префиксный селектор обучен только на разработочной генераторной выборке. Бюджетный перезапуск полностью повторяет согласованный поток и использует заранее измеренный порядок флагов. Эти правила и исходные хеши фиксировались до записи настоящих методов.",
            (f"Для двуиндексных типов использован заранее зафиксированный формат {scope['array_bridge']}. "
             f"Преобразовано {sum(row.get('bridge_edited',False) for row in m['sessions'])} принятых полных сеансов, "
             f"отклонено преобразованием и аудитом {len(m.get('adapter_excluded',[]))}. Во всех режимах общий --arrays-exp."
             if scope.get("array_bridge") else "Использован исходный формат KSMT без переноса типа массивов."),
            f"Вторая оценка ДИ кластеризуется по классам, основная объединяет целые проекты ({B} выборок). Разброс между проектами оценивается ограниченным числом библиотек. У дубля default медиана абсолютной разницы {M['noise_ms']:.1f} мс на {M['noise_n']} парах решённых.",
            f"Проектов с аварией записи: {len(m['failed_projects'])}. Возможные непринятые трассы и классы учтены ниже. Проверка относится к воспроизведению клиента."
        ],
        "tables":[card_table(f"Независимый перенос · {n} классов, {r} повторов, по {n*r} сеансов на строку",
                  ["Режим","PAR2, с","К default ДИ по проектам","К default ДИ по классам",
                   "Таймауты","Ошибки + unknown + несовпадения"],
                  [[k,f'{q["par2"]:.3f}',fmt_ci(q["ratio_family"]),fmt_ci(q["ratio_program"]),q["counts"].get("timeout",0),
                    sum(q["counts"].get(x,0) for x in ("error","unknown","incorrect"))] for k,q in outcomes.items()]),
            card_table("Захват USVM · одна попытка на класс",
                  ["Проект","Методов","Принято до отсева общих дубликатов","Отклонено трасс","Не разобрано"],
                  [[k,c["driver_methods"],c["programs"],c["rejected"],c["missing_or_unparsed_methods"]] for k,c in m["counts"].items()])],
        "paths":["protocols/new-libraries.json","runs/12-new-libraries/corpus/manifest.json",
                 "runs/12-new-libraries/corpus/<проект>/z3-audit.jsonl","runs/12-new-libraries/replay/metrics.json",
                 "runs/12-new-libraries/replay/VERIFY.json","runs/12-new-libraries/replay/worker-{0..5}.jsonl"],
        "recommendation":"Оценивать смену конфигураций по целым независимым проектам с учётом цены прогноза и пересоздания состояния.",
        "next":"Выделить трудные программы до замеров и проверить, концентрируется ли переносимый выигрыш около рабочего предела."
    })
    build()

def main():
    import bridge
    bridge_result=None
    if bridge.ORIGINAL.exists() and not SCOPE.exists():
        try:
            bridge_result=bridge.main()
        except Exception:
            import traceback
            BASE.mkdir(exist_ok=True)
            with (BASE/"array-bridge-failure.log").open("w") as out:
                traceback.print_exc(file=out)
    scope=freeze(bridge_result)
    base=BASE/"corpus"
    manifest=base/"manifest.json"
    if not manifest.exists():
        manifest=capture(base,scope,SCOPE)
    extra={}
    if scope["array_bridge"]:
        adapted=BASE/"portable-corpus"
        portable=adapted/"manifest.json"
        if not portable.exists():
            portable=bridge.independent_single(manifest,adapted,scope["array_bridge"])
        manifest=portable
        assert load(manifest)["group_count"]>=scope["minimum_admitted_total"]
        extra={arm:["--arrays-exp"] for arm in scope["configs"]}
    d=BASE/"replay"
    if not (d/"VERIFY.json").exists():
        launch(d,manifest,scope["configs"],REPEATS,322137,3600,fallback_by_arm=scope["fallback_by_arm"],
               extra_flags_by_arm=extra)
    analyse(d,manifest,scope)
if __name__=="__main__":
    main()
