"""CryLocal: conservative CryEngine SpreadsheetML localization editor (stdlib only)."""
from __future__ import annotations

from i18n import tr
import games

import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import xml.etree.ElementTree as ET
from xml.parsers import expat
from xml.sax.saxutils import escape
import zipfile
from collections import Counter

SS = 'urn:schemas-microsoft-com:office:spreadsheet'
S = '{' + SS + '}'
SCHEMA = 'crylocal-project-v1'
CSV_FIELDS = ['id', 'file', 'sheet', 'key', 'field', 'source', 'translation', 'context']


class LocalizerError(ValueError):
    pass


def canonical(name):
    return name.replace('\\', '/')


def safe_name(name):
    name = canonical(name)
    if name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/')) or ':' in name:
        raise LocalizerError(tr('Небезпечний шлях в архіві: {0}').format(name))
    return name


class Pak:
    """Allow Crytek's slash mismatch only; keep ZipFile CRC/bounds/encryption checks."""
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as e:
            raise LocalizerError(tr('PAK не є відкритим ZIP. Зашифровані PAK та CryXmlB наразі не підтримуються.')) from e
        self.items = {}
        for item in self.zip.infolist():
            if item.is_dir():
                continue
            name = safe_name(item.filename)
            if name.casefold() in self.items:
                self.zip.close()
                raise LocalizerError(tr('Дубльований шлях у PAK: {0}').format(name))
            self.items[name.casefold()] = item

    def read(self, item):
        if isinstance(item, str):
            item = self.items[canonical(item).casefold()]
        if item.flag_bits & 1:
            raise LocalizerError(tr('Зашифрований файл: {0}').format(item.filename))
        with self.path.open('rb') as f:
            f.seek(item.header_offset)
            header = f.read(30)
            if len(header) != 30 or header[:4] != b'PK\x03\x04':
                raise LocalizerError(tr('Пошкоджений локальний заголовок ZIP'))
            name_len = struct.unpack_from('<H', header, 26)[0]
            raw_name = f.read(name_len)
        name = raw_name.decode('utf-8' if item.flag_bits & 0x800 else 'cp437')
        if canonical(name) != canonical(item.orig_filename):
            raise LocalizerError(tr('Заголовки ZIP не збігаються: {0}').format(item.filename))
        adjusted = copy.copy(item)
        adjusted.orig_filename = name
        return self.zip.read(adjusted)

    def close(self):
        self.zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def discover(root):
    root = Path(root).expanduser().resolve()
    if root.is_file():
        return [root] if root.suffix.lower() == '.pak' else []
    # Match actual directory names case-insensitively, without walking textures/levels.
    result = []
    todo = [(root, 0, False)]
    while todo:
        folder, depth, localized = todo.pop()
        try:
            for p in sorted(folder.iterdir()):
                if p.is_file() and p.suffix.lower() == '.pak' and (localized or depth == 0):
                    if localized or re.fullmatch(r'[a-z]+(?:_xml)?', p.stem, re.I) and p.stem.lower() in {
                        'english', 'russian', 'ukrainian', 'english_xml', 'russian_xml', 'ukrainian_xml'}:
                        result.append(p)
                elif p.is_dir() and depth < 3:
                    low = p.name.lower()
                    if low in ('localized', 'localization', 'languages'):
                        todo.append((p, depth + 1, True))
                    elif depth == 0 and (low.startswith('game') or low in ('crysis2', 'crysis3')):
                        todo.append((p, depth + 1, False))
        except OSError:
            continue
    return sorted(set(result), key=lambda p: str(p).lower())


def cell_map(row):
    result, col = {}, 0
    for cell in row.findall(S + 'Cell'):
        col = int(cell.get(S + 'Index', col + 1))
        result[col] = cell
        col += int(cell.get(S + 'MergeAcross', '0'))
    return result


def text(cell):
    if cell is None:
        return ''
    data = cell.find(S + 'Data')
    return ''.join(data.itertext()) if data is not None else ''


def normalized_header(value):
    return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()


def xml_bytes(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        value = data.decode('utf-16')
        value = re.sub(r'encoding\s*=\s*[\'\"]UTF-16[\'\"]', 'encoding="UTF-8"', value, flags=re.I)
        return value.encode('utf-8')
    return data


def xml_root(data):
    if re.search(br'<!\s*(DOCTYPE|ENTITY)\b', data, re.I):
        raise LocalizerError(tr('DTD та XML-сутності не підтримуються'))
    if data.startswith(b'CryXmlB'):
        raise LocalizerError(tr('Бінарний CryXmlB не підтримується; потрібен текстовий SpreadsheetML XML'))
    return ET.fromstring(data)


def extract_entries(name, data):
    root = xml_root(xml_bytes(data))
    if root.tag != S + 'Workbook':
        return []
    entries = []
    for si, sheet in enumerate(root.findall(S + 'Worksheet')):
        table = sheet.find(S + 'Table')
        if table is None:
            continue
        rows = table.findall(S + 'Row')
        header, hi = None, 0
        for ri, row in enumerate(rows[:12]):
            headers = {col: normalized_header(text(c)) for col, c in cell_map(row).items()}
            if any(v in ('key', 'audio filename', 'label') for v in headers.values()):
                header, hi = headers, ri
                break
        if header is None:
            continue
        def find(*names):
            return next((k for k, v in header.items() if v in names), None)
        key_col = find('key', 'audio filename', 'label')
        src_col = find('original text', 'english dialogue', 'english text', 'original', 'text')
        dst_col = find('translated text', 'your translation', 'translation', 'localized text', 'translated')
        if src_col is None or dst_col is None:
            continue
        fields = [('text', src_col, dst_col)]
        subtitle_src, subtitle_dst = find('english subtitle'), find('translated subtitle')
        if subtitle_src and subtitle_dst:
            fields.append(('subtitle', subtitle_src, subtitle_dst))
        char_src, char_dst = find('character name'), find('translated character name')
        if char_src and char_dst:
            fields.append(('speaker', char_src, char_dst))
        context_cols = [find('person'), find('context'), find('direction')]
        for ri, row in enumerate(rows):
            if ri <= hi:
                continue
            cells = cell_map(row)
            key = text(cells.get(key_col)).strip()
            # Skip spreadsheet notes, headings and authoring paths.
            if not key or re.search(r'\s|[<>:"\\]', key) or key.endswith('/'):
                continue
            context = ' | '.join(text(cells.get(k)) for k in context_cols if k and text(cells.get(k)))
            for field, source_col, target_col in fields:
                source = text(cells.get(source_col))
                if not source.strip():
                    continue
                identity = f'{canonical(name).lower()}\0{si}\0{ri}\0{key}\0{field}'
                entries.append({
                    'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                    'file': canonical(name), 'sheet': sheet.get(S + 'Name', str(si)),
                    'sheet_index': si, 'row_index': ri, 'key': key, 'field': field,
                    'source': source, 'existing': text(cells.get(target_col)),
                    'translation': '', 'context': context,
                    'source_col': source_col, 'target_col': target_col,
                })
    return entries


def sha(data):
    return hashlib.sha256(data).hexdigest()


def new_project(pak_path, game_id=None):
    pak_path = Path(pak_path).resolve()
    game_id = game_id or games.detect(pak_path) or games.DEFAULT
    if game_id not in games.PROFILES:
        raise LocalizerError(tr('Невідомий профіль гри'))
    entries, files, skipped = [], {}, []
    with Pak(pak_path) as pak:
        for item in pak.items.values():
            if not item.filename.lower().endswith('.xml'):
                continue
            data = pak.read(item)
            try:
                found = extract_entries(item.filename, data)
            except (ET.ParseError, LocalizerError) as e:
                skipped.append({'file': canonical(item.filename), 'reason': str(e)})
                continue
            if found:
                files[canonical(item.filename)] = sha(data)
                entries.extend(found)
            else:
                skipped.append({'file': canonical(item.filename), 'reason': tr('Немає підтримуваної таблиці локалізації')})
    if not entries:
        raise LocalizerError(tr('У PAK немає підтримуваних XML-таблиць локалізації. Спробуйте english_xml.pak або english.pak.'))
    priority = {'ui_text_messages.xml': 0, 'game_text_messages.xml': 1, 'game_controls.xml': 2,
                'text_platformspecific.xml': 3, 'ui_credit_list.xml': 7}
    entries.sort(key=lambda e: (priority.get(Path(e['file']).name.lower(), 5), e['file'].lower(), e['sheet_index'], e['row_index']))
    return {'schema': SCHEMA, 'game_id': game_id, 'source_pak': str(pak_path), 'target_language': 'uk',
            'files': files, 'entries': entries, 'skipped': skipped}


def check_source_pak(project, path):
    """Relocate a project between machines only when its source XML is identical."""
    with Pak(path) as pak:
        for filename, digest in project['files'].items():
            item = pak.items.get(canonical(filename).casefold())
            if item is None or sha(pak.read(item)) != digest:
                raise LocalizerError(tr('Вибраний PAK не відповідає вихідним XML цього проєкту. Створіть новий проєкт для іншої версії гри.'))


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_project(project, path):
    atomic_bytes(path, (json.dumps(project, ensure_ascii=False, indent=2) + '\n').encode())


def load_project(path):
    p = json.loads(Path(path).read_text(encoding='utf-8'))
    if p.get('schema') != SCHEMA or not isinstance(p.get('entries'), list):
        raise LocalizerError(tr('Невідомий формат проєкту'))
    if 'game_id' not in p:
        p['game_id'] = games.detect(p.get('source_pak', '')) or games.DEFAULT
    if p['game_id'] not in games.PROFILES:
        raise LocalizerError(tr('Невідомий профіль гри'))
    return p


def export_csv(project, path):
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, CSV_FIELDS, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(project['entries'])
    atomic_bytes(path, buf.getvalue().encode('utf-8-sig'))


def import_csv(project, path):
    records = {e['id']: e for e in project['entries']}
    pending, seen = [], set()
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames or not {'id', 'source', 'translation'}.issubset(reader.fieldnames):
            raise LocalizerError(tr('CSV має містити колонки id, source, translation'))
        for line, row in enumerate(reader, 2):
            eid = row.get('id')
            if eid not in records or eid in seen:
                raise LocalizerError(tr('CSV, рядок {0}: невідомий або повторний id').format(line))
            seen.add(eid)
            if row.get('source') != records[eid]['source'] or row.get('translation') is None:
                raise LocalizerError(tr('CSV, рядок {0}: змінено оригінал або бракує перекладу').format(line))
            pending.append((records[eid], row['translation']))
    for entry, translation in pending:
        entry['translation'] = translation
    return len(pending)


TOKEN = re.compile(r'%%|%(?:\d+\$)?[-+#0 ]*\d*(?:\.\d+)?(?:hh|ll|[hlLzjt])?[diuoxXfFeEgGaAcspn]|%\d+|\{[^{}\n]+\}|\$\d|\$[A-Za-z_][\w]*|@[A-Za-z_][\w]*|\\[nrt]|##|</?[A-Za-z][^>]*>')


def tokens(value):
    return Counter(TOKEN.findall(value))


def validate(project):
    issues = []
    for entry in project['entries']:
        dst = entry['translation']
        if not dst:
            continue
        invalid = [c for c in dst if not (c in '\t\n\r' or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)]
        if invalid:
            issues.append((entry['id'], entry['key'], tr('Недопустимі для XML символи')))
        if not dst.strip():
            issues.append((entry['id'], entry['key'], tr('Переклад містить лише пробіли')))
        if tokens(entry['source']) != tokens(dst):
            issues.append((entry['id'], entry['key'], tr('Змінено службові вставки: ') + repr(dict(tokens(entry['source'])))))
    return issues


def spans(data):
    """Locate SS Row/Cell/Data byte spans without rewriting namespace declarations."""
    parser = expat.ParserCreate(namespace_separator='|')
    wanted = {SS + '|' + n: n for n in ('Row', 'Cell', 'Data')}
    stack, result = [], {n: [] for n in ('Row', 'Cell', 'Data')}
    opening_tag = re.compile(br'<(?:[^>\'\"]|\"[^\"]*\"|\'[^\']*\')*>')
    def start(name, attrs):
        if name in wanted:
            a = parser.CurrentByteIndex
            end = data.find(b'>', a) + 1
            # '>' may occur in quoted XML attributes.
            match = opening_tag.match(data, a)
            if match:
                end = match.end()
            info = [a, end, None, None, data[a:end].rstrip().endswith(b'/>')]
            result[wanted[name]].append(info)
            stack.append((name, info))
    def end(name):
        if name in wanted:
            actual, info = stack.pop()
            assert actual == name
            if info[4]:
                info[2] = info[3] = info[1]
            else:
                info[2] = parser.CurrentByteIndex
                info[3] = data.find(b'>', info[2]) + 1
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.Parse(data, True)
    return result


def patch_xml(original, changes):
    if not changes:
        return original
    data = xml_bytes(original)
    root = xml_root(data)
    positions = spans(data)
    row_elements = list(root.iter(S + 'Row'))
    cell_elements = list(root.iter(S + 'Cell'))
    data_elements = list(root.iter(S + 'Data'))
    row_span = dict(zip(map(id, row_elements), positions['Row']))
    cell_span = dict(zip(map(id, cell_elements), positions['Cell']))
    data_span = dict(zip(map(id, data_elements), positions['Data']))
    edits, insertions = [], {}
    sheets = root.findall(S + 'Worksheet')
    for (si, ri, col), value in changes.items():
        row = sheets[si].find(S + 'Table').findall(S + 'Row')[ri]
        cells = cell_map(row)
        cell = cells.get(col)
        encoded = escape(value).replace('\r', '&#13;').encode('utf-8')
        if cell is not None:
            d = cell.find(S + 'Data')
            if d is not None:
                a, b, c, end, selfclose = data_span[id(d)]
                if selfclose:
                    opening = data[a:b].rstrip()[:-2] + b'>'
                    tag = re.match(br'<([^\s/>]+)', opening).group(1)
                    edits.append((a, end, opening + encoded + b'</' + tag + b'>'))
                else:
                    edits.append((b, c, encoded))
            else:
                a, b, c, end, selfclose = cell_span[id(cell)]
                inner = f'<ss:Data xmlns:ss="{SS}" ss:Type="String">'.encode() + encoded + b'</ss:Data>'
                if selfclose:
                    opening = data[a:b].rstrip()[:-2] + b'>'
                    tag = re.match(br'<([^\s/>]+)', opening).group(1)
                    edits.append((a, end, opening + inner + b'</' + tag + b'>'))
                else:
                    edits.append((c, c, inner))
        else:
            for existing_col, existing_cell in cells.items():
                merge_end = existing_col + int(existing_cell.get(S + 'MergeAcross', 0))
                if existing_col < col <= merge_end:
                    raise LocalizerError(tr('Переклад потрапляє в об’єднану комірку'))
            following = next((c for k, c in cells.items() if k > col), None)
            pos = cell_span[id(following)][0] if following is not None else row_span[id(row)][2]
            inner = (f'<ss:Cell xmlns:ss="{SS}" ss:Index="{col}"><ss:Data ss:Type="String">'.encode()
                     + encoded + b'</ss:Data></ss:Cell>')
            insertions.setdefault(pos, []).append((col, inner))
    for pos, parts in insertions.items():
        edits.append((pos, pos, b''.join(v for _, v in sorted(parts))))
    for a, b, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        data = data[:a] + replacement + data[b:]
    # Ensure XML and sparse-column addressing survived the byte edits.
    check = xml_root(data)
    for (si, ri, col), value in changes.items():
        row = check.findall(S + 'Worksheet')[si].find(S + 'Table').findall(S + 'Row')[ri]
        if text(cell_map(row).get(col)) != value:
            raise LocalizerError(tr('Не вдалося зберегти індекси комірок XML'))
    return data


def build(project, output, font_pak=None, progress=None):
    """Build a full replacement of the source language slot; never install it."""
    game_id = project.get('game_id', games.detect(project.get('source_pak', '')) or games.DEFAULT)
    if game_id not in games.PROFILES:
        raise LocalizerError(tr('Невідомий профіль гри'))
    issues = validate(project)
    if issues:
        raise LocalizerError(tr('Виправте {0} помилок перед збиранням. {1}: {2}').format(len(issues), issues[0][1], issues[0][2]))
    source, output = Path(project['source_pak']).resolve(), Path(output).resolve()
    if source == output or (output.exists() and os.path.samefile(source, output)):
        raise LocalizerError(tr('Збережіть новий PAK окремо від оригінального'))
    grouped, references = {}, {}
    translated = 0
    for e in project['entries']:
        if e['translation']:
            translated += 1
            changes = grouped.setdefault(e['file'].casefold(), {})
            references.setdefault(e['file'].casefold(), []).append(e)
            # The source language slot may read its original column. Write both
            # display columns; the immutable source remains in the project/CSV.
            for col in (e['source_col'], e['target_col']):
                key = (e['sheet_index'], e['row_index'], col)
                if key in changes and changes[key] != e['translation']:
                    raise LocalizerError(tr('Конфлікт перекладів однієї комірки'))
                changes[key] = e['translation']
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + output.name + '-', dir=output.parent)
    os.close(fd)
    fonts, font_source = {}, None
    try:
        if font_pak:
            font_source = Path(font_pak).resolve()
            if output == font_source or (output.exists() and os.path.samefile(output, font_source)):
                raise LocalizerError(tr('Вихідний файл збігається з пакетом шрифтів'))
            with Pak(font_source) as donor:
                for name, item in donor.items.items():
                    if 'font' in name and name.endswith(('.gfx', '.ttf', '.otf')):
                        fonts[name] = donor.read(item)
            if not fonts:
                raise LocalizerError(tr('У вибраному пакеті немає розпізнаних шрифтів'))
        with Pak(source) as pak, zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as dest:
            expected = {name.casefold(): digest for name, digest in project['files'].items()}
            if not expected.keys() <= pak.items.keys():
                raise LocalizerError(tr('У вихідному PAK бракує файлів проєкту'))
            if not grouped.keys() <= expected.keys():
                raise LocalizerError(tr('Проєкт посилається на невідомий XML-файл'))
            for num, (name, item) in enumerate(pak.items.items(), 1):
                value = pak.read(item)
                if name in expected and sha(value) != expected[name]:
                    raise LocalizerError(tr('Вихідний XML змінився: {0}. Створіть новий проєкт.').format(item.filename))
                if name in grouped:
                    originals = {e['id']: e for e in extract_entries(item.filename, value)}
                    fields = ('key', 'field', 'source', 'sheet_index', 'row_index', 'source_col', 'target_col')
                    for entry in references[name]:
                        base = originals.get(entry['id'])
                        if base is None or any(base[k] != entry[k] for k in fields):
                            raise LocalizerError(tr('У проєкті змінено адресу або оригінал рядка: {0}').format(entry['key']))
                    value = patch_xml(value, grouped[name])
                if name in fonts:
                    value = fonts.pop(name)
                info = copy.copy(item)
                info.filename = info.orig_filename = canonical(item.filename)
                info.compress_type = zipfile.ZIP_STORED
                info.extra = b''
                dest.writestr(info, value)
                if progress and num % 400 == 0:
                    progress(num, len(pak.items))
            if fonts:
                raise LocalizerError(tr('Структура шрифтів донора не збігається з базовим PAK'))
        with zipfile.ZipFile(tmp) as check:
            bad = check.testzip()
            if bad:
                raise LocalizerError(tr('Помилка CRC у створеному архіві: {0}').format(bad))
        os.replace(tmp, output)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    report = {'output': str(output), 'game_id': game_id, 'game': games.title(game_id),
              'target_language': project.get('target_language', 'uk'),
              'translated_entries': translated, 'total_entries': len(project['entries']),
              'source_pak': str(source), 'font_donor': str(font_source) if font_source else None,
              'installation': tr('Замініть копією створеного PAK однойменний мовний пакет після резервного копіювання. У грі виберіть мову вихідного пакета.'),
              'in_game_verified': False}
    atomic_bytes(str(output) + '.report.json', (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
    return report
