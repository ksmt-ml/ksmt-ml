"""Add static-only project reserves before any validation/test measurements."""
from pathlib import Path
import os
import subprocess

import freeze

ROOT=freeze.ROOT
base=freeze.base
RESERVES={
    "jackson-core":("test","com/fasterxml/jackson/core/jackson-core/2.18.4",
                    ["com.fasterxml.jackson.core.io.Number","com.fasterxml.jackson.core.io.CharTypes",
                     "com.fasterxml.jackson.core.util."],[]),
    "spring-core":("test","org/springframework/spring-core/6.1.14",
                   ["org.springframework.util.","org.springframework.core."],[]),
    "netty-common":("test","io/netty/netty-common/4.1.118.Final",
                    ["io.netty.util.internal.MathUtil","io.netty.util.internal.StringUtil",
                     "io.netty.util.internal.UnpaddedInternalThreadLocalMap"],[]),
    "commons-compress":("test","org/apache/commons/commons-compress/1.27.1",
                        ["org.apache.commons.compress.utils.ByteUtils",
                         "org.apache.commons.compress.archivers.zip.ZipLong",
                         "org.apache.commons.compress.archivers.zip.ZipShort",
                         "org.apache.commons.compress.archivers.zip.ZipEightByteInteger",
                         "org.apache.commons.compress.compressors.lz77support."],
                        ["commons-io/commons-io/2.17.0"]),
    "commons-codec":("validation","commons-codec/commons-codec/1.17.1",
                     ["org.apache.commons.codec.binary.","org.apache.commons.codec.language."],[]),
    "gson":("test","com/google/code/gson/gson/2.13.2",
            ["com.google.gson.internal.","com.google.gson.stream."],[]),
    "log4j-api":("validation","org/apache/logging/log4j/log4j-api/2.24.3",
                 ["org.apache.logging.log4j.util.Chars","org.apache.logging.log4j.util.StringBuilderFormattable"],[]),
    "commons-exec":("test","org/apache/commons/commons-exec/1.4.0",
                    ["org.apache.commons.exec."],[]),
}

def main():
    target=ROOT/"inputs/expanded-split.json"
    if target.is_file():
        return
    original=ROOT/"inputs/split.json"
    prior=base.read(original)
    eligible=ROOT/"evidence/scan-reserve"
    eligible.mkdir(exist_ok=True)
    scan=ROOT/"build/scan"
    asm=os.pathsep.join(str(p) for p in base.archive.LOCAL_CP if p.name in ("asm-9.7.1.jar","asm-tree-9.7.1.jar"))
    history,hashes=freeze.read_history()
    assert prior["history"]==hashes
    for project,(split,coord,prefix,extra) in RESERVES.items():
        assets=[freeze.locate(p) for p in (coord,*extra)]
        if any(p is None for p in assets):
            prior["excluded"][project]="jar missing"
            continue
        where=eligible/f"{project}.tsv"
        p=["taskset","-c",freeze.SCANNER_CPU,*base.archive.jvm(eligible,"500m"),"-cp",
                   str(scan)+os.pathsep+asm,"ScanPure",assets[0]["path"],*prefix]
        logs=subprocess.run(p,text=True,timeout=30,check=True,env=base.public_env(eligible),capture_output=True)
        where.write_text(logs.stdout)
        (eligible/f"{project}.stderr.log").write_text(logs.stderr)
        picked,counts=freeze.select(project,where,history)
        if len(picked)<2:
            prior["excluded"][project]={"reason":"fewer than two eligible outer classes","eligible":counts}
            continue
        prior["splits"][split].append(dict(project=project,files=assets,methods=picked,eligible=counts,scan_sha=base.sha(where)))
    prior["original_sha256"]=base.sha(original)
    prior["expanded_at"]=base.now()
    base.write(target,prior)
    print({k:{p["project"]:len(p["methods"]) for p in v} for k,v in prior["splits"].items()}, flush=True)

if __name__=="__main__":
    main()
