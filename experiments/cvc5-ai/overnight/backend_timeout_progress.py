"""Recount recorded answer prefixes for every backend-cap timeout."""
from collections import Counter
import gzip
import json

import numpy as np

from backend_cap_probe import BASE, CAPS, RUNS, SCOPE
from replay import ROOT, digest, ev, lines, load
from report import build, write_json
from stability_metrics import card_table


def main():
    q=load(SCOPE)
    certificate=load(BASE/"VERIFY.json")
    rates={(c,a):[] for c in CAPS for a in RUNS}
    rows=[r for f in BASE.glob("worker-*.jsonl") for r in lines(f)]
    assert len(rows)==certificate["rows"]
    for r in rows:
        path=BASE/"raw"/f"{r['i']:02d}-{r['r']}-{r['pos']}.json.gz"
        assert digest(path)==r["output_sha256"]
        with gzip.open(path,"rt") as stream:
            raw=json.load(stream)
        expected=q["inputs"][r["i"]]["oracle"]
        parsed=ev.ANSWERS.findall(raw["stdout"])
        assert parsed==raw["answers"]
        if r["status"]=="timeout":
            assert parsed==expected[:len(parsed)]
            rates[r["cap"],r["arm"]].append(len(parsed)/len(expected))
    buckets=[]
    for (cap,arm),fracs in rates.items():
        classified=Counter("all" if x==1 else "late" if x>=.75 else "middle" if x>=.25 else "early" for x in fracs)
        assert len(fracs)==certificate["caps"][str(cap)]["statuses"][arm].get("timeout",0)
        buckets.append({"cap":cap,"arm":arm,"n":len(fracs),"states":classified,
                        "median_valid_prefix":float(np.median(fracs)) if fracs else None})
    path=BASE/"timeout-progress.json"
    write_json(path,{"programs":certificate["programs"],"projects":certificate["projects"],
                     "repeats":certificate["repeats"],"certificate_sha256":digest(BASE/"VERIFY.json"),"records":buckets})
    total=sum(x["n"] for x in buckets)
    full=sum(x["states"]["all"] for x in buckets)
    card={"title":"35б · Прогресс полного потока при таймауте",
          "summary":f"В {full} из {total} таймаутов при двух пределах успели появиться все ожидаемые ответы проверок.",
          "done":"Пересчитан фактический прогресс таймаутов обоих CLI по сырым потокам.",
          "paragraphs":[
              f"Дополнительный расчёт тех же {certificate['programs']} методов и {certificate['repeats']} повторов. При каждом таймауте stdout разобран заново и сверен с исходным оракулом по префиксу.",
              "Доля показывает, сколько проверок уже ответило к отсечению. Неполный префикс может включать время предыдущих запросов моделей и ядер. Этот расчёт не отделяет печать от поиска и не заменяет сквозной PAR2."
          ],
          "tables":[card_table(f"Отмечено {total} таймаутов в исходных сопоставленных потоках",
              ["Лимит","CLI","Таймауты","До 25%","25–74%","75–99%","Все ответы","Медиана доли"],
              [[x["cap"],x["arm"],x["n"],*[x["states"][k] for k in ("early","middle","late","all")],
                f'{x["median_valid_prefix"]:.3f}' if x["median_valid_prefix"] is not None else "—"] for x in buckets])],
          "paths":[str(path.relative_to(ROOT)),"runs/35-backend-caps/VERIFY.json","runs/35-backend-caps/raw/"],
          "recommendation":"Считать частичные ответы отдельным показателем, сохраняя полный поток единицей сравнения.",
          "next":"На живом клиенте проверить наблюдаемую фазу остановки сквозным профилем."}
    write_json(ROOT/"evidence/35b-backend-timeout-progress.json",card)
    build()


if __name__=="__main__":
    main()
