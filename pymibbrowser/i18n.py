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
from typing import Callable, Optional


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

    # MIB Modules dialog
    "Enabled":              "Вкл.",
    "Module":               "Модуль",
    "Size":                 "Размер",
    "Filter…":              "Фильтр…",
    "All":                  "Все",
    "None":                 "Никакие",
    "Invert":               "Инверсия",
    "Only vendor (no RFC/SNMPv2)": "Только вендор (без RFC/SNMPv2)",
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
    "Not in the compiled cache. Use File → Load MIB to add it.":
        "Модуля нет в кэше компиляции. Добавьте через Файл → Загрузить MIB.",
    "Built-in framework module": "Встроенный framework-модуль",
    "Open log file…":       "Открыть лог…",
    "Save session (CSV)…":  "Сохранить сессию (CSV)…",
    "Exit":                 "Выход",

    # Edit menu
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
    "Run Script…":          "Запустить скрипт…",

    # Help
    "About":                "О программе",

    # Toolbar / labels
    " Address: ":           " Адрес: ",
    "Advanced…":            "Свойства…",
    " OID: ":               " OID: ",
    " Operation: ":         " Операция: ",
    "Go ▶":                 "Выполнить ▶",

    # MIB tree pane
    "MIB Tree":                            "Дерево MIB",
    "Filter MIB tree (substring)…":        "Фильтр MIB дерева (подстрока)…",

    # Result pane
    "Result":               "Результат",
    "Clear":                "Очистить",
    "Stop":                 "Стоп",
    "Find:":                "Найти:",
    "Find in table…":       "Искать в таблице…",
    "Save CSV":             "Сохранить CSV",
    "Log output":           "Лог",

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
}


_current: dict[str, str] = {}


def init_language(override: Optional[str] = None) -> None:
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
