"""Check the report works as one local HTML document and freeze its digest."""
from html.parser import HTMLParser
from pathlib import Path
from verify_live import ROOT, put, read, sha, stamp

class Report(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=set()
        self.links=[]
        self.seen=set()
        self.language=""
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        self.seen.add(tag)
        if tag=="html":
            self.language=a.get("lang")
        if "id" in a:
            assert a["id"] not in self.ids
            self.ids.add(a["id"])
        if tag=="a":
            self.links.append(a["href"])
        assert "src" not in a and tag not in {"link","iframe","object","embed"}, (tag,a)

if __name__=="__main__":
    p=ROOT/"report.html"
    parsed=Report()
    content=p.read_text()
    parsed.feed(content)
    assert parsed.language=="ru" and {"style","title","table","details"}<=parsed.seen
    missing=[]
    for href in parsed.links:
        if href.startswith("#"):
            assert href[1:] in parsed.ids
        elif not href.startswith("https://"):
            if not (ROOT/href).exists():
                missing.append(href)
    assert not missing,missing
    assert len(content)>16_000
    old=ROOT.parent/"live-client-141"
    prior=read(old/"evidence/report-verification.json")
    assert sha(old/"report.html")==prior["sha256"]
    summary=ROOT/"summary.txt"
    assert len(summary.read_text())>300
    rec={"verified_msk":stamp(),"html":sha(p),"summary_validated":True,"language":parsed.language,
         "inline_assets":True,"working_links":len(parsed.links),"historical_report_unchanged":True,
         "verification_sha256":sha(ROOT/"evidence/verification.json")}
    put(ROOT/"evidence/report-verification.json",rec)
    print(rec)
