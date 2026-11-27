"""Compare observed time and completion trade-offs without a cross-cap PAR2 penalty."""
from collections import defaultdict

import numpy as np

from library_caps import BASE as CAPS_BASE, CAPS
from portfolio import frac, group_draws
from replay import ROOT, digest, lines, load, now
from report import build, write_json
from stability_metrics import card_table, fmt_ci, replay_cube

BASE = CAPS_BASE / "replay"
OUTPUT = BASE / "budget-tradeoff.json"
SCENARIOS = [(f"C05@{t}", f"default@{t}") for t in CAPS]
SCENARIOS += [(f"C05@{low}", f"default@{high}") for low, high in zip(CAPS[:-1], CAPS[1:])]


def main():
    plan, groups, arms, scores, status, wall = replay_cube(BASE)
    source = load(ROOT/plan["manifest"])
    families = {r["group"]:r["family"] for r in source["sessions"]}
    draw = group_draws([families[g] for g in groups], np.random.default_rng(814221))
    by = defaultdict(list)
    for file in sorted(BASE.glob("worker-*.jsonl")):
        for item in lines(file):
            by[item["group"],item["arm"]].append(item["solver_cpu_s"])
    solved = (status == "solved").mean(axis=2)
    spent = wall.mean(axis=2)
    cpu = np.array([[np.mean(by[g,a]) for a in arms] for g in groups])
    select = list(dict.fromkeys(a for pair in SCENARIOS for a in pair))
    each = {}
    for k in select:
        i = arms.index(k)
        mean = float(spent[:,i].mean())
        done = float(solved[:,i].mean())
        each[k]={"wall_s_per_attempt":mean,"cpu_s_per_attempt":float(cpu[:,i].mean()),
                 "complete_fraction":done,"completions":int((status[:,i]=="solved").sum()),
                 "actual_wall_s_per_completion":mean/done}
    result = []
    for mode,control in SCENARIOS:
        a,b = map(arms.index,(mode,control))
        take = solved[:,a]-solved[:,b]
        ci = np.percentile((draw@take)/draw.sum(axis=1),[2.5,97.5]).tolist()
        n = status.shape[0]*status.shape[2]
        result.append({"candidate":mode,"control":control,"wall_ratio":frac(spent[:,a],spent[:,b],draw),
                       "cpu_ratio":frac(cpu[:,a],cpu[:,b],draw),
                       "completion_difference": [float(take.mean()),ci],
                       "completion_count_difference":each[mode]["completions"]-each[control]["completions"],
                       "attempts":n})
    check = BASE/"VERIFY.json"
    record={"at":now(),"programs":len(groups),"projects":len(set(families.values())),"repeats":scores.shape[2],
            "source_verification_sha256":digest(check),"per_policy":each,"paired_comparisons":result}
    write_json(OUTPUT,record)
    q=result[-1]
    delta=q["completion_difference"][0]
    summary=f"C05 при 1,5 с потребовал {fmt_ci(q['wall_ratio'])} фактического wall к default при 3 с и дал {100*delta:+.1f} п.п. полных сеансов."
    percent=lambda v: fmt_ci([100*v[0], [100*x for x in v[1]]])
    write_json(ROOT/"evidence/23b-budget-tradeoff.json",{
        "title":"23б · Цена дополнительного времени и доля завершения",
        "done":"Пересчитан фактический ресурс и доля завершения при одинаковом и удвоенном лимите.",
        "summary":summary,
        "paragraphs":[
            f"Пересчёт 76 ранее измеренных полных библиотечных потоков из {record['projects']} проектов, {record['repeats']} повторов. На режим {q['attempts']} фактических запусков. Интервалы пересэмплируют проекты.",
            "Фактический wall включает время израсходованных попыток, в том числе ошибочных и прерванных. Число завершений учитывает только полные корректные сеансы. Таблица сравнения разных лимитов не использует меняющийся штраф PAR2.",
            "Время на завершение равно всему наблюдавшемуся wall, делённому на число корректных завершений. Это оценка для потока той же смеси запросов и фиксированной очереди."
        ],
        "tables":[
            card_table(f"Выбор лимита · по {q['attempts']} попыток на каждую политику",
                       ["Режим","Контроль","Фактический wall / контроль","CPU / контроль","Разность завершения, п.п. [95% ДИ]","Разность завершений"],
                       [[r["candidate"],r["control"],fmt_ci(r["wall_ratio"]),fmt_ci(r["cpu_ratio"]),
                         percent(r["completion_difference"]),r["completion_count_difference"]] for r in result]),
            card_table("Наблюдаемый расход и число завершений",
                       ["Режим","Wall, с/попытку","CPU, с/попытку","Завершено","Wall, с/завершение"],
                       [[a,f'{r["wall_s_per_attempt"]:.3f}',f'{r["cpu_s_per_attempt"]:.3f}',r["completions"],
                         f'{r["actual_wall_s_per_completion"]:.3f}'] for a,r in each.items()]),
        ],
        "paths":[str(OUTPUT.relative_to(ROOT)),str(check.relative_to(ROOT)),"runs/22-library-caps/replay/worker-{0..5}.jsonl"],
        "recommendation":"Выбирать предел по допустимой доле незавершённых методов и фактическому расходу рабочего времени.",
        "next":"Измерить сквозную долю завершения при продуктовых лимитах и на той же нагрузке клиента.",
    })
    build(load(ROOT/"current.json")["current"])


if __name__=="__main__":
    main()
