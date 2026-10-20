"""Small, explicit Ukrainian/English catalog; never translates game content."""
import json
import os
import platform
from pathlib import Path
import paths

EN = json.loads((paths.resources_dir() / 'locales' / 'en.json').read_text(encoding='utf-8'))
_language = 'uk'


def language():
    return _language


def set_language(value):
    global _language
    if value not in ('uk', 'en'):
        raise ValueError('Supported interface languages: uk, en')
    _language = value


def preferred_language():
    value = os.environ.get('CRYLOCAL_LANGUAGE') or paths.load_preferences().get('interface_language')
    return value if value in ('uk', 'en') else ('en' if platform.system() == 'Windows' else 'uk')


def tr(key):
    return EN.get(key, key) if _language == 'en' else key


def source_key(value):
    if value in EN:
        return value
    return next((key for key, translated in EN.items() if translated == value), None)


set_language(preferred_language())
