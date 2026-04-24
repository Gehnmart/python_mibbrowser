"""
Tiny dict-based internationalisation.

Usage:
    from pymibbrowser.i18n import _t
    label = _t("Filter MIB tree (substring)…")

init_language() is called from main(); it picks "ru" if $LANG starts with
"ru", otherwise English (identity). The global language can be switched at
runtime via set_language("ru" | "en"); widgets that cache localised text need
to be rebuilt.
"""
from __future__ import annotations

import os

_RU = {
    # Window titles / menu
    "MIB Browser (Python)": "MIB Browser (Python)",
    "&File":                "&Файл",
    "&Edit":                "&Правка",
    "&Operations":          "&Операции",
    "&Tools":               "&Инструменты",
    "&Help":                "&Помощь",

    # File menu
    "Load MIB…":            "Загрузить MIB…",
    "MIB Modules…":         "Модули MIB…",
    "MIB Modules":          "Модули MIB",
    "Rebuild MIB cache":    "Пересобрать кэш MIB",
    "Recompile all MIBs…":  "Пересобрать все MIB…",
    "This wipes the compiled cache and recompiles every MIB file found under mibs-src/ (and any extra source dirs). Can take 30–90s. Active MIB tree will reload afterwards.":
        "Будет очищен кэш и заново скомпилированы все файлы MIB из mibs-src/ (и добавленных папок). Займёт 30–90 секунд. После компиляции дерево MIB будет перезагружено.",

    # MIB Modules dialog
    "Enabled":              "Вкл.",
    "Module":               "Модуль",
    "Size":                 "Размер",
    "Filter…":              "Фильтр…",
    "All":                  "Все",
    "None":                 "Никакие",
    "Invert":               "Инверсия",
    "Only vendor (no RFC/SNMPv2)": "Только вендор (без RFC/SNMPv2)",
    "Select vendor only":   "Только вендор",
    "Enable every vendor (enterprise) MIB and disable RFC/SNMPv2 ones. Useful when you opened a device-specific module and want its sibling vendor MIBs enabled too.":
        "Включить все вендорские (enterprise) MIB и отключить RFC/SNMPv2. Полезно, когда открыли модуль производителя и хотите включить остальные вендорские рядом.",
    "Delete the compiled JSON file(s) for the selected rows. Irreversible — to re-add, use File → Load MIB.":
        "Удалить скомпилированные JSON-файлы для выбранных строк. Необратимо — чтобы вернуть, используйте Файл → Загрузить MIB.",
    "{enabled} of {total} modules enabled":
        "Включено модулей: {enabled} из {total}",
    "Unload selected":      "Выгрузить выбранные",
    "Framework module — used by many others.":
        "Framework-модуль — используется многими другими.",
    "Delete compiled files for these modules?":
        "Удалить скомпилированные файлы этих модулей?",
    "Warning: unloading framework modules ({}) will break others.":
        "Внимание: выгрузка framework-модулей ({}) сломает зависящие от них.",
    "Organization":         "Организация",
    "Contact":              "Контакт",
    "Last updated":         "Обновлено",
    "Revisions":            "Ревизии",
    "Imports from":         "Импортирует из",
    "Imported by":          "Импортируется модулями",
    "Definitions":          "Определения",
    "File Name":            "Имя файла",
    "No revisions recorded.": "Ревизии не указаны.",
    "No imports / dependents.": "Нет импортов / зависимостей.",
    "Imports":              "Импорты",
    "Not in the compiled cache. Use File → Load MIB to add it.":
        "Модуля нет в кэше компиляции. Добавьте через Файл → Загрузить MIB.",
    "Built-in framework module": "Встроенный framework-модуль",
    "Open log file…":       "Открыть лог…",
    "Save results as CSV…": "Сохранить результаты в CSV…",
    "Run / Refresh":        "Выполнить / Обновить",
    "Clear results":        "Очистить результаты",
    "Community":            "Community",
    "Expand all":           "Развернуть всё",
    "Collapse all":         "Свернуть всё",
    "Manage agents…":       "Агенты…",
    "Manage agents":        "Агенты",
    "Add":                  "Добавить",
    "Duplicate":            "Дублировать",
    "Remove":               "Удалить",
    "Use selected":         "Использовать",

    # Table View toolbar
    "Refresh":              "Обновить",
    "Rotate":               "Повернуть",
    "Export CSV":           "Экспорт CSV",
    "—":                    "—",

    # Graph toolbar
    "⏸ Pause":              "⏸ Пауза",
    "↻ Restart":            "↻ Перезапуск",
    "Save PNG":             "PNG",
    "Import CSV":           "Импорт CSV",

    # Trap Sender dialog
    "Trap Sender":          "Отправка трапа",
    "+ Add var-bind":       "+ Добавить var-bind",
    "− Remove":             "− Удалить",
    "Send trap":            "Отправить",
    "Close":                "Закрыть",
    "Variable bindings:":   "Переменные (var-bindings):",

    # Agent simulator / MIB Editor / Trapd console
    "SNMP Agent Simulator": "Симулятор SNMP-агента",
    "0.0.0.0 to listen on all interfaces": "0.0.0.0 — слушать на всех интерфейсах",
    "Browse…":              "Обзор…",
    "Start":                "Старт",
    "Stop":                 "Стоп",
    "stopped":              "остановлен",
    "MIB Editor":           "Редактор MIB",
    "Open…":                "Открыть…",
    "Save":                 "Сохранить",
    "Save as…":             "Сохранить как…",
    "Parse check":          "Проверить синтаксис",
    "Trapd console":        "Trapd консоль",
    "Clear":                "Очистить",
    "Port:":                "Порт:",

    # Trap Receiver main-window
    "Trap Receiver":        "Приёмник трапов",
    "Rules…":               "Правила…",
    "Save…":                "Сохранить…",
    "Trap rules":           "Правила трапов",
    "Add":                  "Добавить",
    "Edit":                 "Изменить",
    "Delete":               "Удалить",

    # Agent properties + SET
    "Agent properties":     "Свойства агента",

    # MIB Load dialog
    "Add source directory…": "Добавить папку с MIB…",
    "Compile":              "Компилировать",
    "Save session (CSV)…":  "Сохранить сессию (CSV)…",
    "Exit":                 "Выход",

    # AgentDialog form rows
    "Host":                 "Хост",
    "Port":                 "Порт",
    "Version":              "Версия",
    "Timeout (s)":          "Таймаут (с)",
    "Retries":              "Повторы",
    "Read community":       "Read community",
    "Write community":      "Write community",
    "Max repetitions (bulk)": "Max repetitions (bulk)",
    "Non repeaters (bulk)": "Non repeaters (bulk)",
    "User":                 "Пользователь",
    "Auth proto":           "Auth-протокол",
    "Auth password":        "Auth-пароль",
    "Priv proto":           "Priv-протокол",
    "Priv password":        "Priv-пароль",
    "General":              "Общие",
    "SNMPv3 (passthrough)": "SNMPv3 (passthrough)",

    # Trap Rule dialog
    "Trap OID matches":     "Trap OID (маска)",
    "Source IPs allowed":   "Разрешённые IP",
    "Payload contains":     "Payload содержит",
    "Action":               "Действие",
    "Actions":              "Действия",
    "Conditions":           "Условия",
    "Add Rule":             "Добавить правило",
    "Trap rule":            "Правило трапа",
    "Run command":          "Выполнить команду",
    "Play sound":           "Проиграть звук",
    "Templates in Message / Run command: {oid}, {src}, {sev}, {msg}, {name}":
        "Шаблоны в Message / Run command: {oid}, {src}, {sev}, {msg}, {name}",
    "Set severity":         "Уровень",
    "Set message":          "Сообщение",

    # Trap sender form
    "Receiver host":        "Хост приёмника",
    "Trap OID":             "OID трапа",

    # Agent sim form
    "Bind host":            "Интерфейс",
    "Walk file":            "Walk-файл",

    # SET dialog
    "Type":                 "Тип",
    "Value":                "Значение",

    # Result/Trap table column headers
    "Name/OID":             "Имя/OID",
    "IP:Port":              "IP:Порт",
    "Time":                 "Время",
    "Source":               "Источник",
    "Severity":             "Уровень",
    "Message":              "Сообщение",

    # Edit menu
    "Find":                 "Найти",
    "Find in MIB tree":     "Поиск в MIB дереве",
    "Find in Result":       "Поиск в результатах",

    # Operations
    "Table View…":          "Вид таблицы…",
    "Graph…":               "График…",
    "Get":                  "Get",
    "Get Next":             "Get Next",
    "Get Bulk":             "Get Bulk",
    "Get Subtree":          "Get Subtree",
    "Walk":                 "Walk",
    "Set":                  "Set",

    # Tools
    "Trap Receiver…":       "Приём трапов…",
    "Trap Sender…":         "Отправка трапа…",
    "Agent Simulator…":     "Симулятор агента…",
    "MIB Editor…":          "Редактор MIB…",
    "Trapd Console…":       "Консоль Trapd…",
    "Trap daemon log…":     "Журнал трап-демона…",
    "Trap daemon log":      "Журнал трап-демона",
    "Run Script…":          "Запустить скрипт…",
    "Run Script":           "Запустить скрипт",

    # Network tools
    "Ping":                 "Ping",
    "Ping…":                "Ping…",
    "Ping ▶":               "Ping ▶",
    "Traceroute":           "Traceroute",
    "Traceroute…":          "Traceroute…",
    "Trace ▶":              "Trace ▶",
    "Network Discovery":    "Обнаружение сети",
    "Network Discovery…":   "Обнаружение сети…",
    "Discover ▶":           "Сканировать ▶",
    "Host":                 "Хост",
    "Count":                "Количество",
    "Max hops":             "Макс. хопов",
    "Running…":             "Выполняется…",
    "Finished (exit {code})": "Готово (код {code})",
    "Failed":               "Ошибка",
    "Neither tracepath nor traceroute is installed. On Debian/Ubuntu: sudo apt install iputils-tracepath or traceroute.":
        "Не установлены ни tracepath, ни traceroute. На Debian/Ubuntu: sudo apt install iputils-tracepath или traceroute.",
    "Subnet (CIDR)":        "Подсеть (CIDR)",
    "Probe SNMP (sysName / sysDescr)":
        "Опрашивать SNMP (sysName / sysDescr)",
    "Sweeping…":            "Сканирование…",
    "Scanned {done} / {total}": "Проверено: {done} / {total}",
    "Found {live} live host(s)": "Найдено хостов: {live}",
    "IP":                   "IP",
    "RTT (ms)":             "RTT (мс)",
    "Add selected to agents": "Добавить выбранные в агенты",
    "Create a saved-agent entry for each ticked row using your current community/version defaults. Duplicates (same host:port) are skipped.":
        "Создать запись сохранённого агента для каждой отмеченной строки с вашими текущими значениями community/версии. Дубли (host:port) пропускаются.",
    "Added {added} agent(s), skipped {skipped} duplicate(s).":
        "Добавлено агентов: {added}, пропущено дублей: {skipped}.",

    # Save walk / Compare
    "Save walk to file":    "Сохранить walk в файл",
    "Save walk to file…":   "Сохранить walk в файл…",
    "Walk a subtree of {host}:{port} and save the result as a snmpwalk-compatible text file. Use it offline for diagnosis, share with colleagues, or load it into Agent Simulator as a mock device.":
        "Обойти поддерево {host}:{port} и сохранить результат в snmpwalk-совместимый текстовый файл. Для диагностики офлайн, обмена с коллегами или загрузки в Симулятор агента.",
    "Starting OID":         "Начальный OID",
    "Walk ▶":               "Walk ▶",
    "Walking…":             "Обход…",
    "Walked {n} rows…":     "Обойдено строк: {n}…",
    "Walk finished — {n} rows": "Обход завершён — строк: {n}",
    "Walk output will appear here. Click 'Save…' to write to disk.":
        "Здесь появится вывод walk'а. Нажмите «Сохранить…» для записи в файл.",
    "Saved to":             "Сохранено в",
    "rows":                 "строк",
    "Compare devices":      "Сравнить устройства",
    "Compare devices…":     "Сравнить устройства…",
    "Compare ▶":            "Сравнить ▶",
    "Left":                 "Слева",
    "Right":                "Справа",
    "Live agent":           "Живой агент",
    "Walk file":            "Walk-файл",
    "Path to .walk file":   "Путь к .walk файлу",
    "Subtree OID":          "OID поддерева",
    "Hide equal rows":      "Скрывать одинаковые строки",
    "Walking both sources…": "Обход обоих источников…",
    "Equal: {eq} · Diff: {df} · Only left: {ol} · Only right: {orr}":
        "Одинаковых: {eq} · Различаются: {df} · Только слева: {ol} · Только справа: {orr}",

    # Watches history
    "History…":             "История…",
    "Watch history":        "История наблюдений",
    "Clear history":        "Очистить историю",
    "Substring in any column": "Подстрока в любом столбце",
    "Delete all recorded watch transitions?":
        "Удалить все записанные переходы наблюдений?",
    "From":                 "Было",
    "State":                "Стало",
    "Time":                 "Время",
    "Watch":                "Наблюдение",
    "{shown} of {total} event(s)": "Показано: {shown} из {total}",

    # QoL misc
    "Also search in descriptions": "Искать также в описаниях",
    "Match the filter text against each node's DESCRIPTION as well as its name. Slower on big trees.":
        "Сопоставлять текст фильтра не только с именем узла, но и с его DESCRIPTION. Медленнее на больших деревьях.",
    "Recent OIDs":          "Недавние OID",
    "(no history yet)":     "(история пуста)",
    "Show raw PDU (hex dump)": "Показать сырой PDU (hex-дамп)",
    "Raw PDU hex dump":     "Hex-дамп PDU",
    "No saved agent at slot {n}": "Нет сохранённого агента на позиции {n}",
    "Switched to agent #{n}: {host}:{port}":
        "Переключено на агента №{n}: {host}:{port}",
    "File":                 "Файл",
    "Path to script file (optional)": "Путь к файлу скрипта (необязательно)",
    "Script":               "Скрипт",
    "Reference":            "Справка",
    "Load example":         "Загрузить пример",
    "Periodic GET":         "Периодический GET",
    "Threshold alarm":      "Сигнал по порогу",
    "Bulk SET":             "Массовый SET",
    "Run ▶":                "Запустить ▶",
    "Clear output":         "Очистить вывод",
    "Replace the current script with this example?":
        "Заменить текущий скрипт этим примером?",
    "Open Script":          "Открыть скрипт",
    "Save Script":          "Сохранить скрипт",
    "A script is already running.":
        "Скрипт уже выполняется.",
    "Script is empty.":     "Скрипт пуст.",
    "Running script":       "Выполнение скрипта",
    "Script finished":      "Скрипт завершён",
    "Script failed":        "Скрипт завершился ошибкой",
    "Type or paste script here, or click 'Load example'":
        "Введите или вставьте скрипт, либо нажмите «Загрузить пример»",

    # Help
    "About":                "О программе",

    # Toolbar / labels
    " Address: ":           " Адрес: ",
    "Advanced…":            "Свойства…",
    "Agent…":               "Агент…",
    "SNMP":                 "SNMP",
    "SNMP version":         "Версия SNMP",
    "Edit the full agent properties (timeout, retries, SNMPv3…) for this host.":
        "Редактировать все свойства агента (таймаут, повторы, SNMPv3…) для этого хоста.",
    " OID: ":               " OID: ",
    " Operation: ":         " Операция: ",
    "Go ▶":                 "Выполнить ▶",

    # MIB tree pane
    "MIB Tree":                            "Дерево MIB",
    "SNMP MIBs":                           "SNMP MIB",
    "Filter MIB tree (substring)…":        "Фильтр MIB дерева (подстрока)…",

    # Result pane
    "Result":               "Результат",
    "Clear":                "Очистить",
    "Stop":                 "Стоп",
    "Find:":                "Найти:",
    "Find in table…":       "Искать в таблице…",
    "Save CSV":             "Сохранить CSV",
    "Log output":           "Лог",
    "Log":                  "Лог",
    "Show / hide the log pane": "Показать / скрыть панель лога",

    # Properties pane rows
    "Name":                 "Имя",
    "OID":                  "OID",
    "MIB":                  "MIB",
    "Type":                 "Тип",
    "Syntax":               "Синтаксис",
    "Access":               "Доступ",
    "Status":               "Статус",
    "Units":                "Единицы",
    "Indices":              "Индексы",
    "Values":               "Значения",
    "Description":          "Описание",

    # Status bar
    "Ready.":               "Готово.",

    # Log hints
    "hint: that OID has no scalar instance. For a column, use Walk or Get Next; for a table, use Get Subtree or open Table View.":
        "подсказка: у этого OID нет скалярного инстанса. Для колонки — Walk или Get Next; для таблицы — Get Subtree или Table View.",

    # Table View
    "Refresh":              "Обновить",
    "Rotate":               "Повернуть",
    "Export CSV":           "Экспорт CSV",
    " Poll (s): ":          " Опрос (с): ",

    # Graph
    "⏸ Pause":              "⏸ Пауза",
    "▶ Resume":             "▶ Продолжить",
    "↻ Restart":            "↻ Перезапуск",
    " Interval (s): ":      " Интервал (с): ",
    "Rate (delta)":         "Скорость (Δ)",
    "Grid":                 "Сетка",
    "Save PNG":             "Сохранить PNG",
    "Import CSV":           "Импорт CSV",

    # Trap window
    "Trap Receiver":        "Приёмник трапов",
    "▶ Start":              "▶ Старт",
    "⏸ Stop":               "⏸ Стоп",
    " Port: ":              " Порт: ",
    " Filter: ":            " Фильтр: ",
    "Rules…":               "Правила…",
    "Save…":                "Сохранить…",
    "Not listening.":       "Не слушаю.",
    "Stopped.":             "Остановлен.",
    "Time":                 "Время",
    "Source":               "Источник",
    "Severity":             "Уровень",
    "Trap OID":             "OID трапа",
    "Community":            "Сообщество",
    "Message":              "Сообщение",

    # Dialogs (Agent props)
    "Agent properties":     "Свойства агента",
    "General":              "Общие",
    "Host":                 "Хост",
    "Port":                 "Порт",
    "Version":              "Версия",
    "Timeout (s)":          "Таймаут (с)",
    "Retries":              "Повторы",
    "Read community":       "Read community",
    "Write community":      "Write community",
    "Max repetitions (bulk)": "Max repetitions (bulk)",
    "Non repeaters (bulk)": "Non repeaters (bulk)",

    # Agent simulator
    "SNMP Agent Simulator":          "Симулятор SNMP агента",
    "Walk file":                     "Walk-файл",
    "Browse…":                       "Обзор…",
    "Start":                         "Старт",
    "stopped":                       "остановлен",

    # Common dialog buttons
    "Ok":                   "ОК",
    "Cancel":               "Отмена",
    "Close":                "Закрыть",
    "Apply":                "Применить",

    # Preferences dialog
    "Preferences":          "Настройки",
    "Preferences…":         "Настройки…",
    "(restart required)":   "(требуется перезапуск)",
    "Language":             "Язык",
    "Single root (.iso) in MIB tree":
        "Единый корень (.iso) в дереве MIB",
    "SNMP":                 "SNMP",
    "Default version":      "Версия по умолчанию",
    "Default read community":  "Read community по умолчанию",
    "Default write community": "Write community по умолчанию",
    "Fetch missing dependencies from mibs.pysnmp.com":
        "Подтягивать недостающие зависимости с mibs.pysnmp.com",
    "Lenient MIB parser":   "Снисходительный парсер MIB",
    "Open MIB Modules manager…":
        "Открыть менеджер модулей MIB…",
    "Traps":                "Трапы",
    "Default trap port":    "Порт приёмника трапов",
    "Note: ports under 1024 require root. Use 11162+ for tests.":
        "Порты ниже 1024 требуют root. Для тестов используйте 11162+.",
    "Accept traps from":    "Принимать трапы от",
    "Comma-separated list of hosts / CIDRs the Trap Receiver will accept from. Empty = accept any source. Non-matching datagrams are dropped before parsing — DoS-resistant.":
        "Список хостов / CIDR через запятую, от которых принимать трапы. Пусто — принимать от всех. Несовпадающие датаграммы отбрасываются до парсинга — защита от DoS.",
    "Graph":                "График",
    "Max graph data points": "Макс. точек на графике",
    "Logging":              "Логирование",
    "Console log level":    "Уровень логов в консоли",
    "Log file:":            "Лог-файл:",

    # Bookmarks
    "&Bookmarks":           "&Закладки",
    "Bookmarks":            "Закладки",
    "Bookmark current OID…": "Добавить текущий OID…",
    "Edit bookmarks…":      "Редактировать закладки…",
    "Edit bookmarks":       "Редактировать закладки",
    "Manage Bookmarks":     "Управление закладками",
    "Go":                   "Перейти",
    "Edit…":                "Изменить…",
    "Remove agent '{host}' from the saved list?":
        "Удалить агента '{host}' из сохранённых?",
    "Move up":              "Переместить вверх",
    "Move down":            "Переместить вниз",
    "Bookmark":             "Закладка",
    "Bookmark OID":         "OID в закладки",
    "Bookmark…":            "В закладки…",
    "Copy OID":             "Копировать OID",
    "Copy Name":            "Копировать имя",
    "No OID in the toolbar to bookmark.":
        "В строке OID ничего не введено — нечего добавлять в закладки.",
    "Name for bookmark:":   "Имя закладки:",
    "Tip: Shift+click to load without running":
        "Подсказка: Shift+клик — только загрузить без запуска",
    "Bookmark loaded — press Go ▶ to run.":
        "Закладка загружена — нажмите «Выполнить ▶» для запуска.",
    "Open as":              "Открывать как",
    "Operation":            "Операция",

    # MIB menu (top-level now)
    "&MIB":                 "&MIB",
    "&View":                "&Вид",
    "&Polls":               "&Опросы",
    "Polls":                "Опросы",
    "Create Poll":          "Создать опрос",
    "Create Poll…":         "Создать опрос…",
    "Edit Poll":            "Редактировать опрос",
    "Manage Polls":         "Управление опросами",
    "Manage Polls…":        "Управление опросами…",
    "Poll Name":            "Название опроса",
    "Poll Name is required.": "Требуется название опроса.",
    "Interval":             "Интервал",
    "seconds":              "с",
    "SNMP Agents":          "SNMP-агенты",
    "Add Agent":            "Добавить агента",
    "Remove Agent":         "Удалить агента",
    "Clear Agents":         "Очистить",
    "Agent (host:port):":   "Агент (хост:порт):",
    "At least one agent is required.": "Нужен хотя бы один агент.",
    "Variables to poll":    "Переменные для опроса",
    "Add Variable":         "Добавить переменную",
    "Modify":               "Изменить",
    "Variable Name":        "Имя переменной",
    "Variable OID":         "OID переменной",
    "Agent":                "Агент",
    "Poll Variable":        "Переменная опроса",
    "OID is required.":     "Требуется OID.",
    "Add at least one variable to poll.":
        "Добавьте хотя бы одну переменную.",
    "Polling {n} agent(s)…": "Опрос {n} агент(ов)…",
    "Interval:":            "Интервал:",
    "Run":                  "Запуск",

    # Watches
    "Watches":              "Наблюдения",
    "Watches…":             "Наблюдения…",
    "Add to Watches":       "Добавить в наблюдения",
    "Add to Watches…":      "Добавить в наблюдения…",
    "SNMP Operation":       "SNMP-операция",
    "Normal state if result": "Нормально, если результат",
    "Condition":            "Условие",
    "Status":               "Статус",
    "Last Query":           "Последний запрос",
    "normal":               "норма",
    "alarm":                "тревога",
    "n/a":                  "н/д",
    "error":                "ошибка",
    "no data":              "нет данных",
    "invalid oid":          "неверный OID",
    "Watching {n} OIDs · {stamp}": "Наблюдение за {n} OID · {stamp}",

    # Device Snapshot
    "Device Snapshot":      "Снимок устройства",
    "Device Snapshot…":     "Снимок устройства…",
    "Basic Information":    "Основная информация",
    "Interface Information": "Информация об интерфейсах",
    "System Resources":     "Ресурсы системы",
    "Target":               "Цель",
    "Fetching from {host}:{port}…": "Опрос {host}:{port}…",
    "Snapshot from {host}:{port} · {rows} interfaces":
        "Снимок с {host}:{port} · интерфейсов: {rows}",
    "Error: {msg}":         "Ошибка: {msg}",

    # Port View
    "Port View":            "Порты",
    "Port View…":           "Порты…",
    "Refreshing…":          "Обновление…",
    "Interface is up":      "Интерфейс поднят",
    "Interface is down":    "Интерфейс опущен",
    "{n} interface(s) · updated every {s}s":
        "Интерфейсов: {n} · обновление каждые {s} с",

    # Log directory chooser
    "Log directory":        "Папка логов",
    "Default":              "По умолчанию",

    # Tab context menu
    "Pin tab":              "Закрепить вкладку",
    "Unpin tab":            "Открепить вкладку",
    "Close other tabs":     "Закрыть другие вкладки",
    "Close all tabs":       "Закрыть все вкладки",
    "Unpin and close":      "Открепить и закрыть",

    # Context-sensitive Get hints
    "hint: this OID is a notification (TRAP/INFORM). It isn't pollable with GET — it's sent by the agent. Use Tools → Trap Receiver to listen for it.":
        "подсказка: этот OID — уведомление (TRAP/INFORM). Его нельзя запросить через GET, он приходит от агента. Для приёма используйте Инструменты → Приём трапов.",
    "hint: this OID is a table/row. Use Get Subtree or open Table View to see rows.":
        "подсказка: этот OID — таблица/строка. Используйте Get Subtree или откройте Вид таблицы, чтобы увидеть строки.",
    "hint: this OID is a table column. Use Walk or Get Next to enumerate instances.":
        "подсказка: этот OID — столбец таблицы. Используйте Walk или Get Next для перечисления инстансов.",

    # Preferences hints
    "UI language. '(auto)' uses your $LANG env variable; the choice is stored and applied on next launch.":
        "Язык интерфейса. '(auto)' использует переменную $LANG; выбор сохраняется и применяется при следующем запуске.",
    "When on, the tree starts from a single iso (.1) root. When off, the top level lists iso's immediate children (org, etc.) side-by-side — useful if you always work inside mgmt.mib-2 and want one less click.":
        "Если включено, дерево начинается с единого корня iso (.1). Если выключено, на верхнем уровне сразу отображаются дети iso (org и т.д.) — удобно, если вы всегда работаете в mgmt.mib-2.",
    "SNMP version used when adding a fresh agent. v2c is the usual choice; v1 only if the device is ancient.":
        "Версия SNMP для нового агента. v2c — обычный выбор; v1 только для устаревших устройств.",
    "Read-only community string for GET/WALK. Typically 'public' on lab devices.":
        "Community только на чтение (GET/WALK). Обычно 'public' на лабораторных устройствах.",
    "Write community for SET. Keep this secret — avoid the default 'private' on production.":
        "Community для записи (SET). Держите в секрете — не оставляйте 'private' в продакшене.",
    "How long to wait for each UDP response before giving up. Raise for slow links; lower for faster failures.":
        "Сколько ждать каждый UDP-ответ перед отменой. Увеличьте для медленных линков; уменьшите для быстрого отказа.",
    "Number of times to re-send a request after a timeout before declaring the agent unreachable.":
        "Сколько раз повторно отправить запрос после таймаута перед тем, как признать агента недоступным.",
    "GETBULK's 'max-repetitions': how many rows the agent packs per response. 10–50 is a good range.":
        "'max-repetitions' для GETBULK: сколько строк агент упаковывает в ответ. 10–50 — разумный диапазон.",
    "GETBULK's 'non-repeaters': leading varbinds treated as a plain GET. Usually 0.":
        "'non-repeaters' для GETBULK: первые varbind-ы обрабатываются как обычный GET. Обычно 0.",
    "When compiling a MIB, if an IMPORTS statement names a module you don't have locally, download it from mibs.pysnmp.com. Off means fully offline — missing deps cause the compile to fail.":
        "При компиляции MIB, если в IMPORTS указан недостающий модуль, скачать его с mibs.pysnmp.com. Если выключено — работа полностью офлайн, недостающие зависимости ломают компиляцию.",
    "Accept vendor MIBs that bend SMIv2 rules — missing DESCRIPTION clauses, ad-hoc types, reserved words. Turn off only if you want a strict validator.":
        "Принимать вендорские MIB, нарушающие SMIv2 — без DESCRIPTION, с самодельными типами, зарезервированными словами. Отключайте, только если нужен строгий валидатор.",
    "Enable or disable individual compiled modules in the MIB tree — useful to hide enterprise noise you don't care about.":
        "Включить или отключить отдельные скомпилированные модули в дереве MIB — полезно, чтобы скрыть ненужные enterprise-модули.",
    "UDP port the Trap Receiver binds to. The standard port is 162 but it requires root; 11162 is safe for unprivileged testing.":
        "UDP-порт, на котором слушает приёмник трапов. Стандартный порт 162 требует root; 11162 подходит для тестов без привилегий.",
    "Sliding-window size for real-time graphs. Older samples are dropped once this many have been collected. Bigger = longer history, more memory.":
        "Размер скользящего окна для графиков в реальном времени. Старые отсчёты отбрасываются при превышении. Больше = длиннее история, больше памяти.",
    "Verbosity on the console/stderr. The file handler always writes DEBUG — this only filters terminal output.":
        "Уровень подробности на консоли/stderr. В файл всегда пишется DEBUG — это фильтрует только вывод в терминал.",
    "Where rotating log files live. Empty = the default under your XDG data directory.":
        "Где хранятся ротационные лог-файлы. Пусто — каталог по умолчанию в XDG data directory.",
    "Open the current log file in your default viewer.":
        "Открыть текущий лог-файл во внешнем просмотрщике.",
    "These are defaults for NEW agents. To edit your current agent, use the toolbar's Advanced… button or Tools → Manage agents.":
        "Это значения по умолчанию для НОВЫХ агентов. Для редактирования текущего агента нажмите «Свойства…» на панели или откройте «Инструменты → Агенты».",

    # Graph — editable OID inside the tab
    "Cannot resolve OID":   "Не удалось разобрать OID",
    "This will discard {n} samples of the current trace.\nExport CSV first if you want to keep them.\n\nContinue?":
        "Будет отброшено {n} отсчётов текущего графика.\nСначала экспортируйте CSV, если они нужны.\n\nПродолжить?",
    "{op} on a broad subtree '{scope}' — this may return thousands of varbinds and take minutes.\n\nKnown MIB nodes below it: ~{approx}{plus}.\n\nRun anyway?":
        "{op} на широком поддереве '{scope}' — может вернуть тысячи varbind-ов и занять минуты.\n\nИзвестных узлов ниже: ~{approx}{plus}.\n\nВсё равно выполнить?",

    # Help → Keyboard shortcuts
    "Keyboard shortcuts…":  "Горячие клавиши…",
    "User Guide":           "Руководство пользователя",
    "User Guide…":          "Руководство пользователя…",
    "The User Guide isn't installed with this build.\nOnline version: https://github.com/Gehnmart/python_mibbrowser/blob/main/docs/guide/index.html":
        "Руководство пользователя не входит в эту сборку.\nОнлайн-версия: https://github.com/Gehnmart/python_mibbrowser/blob/main/docs/guide/index.html",
    "Keyboard shortcuts":   "Горячие клавиши",
    "MIB tree icons…":      "Иконки дерева MIB…",
    "MIB tree icons":       "Иконки дерева MIB",
    "Organisational group (container, no value)":
        "Организационная группа (контейнер, без значения)",
    "Conceptual table (SMIv2 TABLE)":
        "Таблица (SMIv2 TABLE)",
    "Table row (entry — defines per-instance columns)":
        "Строка таблицы (entry — задаёт колонки для инстансов)",
    "Writable scalar (read-write / read-create)":
        "Скаляр с записью (read-write / read-create)",
    "Read-only scalar":     "Скаляр только для чтения",
    "Notification / TRAP / INFORM":
        "Уведомление / TRAP / INFORM",
    "Leaf object without a specific role":
        "Лист без явной роли",
}


_current: dict[str, str] = {}


def init_language(override: str | None = None) -> None:
    global _current
    lang = override
    if lang is None:
        lang = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "")
        lang = lang.split(".")[0].split("_")[0].lower()
    _current = _RU if lang == "ru" else {}


def set_language(lang: str) -> None:
    init_language(lang)


def current_language() -> str:
    return "ru" if _current is _RU else "en"


def _t(key: str) -> str:
    """Translate a key to the current language, falling back to the key."""
    if not _current:
        init_language()
    return _current.get(key, key)
