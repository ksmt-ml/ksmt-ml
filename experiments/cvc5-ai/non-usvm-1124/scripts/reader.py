from pathlib import Path

def reader_edition(raw, root):
    head, body = raw.split('</style>', 1)
    sections = body.split('<main>', 1)[1].split('</main>', 1)[0]
    details, times = sections.rsplit('<section><h2>Хронометраж и файлы', 1)
    style = 'header,main{max-width:990px} h1{max-width:27ch}.keyline{padding:16px 20px}main>section{margin-bottom:32px} summary{padding:12px} table{min-width:0} @media(max-width:540px){th,td{padding:6px;font-size:.91rem}}'
    return (head + style + '</style>' + (root/'scripts/reader.html').read_text()
            + '<details><summary>Подробные измерения, источники данных и условия воспроизведения</summary>'
            + '<p>Обозначения в журналах: TRAIN — задачи для подбора, validation — промежуточная проверка, test — итоговая проверка. Default и A — заводские настройки, A′ — повтор с теми же настройками для оценки шума. Seed — номер независимой попытки поиска.</p>'
            + details + '</details><section><h2>Хронометраж и файлы' + times + '</main></html>\n')
