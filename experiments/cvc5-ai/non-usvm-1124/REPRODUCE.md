# Воспроизведение · 24 ноября 2026

Рабочий каталог `non-usvm-1124`. В начале прочтите `CHECKPOINT.md` и
`current.json`. Все измерения используют CPU 12–15, по одному номеру
логического CPU на физическое ядро. cvc5 1.4.1, Yosys 0.23, Z3 и Dafny
загружаются в `deps/`, SMAC3 2.4.0 берётся только для чтения из соседнего
окружения. Установки в ОС нет. Перед измерением запишите часы хоста в МСК.

```bash
export TZ=Europe/Moscow PYTHONDONTWRITEBYTECODE=1
date -Is
scripts/fetch-deps.sh
sha256sum -c evidence/deps.sha256
sha256sum -c protocols/holdout-generators.sha256
python -B scripts/report.py
```

Системный Python использует Linux pidfd для точного ожидания процесса.
Окружение Python 3.12 из SMAC обращается к тому же pidfd через libc.
Каждый сырой сеанс имеет `start.json`, `process.json` и `result.json`.
Прерванные сырые каталоги сохраняются под новым именем. В каталогах
`raw/phases/` лежат start/stop с кодом выхода, аргументы, логи, PID и lock.
Код поиска восстанавливает нативную историю ask/tell из завершённых
оценок, до исходного срока раунда. Если завершённый раунд с ошибкой
нужно повторить, сначала сохраните целиком его каталог и явно отметьте
новую попытку. Не переносите результаты между одинаково названными
попытками. Задание не измеряет ничего после 11:08 МСК по умолчанию.
Для самостоятельного нового воспроизведения в отдельном рабочем пространстве
можно установить `CVC_CAMPAIGN_CUTOFF_MSK` в ISO 8601, обязательно с UTC offset.

## Пробы и первичная панель

Старые пробные замеры служат диагностикой квантизации таймера. Основные
панели и точные команды последовательно воспроизводятся ниже. Следите,
чтобы работающие фазы не делили одни и те же ядра.

```bash
taskset -c 12 python -B scripts/rtl.py train
taskset -c 14 python -B scripts/constraints.py train
taskset -c 12,13 python -B scripts/eval.py screen hw --cpus 12 13 --cap 7 --reps 2 \
  --arms 0 AA gauss bool prop minisat static repeat ite eager-eval pow2 abstraction simpl-none no-eq internal
taskset -c 14,15 python -B scripts/eval.py screen opt --cpus 14 15 --cap 7 --reps 2
taskset -c 14,15 python -B scripts/eval.py oracle opt --cpus 14 15 --cap 35
python -B scripts/dafny_data.py
```

Проверка одинаковых команд провода Yosys и аннотированного dump использует
отдельную обёртку. Замеры настройки используют штатный сокет stdin/stdout
процесса решателя, запись dump и ответов из самого yosys-smtbmc. Контрольное
чтение SMT потока Z3 не входит во время живого сеанса.

```bash
scripts/run_phase.sh hw-wire-train taskset -c 12-15 python -B scripts/hw_audit.py train
```

## Поиск, замок, тест

На `protocols/opt-search.json` и `protocols/hw-search.json` указаны
только обучающие входы, зафиксированные пределы и опции. План Yosys
запускается после соответствующего TRAIN решения. Логи модели
сохраняют запрос, JSON события, структурированный ответ и точный timeout,
а временное состояние клиента живёт в tmpfs. Не копируйте учётные данные.
Получение ответов использует авторизацию ChatGPT, конфиг и плагины
пользователя скрыты, инструменты Sol запрещены.

```bash
scripts/run_phase.sh opt-preflight taskset -c 14,15 python -B scripts/combo_preflight.py \
  protocols/opt-search.json machine-shop-4-sat machine-shop-4-unsat
scripts/run_phase.sh opt-search taskset -c 12-15 ../campaign/.venv/bin/python -B scripts/search.py protocols/opt-search.json
scripts/run_phase.sh opt-validation taskset -c 12-15 python -B scripts/study.py \
  validation protocols/opt-search.json --contrast=--ite-simp
scripts/run_phase.sh opt-test taskset -c 12-15 python -B scripts/study.py test protocols/opt-search.json
```

Фаза validation не раскроет test. Фаза test читает SHA артефакта lock
и проверяет SHA зафиксированных генераторов. Seed появляется впервые
при открытии и затем переиспользуется. Прямой запуск генераторов test
также требует этот release. При восстановлении завершённые seанс/блок
и закрытый выбор не пересчитываются. Вывод отчёта и статус атомарны.

SMT-LIB public архивы имеют штатную MD5 с Zenodo и собственную SHA-256.
Выбирайте их только по рецепту `scripts/public.py`, отдельно от primary
и только после lock для любых измерений. `evidence/access/` содержит
историю получения, в том числе неуспешные попытки.

```bash
python -B scripts/analyze.py
python -B scripts/report.py
```

## Продолжение и проверки

```bash
scripts/run_phase.sh continuation scripts/continue.sh
scripts/run_phase.sh followups scripts/followups.sh
python -B scripts/oracle_audit.py
python -B scripts/verify_artifacts.py --deep
python -B scripts/option_effects.py
python -B scripts/report.py
```

`continue.sh` и `followups.sh` можно запустить заранее: условие барьера —
завершённый обязательный эксперимент и lock, а не наступление времени.
Они сохраняют отметки завершённых этапов в `evidence/*.complete`.
Не запускайте второй экземпляр каждого из этих исполнителей.
Для пилота Why3 нужны оригинальные данные Debian в `deps/why3`,
их контрольные суммы в `protocols/why3-transfer.sha256`. Установка в ОС
не нужна, пути пакета предоставляются через mount namespace.

Дополнительные зафиксированные проверки запускаются последовательно
после основной очереди, при наличии времени. Для уже завершённой кампании
достаточно читать готовые журналы и сверять их хеши.

```bash
scripts/run_phase.sh late scripts/late.sh
scripts/run_phase.sh diagnostics-retry scripts/diagnostics-retry.sh
sha256sum -c protocols/hw-search-runtime.sha256
sha256sum -c protocols/09-hw-validation.sha256
sha256sum -c protocols/why3-transfer.sha256
sha256sum -c evidence/deliverables.sha256
```

Краткая трактовка в `evidence/INTERPRETATION.md`, свойства и алгоритмы
оракулов в `evidence/ORACLES.md`. Сбои генераторов программ с
хронометражем перечислены в `evidence/program-retries.md`.

```bash
# Аналитические проверки без нового сбора.
python -B scripts/uncertainty_audit.py
python -B scripts/verify_artifacts.py --deep
sha256sum -c evidence/deliverables.sha256
```

Итоговый отчёт хешируется после окончательной отрисовки. Перерисовка
меняет служебное время и SHA HTML. Для проверки опубликованного отчёта
используйте итоговый хеш до нового рендера.
