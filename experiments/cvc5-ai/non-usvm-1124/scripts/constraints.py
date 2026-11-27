"""Fresh scheduling/packing instances and independent exact oracles."""
import argparse
from functools import lru_cache
from itertools import combinations
import random

from common import ROOT, now, read, sha, stable_seed, write

FAMILIES = {
    "train": ("flow-line", "identical-bins", "shift-windows", "machine-shop", "exact-cover"),
    "validation": ("route-setup", "partition-pairs", "crew-assignment"),
    "test": (),
}
SIZES = {
    "flow-line": (6,8), "identical-bins": (12,16), "shift-windows": (11,15), "machine-shop": (4,5),
    "exact-cover": (18,24), "route-setup": (8,10), "partition-pairs": (10,14), "crew-assignment": (8,11),
    "open-shop": (4,5), "shelf-loading": (10,13), "time-lag": (10,14), "board-tiling": (4,5),
    "machine-balancing": (10,13), "interval-reservations": (9,13),
}


class Smt:
    def __init__(self):
        self.lines=["(set-logic QF_LIA)"]

    def declare(self,x,sort="Int"):
        self.lines.append(f"(declare-const {x} {sort})")
        return x

    def add(self, expr):
        self.lines.append(f"(assert {expr})")

    def bounds(self,x,lo,hi):
        lower=f"(- {-lo})" if lo<0 else str(lo)
        upper=f"(- {-hi})" if hi<0 else str(hi)
        self.add(f"(<= {lower} {x})")
        self.add(f"(<= {x} {upper})")

    def text(self):
        return "\n".join(self.lines+["(check-sat)","(exit)",""])


def add(terms):
    terms=list(terms)
    if not terms:
        return "0"
    if len(terms)==1:
        return str(terms[0])
    return "(+ " + " ".join(map(str,terms)) + ")"


def before(a,d,b):
    return f"(<= (+ {a} {d}) {b})"


def exclusive(smt,a,da,b,db):
    smt.add(f"(or {before(a,da,b)} {before(b,db,a)})")


def flow(rng,n):
    lengths=[[rng.randint(1,9) for _ in range(3)] for _ in range(n)]

    # Exhaustive permutation tree with a monotone lower bound on completion.
    best=[float("inf"), None]
    nodes=0
    def visit(left,times,route):
        nonlocal nodes
        nodes+=1
        if times[-1]>=best[0]:
            return
        if not left:
            best[:]=[times[-1],route]
            return
        for j in left:
            end=[]
            for t,d in zip(times,lengths[j]):
                end.append(max(t,end[-1] if end else 0)+d)
            visit([i for i in left if i!=j],end,route+[j])
    visit(list(range(n)),[0,0,0],[])
    optimum,route=best
    assert sorted(route)==list(range(n))

    def encode(cap):
        smt=Smt()
        starts=[[smt.declare(f"t{j}_{k}") for k in range(3)] for j in range(n)]
        for j,row in enumerate(starts):
            for k,x in enumerate(row):
                smt.bounds(x,0,cap-lengths[j][k])
                if k:
                    smt.add(before(row[k-1],lengths[j][k-1],x))
        for i,j in combinations(range(n),2):
            l=" ".join(before(starts[i][k],lengths[i][k],starts[j][k]) for k in range(3))
            r=" ".join(before(starts[j][k],lengths[j][k],starts[i][k]) for k in range(3))
            smt.add(f"(or (and {l}) (and {r}))")
        return smt
    return optimum,dict(lengths=lengths,order=route,exact_nodes=nodes),encode


def bins(rng,n,mode="identical-bins"):
    k=3 if n<15 else 4
    sizes=[rng.randint(4,15) for _ in range(n)]
    allowed=[[b for b in range(k) if mode!="shelf-loading" or rng.random()<.82] for _ in sizes]
    allowed=[bs or [rng.randrange(k)] for bs in allowed]
    extra=[[rng.randint(0,4) if mode=="machine-balancing" else 0 for _ in range(k)] for _ in sizes]
    weights=[[s+x for x in row] for s,row in zip(sizes,extra)]
    order=sorted(range(n),key=lambda i:(len(allowed[i]),-sizes[i]))
    symmetric=mode=="identical-bins"
    witness=None
    nodes=0
    def feasible(cap):
        @lru_cache(None)
        def visit(i,loads):
            nonlocal nodes
            nodes+=1
            if i==n:
                return ()
            j=order[i]
            equivalent=set()
            for b in allowed[j]:
                if symmetric and loads[b] in equivalent:
                    continue
                equivalent.add(loads[b])
                next_load=loads[b]+weights[j][b]
                if next_load<=cap:
                    next_loads=loads[:b]+(next_load,)+loads[b+1:]
                    rest=visit(i+1,next_loads)
                    if rest is not None:
                        return (b,*rest)
            return None
        return visit(0,(0,)*k)
    lo=(sum(min(ws[b] for b in bs) for bs,ws in zip(allowed,weights))+k-1)//k
    hi=sum(max(ws) for ws in weights)
    while lo<hi:
        cap=(lo+hi)//2
        if feasible(cap) is not None:
            hi=cap
        else:
            lo=cap+1
    optimum=lo
    result=feasible(optimum)
    assert result is not None and feasible(optimum-1) is None
    witness=dict(zip(order,result))
    assert max(sum(weights[i][b] for i in witness if witness[i]==b) for b in range(k))==optimum
    def encode(cap):
        smt=Smt()
        labels=[smt.declare(f"bin{i}") for i in range(n)]
        for x,bs in zip(labels,allowed):
            smt.bounds(x,0,k-1)
            if len(bs)!=k:
                smt.add("(or "+" ".join(f"(= {x} {b})" for b in bs)+")")
        for b in range(k):
            load=add(f"(ite (= {x} {b}) {weights[i][b]} 0)" for i,x in enumerate(labels))
            smt.add(f"(<= {load} {cap})")
        if symmetric:
            smt.add(f"(= {labels[order[0]]} 0)")
        return smt
    return optimum,dict(weights=weights,allowed=allowed,assignment=witness,exact_nodes=nodes),encode


def windows(rng,n,mode="shift-windows"):
    duration=[rng.randint(1,8) for _ in range(n)]
    releases=[rng.randrange(0,25) for _ in range(n)]
    constraints=[(i,j,rng.randrange(0,4) if mode=="time-lag" else 0)
                 for i,j in combinations(range(n),2) if rng.random()<.18]
    pred=[{i:lag for i,x,lag in constraints if x==j} for j in range(n)]
    due=[sum(duration)+max(releases)+5 for _ in range(n)]
    if mode=="interval-reservations":
        cursor=0
        for j in range(n):
            cursor=max(cursor,releases[j])+duration[j]
            due[j]=cursor+rng.randrange(0,7)
    nodes=0
    best=[float("inf"),None]
    if mode=="time-lag":
        # Exact prefix search. Unlike simple earliest-finish DP, waiting for
        # predecessor-specific lags depends on the exact prefix timings.
        def visit(left,finish,t,job_order):
            nonlocal nodes
            nodes+=1
            if t+sum(duration[j] for j in left)>=best[0]:
                return
            if not left:
                best[:]=[t,tuple(job_order)]
                return
            for j in left:
                if any(i in left for i in pred[j]):
                    continue
                e=max(t,releases[j],*[finish[i]+lag for i,lag in pred[j].items()])+duration[j]
                if e<=due[j]:
                    visit(tuple(i for i in left if i!=j),dict(finish,**{})|{j:e},e,job_order+[j])
        visit(tuple(range(n)),{},0,[])
    else:
        @lru_cache(None)
        def visit(mask):
            nonlocal nodes
            nodes+=1
            if not mask:
                return 0,()
            winner=(float("inf"),())
            for j in range(n):
                bit=1<<j
                if not mask&bit or any((mask & (1<<i))==0 for i in pred[j]):
                    continue
                t,route=visit(mask^bit)
                e=max(t,releases[j])+duration[j]
                if e<=due[j] and e<winner[0]:
                    winner=(e,route+(j,))
            return winner
        best[:]=visit((1<<n)-1)
    optimum,order=best
    assert len(order)==n,(mode,order)
    starts={}
    finish=0
    for j in order:
        starts[j]=max(finish,releases[j],*[starts[i]+duration[i]+lag for i,lag in pred[j].items()])
        finish=starts[j]+duration[j]
        assert finish<=due[j]
    assert finish==optimum
    def encode(cap):
        smt=Smt()
        xs=[smt.declare(f"start{j}") for j in range(n)]
        for j,x in enumerate(xs):
            smt.bounds(x,releases[j],min(due[j],cap)-duration[j])
        for i,j,lag in constraints:
            smt.add(before(xs[i],duration[i]+lag,xs[j]))
        for i,j in combinations(range(n),2):
            exclusive(smt,xs[i],duration[i],xs[j],duration[j])
        return smt
    return optimum,dict(release=releases,duration=duration,due=due,precedence=constraints,starts=starts,exact_nodes=nodes),encode


def shop(rng,n,mode="machine-shop"):
    machines=3
    routes=[rng.sample(range(machines),machines) for _ in range(n)]
    durations=[[rng.randint(1,9) for _ in range(machines)] for _ in range(n)]
    open_shop=mode=="open-shop"
    # Exact serial enumeration of active operation schedules in a cap.
    # Every feasible timetable induces a topological order of all machine and
    # job precedence edges. Earliest starts in that order preserve feasibility.
    limits=tuple(tuple(r) for r in durations)
    flat=[(j,k) for j in range(n) for k in range(machines)]
    nodes=0
    @lru_cache(None)
    def bounds_dummy():
        return max(max(map(sum,durations)),max(sum(durations[j][k] for j,k in flat if routes[j][k]==m)
                                                for m in range(machines)))

    def feasible(cap):
        @lru_cache(None)
        def visit(mask,ready_job,ready_machine):
            nonlocal nodes
            nodes+=1
            if mask==(1<<len(flat))-1:
                return ()
            for j in range(n):
                if ready_job[j]+sum(d for k,d in enumerate(limits[j]) if not mask & (1<<(j*machines+k)))>cap:
                    return None
            for m in range(machines):
                if ready_machine[m]+sum(limits[j][k] for j,k in flat
                    if routes[j][k]==m and not mask & (1<<(j*machines+k)))>cap:
                    return None
            for i,(j,k) in enumerate(flat):
                if mask & (1<<i) or (not open_shop and k and not mask & (1<<(i-1))):
                    continue
                m=routes[j][k]
                start=max(ready_job[j],ready_machine[m])
                end=start+limits[j][k]
                if end>cap:
                    continue
                tail=visit(mask | (1<<i),ready_job[:j]+(end,)+ready_job[j+1:],
                           ready_machine[:m]+(end,)+ready_machine[m+1:])
                if tail is not None:
                    return ((j,k,start),*tail)
            return None
        return visit(0,(0,)*n,(0,)*machines)

    lo=bounds_dummy()
    hi=sum(map(sum,durations))
    while lo<hi:
        cap=(lo+hi)//2
        if feasible(cap) is not None:
            hi=cap
        else:
            lo=cap+1
    optimum=lo
    schedule=feasible(optimum)
    assert schedule is not None and feasible(optimum-1) is None
    start={(j,k):t for j,k,t in schedule}
    assert len(start)==len(flat)
    for (i,k),(j,l) in combinations(flat,2):
        if i==j or routes[i][k]==routes[j][l]:
            assert start[i,k]+durations[i][k]<=start[j,l] or start[j,l]+durations[j][l]<=start[i,k]
        if not open_shop and i==j:
            assert start[i,k]+durations[i][k]<=start[j,l]
    def encode(cap):
        smt=Smt()
        xs={(j,k):smt.declare(f"t{j}_{k}") for j,k in flat}
        for j,k in flat:
            smt.bounds(xs[j,k],0,cap-durations[j][k])
            if k and not open_shop:
                smt.add(before(xs[j,k-1],durations[j][k-1],xs[j,k]))
        for (i,k),(j,l) in combinations(flat,2):
            if (i==j and open_shop) or routes[i][k]==routes[j][l]:
                exclusive(smt,xs[i,k],durations[i][k],xs[j,l],durations[j][l])
        return smt
    return optimum,dict(routes=routes,durations=durations,schedule=schedule,exact_nodes=nodes),encode


def cover(rng,n,mode="exact-cover"):
    if mode=="board-tiling":
        cells=list(range(n*n))
        subsets=[(i,i+1) for i in cells if i%n<n-1]+[(i,i+n) for i in cells if i//n<n-1]
        subsets+= [(i,i+1,i+n) for i in cells if i%n<n-1 and i//n<n-1]
        subsets+= [(i,) for i in cells]
    else:
        cells=list(range(n))
        subsets=list(dict.fromkeys(tuple(sorted(rng.sample(cells,rng.choice((2,3,3,4)))))
                                   for _ in range(n*3)))
        subsets+= [(i,) for i in cells]
    masks=[sum(1<<j for j in subset) for subset in subsets]
    by_cell=[[i for i,s in enumerate(subsets) if j in s] for j in cells]
    @lru_cache(None)
    def visit(left):
        if not left:
            return ()
        choices=min(([i for i in by_cell[j] if masks[i]&left==masks[i]] for j in cells if left & (1<<j)),key=len)
        return min(((i,*visit(left^masks[i])) for i in choices),key=len)
    witness=visit((1<<len(cells))-1)
    optimum=len(witness)
    assert sorted(j for i in witness for j in subsets[i])==cells
    def encode(cap):
        smt=Smt()
        xs=[smt.declare(f"set{i}","Bool") for i in range(len(subsets))]
        for choices in by_cell:
            smt.add(f"(= {add(f'(ite {xs[i]} 1 0)' for i in choices)} 1)")
        smt.add(f"(<= {add(f'(ite {x} 1 0)' for x in xs)} {cap})")
        return smt
    return optimum,dict(subsets=subsets,witness=witness,exact_nodes=visit.cache_info().currsize),encode


def routing(rng,n):
    travel=[[0 if i==j else rng.randint(2,13) for j in range(n)] for i in range(n)]
    @lru_cache(None)
    def visit(mask,last):
        if mask==1<<last:
            return 0,(last,)
        candidates=[]
        for pred in range(n):
            if pred!=last and mask&(1<<pred):
                length,path=visit(mask^(1<<last),pred)
                candidates.append((length+travel[pred][last],path+(last,)))
        return min(candidates)
    optimum,path=min(visit((1<<n)-1,last) for last in range(n))
    assert sum(travel[i][j] for i,j in zip(path,path[1:]))==optimum
    def encode(cap):
        smt=Smt()
        order=[smt.declare(f"p{i}") for i in range(n)]
        time=[smt.declare(f"s{i}") for i in range(n)]
        for i in range(n):
            smt.bounds(order[i],0,n-1)
            smt.bounds(time[i],0,cap)
        smt.add("(distinct "+" ".join(order)+")")
        for i in range(n):
            for j in range(n):
                if i!=j:
                    smt.add(f"(=> (= (+ {order[i]} 1) {order[j]}) {before(time[i],travel[i][j],time[j])})")
        return smt
    return optimum,dict(travel=travel,path=path,exact_nodes=visit.cache_info().currsize),encode


def assignment(rng,n,mode):
    cost=[[rng.randint(2,20) for _ in range(n)] for _ in range(n)]
    # Minimum-cost perfect matching via subset DP.
    @lru_cache(None)
    def visit(left):
        if not left:
            return 0,()
        pos=n-left.bit_count()
        answers=[]
        for j in range(n):
            if left&(1<<j):
                c,route=visit(left^(1<<j))
                answers.append((c+cost[pos][j],(j,*route)))
        return min(answers)
    optimum,match=visit((1<<n)-1)
    assert len(set(match))==n and sum(cost[i][j] for i,j in enumerate(match))==optimum
    def encode(cap):
        smt=Smt()
        xs=[smt.declare(f"worker{i}") for i in range(n)]
        for x in xs:
            smt.bounds(x,0,n-1)
        smt.add("(distinct "+" ".join(xs)+")")
        if mode=="partition-pairs":
            smt.add(f"(<= {add(f'(ite (= {x} {j}) {cost[i][j]} 0)' for i,x in enumerate(xs) for j in range(n))} {cap})")
        else:
            costvars=[]
            for i,x in enumerate(xs):
                v=smt.declare(f"cost{i}")
                costvars.append(v)
                for j in range(n):
                    smt.add(f"(=> (= {x} {j}) (= {v} {cost[i][j]}))")
            smt.add(f"(<= {add(costvars)} {cap})")
        return smt
    return optimum,dict(costs=cost,match=match,exact_nodes=visit.cache_info().currsize),encode


def generate(family,rng,n):
    if family=="flow-line":
        return flow(rng,n)
    if family in ("identical-bins","shelf-loading","machine-balancing"):
        return bins(rng,n,family)
    if family in ("shift-windows","time-lag","interval-reservations"):
        return windows(rng,n,family)
    if family in ("machine-shop","open-shop"):
        return shop(rng,n,family)
    if family in ("exact-cover","board-tiling"):
        return cover(rng,n,family)
    if family=="route-setup":
        return routing(rng,n)
    if family in ("partition-pairs","crew-assignment"):
        return assignment(rng,n,family)
    raise ValueError(family)


def build(part,profile,release=None):
    if part=="test":
        assert release and (ROOT/"evidence/opt-test-release.json").is_file()
        from heldout_opt import GENERATE, SIZES as TEST_SIZES
        families=GENERATE
        target_sizes=TEST_SIZES
    else:
        families=FAMILIES[part]
        target_sizes=SIZES
    out=ROOT/"inputs"/profile/part/"opt"
    marker=out/"manifest.json"
    if marker.exists():
        return read(marker)
    rows=[]
    for family in families:
        sizes=target_sizes[family]
        if profile=="probe":
            sizes=sizes[:1]
        elif profile=="hard":
            sizes=tuple(x+1 for x in sizes)
        for n in sizes:
            rng=random.Random(stable_seed("opt",family,n,release or 1189))
            started=now()
            builder=families[family] if part=="test" else lambda r,k: generate(family,r,k)
            optimum,certificate,encode=builder(rng,n)
            print(now(),family,n,optimum,certificate["exact_nodes"],flush=True)
            for slack in (0,-1):
                id=f"{family}-{n}-{'sat' if slack==0 else 'unsat'}"
                folder=out/id
                folder.mkdir(parents=True,exist_ok=True)
                path=folder/"constraint.smt2"
                path.write_text(encode(optimum+slack).text())
                oracle=folder/"oracle.json"
                write(oracle,dict(start=started,stop=now(),family=family,cap=optimum+slack,optimum=optimum,**certificate))
                rows.append(dict(id=id,family=family,producer="opt",path=str(path.relative_to(ROOT)),sha256=sha(path),
                                 expected="sat" if slack==0 else "unsat", oracle_sha256=sha(oracle)))
    write(marker,dict(at=now(),part=part,profile=profile,items=rows))
    return read(marker)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("part",choices=FAMILIES)
    p.add_argument("--profile",default="base",choices=("base","probe","hard"))
    a=p.parse_args()
    nonce=read(ROOT/"evidence/opt-test-release.json")["nonce"] if a.part=="test" else None
    r=build(a.part,a.profile,nonce)
    print(r["at"],len(r["items"]),flush=True)
