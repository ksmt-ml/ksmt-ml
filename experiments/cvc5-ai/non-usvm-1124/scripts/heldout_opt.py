"""Independent design families reserved for the primary constraint holdout."""
from functools import lru_cache
from itertools import combinations

from constraints import Smt, add, before, exclusive

SIZES={
    "rectangular-strip": (6,8),
    "oven-batches": (9,12),
    "crew-cover": (12,16),
    "parallel-dag": (7,9),
    "container-conflicts": (9,12),
    "lane-merge": (5,7),
}


def rectangle(rng,n):
    width=6
    shapes=[(rng.randrange(1,4),rng.randrange(2,5)) for _ in range(n)]
    order=sorted(range(n),key=lambda i:-shapes[i][0]*shapes[i][1])
    nodes=0

    def feasible(height):
        choices=[]
        for w,h in shapes:
            choices.append([(x,y,sum(((1<<w)-1)<<((y+j)*width+x) for j in range(h)))
                            for x in range(width-w+1) for y in range(height-h+1)])
        @lru_cache(None)
        def visit(i,used):
            nonlocal nodes
            nodes+=1
            if i==n:
                return ()
            for x,y,cells in choices[order[i]]:
                if not cells&used:
                    tail=visit(i+1,used|cells)
                    if tail is not None:
                        return ((order[i],x,y),*tail)
            return None
        return visit(0,0)

    lo=max(max(h for w,h in shapes),(sum(w*h for w,h in shapes)+width-1)//width)
    while (witness:=feasible(lo)) is None:
        lo+=1
    assert feasible(lo-1) is None

    def encode(h):
        smt=Smt()
        xs=[smt.declare(f"x{i}") for i in range(n)]
        ys=[smt.declare(f"y{i}") for i in range(n)]
        for i,(wi,hi) in enumerate(shapes):
            smt.bounds(xs[i],0,width-wi)
            smt.bounds(ys[i],0,h-hi)
        for i,j in combinations(range(n),2):
            a,b=shapes[i],shapes[j]
            smt.add(f"(or {before(xs[i],a[0],xs[j])} {before(xs[j],b[0],xs[i])} "
                    f"{before(ys[i],a[1],ys[j])} {before(ys[j],b[1],ys[i])})")
        return smt
    return lo,dict(width=width,shapes=shapes,placements=witness,exact_nodes=nodes),encode


def oven(rng,n):
    duration=[rng.randrange(2,12) for _ in range(n)]
    incompatible=set((i,j) for i,j in combinations(range(n),2) if rng.random()<.26)
    masks=[]
    for count in range(1,4):
        for batch in combinations(range(n),count):
            if not any(edge in incompatible for edge in combinations(batch,2)):
                masks.append((sum(1<<j for j in batch),max(duration[j] for j in batch),batch))
    @lru_cache(None)
    def optimum(left):
        if not left:
            return 0,()
        first=left&-left
        results=[]
        for mask,cost,batch in masks:
            if mask&first and mask&left==mask:
                value,witness=optimum(left^mask)
                results.append((cost+value,(batch,*witness)))
        return min(results)
    best,witness=optimum((1<<n)-1)

    def encode(cap):
        smt=Smt()
        bs=[smt.declare(f"batch{i}") for i in range(n)]
        lengths=[smt.declare(f"duration{i}") for i in range(n)]
        for i,b in enumerate(bs):
            smt.bounds(b,0,i)
        for k,value in enumerate(lengths):
            smt.bounds(value,0,max(duration))
            smt.add(f"(<= {add(f'(ite (= {b} {k}) 1 0)' for b in bs)} 3)")
            for j,job in enumerate(bs):
                if j>=k:
                    smt.add(f"(=> (= {job} {k}) (>= {value} {duration[j]}))")
        for i,j in sorted(incompatible):
            smt.add(f"(distinct {bs[i]} {bs[j]})")
        smt.add(f"(<= {add(lengths)} {cap})")
        return smt
    return best,dict(duration=duration,incompatible=sorted(incompatible),batches=witness,exact_nodes=optimum.cache_info().currsize),encode


def crew(rng,n):
    shifts=6
    workers=[tuple(sorted(rng.sample(range(shifts),rng.choice((2,3,3,4))))) for _ in range(n)]
    demand=[min(3,sum(s in w for w in workers)) for s in range(shifts)]
    costs=[rng.randrange(2,11) for _ in workers]
    @lru_cache(None)
    def visit(i,need):
        if not any(need):
            return 0,()
        if i==n:
            return float("inf"),()
        skip=visit(i+1,need)
        cost,picked=visit(i+1,tuple(max(0,x-int(j in workers[i])) for j,x in enumerate(need)))
        return min(skip,(cost+costs[i],(i,*picked)))
    best,assigned=visit(0,tuple(demand))
    assert all(sum(j in workers[i] for i in assigned)>=demand[j] for j in range(shifts))
    assert sum(costs[i] for i in assigned)==best

    def encode(cap):
        smt=Smt()
        xs=[smt.declare(f"worker{i}","Bool") for i in range(n)]
        for j,d in enumerate(demand):
            smt.add(f"(>= {add(f'(ite {x} 1 0)' for i,x in enumerate(xs) if j in workers[i])} {d})")
        smt.add(f"(<= {add(f'(ite {x} {c} 0)' for x,c in zip(xs,costs))} {cap})")
        return smt
    return best,dict(availability=workers,demand=demand,costs=costs,selected=assigned,exact_nodes=visit.cache_info().currsize),encode


def dag(rng,n):
    size=[rng.randrange(1,8) for _ in range(n)]
    pred=[tuple(i for i in range(j) if rng.random()<.23) for j in range(n)]
    bound=[sum(size),[]]
    nodes=0
    def visit(todo,finish,loads,route):
        nonlocal nodes
        nodes+=1
        if max(loads)>=bound[0]:
            return
        if not todo:
            bound[:]=[max(loads),route]
            return
        for j in sorted(todo):
            if any(p in todo for p in pred[j]):
                continue
            after=max((finish[p] for p in pred[j]),default=0)
            for i,load in enumerate(loads):
                if i and load==loads[0]:
                    continue
                start=max(load,after)
                stop=start+size[j]
                visit(todo-{j},finish|{j:stop},loads[:i]+(stop,)+loads[i+1:],route+[(j,i,start)])
    # Serial topological order is always a valid witness, also for a chain.
    bound[1]=[(j,0,sum(size[:j])) for j in range(n)]
    visit(set(range(n)),{},(0,0),[])
    best,witness=bound

    def encode(cap):
        smt=Smt()
        xs=[smt.declare(f"t{i}") for i in range(n)]
        machines=[smt.declare(f"p{i}","Bool") for i in range(n)]
        for i,x in enumerate(xs):
            smt.bounds(x,0,cap-size[i])
            for j in pred[i]:
                smt.add(before(xs[j],size[j],x))
        for i,j in combinations(range(n),2):
            smt.add(f"(=> (= {machines[i]} {machines[j]}) (or {before(xs[i],size[i],xs[j])} {before(xs[j],size[j],xs[i])}))")
        return smt
    return best,dict(duration=size,pred=pred,placement=witness,exact_nodes=nodes),encode


def containers(rng,n):
    forbidden={(i,j) for i,j in combinations(range(n),2) if rng.random()<.53}
    groups=[(tuple(i for i in range(n) if m&(1<<i)),m) for m in range(1,1<<n)]
    groups=[(m,group) for group,m in groups if all(pair not in forbidden for pair in combinations(group,2))]
    @lru_cache(None)
    def solve(left):
        if not left:
            return ()
        first=left & -left
        return min(((group,*solve(left^mask)) for mask,group in groups if mask&left==mask and mask&first),key=len)
    packing=solve((1<<n)-1)
    best=len(packing)
    def encode(cap):
        smt=Smt()
        labels=[smt.declare(f"palette{i}") for i in range(n)]
        for i,label in enumerate(labels):
            smt.bounds(label,0,min(i,cap-1))
        for i,j in sorted(forbidden):
            smt.add(f"(distinct {labels[i]} {labels[j]})")
        return smt
    return best,dict(forbidden=sorted(forbidden),partition=packing,exact_nodes=solve.cache_info().currsize),encode


def merge(rng,n):
    times=[[rng.randrange(2,10) for _ in range(n)] for _ in range(2)]
    releases=[sorted(rng.sample(range(26),n)) for _ in range(2)]
    change=rng.randrange(2,6)
    @lru_cache(None)
    def visit(a,b,last):
        if not (a or b):
            return 0,()
        k=(a,b)[last]-1
        if k<0:
            return float("inf"),()
        counts=[a,b]
        counts[last]-=1
        outputs=[]
        for prev in range(2):
            t,path=visit(*counts,prev)
            delay=change if sum(counts)>0 and prev!=last else 0
            end=max(t+delay,releases[last][k])+times[last][k]
            outputs.append((end,(*path,(last,k,end-times[last][k]))))
        return min(outputs)
    best,schedule=min(visit(n,n,k) for k in range(2))
    def encode(cap):
        smt=Smt()
        xs=[[smt.declare(f"event{lane}_{i}") for i in range(n)] for lane in range(2)]
        for k in range(2):
            for i,x in enumerate(xs[k]):
                smt.bounds(x,releases[k][i],cap-times[k][i])
                if i:
                    smt.add(before(xs[k][i-1],times[k][i-1],x))
        for i in range(n):
            for j in range(n):
                exclusive(smt,xs[0][i],times[0][i]+change,xs[1][j],times[1][j]+change)
        return smt
    return best,dict(duration=times,releases=releases,change=change,schedule=schedule,exact_nodes=visit.cache_info().currsize),encode


GENERATE={
    "rectangular-strip":rectangle,
    "oven-batches":oven,
    "crew-cover":crew,
    "parallel-dag":dag,
    "container-conflicts":containers,
    "lane-merge":merge,
}
