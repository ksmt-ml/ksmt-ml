# References and scope

## Deployment context

- [KSMT](https://github.com/UnitTestBot/ksmt) supplies the solver API and shared
  tuning contracts.
- [cvc5](https://cvc5.github.io/) is the initial target solver. Parameter domains
  are pinned to an exact version and validated on fresh instances.
- [TSA](https://github.com/espritoxyz/tsa) is a candidate symbolic-execution
  workload source. Its current Yices/Z3 composition requires a cvc5 adapter.

## Pre-trained workload models

- Dana Van Aken et al., “Automatic Database Management System Tuning Through
  Large-scale Machine Learning”
  ([paper](https://db.cs.cmu.edu/papers/2017/p1009-van-aken.pdf)). Relevant for
  training a reusable configuration model from earlier workload observations;
  database-specific lifecycle assumptions are not inherited automatically.

## Future adaptive tuning

- Frank Hutter, Holger Hoos, Kevin Leyton-Brown, and Thomas Stützle, “ParamILS:
  An Automatic Algorithm Configuration Framework”
  ([paper](https://arxiv.org/abs/1401.3492)). Relevant if a later version adds
  measured online configuration search.
- Jasper Snoek, Hugo Larochelle, and Ryan Adams, “Practical Bayesian
  Optimization of Machine Learning Algorithms”
  ([paper](https://arxiv.org/abs/1206.2944)). Relevant to a later expensive
  black-box evaluator, not to the inference-only MVP.

- [DACBench](https://github.com/automl/DACBench) is useful for reproducible
  observation/action/reward definitions. RL, an online evaluator, and online
  search are future extensions. A trained policy may later be exported to ONNX.
