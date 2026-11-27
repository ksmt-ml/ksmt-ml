# End-to-end эксперимент на demo-ветках

Эта инструкция проверяет экспериментальный контур:

```text
TSA workload
  -> KSMT solver profiles
  -> default baseline
  -> Random Search / evolutionary search / SMAC
  -> finalists
  -> replay на holdout
  -> единый summary и correctness/performance gate
```

Контур предназначен для экспериментов и сверки production-реализаций. Скрипты,
Python-модули, run history и прямой запуск TSA не являются production API.

## 1. Подготовка checkout

Четыре репозитория должны лежать рядом:

```text
blue-project/
  ksmt/
  ksmt-ml-tuner/
  ksmt-ml/
  tsa/
```

Во всех четырёх репозиториях должна быть выбрана ветка
`dev/litvinovmitch11/demo`. Из `ksmt-ml` это можно проверить так:

```bash
for repo in ../ksmt ../ksmt-ml-tuner . ../tsa; do
  git -C "$repo" status --short --branch
done
```

Проверенный набор инструментов: JDK 17, Python 3.12, GNU Make, `jq`, GNU
`timeout` и Gradle wrapper из TSA. Для SMAC нужны зафиксированные версии из
`requirements-search.txt`:

```bash
make search-env
make check
```

`make search-env` создаёт `.venv` и устанавливает ConfigSpace 1.2.2 и SMAC
2.4.1. Сгенерированные профили и run history сохраняются под `experiments/` и
игнорируются Git.

## 2. Выбор campaign и workload

Задайте новый идентификатор. Старый campaign нельзя молча переиспользовать для
нового набора профилей:

```bash
CAMPAIGN=my-cvc5-campaign-v0
DEV_CONTRACT=/absolute/path/to/dev-contract.fc
HOLDOUT_CONTRACT=/absolute/path/to/independent-holdout-contract.fc
```

Development и holdout должны быть независимыми workload-группами. Нельзя
делить отдельные solver checks одного запуска между ними. До claim-bearing
эксперимента также зафиксируйте ревизии четырёх репозиториев, contracts, seed,
таймауты, бюджеты и число повторений.

По умолчанию capture анализирует два receiver-метода и делит переданный
wall-clock budget между ними. Для анализа всех методов задайте
`CAPTURE_TARGET=all` и точный `CAPTURE_METHOD_COUNT_HINT`.

## 3. Сбор development и holdout профилей

Production-подобный запуск требует доступного пользовательского systemd scope:

```bash
scripts/capture-func-workload-v0.sh \
  "$CAMPAIGN" dev "$DEV_CONTRACT" 1800

scripts/capture-func-workload-v0.sh \
  "$CAMPAIGN" holdout "$HOLDOUT_CONTRACT" 1800
```

Скрипт:

- собирает TSA с `-PuseLocalTuningContracts=true`, то есть против соседних
  demo-checkout KSMT и tuner;
- запускает каждый capture в отдельном JVM;
- записывает application, integer-blasted и regular-bv профили;
- принимает dataset только если профили закрыты, replayable и содержат хотя бы
  один complete regular-bv профиль;
- отвергает partial capture, hard timeout и interpreter failure.

Если user systemd недоступен, для локальной проверки проводки можно явно
отключить native-memory scope:

```bash
CAPTURE_USE_SYSTEMD_SCOPE=0 CAPTURE_MAX_HEAP_MB=4096 \
  scripts/capture-func-workload-v0.sh \
  "$CAMPAIGN" dev "$DEV_CONTRACT" 60
```

Это допустимо только для smoke-проверки: в таком режиме ограничен heap JVM, но
не вся native memory процесса.

Профили появятся здесь:

```text
experiments/runs/campaigns/$CAMPAIGN/profiles/dev/
experiments/runs/campaigns/$CAMPAIGN/profiles/holdout/
```

## 4. Default baseline

Baseline запускается только на development-профилях и использует пустой patch,
то есть штатные defaults cvc5 1.3.4:

```bash
DEV_BASELINE_REPETITIONS=3 \
  scripts/run-tuning-campaign-v0.sh baseline "$CAMPAIGN"
```

Основные артефакты:

```text
baseline/dev-default-v0.json
baseline/dev-default-manifest-v0.json
```

Manifest связывает baseline с точными SHA-256 parameter space, TSA CLI jar и
каждого профиля. Если оба baseline-файла уже существуют, скрипт сохраняет их,
а не переизмеряет выборочно.

`UNKNOWN`, полученный default cvc5 по достижении записанного solver timeout,
сохраняется как censored observation, а не как неверный ответ. Его стоимость
считается по зафиксированному PAR10-подобному правилу: из измерения вычитается
фактическая длительность timeout и добавляется `10 × timeout budget`. Wrong
answer и replay failure по-прежнему запрещены в baseline. Для проверяемого
кандидата любой timeout известного SAT/UNSAT, wrong answer или replay failure
остаётся hard failure.

## 5. RS, ES и SMAC

Все три алгоритма используют один evaluator, один development workload,
одинаковый wall-clock budget, timeout и seed:

```bash
SEARCH_BUDGET_SECONDS=1800 \
PER_TRIAL_TIMEOUT_SECONDS=600 \
MAX_EVALUATIONS=1000000 \
SEARCH_SEED=17 \
EVOLUTION_POPULATION_SIZE=4 \
SMAC_INITIAL_CONFIGURATIONS=8 \
  scripts/run-tuning-campaign-v0.sh search "$CAMPAIGN"
```

Перед стартом скрипт проверяет, что заявленного бюджета хватает минимум на две
baseline-sized ES population. Порядок по умолчанию — `random,evolution,smac`.
Его можно заранее рандомизировать через `SEARCH_ORDER`, но переменная должна
содержать все три имени ровно по одному разу.

Run history и resumable state находятся в:

```text
search/random-seed17-v0/
search/evolution-seed17-v0/
search/smac-seed17-v0/
```

Проверка завершения:

```bash
for strategy in random evolution smac; do
  jq '{status, stopReason, completedTrialCount, actualEvaluationCount,
       cacheHitCount, incumbent}' \
    "experiments/runs/campaigns/$CAMPAIGN/search/${strategy}-seed17-v0/state.json"
done
```

## 6. Holdout confirmation

Только после завершения поиска откройте holdout:

```bash
SEARCH_SEED=17 CONFIRMATION_REPETITIONS=3 \
  scripts/run-tuning-campaign-v0.sh confirm "$CAMPAIGN"
```

Шаг строит default baseline holdout, извлекает incumbents RS/ES/SMAC,
повторно запускает каждого на полном holdout и формирует:

```text
finalists/{random,evolution,smac}-candidate-v0.json
confirmation/{random,evolution,smac}-report-v0.json
confirmation/{random,evolution,smac}-score-v0.json
confirmation/summary-v0.json
```

Краткий просмотр результата:

```bash
jq '.results[] | {
  candidateId,
  eligible,
  profileCount,
  geometricSpeedup,
  wrongAnswerCount,
  knownStatusTimeoutCount,
  failedReplayCount,
  passesReplayGainGate
}' "experiments/runs/campaigns/$CAMPAIGN/confirmation/summary-v0.json"
```

Retained candidate обязан иметь ноль wrong answers, timeout-ов известных
ответов и failed replay. `passesReplayGainGate=true` дополнительно требует
заранее объявленное улучшение, достаточное число workload-профилей, нужную долю
положительных профилей и положительную нижнюю границу paired bootstrap interval.

Этот summary подтверждает только replay-level gain. Перенос ускорения в полное
время TSA требует отдельного свежего запуска TSA с уже выбранной конфигурацией;
текущий demo-контур такой performance gate не автоматизирует.

## 7. Один вызов после capture

После того как обе группы профилей собраны, baseline, три поиска и confirmation
можно выполнить одной командой:

```bash
SEARCH_BUDGET_SECONDS=1800 \
PER_TRIAL_TIMEOUT_SECONDS=600 \
SEARCH_SEED=17 \
CONFIRMATION_REPETITIONS=3 \
  scripts/run-tuning-campaign-v0.sh all "$CAMPAIGN"
```

## 8. Быстрый smoke-test проводки

Следующие параметры проверяют только совместимость четырёх demo-веток. Они не
дают статистически осмысленной оценки качества алгоритмов:

```bash
SEARCH_BUDGET_SECONDS=20 \
PER_TRIAL_TIMEOUT_SECONDS=10 \
MAX_EVALUATIONS=4 \
EVOLUTION_POPULATION_SIZE=2 \
SMAC_INITIAL_CONFIGURATIONS=1 \
SEARCH_SEED=17 \
CONFIRMATION_REPETITIONS=1 \
MINIMUM_HOLDOUT_PROFILE_COUNT=2 \
MINIMUM_HOLDOUT_IMPROVEMENT=0.000001 \
MINIMUM_POSITIVE_PROFILE_FRACTION=0.000001 \
  scripts/run-tuning-campaign-v0.sh all "$CAMPAIGN"
```

Для smoke достаточно завершения всех стадий и отсутствия correctness failures.
Отрицательный performance-gate на таком запуске является нормальным результатом.

## 9. Когда начинать новый campaign

Используйте новый ID, если изменился хотя бы один из следующих входов:

- contract или dev/holdout split;
- одна из четырёх ревизий кода;
- версия cvc5, profile schema или parameter-space ID;
- evaluator objective, timeout, repetitions или host policy;
- search algorithm, seed или stopping rule.

Не коммитьте содержимое `experiments/`: raw formulas, профили, кеши и полный
run history должны оставаться вне Git.
