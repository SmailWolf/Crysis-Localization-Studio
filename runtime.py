"""App-owned TranslateGemma download, integrity check and llama.cpp process."""
from __future__ import annotations

from i18n import tr
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
import zipfile
import core

MODEL_REPO = 'mradermacher/translategemma-4b-it-GGUF'
MODEL_REVISION = 'd37868f759d2f817a43d28dd80cf9f982ffa62fd'
MODEL_NAME = 'translategemma-4b-it.Q4_K_M.gguf'
MODEL_SIZE = 2489909760
MODEL_SHA256 = '81200d03e843d2ec1ece6eeafe7d13cb6e5211e1fcd336ade55790b683a08330'
MODEL_URL = f'https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{MODEL_NAME}'
LLAMA_TAG = 'b11756'
BUILDS = {
    ('Linux', 'x86_64', 'cpu'): ('llama-b11756-bin-ubuntu-x64.tar.gz', '9def57d9e9b0149979a9ceba40896a4e2bf88c94734069a5567d2f487ceab551', 22240705),
    ('Windows', 'AMD64', 'cpu'): ('llama-b11756-bin-win-cpu-x64.zip', 'd164195b8f3e92f8fd92bc3a5c0eeaef8707b2f1a979e36360ce8192ebf4fbb3', 23959301),
    ('Linux', 'x86_64', 'vulkan'): ('llama-b11756-bin-ubuntu-vulkan-x64.tar.gz', '7b23a9192e013ee8cf2011c192fd6a6197a41b562ff1b1a8e41f523a933d75cf', 34877974),
    ('Windows', 'AMD64', 'vulkan'): ('llama-b11756-bin-win-vulkan-x64.zip', '75ce69fdda503c3d6d3b3401f61369bd072194746ec65fbbc5fd7e749edd2914', 36751610),
}


class Cancelled(core.LocalizerError):
    pass


def file_hash(path, cancel=lambda: False):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(4 * 1024 * 1024):
            if cancel():
                raise Cancelled(tr('Дію скасовано'))
            h.update(block)
    return h.hexdigest()


def download(url, destination, digest, size, progress=lambda *args: None, cancel=lambda: False):
    """Resumable Range download with a pinned revision, size and SHA-256."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(str(destination) + '.part')
    if destination.exists() and destination.stat().st_size == size:
        progress(size, size, tr('Перевірка завантаженого файлу…'))
        if file_hash(destination, cancel) == digest:
            return destination
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        partial.unlink()
        offset = 0
    if shutil.disk_usage(destination.parent).free < size - offset + 256 * 1024 * 1024:
        raise core.LocalizerError(tr('Недостатньо вільного місця для моделі та середовища запуску'))
    if offset < size:
        headers = {'User-Agent': 'CryLocal/0.1', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        request = Request(url, headers=headers)
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
                if response.status == 206:
                    content_range = response.headers.get('Content-Range', '')
                    if not re.match(rf'bytes {offset}-\d+/{size}$', content_range):
                        raise core.LocalizerError(tr('Сервер повернув неправильний діапазон завантаження'))
                elif response.status == 200:
                    offset = 0
                else:
                    raise core.LocalizerError(tr('Неочікувана відповідь HTTP {0}').format(response.status))
                received = offset
                last = 0
                with partial.open('ab' if offset else 'wb') as out:
                    while True:
                        if cancel():
                            raise Cancelled(tr('Завантаження призупинено. Наступного разу воно продовжиться.'))
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        received += len(block)
                        if received > size:
                            raise core.LocalizerError(tr('Розмір завантаження перевищив очікуваний'))
                        out.write(block)
                        if time.monotonic() - last > 0.15:
                            progress(received, size, destination.name)
                            last = time.monotonic()
        except HTTPError as exc:
            raise core.LocalizerError(tr('Не вдалося завантажити файл: HTTP {0}. Часткове завантаження збережено.').format(exc.code)) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise core.LocalizerError(tr('Завантаження перервано: {0}. Натисніть завантаження знову для продовження.').format(exc)) from exc
    if not partial.exists() or partial.stat().st_size != size:
        raise core.LocalizerError(tr('Файл завантажено не повністю. Повторіть завантаження для продовження.'))
    progress(size, size, tr('Перевірка SHA-256…'))
    if file_hash(partial, cancel) != digest:
        partial.unlink()
        raise core.LocalizerError(tr('Контрольна сума не збігається. Неправильний файл видалено; повторіть завантаження.'))
    os.replace(partial, destination)
    return destination


def extract_archive(archive, target):
    """Extract trusted, hashed runtime, still constraining all paths and links."""
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    def inside(name):
        name = name.replace('\\', '/')
        if name.startswith('/') or ':' in name or '..' in name.split('/'):
            raise core.LocalizerError(tr('Небезпечний шлях у середовищі запуску'))
        path = (target / name).resolve()
        if not path.is_relative_to(target):
            raise core.LocalizerError(tr('Файл середовища виходить за межі папки'))
        return path
    if str(archive).endswith('.zip'):
        with zipfile.ZipFile(archive) as z:
            if sum(i.file_size for i in z.infolist()) > 1024 ** 3:
                raise core.LocalizerError(tr('Завеликий архів середовища'))
            for item in z.infolist():
                path = inside(item.filename)
                if item.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(item) as src, path.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive, 'r:*') as tar:
            if sum(m.size for m in tar.getmembers()) > 1024 ** 3:
                raise core.LocalizerError(tr('Завеликий архів середовища'))
            links = []
            for item in tar.getmembers():
                path = inside(item.name)
                if item.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif item.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(item) as src, path.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
                    path.chmod(0o755 if item.mode & 0o111 else 0o644)
                elif item.issym() or item.islnk():
                    links.append((path, item))
                else:
                    raise core.LocalizerError(tr('Непідтримуваний тип файлу середовища'))
            while links:
                pending = []
                for path, item in links:
                    link = (path.parent / item.linkname).resolve() if item.issym() else inside(item.linkname)
                    if not link.is_relative_to(target):
                        raise core.LocalizerError(tr('Небезпечне посилання в архіві середовища'))
                    if not link.is_file():
                        pending.append((path, item))
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(link, path)  # avoid platform symlink privileges
                if len(pending) == len(links):
                    raise core.LocalizerError(tr('Циклічне або відсутнє посилання в архіві середовища'))
                links = pending


class Runtime:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.model = self.folder / 'models' / MODEL_NAME
        self.runtime = self.folder / 'llama.cpp' / LLAMA_TAG
        self.process = None
        self.running_backend = None
        self.log = None
        self.base_url = None

    def binary(self):
        name = 'llama-server.exe' if platform.system() == 'Windows' else 'llama-server'
        return next(self.runtime.rglob(name), None) if self.runtime.exists() else None

    def installed(self, backend=None):
        if not (self.model.is_file() and self.model.stat().st_size == MODEL_SIZE and self.binary() is not None):
            return False
        if backend is None:
            return True
        try:
            metadata = json.loads((self.folder / 'installed.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return backend == 'cpu'  # Upgrade older CPU-only installs without re-downloading the runtime.
        return metadata.get('backend', 'cpu') == backend

    def install(self, progress=lambda *args: None, cancel=lambda: False, backend='cpu'):
        if backend not in ('cpu', 'vulkan'):
            raise core.LocalizerError(tr('Невідомий режим прискорення'))
        key = (platform.system(), platform.machine(), backend)
        if key not in BUILDS:
            raise core.LocalizerError(tr('Автозавантаження доступне для Linux x64 і Windows x64. Можна підключити зовнішній LM Studio / llama.cpp.'))
        asset, digest, size = BUILDS[key]
        archive = self.folder / 'downloads' / asset
        download(f'https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{asset}', archive, digest, size, progress, cancel)
        if not self.installed(backend):
            progress(0, 0, tr('Розпакування середовища llama.cpp…'))
            self.runtime.parent.mkdir(parents=True, exist_ok=True)
            temp = Path(tempfile.mkdtemp(prefix='.install-', dir=self.runtime.parent))
            try:
                extract_archive(archive, temp)
                if self.runtime.exists():
                    shutil.rmtree(self.runtime)
                os.replace(temp, self.runtime)
            finally:
                if temp.exists():
                    shutil.rmtree(temp)
            if not self.binary():
                raise core.LocalizerError(tr('У середовищі не знайдено llama-server'))
        download(MODEL_URL, self.model, MODEL_SHA256, MODEL_SIZE, progress, cancel)
        metadata = {'repo': MODEL_REPO, 'revision': MODEL_REVISION, 'filename': MODEL_NAME,
                    'sha256': MODEL_SHA256, 'bytes': MODEL_SIZE, 'llama_cpp': LLAMA_TAG,
                    'backend': backend}
        core.atomic_bytes(self.folder / 'installed.json', json.dumps(metadata, indent=2).encode())
        return metadata

    def start(self, progress=lambda *args: None, cancel=lambda: False, backend='cpu'):
        if self.process and self.process.poll() is None:
            if self.running_backend == backend:
                return self.base_url
            self.stop()
        if not self.model.is_file() or self.model.stat().st_size != MODEL_SIZE:
            raise core.LocalizerError(tr('Спочатку натисніть «Завантажити TranslateGemma 4B»'))
        if not self.installed(backend):
            self.install(progress, cancel, backend)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        binary = self.binary()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.log = (self.folder / 'server.log').open('wb')
        env = os.environ.copy()
        if platform.system() == 'Linux':
            env['LD_LIBRARY_PATH'] = str(binary.parent) + os.pathsep + env.get('LD_LIBRARY_PATH', '')
        elif platform.system() == 'Windows':
            import sys
            import importlib.util
            # The portable Python distribution includes the Microsoft runtime.
            search = [str(binary.parent), str(Path(sys.executable).parent)]
            qt = importlib.util.find_spec('PySide6')
            if qt and qt.origin:
                search.append(str(Path(qt.origin).parent))
            env['PATH'] = os.pathsep.join(search + [env.get('PATH', '')])
        gpu_layers = '999' if backend == 'vulkan' else '0'
        args = [str(binary), '--model', str(self.model), '--host', '127.0.0.1', '--port', str(port),
                '--ctx-size', '4096', '--parallel', '1', '--n-gpu-layers', gpu_layers, '--threads', str(min(os.cpu_count() or 4, 8)),
                '--no-webui', '--no-warmup', '--alias', 'translategemma-4b']
        flags = subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        self.process = subprocess.Popen(args, cwd=binary.parent, env=env, stdin=subprocess.DEVNULL,
                                        stdout=self.log, stderr=subprocess.STDOUT, creationflags=flags)
        self.running_backend = backend
        self.base_url = f'http://127.0.0.1:{port}'
        opener = build_opener(ProxyHandler({}))
        until = time.monotonic() + 180
        while time.monotonic() < until:
            if cancel():
                self.stop()
                raise Cancelled(tr('Запуск моделі скасовано'))
            if self.process.poll() is not None:
                self.log.close()
                detail = (self.folder / 'server.log').read_text(errors='replace')[-2500:]
                raise core.LocalizerError(tr('Модель не запустилася. ') + detail)
            try:
                with opener.open(self.base_url + '/health', timeout=1) as response:
                    if json.load(response).get('status') == 'ok':
                        return self.base_url
            except (URLError, OSError, ValueError):
                pass
            progress(0, 0, tr('Завантаження моделі в оперативну пам’ять…'))
            time.sleep(0.4)
        self.stop()
        raise core.LocalizerError(tr('Час очікування запуску моделі вичерпано. Перевірте runtime/server.log'))

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process = None
        self.running_backend = None
        if self.log and not self.log.closed:
            self.log.close()
        self.base_url = None
