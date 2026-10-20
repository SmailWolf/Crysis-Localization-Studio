import copy
import csv
import io
from pathlib import Path
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import core

XML = ('''<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet" xmlns:x="urn:schemas-microsoft-com:office:excel">
 <Styles><Style ss:ID="s1"><x:Something>preserve me</x:Something></Style></Styles>
 <Worksheet ss:Name="UI"><Table ss:ExpandedColumnCount="5">
 <Row><Cell><Data ss:Type="String">KEY</Data></Cell><Cell><Data ss:Type="String">ORIGINAL TEXT</Data></Cell><Cell><Data ss:Type="String">TRANSLATED TEXT</Data></Cell></Row>
 <Row ss:Index="5"><Cell><Data ss:Type="String">ui_test</Data></Cell><Cell><Data ss:Type="String">Hello %s &amp; {0}</Data></Cell><Cell ss:Index="5"><Data ss:Type="String">metadata</Data></Cell></Row>
 <Row><Cell><Data ss:Type="String">ui_more</Data></Cell><Cell><Data ss:Type="String">More</Data></Cell><Cell ss:StyleID="s1" /></Row>
 </Table></Worksheet></Workbook>''').encode()


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pak = self.root / 'english.pak'
        with zipfile.ZipFile(self.pak, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('languages/ui.xml', XML)
            z.writestr('languages/dialog/voice.mp2', b'unchanged voice\x00' * 100)

    def tearDown(self):
        self.temp.cleanup()

    def test_crytek_slash_mismatch_and_crc(self):
        data = self.pak.read_bytes()
        # Only local names use backslashes, central-directory names use slashes.
        offset = data.index(b'languages/ui.xml')
        data = data[:offset] + b'languages\\ui.xml' + data[offset + len(b'languages/ui.xml'):]
        self.pak.write_bytes(data)
        with self.assertRaises(zipfile.BadZipFile), zipfile.ZipFile(self.pak) as z:
            z.read('languages/ui.xml')
        with core.Pak(self.pak) as z:
            self.assertEqual(z.read('languages/ui.xml'), XML)
        offset = data.index(b'languages\\ui.xml')
        self.pak.write_bytes(data[:offset] + b'othername/ui.xml' + data[offset + len(b'languages/ui.xml'):])
        with core.Pak(self.pak) as z, self.assertRaises(core.LocalizerError):
            z.read('languages/ui.xml')

    def test_sparse_cells_preserve_unknown_data(self):
        project = core.new_project(self.pak)
        self.assertEqual(len(project['entries']), 2)
        entry = project['entries'][0]
        self.assertEqual(entry['row_index'], 1)  # physical row, not ss:Index
        project['entries'][0]['translation'] = 'Привіт %s & {0}'
        project['entries'][1]['translation'] = 'Ще ҐґЄєІіЇї\r\nтекст'
        output = self.root / 'built.pak'
        core.build(project, output)
        with core.Pak(output) as z:
            b = z.read('languages/ui.xml')
            entries = core.extract_entries('languages/ui.xml', b)
            self.assertEqual(entries[0]['source'], 'Привіт %s & {0}')
            self.assertEqual(entries[0]['existing'], 'Привіт %s & {0}')
            self.assertEqual(entries[1]['existing'], 'Ще ҐґЄєІіЇї\r\nтекст')
            self.assertIn(b'<x:Something>preserve me</x:Something>', b)
            self.assertIn(b'<Data ss:Type="String">metadata</Data>', b)
            self.assertEqual(z.read('languages/dialog/voice.mp2'), b'unchanged voice\x00' * 100)

    def test_unchanged_xml_byte_identity_and_overwrite_guard(self):
        p = core.new_project(self.pak)
        output = self.root / 'out.pak'
        core.build(p, output)
        with core.Pak(output) as z:
            self.assertEqual(z.read('languages/ui.xml'), XML)
        with self.assertRaises(core.LocalizerError):
            core.build(p, self.pak)
        self.assertEqual(core.patch_xml(XML, {}), XML)

    def test_build_replaces_matching_prebuilt_font_assets_from_donor(self):
        # Font glyphs are compiled into game assets; CryLocal can copy a
        # compatible prebuilt resource but does not synthesize new glyphs.
        source = self.root / 'source-with-font.pak'
        donor = self.root / 'uk-font-donor.pak'
        with zipfile.ZipFile(self.pak) as original, zipfile.ZipFile(source, 'w') as z:
            for info in original.infolist():
                z.writestr(info, original.read(info.filename))
            z.writestr('fonts/ui_font.gfx', b'base glyph set')
        with zipfile.ZipFile(donor, 'w') as z:
            z.writestr('fonts/ui_font.gfx', 'extended glyph set: Ґ Є І Ї Ў'.encode('utf-8'))
        project = core.new_project(source)
        output = self.root / 'font-built.pak'
        core.build(project, output, donor)
        with core.Pak(output) as built:
            self.assertEqual(built.read('fonts/ui_font.gfx'), 'extended glyph set: Ґ Є І Ї Ў'.encode('utf-8'))

    def test_tokens_invalid_characters_and_atomic_build(self):
        p = core.new_project(self.pak)
        p['entries'][0]['translation'] = 'Привіт {0}'
        self.assertTrue(core.validate(p))
        out = self.root / 'out.pak'
        out.write_bytes(b'existing output')
        with self.assertRaises(core.LocalizerError):
            core.build(p, out)
        self.assertEqual(out.read_bytes(), b'existing output')
        p['entries'][0]['translation'] = 'Привіт %s {0}\x01'
        self.assertTrue(core.validate(p))
        self.assertEqual(core.tokens('%% %1$d %s {0} @action $4 \\n ## <b>x</b>'),
                         core.tokens('%% %1$d %s {0} @action $4 \\n ## <b>у</b>'))

    def test_csv_multiline_and_transactional_rejection(self):
        p = core.new_project(self.pak)
        p['entries'][0]['translation'] = 'Привіт, %s; {0}\n"текст"'
        path = self.root / 'translations.csv'
        core.export_csv(p, path)
        other = core.new_project(self.pak)
        self.assertEqual(core.import_csv(other, path), 2)
        self.assertEqual(other['entries'][0]['translation'], p['entries'][0]['translation'])
        before = copy.deepcopy(other)
        lines = list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
        lines[0]['translation'] = 'new translation'
        lines[1]['source'] = 'tampered source'
        with path.open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, core.CSV_FIELDS)
            writer.writeheader()
            writer.writerows(lines)
        with self.assertRaises(core.LocalizerError):
            core.import_csv(other, path)
        self.assertEqual(other, before)

    def test_source_changed_and_unsupported_formats(self):
        p = core.new_project(self.pak)
        p['files']['languages/ui.xml'] = 'wrong hash'
        with self.assertRaises(core.LocalizerError):
            core.build(p, self.root / 'out.pak')
        self.assertFalse((self.root / 'out.pak').exists())
        p = core.new_project(self.pak)
        p['entries'][0]['translation'] = 'Привіт %s & {0}'
        p['entries'][0]['target_col'] = 5
        with self.assertRaises(core.LocalizerError):
            core.build(p, self.root / 'out.pak')
        self.assertFalse((self.root / 'out.pak').exists())
        with self.assertRaises(core.LocalizerError):
            core.extract_entries('binary.xml', b'CryXmlB\x00')
        with self.assertRaises(core.LocalizerError):
            core.extract_entries('unsafe.xml', b'<!DOCTYPE test><test/>')

    def test_discovery_case_insensitive(self):
        folder = self.root / 'GameCrysis3' / 'Localization'
        folder.mkdir(parents=True)
        (folder / 'English_XML.pak').write_bytes(b'')
        self.assertIn(folder / 'English_XML.pak', core.discover(self.root))


if __name__ == '__main__':
    unittest.main()
