# Архив экспериментов cvc5 v0

| Набор | Результат | Данные |
|---|---|---|
| Historical smoke | Replay и confirmation отработали. Ни один вариант не прошёл holdout gate. | [Provenance](historical-smoke-v0/provenance-summary.json), [confirmation](historical-smoke-v0/confirmation-summary.json), [SHA-256 финального прогона](historical-smoke-v0/final-sha256s-v0.txt) |
| Multi-seed | 9 поисков, 36 confirmation trials, 16 correctness и 12 performance variants. Общего ускорения не подтверждено. | [Отчёт](multi-seed-v0/aggregate-report-v0.json), [исторический runbook](../next-multi-seed-experiment-runbook-ru.md) |

В multi-seed Random 17 прошёл проверку корректности и дал общий speedup 0,9888×.
Random 23 отклонён после 12 неверных ответов. Ablation воспроизвела ошибку в
четырёх подмножествах. Все четыре содержат `ee-mode=central` и
`theoryof-mode=term`.

[Замороженный parameter space](multi-seed-v0/frozen/cvc5-regular-bv-parameter-space-v0.json)
сохраняет исходные байты для диагностического replay. Он содержит небезопасное
сочетание. Текущий [guarded parameter space](../../configs/cvc5-regular-bv-parameter-space-v0.json)
имеет отдельный идентификатор и запрещает эту пару.

Полные наборы из исходной рабочей директории упакованы отдельно в `.tar.zst`.
В Git лежат [метаданные smoke](historical-smoke-v0/archive.json) и
[метаданные multi-seed](multi-seed-v0/archive.json), включая SHA-256 и размер
архивов. После распаковки выполните `sha256sum --strict -c /path/to/FILES.sha256`
из корня соответствующего набора, используя его ведомость
[smoke](historical-smoke-v0/FILES.sha256) или [multi-seed](multi-seed-v0/FILES.sha256).
Пути в обоих файлах относительные. Содержимое архивов проверено по каждому
исходному файлу. Обзорные JSON из smoke используют имена файлов вместо путей
рабочей машины. Точные оригиналы находятся в полном архиве.
