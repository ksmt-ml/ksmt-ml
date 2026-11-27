# Следующий multi-seed эксперимент v0

Цель этапа — получить доказательное сравнение Random Search, ES и SMAC на
одинаковом реальном workload, нескольких seed и независимых test-группах.
Этот запуск проверяет перенос одной fixed configuration с development workload
на новые нагрузки. Полный эксперимент `n% tuning / (100-n)% execution` будет
следующим этапом после положительного solver-level результата.

## Что будет создано

```text
EXPERIMENT_DIR/
    workloads-v0.tsv
    workloads/<group>/profiles
    workloads/<group>/baseline
    inputs/
    design/corpus-v0.json
    design/campaign-v0.json
    campaign-workspace-v0/
        calibration/
        runs/{random,evolution,smac}/seed-*/
        confirmation/
        reports/
```

Test-профили и их default baseline собираются заранее, но найденные кандидаты
на них не запускаются до финализации. Калибровка шума выполняется только на
`validation`, поэтому sealed test не открывается до завершения всех поисков.

## 0. Описать независимые workload groups

```bash
cd ksmt-ml

BLUE_PROJECT=$(cd .. && pwd)
EXPERIMENT="$BLUE_PROJECT/MULTI_SEED_EVIDENCE_EXPERIMENT_V0"
mkdir -p "$EXPERIMENT"
cp configs/workloads-v0.tsv.example \
  "$EXPERIMENT/workloads-v0.tsv"
```

Отредактировать `workloads-v0.tsv`. Поля разделяются настоящим TAB:

```text
partition  group-id  leakage-group-id  contract.fc  capture-seconds  method-count-hint  minimum-profiles
```

Для первого запуска требуется:

- ровно одна development-группа;
- минимум одна validation-группа;
- минимум три независимые test-группы, рекомендуется пять;
- каждая строка представляет независимое семейство и имеет уникальный
  `leakage-group-id`; связанные ревизии нельзя выдавать за независимые строки;
- один и тот же файл контракта не повторяется.

Значение `method-count-hint` должно соответствовать числу анализируемых методов:
общий capture budget делится на это число. Лучше задавать абсолютные пути к
контрактам.

## 1. Собрать профили

```bash
./scripts/capture-corpus-workloads-v0.sh \
  "$EXPERIMENT/workloads-v0.tsv" \
  "$EXPERIMENT"
```

Скрипт применяет существующие ограничения heap/native memory и не перезаписывает
готовый capture. После выполнения нужно остановиться и проверить каждый каталог
`workloads/<group>/profiles/dev`: профили закрыты, replayable, относятся к cvc5
1.3.4 и не содержат записанных profiler failures.

## 2. Построить default baselines и заморозить campaign

```bash
CAMPAIGN_SEEDS=17,23,31 \
SEARCH_WALL_CLOCK_SECONDS=1800 \
PER_TRIAL_TIMEOUT_SECONDS=600 \
CONFIRMATION_REPETITIONS=3 \
./scripts/prepare-multi-seed-campaign-v0.sh \
  "$EXPERIMENT" \
  cvc5-multi-workload-evidence-v0
```

Это тяжёлый шаг: он реально воспроизводит cvc5 default для каждой группы.
Скрипт замораживает TSA jar, parameter space и default candidate, создаёт corpus,
campaign manifest и run slots. Повторный запуск использует уже готовые полные
baseline artifacts, но отказывается принимать частичные или изменённые inputs.
Новый campaign по умолчанию использует objective
`workload-total-effective-cost-log-speedup-v0`: effective cost всех профилей
суммируется, поэтому короткий профиль не получает тот же вес, что и профиль,
занимающий большую часть времени.

После выполнения проверить все `default-manifest-v0.json` и
`default-report-v0.json`: нет wrong answers/replay failures, timeout трактуются
только согласно заранее зафиксированной policy.

## 3. Измерить шум на validation

Если workspace был создан со старым profile-macro objective, сначала создать
новый campaign, не пересобирая capture и baseline:

```bash
./scripts/fork-total-objective-campaign-v0.sh "$EXPERIMENT"
WORKSPACE="$EXPERIMENT/campaign-workspace-total-objective-v0"
```

Старые 24 отчёта можно пересчитать только для диагностической проверки формулы:

```bash
./scripts/rescore-default-noise-calibration-v0.sh \
  "$EXPERIMENT/campaign-workspace-v0/calibration/default-noise-validation-balanced-v0" \
  "$WORKSPACE/design/campaign-v0.json" \
  "$WORKSPACE/calibration/exploratory-rescore-v0"
```

Такой rescore не является независимым измерением и не открывает search gate.
После него всё равно выполняется новая калибровка:

```bash
CAMPAIGN_WORKSPACE="$WORKSPACE" \
CALIBRATION_WARMUPS=1 \
CALIBRATION_TRIAL_TIMEOUT_SECONDS=600 \
CALIBRATION_REPETITIONS=12 \
./scripts/calibrate-multi-seed-campaign-v0.sh "$EXPERIMENT"
```

Проверяем файл:

```text
campaign-workspace-total-objective-v0/calibration/default-noise-validation-balanced-v0/default-noise-summary-v0.json
```

Скрипт создаёт 12 парных повторов и строго балансирует порядок внутри пары:
шесть раз default запускается первым, шесть раз вторым. Это убирает постоянное
смещение прогрева/дрейфа. Bootstrap строится по парным повторам, а не по workload
groups: поэтому калибровочный интервал остаётся осмысленным и при единственной
validation-группе. В финальной confirmation статистической единицей по-прежнему
является независимая workload group.

Артефакт отдельно содержит интервалы для текущего replay proxy, полного solver
time и wall time. Автоматический gate требует:

- не менее 12 завершённых пар без wrong answers, известных timeout и replay
  failures;
- чтобы каждый из трёх 95% интервалов содержал ноль;
- чтобы абсолютное смещение медианы не превышало четверти целевого эффекта 5%;
- чтобы ширина интервала не превышала целевой эффект 5% в log-space.

При провале gate скрипт завершается с ошибкой и поиск запускать нельзя. Старый
каталог `default-noise-validation-v0` не переиспользуется и остаётся
диагностическим артефактом. Сначала устраняются дрейф/шум или усиливается
workload, затем калибровка повторяется в новом каталоге; менять замороженный
campaign после просмотра test запрещено. Скрипты запуска каждого seed также
проверяют привязку summary к campaign и отказываются запускать поиск без
успешного gate, поэтому этот этап нельзя случайно пропустить.

## 4. Запустить алгоритмы

Каждый алгоритм использует те же seed, frozen objective и budget. По умолчанию три seed по
30 минут означают примерно 90 минут поиска на один алгоритм без учёта replay
overhead.

```bash
WORKSPACE="$EXPERIMENT/campaign-workspace-total-objective-v0"

./scripts/run-multi-seed-algorithm-v0.sh \
  "$WORKSPACE" random "$EXPERIMENT/inputs"

./scripts/run-multi-seed-algorithm-v0.sh \
  "$WORKSPACE" evolution "$EXPERIMENT/inputs"

./scripts/run-multi-seed-algorithm-v0.sh \
  "$WORKSPACE" smac "$EXPERIMENT/inputs"
```

Для поштучного запуска или восстановления одного seed:

```bash
./scripts/run-multi-seed-slot-v0.sh \
  "$WORKSPACE" random 17 "$EXPERIMENT/inputs"
```

Run directories resume-safe. Не удалять `state.json`, `events.jsonl`, cache или
trial artifacts между продолжениями. После каждого алгоритма стоит отдельно
проверить, что все его seed имеют `status=FINISHED`.

## 5. Единожды открыть sealed test и финализировать

Только когда завершены все объявленные seed:

Сначала без replay строится qualification index. Каждый slot получает явный
статус `QUALIFIED` либо `NO_FEASIBLE_CANDIDATE`. Отсутствие incumbent является
валидным отрицательным результатом, а не повреждением кампании. SMAC incumbent
квалифицируется только после успешного прохождения всех development-профилей;
частичные instance-level costs и кандидаты с timeout/native crash в sealed test
не допускаются.

До открытия test можно безопасно проверить состав финалистов и расписание:

```bash
WORKSPACE="$EXPERIMENT/campaign-workspace-total-objective-v0"

PYTHONPATH=src .venv/bin/python -m ksmt_ml.multi_seed_campaign \
  collect-finalists --workspace "$WORKSPACE"
PYTHONPATH=src .venv/bin/python -m ksmt_ml.multi_seed_campaign \
  summarize-anytime --workspace "$WORKSPACE"
PYTHONPATH=src .venv/bin/python -m ksmt_ml.multi_seed_campaign \
  prepare-confirmation --workspace "$WORKSPACE"
```

Проверяем `confirmation/finalists/index-v0.json`, `confirmation/schedule-v0.json`
и `confirmation/candidate-index-v0.json`. Эти команды не запускают solver и не
снимают измерения с test workload.

После проверки:

```bash
CONFIRMATION_WARMUPS=1 \
CONFIRMATION_TRIAL_TIMEOUT_SECONDS=600 \
./scripts/finalize-campaign-experiment-v0.sh "$EXPERIMENT"
```

Финализация:

1. проверяет одинаковые budget и development baseline всех запусков;
2. извлекает incumbent каждого seed без cherry-picking;
3. строит общий randomized interleaved test schedule;
4. resume-safe измеряет `default/candidate` пары;
5. создаёт grouped bootstrap и anytime summaries.

Основные результаты:

```text
campaign-workspace-v0/reports/anytime-summary-v0.json
campaign-workspace-v0/reports/confirmation-summary-v0.json
campaign-workspace-v0/confirmation/run/observations-v0.json
```

Положительный результат требует одновременно нулевых wrong answers/failures,
объявленного минимального эффекта, достаточной доли положительных независимых
test-групп и положительной нижней границы paired bootstrap interval. Один удачный
seed или ускорение на отдельных solver checks доказательством не считаются.

## 6. Диагностика после первого sealed confirmation

Первый confirmation v0 завершился отрицательно: корректный finalist seed 17 не
ускорил общий test corpus, а seed 23 дал сильное локальное ускорение, но нарушил
корректность на `restricted-wallet`. После открытия эти test workload считаются
диагностическими и не могут повторно использоваться для финального заявления.

Следующий шаг — exhaustive ablation всех 16 подмножеств четырёх явно заданных
опций seed 23. Сначала запускается короткий correctness-screen:

```bash
PYTHON_BIN="$PWD/.venv/bin/python" \
./scripts/run-seed23-correctness-ablation-v0.sh "$EXPERIMENT"
```

Команда resume-safe сохраняет каждый replay и итоговый
`diagnostics/seed23-option-subset-ablation-v0/restricted-wallet-correctness-v0/results-v0.json`.
Только подмножества без wrong answers, timeout и replay failures допускаются к
отдельному performance-screen на `highload-wallet`. После фиксации безопасного
parameter space требуется новый поиск и новый sealed test corpus.

После проверки correctness-отчёта performance-screen запускается отдельно:

```bash
PYTHON_BIN="$PWD/.venv/bin/python" \
./scripts/run-seed23-performance-ablation-v0.sh "$EXPERIMENT"
```

Runner читает решения correctness-screen и физически не запускает отклонённые
подмножества. Три повтора и свежий default-subset дают диагностическую оценку
эффекта, но не заменяют новое sealed confirmation.
