"""i18n switching, environment auto-detect, fallback to key."""
from __future__ import annotations

import pytest

from pymibbrowser.infra import i18n


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Reset module state between tests so they don't leak language."""
    monkeypatch.setattr(i18n, "_current", {})
    yield
    monkeypatch.setattr(i18n, "_current", {})


def test_set_language_ru_translates_known_key():
    i18n.set_language("ru")
    # Pick any key actually in the translation dict.
    sample_en = next(iter(i18n._RU))
    assert i18n._t(sample_en) == i18n._RU[sample_en]


def test_set_language_en_falls_through_to_key():
    i18n.set_language("en")
    assert i18n._t("OID") == "OID"
    assert i18n._t("nonexistent-key") == "nonexistent-key"


def test_unknown_language_treated_as_english():
    """Anything other than 'ru' is identity-translated."""
    i18n.set_language("fr")
    assert i18n._t("Save") == "Save"


def test_init_language_picks_ru_from_env(monkeypatch):
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    i18n.init_language(None)
    assert i18n.current_language() == "ru"


def test_init_language_falls_back_to_lc_all(monkeypatch):
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    i18n.init_language(None)
    assert i18n.current_language() == "ru"


def test_init_language_defaults_to_english_for_unknown_locale(monkeypatch):
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    i18n.init_language(None)
    assert i18n.current_language() == "en"


def test_init_language_explicit_override_beats_env(monkeypatch):
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    i18n.init_language("ru")
    assert i18n.current_language() == "ru"


def test_t_auto_initialises_when_called_first(monkeypatch):
    """If _t is called before init_language, it should still produce a
    string (auto-init from env), not crash."""
    monkeypatch.setattr(i18n, "_current", {})
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    out = i18n._t("Save")
    assert out == "Save"
