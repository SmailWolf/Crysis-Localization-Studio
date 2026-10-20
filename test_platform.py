import ast
import json
import os
from pathlib import Path
import string
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import core
import i18n
import paths
from test_core import XML


class PlatformTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language('uk')

    def test_catalog_complete_and_format_fields_preserved(self):
        folder = Path(__file__).parent
        for filename in ('app.py', 'core.py', 'runtime.py', 'translate.py', 'crylocal.py'):
            for node in ast.walk(ast.parse((folder / filename).read_text(encoding='utf-8'))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr':
                    if isinstance(node.args[0], ast.Constant):
                        self.assertIn(node.args[0].value, i18n.EN, (filename, node.lineno))
        formatter = string.Formatter()
        for key, value in i18n.EN.items():
            self.assertTrue(value, key)
            fields = lambda text: [(field, spec, conversion) for _, field, spec, conversion in formatter.parse(text) if field is not None]
            self.assertEqual(fields(key), fields(value), key)

    def test_windows_data_location_and_preferences(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}), patch('platform.system', return_value='Windows'):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('CRYLOCAL_DATA_DIR', None)
                os.environ.pop('CRYLOCAL_LANGUAGE', None)
                self.assertEqual(paths.data_dir(), Path(tmp) / 'CryLocal')
                self.assertEqual(i18n.preferred_language(), 'en')
                paths.save_preferences({'interface_language': 'uk'})
                paths.save_preferences({'last_game_directory': 'D:/Ігри/Crysis Remastered'})
                self.assertEqual(i18n.preferred_language(), 'uk')
                self.assertEqual(paths.load_preferences()['last_game_directory'], 'D:/Ігри/Crysis Remastered')

    def test_project_relocation_checks_source_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, other = (root / name for name in ('original.pak', 'перенесений файл.pak', 'other.pak'))
            for target, value in [(first, XML), (second, XML), (other, XML.replace(b'Hello', b'Howdy'))]:
                with zipfile.ZipFile(target, 'w') as z:
                    z.writestr('languages/ui.xml', value)
            project = core.new_project(first)
            core.check_source_pak(project, second)
            with self.assertRaises(core.LocalizerError):
                core.check_source_pak(project, other)
            self.assertEqual(project['source_pak'], str(first))

    def test_language_switch_keeps_draft_filter_and_preferences(self):
        from PySide6.QtWidgets import QApplication, QPushButton, QDialogButtonBox
        from PySide6.QtCore import Qt
        from app import Window
        from ui_messages import localize_buttons
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CRYLOCAL_DATA_DIR': tmp}):
            root = Path(tmp)
            pak, path = root / 'english.pak', root / 'project.json'
            with zipfile.ZipFile(pak, 'w') as z:
                z.writestr('languages/ui.xml', XML)
            core.save_project(core.new_project(pak), path)
            window = Window(path, language='uk')
            window.show()
            app.processEvents()
            original = window.source.toPlainText()
            draft = 'Привіт %s & {0}'
            window.translation.setPlainText(draft)
            window.search.setText('ui_test')
            window.table.selectRow(0)
            window.interface_language.setCurrentIndex(window.interface_language.findData('en'))
            self.assertEqual(window.translation.toPlainText(), draft)
            self.assertEqual(window.source.toPlainText(), original)
            self.assertEqual(window.search.text(), 'ui_test')
            self.assertEqual(window.proxy.rowCount(), 1)
            self.assertEqual(window.model.headerData(1, Qt.Horizontal), 'Key')
            self.assertIn('New project', [b.text() for b in window.findChildren(QPushButton)])
            self.assertIn('rows', window.summary.text())
            self.assertEqual(paths.load_preferences()['interface_language'], 'en')
            window.save()
            window.close()
            restored = Window(path)
            self.assertEqual(restored.interface_language.currentData(), 'en')
            restored.interface_language.setCurrentIndex(restored.interface_language.findData('uk'))
            self.assertEqual(restored.model.headerData(1, Qt.Horizontal), 'Ключ')
            self.assertEqual(restored.translation.toPlainText(), draft)
            box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            localize_buttons(box)
            self.assertEqual(box.button(QDialogButtonBox.Save).text(), 'Зберегти')
            restored.close()
