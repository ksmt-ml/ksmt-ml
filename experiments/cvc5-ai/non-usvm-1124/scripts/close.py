"""Record final collection bounds and hash reproducibility artifacts."""
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
import os
from pathlib import Path
import subprocess
import time

from common import ROOT, CUTOFF, MSK, now, read, sha, write
from report import render
from verify_artifacts import verify

DEADLINE=datetime(2026,11,24,11,13,40,tzinfo=MSK).timestamp()


def collection():
    first=None
    last=None
    results=Counter()
    for p in (ROOT/"raw").rglob("process.json"):
        row=read(p)
        if "start" not in row or "stop" not in row:
            continue
        first=min(first,row["start"]) if first else row["start"]
        last=max(last,row["stop"]) if last else row["stop"]
        results["subprocesses"]+=1
        results["timeout"]+=int(row["timeout"])
        results["errors"]+=int(row["error"] is not None)
    return first,last,dict(results)


def hashes():
    paths=set()
    for pattern in ("scripts/*.py","scripts/*.sh","protocols/*.md","protocols/*.json","protocols/*.sha256",
                    "protocols/sol/*.json","inputs/**/manifest.json","evidence/*lock.json","evidence/*test-release.json",
                    "evidence/*audit.json","evidence/*.txt","evidence/*.md","evidence/close.json","evidence/analysis.json",
                    "evidence/deps.sha256","evidence/*matched-train.json","evidence/sol-auth-mode.json","evidence/report-check.json",
                    "evidence/exact-bootstrap-source.json",
                    "raw/phases/*.start","raw/phases/*.stop","raw/*/search-summary.json","raw/*/test/score-map.json",
                    "raw/*/test/rows.json","raw/*/validation/rows.json","raw/*/summary.json","raw/*/rows.json",
                    "raw/*/s*/smac/native/**/runhistory.json","report.html",
                    "current.json","REPRODUCE.md","CHECKPOINT.md"):
        paths.update(ROOT.glob(pattern))
    data="".join(f"{sha(f)}  {f.relative_to(ROOT)}\n" for f in sorted(paths) if f.is_file())
    target=ROOT/"evidence/deliverables.sha256"
    temp=target.with_name(".deliverables.sha256.tmp")
    temp.write_text(data)
    os.replace(temp,target)
    return sum(1 for line in data.splitlines() if line)


def check_report():
    class Standalone(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags=Counter()
            self.bad=[]
            self.columns=None
            self.row=None

        def handle_starttag(self,tag,attrs):
            self.tags[tag]+=1
            a=dict(attrs)
            if tag in {"script","iframe","link","img"}:
                self.bad.append(tag)
            if tag=="a" and not a.get("href","").startswith("https://"):
                self.bad.append(a.get("href"))
            if tag=="table":
                self.columns=None
            if tag=="tr":
                self.row=0
            if tag in {"td","th"} and self.row is not None:
                self.row+=1

        def handle_endtag(self,tag):
            if tag=="tr" and self.row is not None:
                if self.columns is None:
                    self.columns=self.row
                elif self.row!=self.columns:
                    self.bad.append(f"table row {self.row}/{self.columns}")
                self.row=None

    parser=Standalone()
    path=ROOT/"report.html"
    parser.feed(path.read_text())
    parser.close()
    complete=not parser.bad and parser.tags["h1"]==1 and parser.tags["table"]>=10
    write(ROOT/"evidence/report-check.json",dict(at=now(),sha256=sha(path),complete=complete,
             tables=parser.tags["table"],source_links=parser.tags["a"],unexpected=parser.bad))
    assert complete,parser.bad


def finish():
    assert CUTOFF<=time.time()<DEADLINE
    intended=["opt-search","opt-validation","opt-test","hw-search","hw-validation","hw-test",
              "hw-wire-train","hw-wire-test","dafny-contracts-v2","why3-transfer-retry","public-hw","public-hw-cap45",
              "public-idl-final","floor-replication","horizon-transfer","live-replay-test",
              "karatsuba-oracle","hw-wire-fixed","why3-live","public-idl-cap30","public-hw-oracle110","arbiter-oracle"]
    completed=[name for name in intended if (ROOT/"evidence"/f"{name}.complete").is_file()]
    pending=[name for name in intended if name not in completed]
    clean=verify(deep=True)
    first,last,raw=collection()
    size=int(subprocess.check_output(["du","-sx","-B1",ROOT]).split()[0])
    write(ROOT/"evidence/close.json",dict(at=now(),window_start="2026-11-24T07:13:40+03:00",
        host_first_verified="2026-11-24T07:14:24+03:00",
        collection_cutoff="2026-11-24T11:08:00+03:00",
        last_collection=last,first_instrumented=first,deadline="2026-11-24T11:13:40+03:00",
        raw_counts=raw,workspace_bytes=size,integrity=clean,completed=completed,pending=pending))
    write(ROOT/"current.json",dict(current="Сбор завершён. Итоги и воспроизведение в report.html",
        done=completed,next=pending,updated=now()))
    with (ROOT/"CHECKPOINT.md").open("a") as f:
        f.write(f"\n## Закрытие {now()}\n")
        f.write(f"Последний инструментированный сеанс завершён {last}. Окно сбора закрыто в 11:08 МСК.\n")
        f.write(f"Завершённые фазы {', '.join(completed)}.\n")
        f.write(f"Фазы вне полного результата {', '.join(pending) if pending else 'отсутствуют'}.\n")
        f.write(f"Глубокая проверка хешей и времени: {'прошла' if clean else 'см. ошибки в evidence/artifact-audit.json'}.\n")
    render()
    check_report()
    count=hashes()
    print(now(),last,size,count,clean,flush=True)


if __name__=="__main__":
    finish()
