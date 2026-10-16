# Ревизия пространства опций cvc5 1.3.4 для TSA, версия 0

## Статус документа

Документ остаётся полной человекочитаемой инвентаризацией. На его основе и на
runtime-выгрузке cvc5 построены:

- `configs/cvc5-1.3.4-option-inventory-v0.json` — классификация всех 518 опций;
- `configs/cvc5-regular-bv-parameter-space-v0.json` — первый активный view.

В active view осталось 57 параметров. Десять опций, вызвавших завершение
native cvc5 при singleton replay, сохранены в inventory со статусом `unsafe`.
Однопрофильная квалификация прошла 73 singleton/boundary и 20 случайных
комбинированных кандидатов без ошибок. До прогона по замороженному
многопрофильному корпусу статус пространства — smoke-qualified, но не
corpus-qualified.

## Зафиксированная версия

KSMT локально собирает Java API и native libraries cvc5 **1.3.4**. Каталог
получен не из документации другой версии, а вызовами `Solver.getVersion()`,
`Solver.getOptionNames()` и `Solver.getOptionInfo()` у той же библиотеки,
которую загружает `KCvc5Solver`.

Официальный справочник этой версии:
<https://cvc5.github.io/docs/cvc5-1.3.4/options.html>.

## Полный счёт

- API вернул 531 принимаемых имён;
- после сведения aliases осталось **518 канонических опций**;
- категории: expert — 324, regular — 139, common — 54, undocumented — 1;
- типы API: ValueInfo — 344, NumberInfo — 73, ModeInfo — 90, VoidInfo — 11.

Число 531 нельзя использовать как размер независимого пространства: API
возвращает также aliases, которые ведут к тем же каноническим опциям. Для
проектирования пространства используется число 518.

## Кластеры и предварительное предложение

| Официальный кластер | Количество | Предварительный статус | Предложение |
|---|---:|---|---|
| Most Commonly-Used cvc5 Options | 54 | смешанный | Разделить по назначению. Здесь вместе находятся реальные эвристики (`decision`, `nl-ext`, quantifier modes), lifecycle/resource limits, вывод, проверки и обязательные KSMT-флаги. |
| Arithmetic Theory Module | 62 | условно широкий | Активировать для integer-blasted и профилей с LIA/NIA/RA. Не сокращать только из-за категории expert; отдельно запретить диагностические и заведомо unsafe режимы. |
| Arrays Theory Module | 7 | активный | Релевантен обоим TSA backend. `arrays-exp` фиксирован KSMT, остальные опции проходят singleton smoke и остаются кандидатами. |
| Bags Theory Module | 1 | theory-gated | Не активировать без Bag-термов; сохранить в полном каталоге. |
| Base Module | 9 | инфраструктура | Каналы, trace, safe-mode, resource weights и plugin controls не смешивать с solver-performance search. |
| Bitvector Theory Module | 13 | активный | Главный кластер regular-bv. Исключить только печать; остальные режимы валидировать на incremental/model/core lifecycle. |
| Datatypes Theory Module | 17 | theory-gated | DT-эвристики включать при наличии datatype terms; SyGuS-подкластер держать выключенным для обычного SMT. |
| Decision Heuristics Module | 3 | активный | Малый общий кластер; оценивать совместно с common `decision` и SAT layer. |
| Expression Module | 2 | защищённый | Type/well-formedness checks относятся к безопасности API, а не к тюнингу. |
| Finite Field Theory Module | 6 | theory-gated | Не активировать без finite-field terms. |
| Floating-Point Module | 3 | theory-gated | `fp-exp` фиксирован KSMT; `fp-lazy-wb` можно тюнить только при FP-нагрузке. |
| Driver Module | 13 | инфраструктура | Dump, portfolio CLI и crash-debugging не входят в однопроцессный честный solver search. |
| Parallel Module | 13 | отдельный эксперимент | Меняет ресурсы и режим исполнения. Не смешивать с однопоточным сравнением; позже дать отдельный равный CPU budget. |
| Parser Module | 8 | инфраструктура | Replay использует Java API и готовый AST, поэтому parser policy не влияет на решаемую нагрузку. |
| Printing Module | 5 | инфраструктура | Меняет представление вывода, а не решение. |
| Proof Module | 24 | lifecycle-gated | При `produce-proofs=false` неактивен. Не включать в текущий model/core workload; отдельный эксперимент для proof-producing режима. |
| SAT Layer Module | 8 | активный | Основной общий кластер. Seed является контролем повторений, dump — инфраструктурой; остальные эвристики проходят smoke. |
| Quantifiers Module | 175 | theory-gated | Сейчас `quantifiers-allowed=false`, поэтому 175 опций не должны раздувать активный поиск. Сохранить каталог и позже отделить SMT quantifiers от SyGuS. |
| Separation Logic Theory Module | 3 | theory-gated | Не активировать без separation-logic terms. |
| Sets Theory Module | 4 | theory-gated | Не активировать без Set/Relation terms. |
| SMT Layer Module | 40 | активный после разметки | Здесь сильные preprocessing/encoding options перемешаны с output/proof/result-production flags. Разметить по одной опции, не отбрасывая expert эвристики автоматически. |
| Strings Theory Module | 26 | theory-gated | Не активировать без String/RegExp/Sequence terms. |
| Theory Layer Module | 9 | активный | Общие equality/theory-combination эвристики релевантны смешанному UF+Array+BV workload. |
| Uninterpreted Functions Theory Module | 12 | активный | UF присутствует в TSA; экспериментальные и higher-order ветви оставить условными по фактическим термам. |
| Undocumented/Internal API | 1 | исключить | `filename` не документирован как solver tuning option и относится к интерфейсу/вводу. |

Итого по кластерам: **518** канонических опций.

## Что предлагается не ограничивать

Категория `expert` сама по себе не является причиной исключения. Для
активных теорий предлагается сначала проверить все смысловые solver-эвристики
одиночными изменениями относительно default, включая expert options. Опция
покидает пространство только по записанной причине:

- не устанавливается через Java API до первой assertion;
- конфликтует с обязательным lifecycle KSMT;
- требует отсутствующего backend или build feature;
- управляет вводом, печатью, диагностикой или завершением процесса;
- нарушает равенство вычислительных ресурсов;
- относится к теории, которой нет в профиле;
- дублирует экспериментальный seed или timeout policy;
- приводит к invalid option, crash либо correctness failure на smoke suite.

Это существенно шире ручного списка из нескольких популярных параметров, но
не заставляет SMAC исследовать сотни заведомо неактивных измерений.

## Предлагаемая структура пространства

### Уровень 0 — полный каталог

Все 518 опций сохраняются в inventory с типом, default, категорией, aliases и
официальным модулем. Это позволяет объяснить каждое включение и исключение.

### Уровень 1 — защищённая инфраструктура

Фиксируются evaluator’ом и не выдаются тюнеру:

- `incremental`, `produce-models`, `produce-unsat-cores`, `arrays-exp`,
  `fp-exp`, logic и per-check timeout;
- global/resource limits и random seeds;
- parser, printing, dump, trace, help, filesystem и output channels;
- проверки proof/model/core, если они не являются частью целевой нагрузки;
- parallel/portfolio controls в однопоточном эксперименте.

Эти опции не «плохие»: они принадлежат протоколу эксперимента и потому не
могут свободно меняться кандидатом.

### Уровень 2 — theory-gated кластеры

Feature extractor определяет фактически присутствующие теории. Активируются
только соответствующие кластеры:

```text
regular-bv:     BV + Arrays + UF + SAT + Decision + SMT/Theory
integer-blasted: Arithmetic + Arrays + UF + SAT + Decision + SMT/Theory
optional:       FP / Datatypes / Strings / Sets / FF / Quantifiers / Sep
```

Гейт не ограничивает значения внутри релевантного кластера. Он только убирает
параметры solver-модулей, которые ни разу не будут вызваны этой нагрузкой.

### Уровень 3 — cluster-first search

Перед совместным поиском проводится короткий скрининг кластеров:

1. singleton smoke каждой опции и каждого enum mode;
2. Random Search внутри одного кластера;
3. оценка наличия измеримого сигнала;
4. объединение активных кластеров;
5. SMAC по условному совместному пространству.

Так сохраняются межкластерные взаимодействия, но surrogate не начинает работу
с сотнями полностью неинформативных координат.

## Предложение для первого широкого поиска

Для `regular-bv` не задавать маленький ручной whitelist. Взять верхнюю
объединённую область из BV, Arrays, UF, SAT, Decision, SMT Layer, Theory Layer
и смысловых common options. До индивидуальной разметки это верхняя граница
около 146 опций; после удаления lifecycle/IO/proof/resource controls ожидается
широкое пространство порядка десятков, а не 5–10 параметров.

Для `integer-blasted` добавить Arithmetic Theory Module (ещё 62 опции), но
только после появления репрезентативной integer-blasted нагрузки: в текущем
профиле этот backend почти не получает checks, поэтому тюнинг на нём пока
не будет статистически содержательным.

Число параметров после smoke не следует фиксировать заранее. В отчёте должна
быть таблица `option -> status -> reason`, чтобы вы могли валидировать каждое
исключение.

## Пространство входных признаков — отдельная задача

Опции cvc5 являются **выходным/action space**. Признаки профиля являются
**входом** модели и не должны смешиваться с каталогом опций.

Для инкрементального solver предлагаются следующие кластеры признаков:

1. **Масштаб сессии:** assertions, checks, scopes, assumptions, unique queries.
2. **Theory footprint:** наличие и доля BV, Array, UF, Int, Real, FP, DT,
   quantifier, String/Sequence/Set операций.
3. **AST-структура:** размеры, глубины, arity, operator histogram, widths,
   constants, ITE, equalities, comparisons, conversions.
4. **Граф и sharing:** число уникальных declarations/subterms, reuse, DAG
   sharing, connected components и отношение shared/total nodes.
5. **Incremental dynamics:** push/pop depth, assertions между checks, доля
   check-with-assumptions, churn активного контекста.
6. **Изменение запросов:** delta размера и теорий между соседними checks,
   повторяемость fingerprints, фазовые изменения нагрузки.
7. **Результаты:** SAT/UNSAT/UNKNOWN ratios, серии одинаковых результатов,
   timeout и reason-of-unknown.
8. **Difficulty/timing:** log-duration, quantiles, tail ratio, trend, variance,
   solver-time share; абсолютные времена хранить вместе с host provenance.
9. **Result access:** частота model, unsat core и reason-of-unknown, потому что
   их стоимость зависит от solver configuration.
10. **Backend/context:** regular-bv или integer-blasted, cvc5/KSMT version и
    стабильные application attributes; не использовать IDs, paths и timestamps.

Для первого extractor лучше агрегированный фиксированный vector: counts, ratios,
log-scaled values, quantiles и slopes. Raw event sequence можно добавить позже
как отдельную модель только после сильного tree-based baseline.

Критически важно строить признаки только из representative default-профиля.
Нельзя добавлять результаты candidate search или финального tuned-run: это
будет утечкой target.

### Связь с MachSMT

[MachSMT](https://cs.stanford.edu/~preiner/publications/2021/ScottNiemetzPreinerNejatiGanesh-TACAS21.pdf)
использует 162 синтаксических признака одиночного SMT-LIB instance: частоты
грамматических конструкций, характеристики array `select`/`store`, цепочек BV
сложения, кванторов и UF. Это хороший источник для **структурной части** нашего
vector, но не готовая схема для инкрементального solver: у MachSMT нет истории
`push`/`pop`, последовательности `check`, изменения активного контекста и
временных рядов сложности.

В TSA уже записываются assertion/assumption counts, tree/unique node counts,
числа констант, function applications, interpreted values и quantifiers, а
также operator/sort/BV-width histograms и fingerprint каждого query. Для
полноценного v0 extractor предлагается добавить:

- mean/max AST depth и arity;
- число `select`, распределение и max длины `store` chains;
- группы BV-операций и статистики adder/concat/extract chains;
- ITE, equality, comparison и conversion counts;
- число связанных квантором переменных, nesting depth и forall/exists ratio;
- arity, application count и reuse для UF;
- delta этих значений между соседними checks, context churn и fingerprint
  reuse;
- quantiles/slopes времени checks и отдельную missing/censored mask;
- стоимость feature extraction, чтобы сама модель не съедала выигрыш.

Размер SMT-LIB-файла из MachSMT копировать не нужно: replay строится из KSMT
AST, и размер JSON был бы свойством сериализатора. Вместо него используются
tree/unique node counts и объём активного solver-контекста.

Таким образом, предлагается не выбирать между «MachSMT-признаками» и
«инкрементальными признаками», а построить двухслойный vector:

```text
структура запросов в духе MachSMT
    + динамика инкрементальной сессии
    + агрегаты наблюдаемой сложности default-конфигурации
```

## Принятые решения v0

1. Первым тюним `regular-bv`; `integer-blasted` получит отдельный view после
   сбора репрезентативной нагрузки.
2. IO/parser/printing/proof/parallel и resource controls принадлежат протоколу,
   а не search space.
3. `sat-solver` остаётся активным. `bv-sat-solver` исключён из v0, поскольку
   активирующий его eager bitblast аварийно завершает native cvc5 на TSA replay.
4. Реальные model и unsat-core операции входят в измеряемую стоимость.
5. Потеря известного `SAT/UNSAT`, включая timeout/`UNKNOWN`, является hard
   failure кандидата.
6. Используются единый полный inventory и отдельные backend-specific views.

## Моя исходная рекомендация

- Первым тюнить `regular-bv`: именно для него уже есть содержательная replay
  нагрузка. `integer-blasted` не удалять, а открыть после сбора его checks.
- Держать один полный inventory, но две experiment-view пространства по backend
  role. Это проще анализировать, чем одно пространство с большим числом
  взаимоисключающих условий.
- IO/parser/printing/debug/proof/parallel исключить из первого однопоточного
  эксперимента как policy controls. Parallel исследовать отдельно при равном
  CPU, а proof вернуть только если proof production станет частью workload.
- `sat-solver` и `bv-sat-solver` оставить кандидатами. В домен включать каждый
  режим, который реально доступен в KSMT build и проходит smoke; не фиксировать
  default backend без эксперимента.
- Считать model/unsat-core access частью стоимости, если приложение реально
  запрашивает результат. Replay уже хранит такие события, поэтому исключение их
  времени исказило бы целевой workload.
- Для записанных известных SAT/UNSAT сохранить жёсткий correctness gate:
  `UNKNOWN`, timeout или противоположный ответ не могут считаться ускорением.
  Изначально записанный `UNKNOWN` оценивать отдельно и не использовать как
  основной speedup target.

## Полный инвентарь по официальным модулям

Ниже перечислены все 518 канонических опций. Это приложение служит проверкой
полноты; aliases отдельно не считаются параметрами.

### Most Commonly-Used cvc5 Options — 54

`nl-ext`, `incremental`, `lang`, `output`, `parse-only`, `preprocess-only`, `quiet`, `rlimit`, `rlimit-per`, `stats`, `stats-all`, `stats-internal`, `tlimit`, `tlimit-per`, `verbose`, `verbosity`, `decision`, `copyright`, `early-exit`, `help`, `help-option-categories`, `help-regular`, `interactive`, `print-success`, `seed`, `show-config`, `version`, `force-logic`, `strict-parsing`, `dag-thresh`, `expr-depth`, `output-lang`, `proof-check`, `proof-granularity`, `proof-print-conclusion`, `proof-print-reference`, `cbqi`, `enum-inst`, `full-saturate-quant`, `print-inst`, `check-abducts`, `check-interpolants`, `check-model-subsolver`, `check-models`, `check-proofs`, `check-synth-sol`, `check-unsat-cores`, `debug-check-models`, `produce-models`, `produce-proofs`, `produce-unsat-assumptions`, `produce-unsat-cores`, `condense-function-values`, `default-function-value-mode`

### Arithmetic Theory Module — 62

`approx-branch-depth`, `arith-brab`, `arith-eq-solver`, `arith-exp`, `arith-prop`, `arith-prop-clauses`, `arith-rewrite-equalities`, `arith-static-learning`, `collect-pivot-stats`, `cut-all-bounded`, `dio-decomps`, `dio-solver`, `dio-turns`, `error-selection-rule`, `fc-penalties`, `heuristic-pivots`, `lemmas-on-replay-failure`, `maxCutsInContext`, `miplib-trick`, `miplib-trick-subs`, `new-prop`, `nl-cov`, `nl-cov-force`, `nl-cov-lift`, `nl-cov-linear-model`, `nl-cov-proj`, `nl-cov-prune`, `nl-cov-var-elim`, `nl-ext-ent-conf`, `nl-ext-factor`, `nl-ext-flatten-mon`, `nl-ext-inc-prec`, `nl-ext-purify`, `nl-ext-rbound`, `nl-ext-rewrite`, `nl-ext-split-zero`, `nl-ext-tf-taylor-deg`, `nl-ext-tf-tplanes`, `nl-ext-tplanes`, `nl-ext-tplanes-interleave`, `nl-icp`, `nl-rlv`, `nl-rlv-assert-bounds`, `pb-rewrites`, `pivot-threshold`, `pp-assert-max-sub-size`, `prop-row-length`, `replay-early-close-depth`, `replay-lemma-reject-cut`, `replay-num-err-penalty`, `replay-reject-cut`, `restrict-pivots`, `revert-arith-models-on-unsat`, `rr-turns`, `se-solve-int`, `simplex-check-period`, `soi-qe`, `standard-effort-variable-order-pivots`, `unate-lemmas`, `use-approx`, `use-fcsimplex`, `use-soi`

### Arrays Theory Module — 7

`arrays-eager-index`, `arrays-eager-lemmas`, `arrays-exp`, `arrays-optimize-linear`, `arrays-prop`, `arrays-reduce-sharing`, `arrays-weak-equiv`

### Bags Theory Module — 1

`bags`

### Base Module — 9

`err`, `in`, `out`, `plugin-notify-sat-clause-in-solve`, `plugin-share-skolems`, `rweight`, `safe-mode`, `stats-every-query`, `trace`

### Bitvector Theory Module — 13

`bitblast`, `bitwise-eq`, `bool-to-bv`, `bv-assert-input`, `bv-eager-eval`, `bv-eq-engine`, `bv-gauss-elim`, `bv-intro-pow2`, `bv-propagate`, `bv-rw-extend-eq`, `bv-sat-solver`, `bv-solver`, `bv-to-bool`

### Datatypes Theory Module — 17

`cdt-bisimilar`, `datatypes-exp`, `dt-binary-split`, `dt-blast-splits`, `dt-cyclic`, `dt-infer-as-lemmas`, `dt-nested-rec`, `dt-polite-optimize`, `dt-share-sel`, `sygus-abort-size`, `sygus-fair`, `sygus-fair-max`, `sygus-rewriter`, `sygus-simple-sym-break`, `sygus-sym-break-lazy`, `sygus-sym-break-pbe`, `sygus-sym-break-rlv`

### Decision Heuristics Module — 3

`jh-rlv-order`, `jh-skolem`, `jh-skolem-rlv`

### Expression Module — 2

`type-checking`, `wf-checking`

### Finite Field Theory Module — 6

`ff`, `ff-bitsum`, `ff-elim-disjunctive-bit`, `ff-field-polys`, `ff-solver`, `ff-trace-gb`

### Floating-Point Module — 3

`fp`, `fp-exp`, `fp-lazy-wb`

### Driver Module — 13

`dump-difficulty`, `dump-instantiations`, `dump-instantiations-debug`, `dump-models`, `dump-proofs`, `dump-unsat-cores`, `dump-unsat-cores-lemmas`, `force-no-limit-cpu-while-dump`, `portfolio-dry-run`, `portfolio-jobs`, `segv-spin`, `show-trace-tags`, `use-portfolio`

### Parallel Module — 13

`append-learned-literals-to-cubes`, `checks-before-partition`, `checks-between-partitions`, `compute-partitions`, `partition-check`, `partition-conflict-size`, `partition-start-time`, `partition-strategy`, `partition-time-interval`, `partition-tlimit`, `partition-when`, `random-partitioning`, `write-partitions-to`

### Parser Module — 8

`filesystem-access`, `fresh-binders`, `fresh-declarations`, `global-declarations`, `parse-skolem-definitions`, `parsing-mode`, `semantic-checks`, `term-sort-overload`

### Printing Module — 5

`bv-print-consts-as-indexed-symbols`, `flatten-ho-chains`, `model-u-print`, `print-arith-lit-token`, `print-skolem-definitions`

### Proof Module — 24

`check-proof-steps`, `check-proofs-complete`, `lfsc-expand-trust`, `lfsc-flatten`, `opt-res-reconstruction-size`, `print-dot-clusters`, `proof-alethe-define-skolems`, `proof-alethe-res-pivots`, `proof-alethe-testing`, `proof-allow-trust`, `proof-chain-m-res`, `proof-dag-global`, `proof-dot-dag`, `proof-elim-subtypes`, `proof-format-mode`, `proof-log`, `proof-pedantic`, `proof-pp-merge`, `proof-pre-simp-lookahead`, `proof-prune-input`, `proof-rewrite-rcons-rec-limit`, `proof-rewrite-rcons-step-limit`, `prop-proof-mode`, `sat-proof-min-dimacs`

### SAT Layer Module — 8

`minisat-dump-dimacs`, `minisat-simplification`, `preregister-mode`, `random-freq`, `restart-int-base`, `restart-int-inc`, `sat-random-seed`, `sat-solver`

### Quantifiers Module — 175

`cbqi-all-conflict`, `cbqi-mode`, `cbqi-skip-rd`, `cbqi-tconstraint`, `cbqi-vo-exp`, `cegis-sample`, `cegqi`, `cegqi-all`, `cegqi-bv`, `cegqi-bv-ineq`, `cegqi-bv-interleave-value`, `cegqi-bv-linear`, `cegqi-bv-rm-extract`, `cegqi-bv-solve-nl`, `cegqi-full`, `cegqi-inf-int`, `cegqi-inf-real`, `cegqi-innermost`, `cegqi-midpoint`, `cegqi-min-bounds`, `cegqi-multi-inst`, `cegqi-nested-qe`, `cegqi-nopt`, `cegqi-round-up-lia`, `cond-var-split-quant`, `conjecture-gen`, `conjecture-gen-gt-enum`, `conjecture-gen-max-depth`, `conjecture-gen-per-round`, `cons-exp-triggers`, `dt-stc-ind`, `dt-var-exp-quant`, `e-matching`, `elim-taut-quant`, `enum-inst-interleave`, `enum-inst-limit`, `enum-inst-rd`, `enum-inst-stratify`, `enum-inst-sum`, `ext-rewrite-quant`, `finite-model-find`, `fmf-bound`, `fmf-bound-blast`, `fmf-bound-lazy`, `fmf-fun`, `fmf-fun-rlv`, `fmf-mbqi`, `fmf-type-completion-thresh`, `full-sygus-verify`, `global-negate`, `ho-elim`, `ho-elim-store-ax`, `ho-matching`, `ho-merge-term-db`, `ieval`, `increment-triggers`, `inst-local`, `inst-max-level`, `inst-max-rounds`, `inst-no-entail`, `inst-when`, `inst-when-phase`, `int-wf-ind`, `ite-dtt-split-quant`, `ite-lift-quant`, `leibniz-elim`, `literal-matching`, `macros-quant`, `macros-quant-mode`, `mbqi`, `mbqi-check-timeout`, `mbqi-enum`, `mbqi-enum-choice-grammar`, `mbqi-enum-choice-grammar-all`, `mbqi-enum-ext-vars-grammar`, `mbqi-enum-free-syms-grammar`, `mbqi-enum-global-syms-grammar`, `mbqi-interleave`, `mbqi-nested-check`, `mbqi-one-inst-per-round`, `miniscope-quant`, `miniscope-quant-user`, `multi-trigger-cache`, `multi-trigger-linear`, `multi-trigger-priority`, `multi-trigger-when-single`, `oracles`, `partial-triggers`, `pool-inst`, `pre-skolem-quant`, `pre-skolem-quant-nested`, `prenex-quant`, `prenex-quant-user`, `print-inst-full`, `purify-triggers`, `quant-alpha-equiv`, `quant-dsplit`, `quant-fun-wd`, `quant-ind`, `quant-rep-mode`, `register-quant-body-terms`, `relational-triggers`, `relevant-triggers`, `sub-cbqi`, `sub-cbqi-timeout`, `sygus`, `sygus-add-const-grammar`, `sygus-arg-relevant`, `sygus-auto-unfold`, `sygus-bool-ite-return-const`, `sygus-core-connective`, `sygus-crepair-abort`, `sygus-enum`, `sygus-enum-fast-num-consts`, `sygus-enum-random-p`, `sygus-eval-unfold`, `sygus-expr-miner-check-timeout`, `sygus-filter-sol`, `sygus-filter-sol-rev`, `sygus-grammar-cons`, `sygus-grammar-ho-partial`, `sygus-grammar-norm`, `sygus-grammar-use-disj`, `sygus-inference`, `sygus-inst`, `sygus-inst-mode`, `sygus-inst-scope`, `sygus-inst-term-sel`, `sygus-inv-templ`, `sygus-inv-templ-when-sg`, `sygus-min-grammar`, `sygus-out`, `sygus-pbe`, `sygus-pbe-multi-fair`, `sygus-pbe-multi-fair-diff`, `sygus-qe-preproc`, `sygus-query-gen`, `sygus-query-gen-dump-files`, `sygus-query-gen-filter-solved`, `sygus-query-gen-thresh`, `sygus-rec-fun`, `sygus-rec-fun-eval-limit`, `sygus-rec-fun-infer`, `sygus-repair-const`, `sygus-repair-const-timeout`, `sygus-rr-synth-accel`, `sygus-rr-synth-check`, `sygus-rr-synth-filter-cong`, `sygus-rr-synth-filter-match`, `sygus-rr-synth-filter-nl`, `sygus-rr-synth-filter-order`, `sygus-rr-synth-input-nvars`, `sygus-rr-synth-rec`, `sygus-sample-fp-uniform`, `sygus-sample-grammar`, `sygus-samples`, `sygus-si`, `sygus-si-abort`, `sygus-si-rcons`, `sygus-si-rcons-limit`, `sygus-si-rcons-p`, `sygus-stream`, `sygus-unif-cond-independent-no-repeat-sol`, `sygus-unif-pi`, `sygus-unif-shuffle-cond`, `sygus-verify-inst-max-rounds`, `sygus-verify-timeout`, `term-db-mode`, `trigger-active-sel`, `trigger-sel`, `user-pat`, `user-pool`, `var-elim-quant`, `var-ent-eq-elim-quant`, `var-ineq-elim-quant`

### Separation Logic Theory Module — 3

`sep`, `sep-min-refine`, `sep-pre-skolem-emp`

### Sets Theory Module — 4

`rels-exp`, `sets-card-exp`, `sets-exp`, `sets-proxy-lemmas`

### SMT Layer Module — 40

`abstract-values`, `ackermann`, `bv-to-int-use-pow2`, `bvand-integer-granularity`, `deep-restart`, `deep-restart-factor`, `difficulty-mode`, `early-ite-removal`, `ext-rew-prep`, `foreign-theory-rewrite`, `iand-mode`, `interpolants-mode`, `ite-simp`, `learned-rewrite`, `minimal-unsat-cores`, `model-cores`, `model-var-elim-uneval`, `on-repeat-ite-simp`, `output-print-defs`, `print-cores-full`, `produce-abducts`, `produce-assertions`, `produce-assignments`, `produce-difficulty`, `produce-interpolants`, `produce-learned-literals`, `proof-mode`, `repeat-simp`, `simp-ite-compress`, `simp-with-care`, `simplification`, `simplification-bcp`, `solve-bv-as-int`, `solve-int-as-bv`, `solve-real-as-int`, `sort-inference`, `static-learning`, `timeout-core-timeout`, `unconstrained-simp`, `unsat-cores-mode`

### Strings Theory Module — 26

`re-elim`, `re-first-class`, `re-inter-mode`, `seq-array`, `strings-alpha-card`, `strings-check-entail-len`, `strings-deq-ext`, `strings-eager-eval`, `strings-eager-len-re`, `strings-eager-reg`, `strings-eager-solver`, `strings-exp`, `strings-ff`, `strings-fmf`, `strings-infer-as-lemmas`, `strings-infer-sym`, `strings-lazy-pp`, `strings-len-norm`, `strings-mbr`, `strings-model-max-len`, `strings-process-loop-mode`, `strings-re-derive-conf`, `strings-re-posc-eager`, `strings-rec-arith-approx`, `strings-regexp-inclusion`, `strings-rexplain-lemmas`

### Theory Layer Module — 9

`assign-function-values`, `conflict-process`, `ee-mode`, `lemma-inprocess`, `lemma-inprocess-infer-eq-lit`, `lemma-inprocess-subs`, `relevance-filter`, `tc-mode`, `theoryof-mode`

### Uninterpreted Functions Theory Module — 12

`eager-arith-bv-conv`, `model-based-arith-bv-conv`, `symmetry-breaker`, `uf-card-exp`, `uf-ho-exp`, `uf-ho-ext`, `uf-lambda-qe`, `uf-lazy-ll`, `uf-ss`, `uf-ss-abort-card`, `uf-ss-fair`, `uf-ss-fair-monotone`

### Undocumented/Internal API — 1

`filename`
