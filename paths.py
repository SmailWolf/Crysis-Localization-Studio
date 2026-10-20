"""Portable resources, per-user Windows data, and UTF-8 preferences."""
import json
import os
from pathlib import Path
import platform
import sys


def app_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


def resources_dir():
    return Path(getattr(sys, '_MEIPASS', app_dir()))


def data_dir():
    if os.environ.get('CRYLOCAL_DATA_DIR'):
        return Path(os.environ['CRYLOCAL_DATA_DIR']).expanduser().resolve()
    if platform.system() == 'Windows':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'CryLocal'
    return app_dir()


def load_preferences():
    try:
        value = json.loads((data_dir() / 'settings.json').read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_preferences(changes):
    # Deliberately avoid importing the localization core (and creating a cycle).
    import tempfile
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    value = dict(load_preferences(), **changes)
    fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, folder / 'settings.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def game_directory(project=None, game_id=None):
    preferences = load_preferences()
    remembered = preferences.get('game_directories', {}).get(game_id)
    if remembered and Path(remembered).is_dir():
        return remembered
    if not game_id and preferences.get('last_game_directory') and Path(preferences['last_game_directory']).is_dir():
        return preferences['last_game_directory']
    if project and (not game_id or project.get('game_id') == game_id):
        candidate = Path(project['source_pak']).parent
        if candidate.is_dir():
            return str(candidate)
    if platform.system() == 'Windows':
        for candidate in (Path('C:/Program Files (x86)/Steam/steamapps/common'), Path('C:/Games')):
            if candidate.is_dir():
                return str(candidate)
    return str(Path.home())
