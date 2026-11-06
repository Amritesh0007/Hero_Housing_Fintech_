"""Website translations: source strings stay in sync with the page, Gemini output is checked and cached."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from valuegraph import i18n

SITE = Path(i18n.__file__).parent / "site"


class _Keys(HTMLParser):
    """Collects data-i18n keys and the English text the HTML ships with (shown before JS runs)."""

    def __init__(self):
        super().__init__()
        self.keys, self.text, self._open = set(), {}, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for attr in ("data-i18n", "data-i18n-placeholder", "data-i18n-aria"):
            if a.get(attr):
                self.keys.add(a[attr])
        self._open.append((tag, a.get("data-i18n"), []))

    def handle_data(self, data):
        for _, key, buf in self._open:
            if key:
                buf.append(data)

    def handle_endtag(self, tag):
        while self._open:
            t, key, buf = self._open.pop()
            if key:
                self.text.setdefault(key, " ".join("".join(buf).split()))
            if t == tag:
                break


def test_every_key_on_the_page_and_in_the_script_has_english_text():
    src = i18n.source()
    page = _Keys()
    page.feed((SITE / "index.html").read_text(encoding="utf-8"))
    assert page.keys - src.keys() == set(), "data-i18n keys missing from en.json"
    # The HTML's built-in English must match en.json, or the page changes when JS loads.
    for key, shown in page.text.items():
        if "{" not in src[key]:
            assert shown == src[key], key

    js = (SITE / "site.js").read_text(encoding="utf-8")
    literal = set(re.findall(r'\bt\(\s*"([\w.]+)"', js)) | set(re.findall(r'showErr\(\s*"([\w.]+)"', js))
    literal |= set(re.findall(r'return "(js\.[\w.]+)"', js))
    assert literal - src.keys() == set(), "t() keys missing from en.json"
    # Keys the script builds from data.
    for g in ("high", "medium", "low", "none"):
        assert f"js.grade.{g}" in src and f"js.grade.{g}.note" in src
    for a in ("carpet", "built_up", "super_built_up"):
        assert f"js.area.{a}" in src


def test_factor_labels_from_the_model_are_all_translatable():
    from valuegraph.model import FACTOR_GROUPS

    src = i18n.source()
    for label in [*FACTOR_GROUPS, "Details not given", "Near-identical flats in the building"]:
        assert f"factor.{label}" in src, label


def test_validate_keeps_good_strings_and_falls_back_on_broken_placeholders():
    src = {"a": "Hello {city}", "b": "Plain", "c": "Missing"}
    out = {"a": "नमस्ते {शहर}", "b": "सादा", "d": "extra"}
    assert i18n.validate(src, out) == {"a": "Hello {city}", "b": "सादा", "c": "Missing"}


def _fake_gemini(calls, bad=False):
    def fake(src, lang, key, model):
        calls.append(lang)
        return {k: (v if bad else f"[{lang}] {v}") for k, v in src.items()}

    return fake


def test_translation_is_made_once_then_served_from_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(i18n, "gemini_translate", _fake_gemini(calls))
    first = i18n.get_strings("hi", tmp_path, "key", "m")
    assert first["nav.cta"] == "[hi] Get valuation" and first["value.lead"].endswith(
        "{cities} Indian cities. Nothing you enter here is stored."
    )
    again = i18n.get_strings("hi", tmp_path, "", "m")  # no key needed once cached
    assert again == first and calls == ["hi"]
    assert i18n.get_strings("en", tmp_path, "", "m") == i18n.source()
    with pytest.raises(KeyError):
        i18n.get_strings("xx", tmp_path, "key", "m")


def test_a_mostly_untranslated_reply_is_rejected_and_not_cached(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(i18n, "gemini_translate", lambda *a: calls.append(1) or {"nav.cta": "x"})
    with pytest.raises(i18n.TranslationError):
        i18n.get_strings("ta", tmp_path, "key", "m")
    assert not (tmp_path / "ta.json").exists()


def test_language_endpoints(client, tmp_path, monkeypatch):
    from valuegraph.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "i18n_cache_dir", tmp_path)
    langs = client.get("/api/v1/public/i18n").json()
    assert {"code": "hi", "name": "हिन्दी"} in langs and langs[0]["code"] == "en"
    assert client.get("/api/v1/public/i18n/en").json()["nav.cta"] == "Get valuation"
    assert client.get("/api/v1/public/i18n/klingon").status_code == 404

    monkeypatch.setattr(s, "gemini_api_key", "")
    assert client.get("/api/v1/public/i18n/te").status_code == 503  # not configured, not cached

    calls = []
    monkeypatch.setattr(s, "gemini_api_key", "test-key")
    monkeypatch.setattr(i18n, "gemini_translate", _fake_gemini(calls))
    r = client.get("/api/v1/public/i18n/te")
    assert r.status_code == 200 and r.json()["nav.cta"] == "[te] Get valuation"
    client.get("/api/v1/public/i18n/te")
    assert calls == ["te"]
    # The key never reaches the browser.
    assert "test-key" not in client.get("/api/v1/config").text


def test_site_script_runs_as_a_module():
    # Classic <script>s share one global scope: site.js's t() once overwrote the minified Lenis's own t()
    # and broke all scrolling. As a module it has its own scope.
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="/site/site.js">' in html
