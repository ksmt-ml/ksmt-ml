"""Independently validate saved feasibility certificates and adjacent thresholds."""
from itertools import combinations
from pathlib import Path
import argparse

from common import ROOT, now, read, sha, write


def exclusive(a,d,b,e):
    return a+d<=b or b+e<=a


def certificate(p):
    family=p["family"]
    cap=p["optimum"]
    if family=="flow-line":
        jobs=p["lengths"]; order=p["order"]
        assert sorted(order)==list(range(len(jobs)))
        end=[0] * len(jobs[0])
        for j in order:
            avail=0
            for machine,duration in enumerate(jobs[j]):
                avail=max(avail,end[machine])+duration
                end[machine]=avail
        return end[-1]
    if family=="identical-bins":
        allowed=p["allowed"]; weights=p["weights"]
        assignment={int(i):b for i,b in p["assignment"].items()}
        assert sorted(assignment)==list(range(len(weights)))
        loads=[0]*len(weights[0])
        for i,b in assignment.items():
            assert b in allowed[i]
            loads[b]+=weights[i][b]
        return max(loads)
    if family in {"shift-windows","time-lag","interval-reservations"}:
        starts={int(i):x for i,x in p["starts"].items()}
        duration=p["duration"]
        assert sorted(starts)==list(range(len(duration)))
        for i,t in starts.items():
            assert p["release"][i]<=t and t+duration[i]<=p["due"][i]
        for i,j,lag in p["precedence"]:
            assert starts[i]+duration[i]+lag<=starts[j]
        for i,j in combinations(starts,2):
            assert exclusive(starts[i],duration[i],starts[j],duration[j])
        return max(starts[i]+duration[i] for i in starts)
    if family in {"machine-shop","open-shop"}:
        durations=p["durations"]; routes=p["routes"]
        flat=[(j,k) for j,row in enumerate(durations) for k in range(len(row))]
        starts={(j,k):t for j,k,t in p["schedule"]}
        assert sorted(starts)==flat and len(p["schedule"])==len(flat)
        for (i,k),(j,l) in combinations(flat,2):
            if i==j and family=="machine-shop":
                assert starts[i,k]+durations[i][k]<=starts[j,l]
            if i==j or routes[i][k]==routes[j][l]:
                assert exclusive(starts[i,k],durations[i][k],starts[j,l],durations[j][l])
        return max(starts[j,k]+durations[j][k] for j,k in flat)
    if family in {"exact-cover","board-tiling"}:
        clusters=p["subsets"]
        selected=p["witness"]
        assert sorted(x for i in selected for x in clusters[i])==sorted({x for v in clusters for x in v})
        return len(selected)
    if family=="route-setup":
        path=p["path"]; weights=p["travel"]
        assert sorted(path)==list(range(len(weights)))
        return sum(weights[a][b] for a,b in zip(path,path[1:]))
    if family in {"partition-pairs","crew-assignment"}:
        weights=p["costs"]; match=p["match"]
        assert sorted(match)==list(range(len(weights)))
        return sum(weights[i][j] for i,j in enumerate(match))
    if family=="rectangular-strip":
        shapes=p["shapes"]; places=p["placements"]; width=p["width"]
        occupied=set()
        assert sorted(i for i,x,y in places)==list(range(len(shapes)))
        for i,x,y in places:
            w,h=shapes[i]
            assert 0<=x<=width-w and y>=0
            cells={(a,b) for a in range(x,x+w) for b in range(y,y+h)}
            assert occupied.isdisjoint(cells)
            occupied.update(cells)
        return max(b for a,b in occupied)+1
    if family=="oven-batches":
        duration=p["duration"]; batches=p["batches"]
        forbidden={tuple(pair) for pair in p["incompatible"]}
        for batch in batches:
            assert 1<=len(batch)<=3 and all(pair not in forbidden for pair in combinations(sorted(batch),2))
        assert sorted(i for batch in batches for i in batch)==list(range(len(duration)))
        return sum(max(duration[i] for i in batch) for batch in batches)
    if family=="crew-cover":
        selected=p["selected"]
        assert len(set(selected))==len(selected)
        assert all(sum(j in p["availability"][i] for i in selected)>=need for j,need in enumerate(p["demand"]))
        return sum(p["costs"][i] for i in selected)
    if family=="parallel-dag":
        duration=p["duration"]; pred=p["pred"]; plan=p["placement"]
        starts={i:(machine,t) for i,machine,t in plan}
        assert len(starts)==len(plan)==len(duration)
        for i,(machine,t) in starts.items():
            assert machine in {0,1} and t>=0
            assert all(starts[j][1]+duration[j]<=t for j in pred[i])
        for i,j in combinations(starts,2):
            if starts[i][0]==starts[j][0]:
                assert exclusive(starts[i][1],duration[i],starts[j][1],duration[j])
        return max(t+duration[i] for i,(machine,t) in starts.items())
    if family=="container-conflicts":
        forbids={tuple(p) for p in p["forbidden"]}
        partition=p["partition"]
        for group in partition:
            assert all(pair not in forbids for pair in combinations(sorted(group),2))
        flat=sorted(i for group in partition for i in group)
        assert flat==list(range(len(flat)))
        return len(partition)
    if family=="lane-merge":
        events=p["schedule"]; release=p["releases"]; duration=p["duration"]
        previous=None
        stop=0
        positions=[0,0]
        for lane,position,start in events:
            assert position==positions[lane]
            assert start>=release[lane][position] and start>=stop
            if previous is not None and previous!=lane:
                assert start>=stop+p["change"]
            previous=lane
            positions[lane]+=1
            stop=start+duration[lane][position]
        assert positions==[len(r) for r in release]
        return stop
    raise KeyError(family)


def verify():
    rows=[]
    for f in sorted((ROOT/"inputs").glob("*/*/opt/manifest.json")):
        for item in read(f)["items"]:
            ref=ROOT/item["path"]
            raw=ref.with_name("oracle.json")
            p=read(raw)
            assert sha(raw)==item["oracle_sha256"] and sha(ref)==item["sha256"]
            check=certificate(p)
            expected="sat" if check<=p["cap"] else "unsat"
            assert check==p["optimum"] and expected==item["expected"]
            assert check-p["cap"] in (0,1) and p["exact_nodes"]>0
            rows.append(dict(id=item["id"],family=p["family"],certificate_cost=check,cap=p["cap"],
                             expected=expected,exact_nodes=p["exact_nodes"],source=str(f.relative_to(ROOT))))
    write(ROOT/"evidence/oracle-certificate-audit.json",dict(at=now(),count=len(rows),rows=rows))
    print(now(),len(rows),flush=True)


if __name__=="__main__":
    verify()
