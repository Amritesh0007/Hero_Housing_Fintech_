"""Website translations.

English is the source of truth: site/i18n/en.json. Other languages are translated from it by Gemini the first
time someone asks for them, checked, and cached on disk. Later requests are served from the cache, so Gemini is
called at most once per language (again only if en.json changes). The API key never leaves the server.
"""

import hashlib
import json
import re
import threading
from pathlib import Path

import httpx

# Code -> name in its own script, shown in the language picker. Only these can be requested, which also caps how
# many Gemini calls anyone can trigger.
LANGUAGES = {
    "en": "English",
    "hi": "हिन्दी",
    "bn": "বাংলা",
    "te": "తెలుగు",
    "mr": "मराठी",
    "ta": "தமிழ்",
    "gu": "ગુજરાતી",
    "kn": "ಕನ್ನಡ",
    "ml": "മലയാളം",
}
ENGLISH_NAMES = {
    "hi": "Hindi",
    "bn": "Bengali",
    "te": "Telugu",
    "mr": "Marathi",
    "ta": "Tamil",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
}
SOURCE = Path(__file__).parent / "site" / "i18n" / "en.json"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_lock = threading.Lock()


class TranslationError(RuntimeError):
    pass


def source() -> dict[str, str]:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _hash(src: dict) -> str:
    return hashlib.sha256(json.dumps(src, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def validate(src: dict[str, str], out: dict) -> dict[str, str]:
    """Keep a translated string only if it is non-empty text with exactly the same {placeholders};
    anything else falls back to English, so a bad translation can't break a sentence."""
    good = {}
    for key, en in src.items():
        tr = out.get(key) if isinstance(out, dict) else None
        ok = isinstance(tr, str) and tr.strip() and set(_PLACEHOLDER.findall(tr)) == set(_PLACEHOLDER.findall(en))
        good[key] = tr.strip() if ok else en
    return good


def _prompt(lang: str) -> str:
    name = ENGLISH_NAMES[lang]
    return (
        f"Translate the values of this JSON object from English into {name} for KSHETRA, an Indian property "
        "valuation website used by home buyers and owners.\n"
        "Rules:\n"
        "- Keep every key exactly as it is; translate values only. Return only the JSON object.\n"
        "- Copy placeholders like {city} or {rate} exactly, untranslated.\n"
        "- Keep 'KSHETRA', '₹', 'BHK', 'sq ft', 'Gemini', 'GitHub', 'AI' and all numbers unchanged.\n"
        f"- Use clear, natural, everyday {name} as written on Indian property websites. Where people normally use "
        "the English real-estate term (for example carpet area, resale), you may keep it or transliterate it.\n"
        "- Keep the tone plain and short; do not add or drop information.\n\n"
    )


def gemini_translate(src: dict[str, str], lang: str, api_key: str, model: str) -> dict:
    """One Gemini call for the whole dictionary. Raises TranslationError on any failure."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": _prompt(lang) + json.dumps(src, ensure_ascii=False)}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    try:
        r = httpx.post(
            GEMINI_URL.format(model=model), headers={"x-goog-api-key": api_key}, json=body, timeout=httpx.Timeout(120)
        )
    except httpx.HTTPError as e:
        raise TranslationError(f"Gemini unreachable: {e.__class__.__name__}") from None
    if r.status_code != 200:
        # Google's error message, without echoing anything that could contain the key.
        msg = (r.json().get("error", {}).get("message", "") if "json" in r.headers.get("content-type", "") else "")[
            :200
        ]
        if r.status_code == 429:
            raise TranslationError("rate_limit: Gemini quota exhausted, try again later")
        raise TranslationError(f"Gemini returned {r.status_code}: {msg}")
    try:
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, ValueError) as e:
        raise TranslationError(f"Gemini reply wasn't the expected JSON ({e.__class__.__name__})") from None


def get_strings(lang: str, cache_dir: Path, api_key: str, model: str) -> dict[str, str]:
    """English, a cached translation, or a fresh Gemini translation (cached for next time)."""
    if lang not in LANGUAGES:
        raise KeyError(lang)
    src = source()
    if lang == "en":
        return src
    h, path = _hash(src), cache_dir / f"{lang}.json"

    def cached():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return doc["strings"] if doc.get("source_hash") == h else None

    if (hit := cached()) is not None:
        return hit
    if not api_key:
        raise TranslationError("Translations need GEMINI_API_KEY on the server")
    with _lock:  # one translation at a time; a second request for the same language waits and reuses it
        if (hit := cached()) is not None:
            return hit
        raw = gemini_translate(src, lang, api_key, model)
        strings = validate(src, raw)
        kept = sum(1 for k in src if isinstance(raw, dict) and strings[k] == (raw.get(k) or "").strip())
        if kept < 0.8 * len(src):  # mostly unusable: don't cache English under another language's name
            raise TranslationError(f"Gemini translated only {kept} of {len(src)} strings correctly")
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"source_hash": h, "model": model, "strings": strings}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(path)  # atomic: readers never see a half-written file
        return strings
