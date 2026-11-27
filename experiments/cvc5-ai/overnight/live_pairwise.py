"""Direct paired contrast of the two live cvc5 modes in their shared completed cells."""
from collections import Counter
import numpy as np

from live_client import BASE, REPEATS, blocks
from portfolio import frac, group_draws
from replay import ROOT, lines, load
from report import build, write_json
from stability_metrics import card_table, fmt_ci

def main():
    plan=load(ROOT/"protocols/live-client.json")
    baseline={}
    rewrite={}
    project={}
    for lib,shard,rep,tags,order,cpu in blocks(plan):
        folder=BASE/lib["project"]/f"shard-{shard:02d}-repeat-{rep}"
        for mode,result in (("cvc5",baseline),("cvc5-table-ite",rewrite)):
            p=folder/mode/"results.jsonl"
            if p.exists():
                for row in lines(p):
                    if row["status"]=="ok":
                        result[row["tag"],rep]=row
                        project[row["tag"]]=lib["project"]
    grouped={}
    for k in baseline.keys()&rewrite.keys():
        tag,_=k
        a,b=baseline[k],rewrite[k]
        grouped.setdefault(tag,[]).append([a["wall_ms"],b["wall_ms"],a["stats"]["model_ms"],b["stats"]["model_ms"],
                                           b["stats"]["coverage_pct"]-a["stats"]["coverage_pct"]])
    names=sorted(grouped)
    metrics=np.array([np.mean(grouped[tag],axis=0) for tag in names])
    fam=[project[g] for g in names]
    draws=group_draws(fam,np.random.default_rng(23529))
    wall=frac(metrics[:,1],metrics[:,0],draws)
    model_total=(draws@metrics[:,3])/(draws@metrics[:,2])
    cover=(draws@metrics[:,4])/draws.sum(axis=1)
    rows=[]
    for n in sorted(set(fam)):
        sel=[i for i,tag in enumerate(names) if project[tag]==n]
        a=metrics[sel]
        rows.append([n,len(sel),sum(len(grouped[names[i]]) for i in sel),f"{a[:,1].mean()/a[:,0].mean():.3f}",
                     f"{a[:,2].mean():.2f}",f"{a[:,3].mean():.2f}",f'{a[:,4].mean():+.2f}'])
    result={"programs":len(names),"projects":len(set(fam)),"repeats":REPEATS,
            "matched_cells":sum(map(len,grouped.values())),"wall_ratio":wall,
            "model_ms_means":[float(metrics[:,i].mean()) for i in (2,3)],
            "model_ratio":[float(metrics[:,3].mean()/metrics[:,2].mean()),np.percentile(model_total,[2.5,97.5]).tolist()],
            "coverage_diff_pp":[float(metrics[:,4].mean()),np.percentile(cover,[2.5,97.5]).tolist()]}
    write_json(BASE/"pairwise-table-ite.json",result)
    t=f"В прямых парах живого JNI cvc5 замена таблиц на ite дала отношение wall {fmt_ci(wall)}."
    write_json(ROOT/"evidence/17z-live-paired.json",{
        "title":"17б · Прямой эффект преобразования таблиц в живом cvc5",
        "done":"Сверены пары двух режимов нативного cvc5 по одному методу и повтору.",
        "summary":t,
        "paragraphs":[
            f"Те же ранее записанные эксперименты. Нативная пара доступна для {result['programs']} методов в {result['projects']} проектах, {result['matched_cells']} совпадающих повторов. Запланированы {REPEATS} повтора на метод. Отсутствовавшие при таймаутах JVM клетки перечислены в главной таблице.",
            f"Длительность извлечения модели при усреднении по методу {result['model_ms_means'][0]:.2f} мс и {result['model_ms_means'][1]:.2f} мс. Их отношение {fmt_ci(result['model_ratio'])}. Оценки относятся к успешно зарегистрированным парам, с проектом как кластером. Полное улучшение wall включает развитие новых путей."
        ],
        "tables":[card_table("Прямое сравнение нативного cvc5",["JAR","Методов","Пар","Wall ite / исходный",
                      "Модель исходная, мс","Модель ite, мс","Δ покрытия, п.п."],rows)],
        "paths":["runs/16-live-client/pairwise-table-ite.json","runs/16-live-client/VERIFY.json","protocols/live-client.json"],
        "recommendation":"Смотреть одновременно на полное время, пропавшие из-за таймаута методы и покрытие, когда сравниваются клиентские преобразования.",
        "next":"Проверить перенос клиентского преобразования и совместимого вывода массивов на целевой версии и наборе методов."
    })
    build(current=load(ROOT/"current.json")["current"])
if __name__=="__main__":
    main()
