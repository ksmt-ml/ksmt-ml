"""Verify that report.html is self-contained and reconciles with the certified run."""
from html.parser import HTMLParser
from pathlib import Path

from common import ROOT, now, read, sha, write
from report import interval, stamp

def validate():
    raw = ROOT / "runs/primary"
    metrics = read(raw/"metrics.json")
    cert = read(raw/"VERIFY.json")
    assert cert["ok"] and cert["metrics_sha256"] == sha(raw/"metrics.json")
    doc = ROOT/"report.html"
    text = doc.read_text()
    refs = set()
    anchors = []
    class Audit(HTMLParser):
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            assert tag not in {"script","iframe","object","embed"}
            assert all(k not in a for k in ("src","srcset","poster","data-src"))
            if tag == "link":
                raise ValueError("External resource link")
            if "id" in a:
                assert a["id"] not in refs
                refs.add(a["id"])
            if tag == "a" and a.get("href","").startswith("#"):
                anchors.append(a["href"][1:])
    p=Audit()
    p.feed(text)
    p.close()
    assert set(anchors) <= refs
    assert len(refs) >= 8
    assert "@import" not in text and "url(" not in text
    for n in ("start","end"):
        assert stamp(metrics[n]) in text
    for name,q in metrics["comparisons"].items():
        assert interval(q["par"]["2"]) in text, name
    if (ROOT/"evidence/experiment-end-msk.txt").exists():
        assert stamp((ROOT/"evidence/experiment-end-msk.txt").read_text()) in text
    stock=ROOT/"runs/stock"
    if (stock/"VERIFY.json").exists():
        other=read(stock/"VERIFY.json")
        assert other["ok"] and other["metrics_sha256"]==sha(stock/"metrics.json")
        data=read(stock/"metrics.json")
        for mark in ("start","end"):
            assert stamp(data[mark]) in text
        for p in other["par2_with_step_failure"].values():
            assert interval(p) in text
    print("Verified HTML",len(text),"characters",sha(doc))
    write(ROOT/"evidence/report-verification.json",dict(ok=True,verified_at_msk=now(),
        sha256=sha(doc),inline_assets_only=True,verified_estimates_from=cert["metrics_sha256"]))

if __name__=="__main__":
    validate()
