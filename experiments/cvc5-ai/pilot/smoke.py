import json, time
import cvc5
from cvc5 import Kind
for opts in ({}, {"bv-intro-pow2":"true"}, {"decision":"justification"}):
    s = cvc5.Solver()
    s.setOption("incremental", "true")
    for key, value in opts.items():
        s.setOption(key, value)
    s.setLogic("QF_BV")
    bv = s.mkBitVectorSort(32)
    x = s.mkConst(bv, "x")
    square = s.mkTerm(Kind.BITVECTOR_MULT, x, x)
    start = time.perf_counter()
    answers = []
    for i in range(1, 21):
        for value in (i*i, 4*i+2):
            s.push()
            s.assertFormula(s.mkTerm(Kind.EQUAL, square, s.mkBitVector(32, value)))
            answers.append(str(s.checkSat()))
            s.pop()
    assert answers == ["sat", "unsat"] * 20
    print(json.dumps(dict(options=opts, checks=len(answers), seconds=time.perf_counter()-start)))
