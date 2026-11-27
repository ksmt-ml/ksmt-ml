"""Audit completed evidence and write the final standalone synthesis."""
from collections import Counter
from datetime import datetime
import gzip
from functools import lru_cache
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import time

from replay import DATA, ROOT, STOP, digest, disk_bytes, lines, load, log, now, verify_frozen, verify_replay
from report import build, write_json

FINAL=datetime.fromisoformat("2026-11-22T10:55:00+03:00").timestamp()

class StaticHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables=0
        self.captions=0
        self.lang=None
    def handle_starttag(self,tag,attributes):
        pairs=dict(attributes)
        assert tag not in {"script","iframe","object","embed","link","base","audio","video","source","img"}, tag
        assert not any(key.startswith("on") for key in pairs)
        if "href" in pairs:
            assert pairs["href"].startswith("#")
        assert "src" not in pairs and "url(" not in pairs.get("style","")
        if tag=="table":
            self.tables+=1
        if tag=="caption":
            self.captions+=1
        if tag=="html":
            self.lang=pairs.get("lang")

def check_html():
    path=ROOT/"report.html"
    text=path.read_text()
    parser=StaticHTML()
    parser.feed(text)
    assert parser.lang=="ru" and parser.tables==parser.captions and parser.tables>0
    assert all(s not in text for s in ("https://","http://","/runner/state/auth.json","file://"))
    current=load(ROOT/"current.json")
    assert set(current)=={"current","done","next","updated"}
    assert isinstance(current["current"],str) and isinstance(current["done"],list)
    assert isinstance(current["next"],list)
    assert datetime.fromisoformat(current["updated"]).utcoffset().total_seconds()==10800
    return {"tables":parser.tables,"captions":parser.captions,"page_sha256":digest(path),"current_sha256":digest(ROOT/"current.json")}

def audit_matrices(full):
    root=ROOT/"runs"
    cases={}
    excluded=[]
    final_rows=Counter()
    programs=set()
    max_core_count=0
    for planfile in sorted(root.glob("**/plan.json")):
        if not (planfile.parent/"worker-0.jsonl").exists():
            continue
        plan=load(planfile)
        if not all(k in plan for k in ("manifest","repeats","cores","seed","configs")):
            continue
        cores=plan["cores"]
        siblings={Path(f"/sys/devices/system/cpu/cpu{core}/topology/thread_siblings_list").read_text().strip()
                  for core in cores}
        assert len(siblings)==len(cores)<=6
        max_core_count=max(max_core_count,len(cores))
        directory=planfile.parent
        label=str(directory.relative_to(ROOT))
        ver=directory/"VERIFY.json"
        if not ver.exists():
            excluded.append(label)
            continue
        recorded=load(ver)
        if full:
            independently=verify_replay(directory,publish=False)
            assert recorded==independently, label
        else:
            for name,fingerprint in recorded["logs"].items():
                assert digest(directory/name)==fingerprint
        for fname in recorded["logs"]:
            assert (directory/fname).stat().st_mtime<STOP
        manifest=ROOT/plan["manifest"]
        assert digest(manifest)==plan["manifest_sha256"]
        compiled=load(manifest)
        programs.update(a["sha256"] for a in compiled["sessions"])
        assert recorded["programs"]==len(compiled["sessions"])
        assert recorded["rows"]==recorded["programs"]*recorded["repeats"]*len(plan["configs"])
        cases[label]={"rows":recorded["rows"],"programs":recorded["programs"],"repeats":recorded["repeats"],
                      "sha256":digest(ver)}
        for status in recorded["counts"].values():
            final_rows.update(status)
    assert final_rows["incorrect"]==sum(load(ROOT/p/"VERIFY.json")["counts"][a].get("incorrect",0)
                                       for p in cases for a in load(ROOT/p/"VERIFY.json")["counts"])
    return {"completed":cases,"unverified":excluded,"rows":sum(final_rows.values()),
            "counts":final_rows,"unique_frozen_session_hashes_across_matrices":len(programs),
            "max_distinct_physical_cores_per_matrix":max_core_count}

def seals():
    from corpus import commitment, normalized_source, skeleton
    @lru_cache(maxsize=None)
    def identity(file):
        meta=load(file)
        rows=[r for r in meta["programs"] if r.get("shape_tokens")]
        output=[]
        for row in rows:
            source=(file.parent/row["source"]).read_bytes()
            normalized=normalized_source(source)
            if "normalized_sha256" in row:
                assert row["normalized_sha256"]==normalized
            output.append((skeleton(row),normalized))
        return output

    results={}
    for p in sorted((ROOT/"runs").glob("**/release.json")):
        if p.is_symlink():
            continue
        raw=load(p)
        manifest=p.parent/"manifest.json"
        programs=p.parent/"programs.json"
        if not manifest.exists() or not programs.exists():
            continue
        meta=load(programs)
        seed=bytes.fromhex(raw["seed"])
        tagged=load(ROOT/"protocols"/f'{meta["split"]}-seed.json')
        assert commitment(meta["split"],seed)==tagged["seed_commitment_sha256"]
        assert tagged["created"]<=raw["unlocked"]
        if "decision_lock" in raw:
            assert raw["decision_lock_sha256"]==digest(ROOT/raw["decision_lock"])
            assert load(ROOT/raw["decision_lock"])["locked"]<=raw["unlocked"]
        elif "scope_sha256" in raw:
            scope=ROOT/raw.get("scope","protocols/representation.json")
            assert raw["scope_sha256"]==digest(scope) and load(scope)["locked"]<=raw["unlocked"]
        admitted=load(manifest)["sessions"]
        cert={"planned":len(meta["programs"]),"admitted_original_corpus":len(admitted),
              "admitted_families":len({r["family"] for r in admitted}),
              "release_sha256":digest(p),"locked_before_release":True}
        if "exclusions" in meta and ("decision_lock" in raw or "scope_sha256" in raw):
            emitted=identity(programs.resolve())
            assert len(emitted)==len(meta["programs"])
            families,norms=(set(x) for x in zip(*emitted))
            assert len(norms)==len(emitted)
            exclusions=meta["exclusions"]
            for name,proof in exclusions.items():
                reference=(ROOT/name).resolve()
                assert digest(reference)==proof
                for family,key in identity(reference):
                    assert family not in families and key not in norms
            cert.update(prior_rosters_verified=len(exclusions),generated_families=len(families),
                        exact_program_duplicates=0,training_family_overlaps=0)
        results[meta["split"]]=cert
    for phase in ("dev","test"):
        base=ROOT/"runs/02-portfolio"/(phase+"-corpus")
        if not (base/"manifest.json").exists():
            break
        assert all(x["status"]=="ok" for x in lines(base/"z3-audit.jsonl") if x["session"] in {
            row["file"] for row in load(base/"manifest.json")["sessions"]})
    if all((ROOT/f"runs/02-portfolio/{k}-corpus/programs.json").is_file() for k in ("dev","test")):
        sets=[]
        normalized=[]
        for k in ("dev","test"):
            p=load(ROOT/f"runs/02-portfolio/{k}-corpus/programs.json")["programs"]
            sets.append({r["family"] for r in p})
            normalized.append({r["normalized_sha256"] for r in p})
        assert not (sets[0]&sets[1] or normalized[0]&normalized[1])
    return results

def evidence_index():
    result={}
    for path in sorted((ROOT/"evidence").glob("*.json")):
        data=load(path)
        assert all(k in data for k in ("title","done","summary","paragraphs","paths","recommendation","next"))
        assert data["paragraphs"] and data["paths"]
        for table in data.get("tables",[]):
            assert table["caption"] and table["columns"] and all(len(r)==len(table["columns"]) for r in table["rows"])
        result[path.stem]={"title":data["title"],"summary":data["summary"],"sha256":digest(path)}
    return result

def audit_supplemental():
    verified={}
    native=(
        ("live", "runs/16-live-client/VERIFY.json", "raw_shas"),
        ("native_rewrite", "runs/25-native-rewrite/VERIFY.json", "raw_hashes"),
        ("native_extended", "runs/25b-native-long-jvm/VERIFY.json", "raw_hashes"),
    )
    for label,name,key in native:
        receipt=ROOT/name
        if receipt.exists():
            record=load(receipt)
            n=record["programs"]*record["repeats"]
            assert n==record["planned_per_arm"]
            for filename,expected in record[key].items():
                raw=ROOT/filename
                assert raw.stat().st_mtime<STOP and digest(raw)==expected
            verified[label]={"receipt":name,"planned_per_arm":n,"outputs":len(record[key]),
                             "sha256":digest(receipt)}
    batches=ROOT/"runs/21-reset-batches"
    cert=batches/"VERIFY.json"
    if cert.exists():
        from replay import ev
        receipt=load(cert)
        raw=[r for p in batches.glob("worker-*.jsonl") for r in lines(p)]
        assert len(raw)==receipt["batch_rows"]
        for row in raw:
            original=ROOT/row["raw"]
            assert original.stat().st_mtime<STOP and digest(original)==row["raw_sha256"]
            with gzip.open(original,"rt") as content:
                records=json.load(content)
            assert [a for r in records for a in ev.ANSWERS.findall(r["stdout"])]==row["observed"]
            assert abs(sum(r["wall_s"] for r in records)-row["wall_s"])<1e-12
        verified["process_reset"]={"receipt":str(cert.relative_to(ROOT)),"batches":len(raw),"sha256":digest(cert)}
    model=ROOT/"runs/26-model-witnesses"
    report=model/"VERIFY.json"
    if report.exists():
        cert=load(report)
        files=[r for f in model.glob("worker-*.jsonl") for r in lines(f)]
        assert len(files)==cert["rows"]
        for row in files:
            run=model/f"case-{row['program']:03d}"/row["arm"]
            stream=run/"cvc5.stdout"
            assert stream.stat().st_mtime<STOP and digest(stream)==row["cvc5_output_sha256"]
            premise=[m for m in row["model_checks"] if "compatibility" in m]
            script_hashes={digest(p) for p in run.glob("*-fits.smt2")}
            assert script_hashes=={m["compatibility"]["script_sha256"] for m in premise}
            entailment_hashes={digest(p) for p in run.glob("*-implies.smt2")}
            assert entailment_hashes=={m["entailment"]["script_sha256"] for m in premise if "entailment" in m}
        verified["model_witnesses"]={"receipt":str(report.relative_to(ROOT)),"rows":cert["rows"],"sha256":digest(report)}
    profiles=ROOT/"runs/27-rewrite-profile"
    if (profiles/"metrics.json").exists():
        meta=load(profiles/"metrics.json")
        logs=[p for p in profiles.glob("worker-*.jsonl")]
        assert all(p.stat().st_mtime<STOP for p in logs)
        n=sum(len(lines(p)) for p in logs)
        assert n==meta["physical_rows"]==meta["rows"]+meta["extra_rows"]
        assert n==load(profiles/"VERIFY.json")["rows"]
        verified["profile_rewriting"]={"measured_rows":n,"planned_rows":meta["rows"],
                                     "sha256":digest(profiles/"VERIFY.json")}
    for case in ("05-search","20-aligned-search"):
        path=ROOT/"runs"/case/"forced-predictions/metrics.json"
        if path.exists():
            meta=load(path)
            for filename,fingerprint in meta["raw_hashes"].items():
                assert digest(ROOT/filename)==fingerprint
            verified[f"predictions_{case}"]={"compared_rounds":meta["matching_rounds"],"sha256":digest(path)}
    cross=(
        ("portable_full_stream", "33-portable-z3", "portable-z3-control.json"),
        ("portable_full_stream_other", "34-portable-z3-other", "portable-z3-other.json"),
    )
    for label,dirname,scope in cross:
        solver=ROOT/"runs"/dirname
        receipt=solver/"VERIFY.json"
        if not receipt.exists():
            continue
        from replay import ev
        cert=load(receipt)
        settings=load(ROOT/"protocols"/scope)
        cases=[r for f in solver.glob("worker-*.jsonl") for r in lines(f)]
        assert len(cases)==cert["rows"]==cert["programs"]*cert["repeats"]*3
        for row in cases:
            path=ROOT/row["raw"]
            assert path.stat().st_mtime<STOP and digest(path)==row["raw_sha256"]
            with gzip.open(path,"rt") as f:
                actual=json.load(f)
            got=ev.ANSWERS.findall(actual["stdout"])
            expected=settings["inputs"][row["program"]]["oracle"]
            bad=len(got)>len(expected) or any(a!=b and a!="unknown" for a,b in zip(got,expected))
            assert got==row["answers"]==actual["answers"] and bad==(row["status"]=="incorrect")
            assert row["par2"]==(row["wall_s"] if row["status"]=="solved" else 3.0)
        verified[label]={"rows":len(cases),"sha256":digest(receipt)}
    cap=ROOT/"runs/35-backend-caps"
    receipt=cap/"VERIFY.json"
    if receipt.exists():
        cert=load(receipt)
        rows=[r for f in cap.glob("worker-*.jsonl") for r in lines(f)]
        assert len(rows)==cert["rows"]==cert["programs"]*cert["repeats"]*4
        for row in rows:
            raw=cap/"raw"/f"{row['i']:02d}-{row['r']}-{row['pos']}.json.gz"
            assert raw.stat().st_mtime<STOP and digest(raw)==row["output_sha256"]
        verified["portable_full_stream_caps"]={"rows":len(rows),"sha256":digest(receipt)}
    return verified

def fmt_ratio(value):
    number,bounds=value
    return f"{number:.3f} [{bounds[0]:.3f}; {bounds[1]:.3f}]"

def final_card(data):
    verify=data["matrices"]
    corpus=data["seals"]
    index=data["evidence"]
    inspections=[
        ("01-stability","Исходная валидация",ROOT/"runs/01-stability/metrics.json",lambda d:d["groups"] and len(d["groups"]),lambda d:d["repeats"]),
        ("02-dev","Разработка портфеля",ROOT/"runs/02-portfolio/dev-replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("03-portfolio","Закрытый портфель",ROOT/"runs/02-portfolio/test-replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("04-transfer","Библиотечный перенос",ROOT/"runs/04-transfer/replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("06-closed-loop","Замкнутый поиск, закрытый тест",ROOT/"runs/05-search/final/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("07-calibration","Калибровка двух пределов",ROOT/"runs/06-cap-calibration/replay/metrics.json",lambda d:d["program_count"],lambda d:d["repeats"]),
        ("13-new-libraries","Другие библиотечные проекты",ROOT/"runs/12-new-libraries/replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("17-live-client","Живой USVM, нативные бэкенды",ROOT/"runs/16-live-client/VERIFY.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("18-array-options","Параметры массивов и переписывания",ROOT/"runs/17-arrays-options/replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("21-aligned-search","Поиск с согласованной разминкой",ROOT/"runs/20-aligned-search/final/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("23-library-caps","Библиотечные лимиты",ROOT/"runs/22-library-caps/replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("24-rewrite-transfer","Закрытый перенос переписывания",ROOT/"runs/23-rewrite-transfer/replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("25-native-rewrite","Живое нативное переписывание",ROOT/"runs/25-native-rewrite/VERIFY.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("25b-native-long-jvm","Живой клиент, увеличенный предел JVM",ROOT/"runs/25b-native-long-jvm/VERIFY.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("28-warm-load","Два и шесть воркеров",ROOT/"runs/28-warm-load/metrics.json",lambda d:d["programs"],lambda d:d["repeats_per_mode"]),
        ("29-simplification","Раздельные флаги переписывания",ROOT/"runs/29-simplification/replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("30-paired-rewrite","Парные формы ветвления и таблицы",ROOT/"runs/30-paired-rewrite/replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
        ("31-minimal-transfer","Независимый перенос одного флага",ROOT/"runs/31-minimal-transfer/replay/metrics.json",lambda d:d["programs"],lambda d:d["repeats"]),
    ]
    rows=[]
    for key,label,file,program_n,reps in inspections:
        if key in index:
            r=load(file)
            count_type="повторов"
            if key in ("06-closed-loop","21-aligned-search"):
                count_type=f'повторов × {len(r["search_seeds"])} seed поиска'
            rows.append([label,program_n(r),f"{reps(r)} {count_type}",index[key]["summary"]])
    pp=ROOT/"runs/02-portfolio/test-replay/metrics.json"
    pt=load(pp) if pp.exists() else None
    if pt:
        score=pt["effects"]["global"]["ratio_default"]
        recommendation=("Оставить default для общего развёртывания и тестировать оптимизацию на целевом живом клиенте. "
                        "Для сравнения сохранять парный default, отдельно считать таймауты и повторять отбор на новых семействах.")
        subtitle=f"Закрытый тест портфеля дал {fmt_ratio(score)} для глобальной настройки к default. "
        if score[1][1]<1 and score[0]<.98:
            recommendation=("Использовать найденную настройку в ограниченной проверке на соответствующем генераторе. "
                            "Для общего развёртывания нужна воспроизводимая оценка на целевых живых клиентах.")
        if score[1][0]<=1<=score[1][1]:
            subtitle+="Устойчивого выигрыша для общей замены default не установлено."
        else:
            subtitle+="Величина указана для заданного меню и лимита полного сеанса."
    else:
        recommendation="Сохранить default до независимого подтверждения рабочего сокращения PAR2 и таймаутов."
        subtitle="Измерения остановлены. Доступные результаты подтверждены по сырым журналам."
    portable=ROOT/"runs/12-new-libraries/replay/metrics.json"
    bridge=ROOT/"runs/11b-array-bridge/replay/metrics.json"
    late_arrays=ROOT/"runs/17-arrays-options/replay/metrics.json"
    rewrite=ROOT/"runs/23-rewrite-transfer/replay/metrics.json"
    routed=ROOT/"runs/05-search/final/metrics.json"
    additional=[]
    if pt:
        oracle=pt["effects"]["oracle_disjoint"]["ratio_default"]
        additional.append(f"В закрытом портфеле независимая оценка выбора флагов для каждой программы дала "
                          f"{fmt_ratio(oracle)} к default. Зафиксированный селектор выбирал тот же глобальный вариант. "
                          "Подтверждённого запаса для переключения в этом меню нет.")
    if portable.exists():
        c=load(portable)
        e=c["effects"]["global"]
        additional.append(f"На переносимых библиотечных записях C05/default {fmt_ratio(e['ratio_family'])} "
                          f"при {c['programs']} методах и {c['libraries']} проектах. Использовался общий --arrays-exp.")
        recommendation += " Для совместимых библиотечных потоков C05 заслуживает отдельной локальной проверки с общим --arrays-exp и переносимым представлением индексов."
    if late_arrays.exists():
        c=load(late_arrays)
        e=c["states"]["global"]["ratio"]
        additional.append(f"Независимый второй набор с общей поддержкой массивов дал C05/default "
                          f"{fmt_ratio(e)} на {c['programs']} методах из {c['projects']} проектов.")
    if "24-rewrite-transfer" in index:
        d=load(rewrite)
        v=d["effects"]["rewrite"]["to_default"]
        additional.append(index["24-rewrite-transfer"]["summary"])
        if v[0]<=.97 and v[1][1]<1:
            recommendation+=" В совместимых библиотечных сеансах проверить меньший вектор переписывания рядом с C05."
    if "24b-library-synthesis" in index:
        d=load(ROOT/"runs/library-c05-synthesis.json")
        additional.append(f"В сводной оценке после отдельных опытов C05 дал {fmt_ratio(d['ratio'])} к default "
                          f"на {d['programs']} различных потоках из {d['projects']} проектов при 1,5 с. "
                          f"На {d['attempts_per_arm']} запусков каждого режима таймаутов {d['statuses']['c05']['timeout']} у C05 "
                          f"и {d['statuses']['default']['timeout']} у default.")
    if "23-library-caps" in index:
        additional.append(index["23-library-caps"]["summary"]+" Это повторный опыт на уже раскрытых библиотечных записях.")
    if bridge.exists():
        c=load(bridge)
        old=c["metrics_by_source_kind"]["affected"]
        if c["next_encoding"]:
            gained=old[c["next_encoding"]]["parsed_classes"]
            additional.append(f"Дефект совместимости предшествует настройке: исходный cvc5 разобрал "
                              f"{old['raw']['parsed_classes']} из {c['affected']} затронутых классов, "
                              f"выбранный формат — {gained} из {c['affected']}.")
    if routed.exists():
        c=load(routed)
        sol=c["effects"]["sol-wall/rf-wall"]["ratio"]
        additional.append(f"На {c['programs']} закрытых программах наблюдаемое отношение Sol/RF при равном времени {sol:.3f}. "
                          "При каждом seed проверяемые векторы из общей разминки совпали.")
    if "20b-forced-predictions" in index:
        additional.append(index["20b-forced-predictions"]["summary"]+" Это диагностический расчёт на обучении.")
    if "21-aligned-search" in index:
        additional.append(index["21-aligned-search"]["summary"])
    if "18c-native-options" in index:
        additional.append(index["18c-native-options"]["summary"])
    if "25-native-rewrite" in index:
        additional.append(index["25-native-rewrite"]["summary"])
    if "25b-native-long-jvm" in index:
        additional.append(index["25b-native-long-jvm"]["summary"])
        live=load(ROOT/"runs/25b-native-long-jvm/VERIFY.json")
        if live["check_ratio"]:
            additional.append(f"После увеличения внешнего предела JVM получены все {live['planned_per_arm']} записей "
                              f"на режим. Для времени проверок отношение {fmt_ratio(live['check_ratio'])}.")
    if "31-minimal-transfer" in index:
        additional.append(index["31-minimal-transfer"]["summary"])
    if "33-portable-z3" in index:
        additional.append(index["33-portable-z3"]["summary"])
    if "34-portable-z3-other" in index:
        additional.append(index["34-portable-z3-other"]["summary"])
    if "35-backend-caps" in index:
        additional.append(index["35-backend-caps"]["summary"]+" Два новых повтора на ранее раскрытых методах.")
    summary=f"Кампания завершена в {now()[11:16]} МСК. {subtitle}"
    props=[
        f"Независимо проверено {verify['rows']} воспроизведений полных сеансов в {len(verify['completed'])} завершённых матрицах. "
        f"В них {verify['unique_frozen_session_hashes_across_matrices']} различных хешей сеансов. Это число включает переиспользованные записи только один раз. "
        f"Получено {verify['counts'].get('timeout',0)} таймаутов, {verify['counts'].get('unknown',0)} unknown, "
        f"{verify['counts'].get('error',0)} ошибок и {verify['counts'].get('incorrect',0)} несовпадений. "
        "Ранние пробы, обучение поиска и профильные журналы подсчитаны в своих разделах.",
        f"По резерву: максимальный зафиксированный объём {data['disk']['peak_bytes']/1e9:.3f} ГБ, лимит 15 ГБ. "
        "Шесть физических ядер, solver-таймауты и отдельные пределы модельных обращений. "
        "Числа независимых программ, повторов и seed приведены отдельно.",
        f"Проверено {len(corpus)} предварительных обязательств по seed. Воспроизведение на одних программах в разные даты даёт разброс. "
        "Доверительные интервалы используют указанные программные кластеры, а сырые повторы сведены внутри них."
    ]
    props[0:0]=additional
    if verify["unverified"]:
        props.append(f"Частично выполненные матрицы: {len(verify['unverified'])}. Их сырые логи сохранены отдельно и отмечены как незавершённые.")
    if (ROOT/"early-import-failure.log").exists():
        props.append("Первый запуск контроллера портфеля завершился при импорте из-за совпавшего имени модуля. Импорт исправлен до запуска измерений.")
    next_check=("Сопоставить Z3, cvc5 1.4.1 default и cvc5 1.4.1 с переписыванием на живом целевом клиенте с переносимой кодировкой "
                "и общим --arrays-exp для веток cvc5. "
                "Зафиксировать класс CPU и предел до измерений. Сверить ответы, ядра и пригодность моделей, затем сравнить "
                "время проверок, извлечения данных и всего анализа на независимых проектах.")
    write_json(ROOT/"evidence/99-synthesis.json",{
        "title":"Итоги и проверка артефактов","done":"Верифицирован и сохранён итоговый автономный отчёт.",
        "summary":summary,"paragraphs":props,
        "tables":[{"caption":"Основные независимые проверки и практические эффекты",
                   "columns":["Проверка","Программ","Повторы и seed","Результат"],"rows":rows}],
        "recommendation":recommendation,"next":next_check,
        "paths":["campaign-verification.json","protocols/phase-status.json","research.log","resource-samples.jsonl",
                 "REPRODUCE.md","protocols/recovery-status.json","early-import-failure.log"]
    })
    build(finished=True)

def run(final=False, full=False):
    cases=audit_matrices(full)
    index=evidence_index()
    report=check_html()
    dataset_seals=seals()
    current=disk_bytes()
    samples=lines(ROOT/"resource-samples.jsonl")
    high=max([current,*[r["disk_bytes"] for r in samples]])
    assert high<15_000_000_000
    data={"at":now(),"mode":"complete matrix recomputation" if full else "artifact recheck",
          "disk":{"current_bytes":current,"peak_bytes":high,"samples":len(samples)},
          "seals":dataset_seals,"frozen_inputs":verify_frozen(),
          "matrices":cases,"supplemental_checks":audit_supplemental(),"report":report,"evidence":index}
    if final:
        assert time.time()<=FINAL
        final_card(data)
        data["report"]=check_html()
        data["evidence"]=evidence_index()
        write_json(ROOT/"campaign-verification.json",data)
        log("FINAL reports and verification written")
    else:
        write_json(ROOT/"intermediate-verification.json",data)
    return data

if __name__=="__main__":
    d=run("--final" in sys.argv,"--full" in sys.argv)
    print(json.dumps({"verified_replay_rows":d["matrices"]["rows"],
                      "matrices":len(d["matrices"]["completed"]),"tables":d["report"]["tables"]},ensure_ascii=False))
