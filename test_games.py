import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import core
import games
from test_core import XML


class GameProfileTests(unittest.TestCase):
    def test_profiles_legacy_loading_and_build_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for game_id, title in games.PROFILES.items():
                folder = root / title
                folder.mkdir()
                pak = folder / 'english.pak'
                with zipfile.ZipFile(pak, 'w') as z:
                    z.writestr('languages/ui.xml', XML)
                self.assertEqual(games.detect(pak), game_id)
                project = core.new_project(pak, game_id)
                project['entries'][1]['translation'] = 'Ще'
                path = folder / 'project.json'
                core.save_project(project, path)
                self.assertEqual(core.load_project(path)['game_id'], game_id)
                result = core.build(project, folder / 'build' / 'english.pak')
                self.assertEqual(result['game_id'], game_id)
                self.assertEqual(result['game'], title)
                project.pop('game_id')
                core.save_project(project, path)
                self.assertEqual(core.load_project(path)['game_id'], game_id)
                project['game_id'] = 'unknown'
                core.save_project(project, path)
                with self.assertRaises(core.LocalizerError):
                    core.load_project(path)
                with self.assertRaises(core.LocalizerError):
                    core.build(project, folder / 'invalid-output.pak')
                self.assertFalse((folder / 'invalid-output.pak').exists())
            self.assertEqual(games.detect(r'D:\Games\Crysis Remastered\Game\Localized\english.pak'), games.DEFAULT)
            self.assertIsNone(games.detect('D:/Games/Unknown/english.pak'))

    def test_game_switch_loads_separate_project_and_cancel_restores(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication, QFileDialog
        from app import Window, QMessageBox
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CRYLOCAL_DATA_DIR': tmp}):
            root = Path(tmp)
            packets = []
            for number in (1, 2):
                pak = root / f'game{number}.pak'
                with zipfile.ZipFile(pak, 'w') as z:
                    z.writestr('languages/ui.xml', XML.replace(b'Hello', b'Welcome') if number == 2 else XML)
                packets.append(pak)
            first, second = root / 'first.json', root / 'second.json'
            core.save_project(core.new_project(packets[0], games.DEFAULT), first)
            window = Window(first, language='uk')
            window.show()
            app.processEvents()
            window.translation.setPlainText('Привіт %s & {0}')
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.Save), \
                 patch.object(QFileDialog, 'getOpenFileName', return_value=(str(packets[1]), '')), \
                 patch.object(QFileDialog, 'getSaveFileName', return_value=(str(second), '')):
                window.game_choice.setCurrentIndex(window.game_choice.findData('crysis2-remastered'))
                self.assertTrue(window.worker.wait(5000))
                app.processEvents()
            self.assertEqual(core.load_project(first)['entries'][0]['translation'], 'Привіт %s & {0}')
            self.assertEqual(window.project['game_id'], 'crysis2-remastered')
            self.assertEqual(window.path, second)
            self.assertEqual(window.source.toPlainText(), 'Welcome %s & {0}')
            self.assertEqual(window.translation.toPlainText(), '')
            with patch.object(QFileDialog, 'getOpenFileName', return_value=('', '')) as picker:
                window.game_choice.setCurrentIndex(window.game_choice.findData('crysis3-remastered'))
                self.assertIn('Crysis 3 Remastered', picker.call_args.args[1])
            self.assertEqual(window.game_choice.currentData(), 'crysis2-remastered')
            self.assertEqual(window.project['source_pak'], str(packets[1]))
            invalid = root / 'invalid.pak'
            invalid.write_bytes(b'not a PAK')
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(invalid), '')), \
                 patch.object(QMessageBox, 'critical') as error:
                window.game_choice.setCurrentIndex(window.game_choice.findData('crysis3-remastered'))
                self.assertTrue(window.worker.wait(5000))
                app.processEvents()
                self.assertEqual(error.call_count, 1)
            self.assertEqual(window.game_choice.currentData(), 'crysis2-remastered')
            self.assertEqual(window.project['game_id'], 'crysis2-remastered')
            window.close()
            reopened = Window(second, language='en')
            self.assertEqual(reopened.game_choice.currentData(), 'crysis2-remastered')
            self.assertIn('Crysis 2 Remastered', reopened.windowTitle())
            reopened.close()
