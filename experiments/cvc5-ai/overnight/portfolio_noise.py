"""Post-lock menu-size and repeat-count analysis of measured selection optimism."""
import numpy as np

from portfolio import BASE, MENU, frac, group_draws
from replay import ROOT, load
from report import build, write_json
from stability_metrics import card_table, fmt_ci, pct, replay_cube

def analytic(path,manifest):
    plan,groups,arms,score,status,wall=replay_cube(path)
    menu=load(MENU)
    ranking=sorted(menu["validation_par2"],key=lambda a:(menu["validation_par2"][a],a))
    glob=menu["global_arm"]
    configurations=list(dict.fromkeys([glob,"default",*ranking]))
    n,_,r=score.shape
    families={a["group"]:a["family"] for a in load(manifest)["sessions"]}
    rng=np.random.default_rng(471003)
    matrix=group_draws([families[g] for g in groups],rng)
    P=1200
    done=[]
    for K in (2,4,len(configurations)):
        subset=[arms.index(a) for a in configurations[:K]]
        data=score[:,subset,:]
        for repeats in (1,2,3,6):
            permutations=np.stack([rng.permutation(r) for _ in range(P)])
            a=data[:,:,permutations[:,:repeats]].mean(axis=3)
            b=data[:,:,permutations[:,repeats:2*repeats]].mean(axis=3)
            I,J=np.ogrid[:n,:P]
            pa=np.argmin(a,axis=1)
            pb=np.argmin(b,axis=1)
            optimistic=((a[I,pa,J]+b[I,pb,J])/2).mean(axis=1)
            corrected=((a[I,pb,J]+b[I,pa,J])/2).mean(axis=1)
            ref=((a[:,0,:]+b[:,0,:])/2).mean(axis=1)
            assert np.all(optimistic<=corrected+1e-10)
            done.append({"configs":K,"repeats_each_half":repeats,
                         "same_to_global":frac(optimistic,ref,matrix),
                         "cross_to_global":frac(corrected,ref,matrix),
                         "mean_optimism_ms":float((corrected-optimistic).mean()*1000),
                         "selection_agreement":float(np.mean(pa==pb))})
    return {"programs":n,"families":len(set(families.values())),"measured_repeats":r,
            "sampled_repeat_splits":P,"order":configurations,"contrasts":done}

def main():
    data={phase:analytic(BASE/f"{phase}-replay",BASE/f"{phase}-corpus/manifest.json") for phase in ("dev","test")}
    write_json(BASE/"repeat-menu-diagnostics.json",data)
    target=data["test"]
    last=target["contrasts"][-1]
    summary=(f"На 37 уже раскрытых программах завышение выигрыша для полного меню "
             f"составило {last['mean_optimism_ms']:.1f} мс даже при шести повторах для выбора.")
    write_json(ROOT/"evidence/03z-portfolio-noise.json",{
        "title":"03б · Оптимизм выбора при разных размерах меню",
        "done":"На записанных матрицах пересчитаны влияние числа опций и повторов и устойчивость оракула.",
        "summary":summary,
        "paragraphs":[
            "Это дополнительный анализ уже раскрытых матриц портфеля. В порядке, зафиксированном по прежней валидации, сравнивались меню из 2, 4 и всех 8 конфигураций. Состав основного теста и модели остался зафиксирован.",
            f"Для каждой строки прогноз наименьшего PAR2 выбирается отдельно в двух непересекающихся половинах. Всего {target['sampled_repeat_splits']} случайных разбиений записанных {target['measured_repeats']} повторов. Повторы усредняются по программе, 95% ДИ пересэмплирует целое семейство. Разбиения описывают изменчивость статистики на сохранённых опытах.",
            "Разница между оценкой на повторах выбора и на отдельной половине показывает, насколько видимое улучшение может скрывать шум измерения. При более широком меню выбирается больше экстремальных измерений."
        ],
        "tables":[card_table(f"Постфактум · {v['programs']} программ, {v['families']} семейств, {v['measured_repeats']} измеренных повторов",
                  ["Меню","Повторов в каждой половине","Оракул на данных выбора / глобальный",
                   "Оракул на другой половине / глобальный [95% ДИ]","Завышение выигрыша, мс","Совпадение выбора"],
                  [[q["configs"],q["repeats_each_half"],f'{q["same_to_global"][0]:.3f}',fmt_ci(q["cross_to_global"]),
                    f'{q["mean_optimism_ms"]:.1f}',pct(q["selection_agreement"])] for q in v["contrasts"]])
                  for phase,v in data.items()],
        "paths":["runs/02-portfolio/repeat-menu-diagnostics.json","runs/02-portfolio/test-replay/VERIFY.json"],
        "recommendation":"При оценке портфеля держать повторы выбора и оценки раздельно и сохранять общий default для контроля шума.",
        "next":"Проверить правдоподобность эффекта на целых библиотечных сеансах и при изменении лимита."
    })
    build(current=load(ROOT/"current.json")["current"])
if __name__=="__main__":
    main()
