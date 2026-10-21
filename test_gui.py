import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import core
from test_core import XML


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'PySide6 is optional for the CLI')
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.application = QApplication.instance() or QApplication([])

    def test_clipboard_edit_filter_and_save(self):
        from app import Window
        from PySide6.QtWidgets import QDialog
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CRYLOCAL_DATA_DIR': tmp}):
            root = Path(tmp)
            pak = root / 'english.pak'
            with zipfile.ZipFile(pak, 'w') as z:
                z.writestr('languages/ui.xml', XML)
            path = root / 'project.json'
            core.save_project(core.new_project(pak), path)
            window = Window(path)
            window.show()
            self.application.processEvents()
            self.assertEqual(window.current, 0)
            window.copy_source()
            self.assertEqual(self.application.clipboard().text(), 'Hello %s & {0}')
            self.application.clipboard().setText('Привіт %s & {0}')
            window.paste_translation()
            window.commit()
            self.assertEqual(window.project['entries'][0]['translation'], 'Привіт %s & {0}')
            window.search.setText('ui_test')
            self.assertEqual(window.proxy.rowCount(), 1)
            window.states.setCurrentIndex(1)
            self.assertEqual(window.proxy.rowCount(), 0)
            window.states.setCurrentIndex(0)
            window.search.clear()
            self.assertEqual(window.proxy.rowCount(), 2)
            window.table.selectRow(1)
            window.translation.setPlainText('Ще')
            window.next()
            self.assertEqual(window.project['entries'][1]['translation'], 'Ще')
            self.assertTrue(window.save())
            self.assertEqual(core.load_project(path)['entries'][1]['translation'], 'Ще')
            self.assertFalse(window.dirty)
            class FakeTranslator:
                def translate(self, value):
                    return 'Більше'
            with patch.object(window, 'translator', return_value=FakeTranslator()), \
                 patch('app.QMessageBox.information') as info:
                window.translate_one()
                self.assertTrue(window.worker.wait(5000))
                self.application.processEvents()
                self.assertEqual(window.translation.toPlainText(), 'Більше')
                self.assertEqual(window.project['entries'][1]['translation'], 'Більше')
                self.assertTrue(info.called)
                self.assertTrue(window.save())
                self.assertEqual(core.load_project(path)['entries'][1]['translation'], 'Більше')
                window.project['entries'][1]['translation'] = ''
                window.refresh_entries()
                with patch.object(QDialog, 'exec', return_value=QDialog.Accepted), patch.object(window, 'show_text'):
                    window.translate_batch()
                    self.assertTrue(window.worker.wait(5000))
                    self.application.processEvents()
                self.assertEqual(core.load_project(path)['entries'][1]['translation'], 'Більше')
                self.assertTrue(window.table.currentIndex().isValid())
            window.close()
