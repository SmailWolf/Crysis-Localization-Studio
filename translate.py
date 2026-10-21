"""Local TranslateGemma adapters; no model download or cloud service required."""
from i18n import tr
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
import core

DEFAULTS = {'provider': 'builtin', 'base_url': 'http://127.0.0.1:8080',
            'model': 'translategemma-4b', 'source_language': 'en',
            'target_language': 'uk', 'timeout': 120}
LANGUAGES = {
    'af': 'Afrikaans', 'ar': 'Arabic', 'az': 'Azerbaijani', 'bg': 'Bulgarian',
    'bn': 'Bengali', 'ca': 'Catalan', 'cs': 'Czech', 'da': 'Danish',
    'de': 'German', 'el': 'Greek', 'en': 'English', 'es': 'Spanish',
    'et': 'Estonian', 'fa': 'Persian', 'fi': 'Finnish', 'fr': 'French',
    'he': 'Hebrew', 'hi': 'Hindi', 'hr': 'Croatian', 'hu': 'Hungarian',
    'id': 'Indonesian', 'it': 'Italian', 'ja': 'Japanese', 'ko': 'Korean',
    'lt': 'Lithuanian', 'lv': 'Latvian', 'ms': 'Malay', 'nl': 'Dutch',
    'no': 'Norwegian', 'pl': 'Polish', 'pt': 'Portuguese', 'ro': 'Romanian',
    'ru': 'Russian', 'sk': 'Slovak', 'sl': 'Slovenian', 'sq': 'Albanian',
    'sr': 'Serbian', 'sv': 'Swedish', 'sw': 'Swahili', 'th': 'Thai',
    'tr': 'Turkish', 'uk': 'Ukrainian', 'vi': 'Vietnamese', 'zh': 'Chinese',
    'zh-TW': 'Traditional Chinese',
}
LANGUAGE_LABELS = {
    'af': 'Afrikaans', 'ar': 'العربية', 'az': 'Azərbaycanca', 'bg': 'Български',
    'bn': 'বাংলা', 'ca': 'Català', 'cs': 'Čeština', 'da': 'Dansk', 'de': 'Deutsch',
    'el': 'Ελληνικά', 'en': 'English', 'es': 'Español', 'et': 'Eesti', 'fa': 'فارسی',
    'fi': 'Suomi', 'fr': 'Français', 'he': 'עברית', 'hi': 'हिन्दी', 'hr': 'Hrvatski',
    'hu': 'Magyar', 'id': 'Bahasa Indonesia', 'it': 'Italiano', 'ja': '日本語',
    'ko': '한국어', 'lt': 'Lietuvių', 'lv': 'Latviešu', 'ms': 'Bahasa Melayu',
    'nl': 'Nederlands', 'no': 'Norsk', 'pl': 'Polski', 'pt': 'Português',
    'ro': 'Română', 'ru': 'Русский', 'sk': 'Slovenčina', 'sl': 'Slovenščina',
    'sq': 'Shqip', 'sr': 'Српски', 'sv': 'Svenska', 'sw': 'Kiswahili', 'th': 'ไทย',
    'tr': 'Türkçe', 'uk': 'Українська', 'vi': 'Tiếng Việt', 'zh': '简体中文',
    'zh-TW': '繁體中文',
}
ACRONYM = re.compile(r'(?<!\w)[A-Z]{3}(?!\w)')


class Translator:
    def __init__(self, settings=None):
        self.settings = dict(DEFAULTS, **(settings or {}))
        self.base = self.settings['base_url'].strip().rstrip('/')
        url = urlsplit(self.base)
        if url.scheme not in ('http', 'https') or not url.hostname or url.query or url.fragment:
            raise core.LocalizerError(tr('Вкажіть коректну HTTP-адресу локального сервера моделі'))
        self.provider = self.settings['provider']
        if self.provider not in ('builtin', 'ollama', 'openai', 'hf'):
            raise core.LocalizerError(tr('Невідомий тип сервера моделі'))
        self.model = self.settings['model'].strip()
        if not self.model:
            raise core.LocalizerError(tr('Вкажіть назву моделі'))
        self.cache = {}
        # Explicit endpoint; system HTTP proxies must not redirect local game text.
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, payload=None, timeout=None):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = Request(self.base + path, data=data, headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=timeout or float(self.settings['timeout'])) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise core.LocalizerError(tr('Відповідь моделі перевищує 8 МБ'))
                result = json.loads(raw)
        except HTTPError as exc:
            detail = exc.read(2048).decode('utf-8', errors='replace')
            raise core.LocalizerError(tr('Сервер моделі повернув HTTP {0}: {1}').format(exc.code, detail)) from exc
        except (URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            raise core.LocalizerError(tr('Немає відповіді від {0}. Запустіть локальний сервер із TranslateGemma 4B та перевірте адресу. {1}').format(self.base, exc)) from exc
        except (ValueError, UnicodeError) as exc:
            raise core.LocalizerError(tr('Сервер повернув некоректний JSON')) from exc
        if not isinstance(result, dict):
            raise core.LocalizerError(tr('Непідтримуваний формат відповіді сервера'))
        if result.get('error'):
            raise core.LocalizerError(str(result['error']))
        return result

    def models(self):
        if self.provider == 'ollama':
            data = self.request('/api/tags', timeout=5)
            return [m['name'] for m in data.get('models', [])]
        base = '' if self.base.endswith('/v1') else '/v1'
        return [m['id'] for m in self.request(base + '/models', timeout=5).get('data', [])]

    def translate(self, source):
        if not source.strip():
            return source
        if len(source) > 5000:
            raise core.LocalizerError(tr('Рядок перевищує 5000 символів. Перекладіть його вручну або розбийте поза утилітою.'))
        target_code = self.settings.get('target_language', 'uk')
        if target_code not in LANGUAGES:
            raise core.LocalizerError(tr('Невідома мова перекладу'))
        cache_key = (self.settings['source_language'], target_code, source)
        if cache_key in self.cache:
            return self.cache[cache_key]
        source_code = self.settings['source_language']
        source_name = LANGUAGES.get(source_code, source_code)
        target_name = LANGUAGES[target_code]
        # Keep the translation input free of game keys, speaker metadata and instructions
        # from other records. Validate placeholders before accepting any result.
        masked_tokens = []
        prefix = 'ZXQPLACEHOLDER'
        while prefix in source:
            prefix += 'X'
        def mask(match):
            marker = f'{prefix}{len(masked_tokens):04d}QXZ'
            masked_tokens.append((marker, match.group()))
            return marker
        # Protect game abbreviations such as MHP and SMG just like format tokens.
        # Merge the matches so neither kind of protected text is translated.
        matches = sorted((*core.TOKEN.finditer(source), *ACRONYM.finditer(source)),
                         key=lambda match: match.start())
        pieces = []
        cursor = 0
        for match in matches:
            if match.start() < cursor:
                continue
            pieces.append(source[cursor:match.start()])
            pieces.append(mask(match))
            cursor = match.end()
        pieces.append(source[cursor:])
        masked = ''.join(pieces)
        prompt = (f'You translate from {source_name} ({source_code}) into {target_name} ({target_code}). '
                  f'Write an accurate, natural {target_name} rendering of the passage. '
                  'Output the translated passage only; omit commentary. '
                  'Copy all ZXQPLACEHOLDER identifiers exactly as written.\n\n\n' + masked)
        if self.provider == 'builtin':
            # Raw completions avoid incompatible GGUF chat templates.
            response = self.request('/completion', {'prompt': '<start_of_turn>user\n' + prompt + '<end_of_turn>\n<start_of_turn>model\n',
                'n_predict': 2048, 'temperature': 0, 'stream': False,
                'stop': ['<end_of_turn>', '<eos>'], 'cache_prompt': True})
            if response.get('truncated') or response.get('stopped_limit'):
                raise core.LocalizerError(tr('Модель обрізала переклад через обмеження довжини'))
            result = response.get('content', '')
        elif self.provider == 'ollama':
            response = self.request('/api/chat', {'model': self.model,
                'messages': [{'role': 'user', 'content': prompt}], 'stream': False,
                'options': {'temperature': 0, 'num_predict': 2048, 'num_ctx': 4096}})
            if response.get('done_reason') == 'length':
                raise core.LocalizerError(tr('Модель обрізала переклад через обмеження довжини'))
            result = response.get('message', {}).get('content', '')
        else:
            content = prompt
            if self.provider == 'hf':
                content = [{'type': 'text', 'source_lang_code': source_code,
                            'target_lang_code': target_code, 'text': masked}]
            base = '' if self.base.endswith('/v1') else '/v1'
            response = self.request(base + '/chat/completions', {'model': self.model,
                'messages': [{'role': 'user', 'content': content}], 'stream': False,
                'temperature': 0, 'max_tokens': 2048})
            choices = response.get('choices', [])
            if not choices or choices[0].get('finish_reason') in ('length', 'content_filter'):
                raise core.LocalizerError(tr('Модель не повернула повний переклад'))
            result = choices[0].get('message', {}).get('content', '')
        if not isinstance(result, str) or not result.strip():
            raise core.LocalizerError(tr('Модель повернула порожній переклад'))
        result = result.strip()
        for marker, token in masked_tokens:
            if result.count(marker) != 1:
                raise core.LocalizerError(tr('Модель змінила службову вставку. Результат не прийнято; перекладіть цей рядок вручну.'))
            result = result.replace(marker, token)
        if prefix in result or core.tokens(source) != core.tokens(result):
            raise core.LocalizerError(tr('Модель змінила службові вставки. Результат не прийнято.'))
        if core.validate({'entries': [{'id': '', 'key': '', 'source': source, 'translation': result}]}):
            raise core.LocalizerError(tr('Модель повернула непридатний для XML переклад'))
        self.cache[cache_key] = result
        return result
