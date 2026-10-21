import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import core
import runtime
from translate import Translator


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class ModelTests(unittest.TestCase):
    def test_runtime_selects_gpu_only_or_cpu_layers(self):
        for backend, expected in [('vulkan', '999'), ('cpu', '0')]:
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                engine = runtime.Runtime(tmp)
                engine.model.parent.mkdir(parents=True)
                with engine.model.open('wb') as model:
                    model.truncate(runtime.MODEL_SIZE)
                binary = engine.runtime / 'bin' / 'llama-server'
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b'')
                (engine.folder / 'installed.json').write_text(
                    json.dumps({'backend': backend}), encoding='utf-8')
                sock = MagicMock()
                sock.__enter__.return_value = sock
                sock.bind.return_value = None
                opener = MagicMock()
                opener.open.return_value.__enter__.return_value = io.BytesIO(b'{"status":"ok"}')
                process = MagicMock()
                process.poll.return_value = None
                with patch('runtime.socket.socket', return_value=sock), \
                     patch('runtime.subprocess.Popen', return_value=process) as popen, \
                     patch('runtime.build_opener', return_value=opener):
                    engine.start(backend=backend)
                args = popen.call_args.args[0]
                self.assertEqual(args[args.index('--n-gpu-layers') + 1], expected)
                if backend == 'vulkan':
                    self.assertTrue(engine.installed('vulkan'))
                engine.stop()

    def test_builtin_payload_token_restore_and_cache(self):
        translator = Translator()
        def reply(path, body):
            self.assertEqual(path, '/completion')
            self.assertIn('<start_of_turn>user\n', body['prompt'])
            self.assertIn('ZXQPLACEHOLDER0000QXZ', body['prompt'])
            self.assertNotIn('Hello %s', body['prompt'])
            return {'content': 'Привіт ZXQPLACEHOLDER0000QXZ!'}
        with patch.object(translator, 'request', side_effect=reply) as mock:
            self.assertEqual(translator.translate('Hello %s!'), 'Привіт %s!')
            self.assertEqual(translator.translate('Hello %s!'), 'Привіт %s!')
            self.assertEqual(mock.call_count, 1)

    def test_three_letter_uppercase_abbreviations_are_preserved(self):
        translator = Translator()
        def reply(path, body):
            prompt = body['prompt']
            self.assertIn('ZXQPLACEHOLDER0000QXZ', prompt)
            self.assertIn('ZXQPLACEHOLDER0001QXZ', prompt)
            self.assertNotIn('MHP', prompt)
            self.assertNotIn('SMG', prompt)
            return {'content': 'Підібрати ZXQPLACEHOLDER0000QXZ для ZXQPLACEHOLDER0001QXZ'}
        with patch.object(translator, 'request', side_effect=reply):
            self.assertEqual(translator.translate('Find MHP for SMG'), 'Підібрати MHP для SMG')

    def test_three_letter_abbreviation_only_is_preserved_through_model(self):
        translator = Translator()
        with patch.object(translator, 'request', return_value={'content': 'ZXQPLACEHOLDER0000QXZ'}):
            self.assertEqual(translator.translate('SMG'), 'SMG')

    def test_reject_changed_placeholder_and_truncation(self):
        translator = Translator()
        with patch.object(translator, 'request', return_value={'content': 'Привіт!'}):
            with self.assertRaises(core.LocalizerError):
                translator.translate('Hello {0}!')
        with patch.object(translator, 'request', return_value={'content': 'Привіт', 'stopped_limit': True}):
            with self.assertRaises(core.LocalizerError):
                translator.translate('Hello')

    def test_openai_ollama_and_hf_contracts(self):
        for provider in ('openai', 'hf', 'ollama'):
            t = Translator({'provider': provider, 'base_url': 'http://127.0.0.1:1234/v1' if provider != 'ollama' else 'http://127.0.0.1:11434'})
            response = {'message': {'content': 'Привіт'}} if provider == 'ollama' else {'choices': [{'message': {'content': 'Привіт'}, 'finish_reason': 'stop'}]}
            with patch.object(t, 'request', return_value=response) as mock:
                self.assertEqual(t.translate('Hello'), 'Привіт')
                path, body = mock.call_args.args
                self.assertEqual(path, '/api/chat' if provider == 'ollama' else '/chat/completions')
                if provider == 'hf':
                    self.assertEqual(body['messages'][0]['content'][0]['target_lang_code'], 'uk')

    def test_resume_download_hash_and_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'model.gguf'
            data = b'GGUFtest-data-contents'
            digest = hashlib.sha256(data).hexdigest()
            partial = Path(str(target) + '.part')
            partial.write_bytes(data[:4])
            response = Response(data[4:], 206, {'Content-Range': f'bytes 4-{len(data)-1}/{len(data)}'})
            with patch('runtime.build_opener') as opener:
                opener.return_value.open.return_value = response
                runtime.download('https://example.test/model', target, digest, len(data))
                self.assertEqual(opener.return_value.open.call_args.args[0].get_header('Range'), 'bytes=4-')
            self.assertEqual(target.read_bytes(), data)
            self.assertFalse(partial.exists())
            target.unlink()
            with patch('runtime.build_opener') as opener:
                opener.return_value.open.return_value = Response(data)
                with self.assertRaises(runtime.Cancelled):
                    runtime.download('https://example.test/model', target, digest, len(data), cancel=lambda: True)
            self.assertFalse(target.exists())
            with patch('runtime.build_opener') as opener:
                opener.return_value.open.return_value = Response(data)
                with self.assertRaises(core.LocalizerError):
                    runtime.download('https://example.test/model', target, '0'*64, len(data))
            self.assertFalse(target.exists())
            self.assertFalse(partial.exists())

    def test_extract_runtime_links_and_reject_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'runtime.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                data = b'lib content'
                item = tarfile.TarInfo('bin/lib.so.1.0')
                item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
                for name, target in [('bin/lib.so', 'lib.so.1'), ('bin/lib.so.1', 'lib.so.1.0')]:
                    item = tarfile.TarInfo(name)
                    item.type = tarfile.SYMTYPE
                    item.linkname = target
                    tar.addfile(item)
            runtime.extract_archive(archive, root / 'out')
            self.assertEqual((root / 'out/bin/lib.so').read_bytes(), data)
            with tarfile.open(archive, 'w:gz') as tar:
                item = tarfile.TarInfo('../outside')
                item.size = 1
                tar.addfile(item, io.BytesIO(b'x'))
            with self.assertRaises(core.LocalizerError):
                runtime.extract_archive(archive, root / 'other')
            self.assertFalse((root / 'outside').exists())


if __name__ == '__main__':
    unittest.main()
