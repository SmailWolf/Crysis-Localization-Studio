"""Explicit project identities; all profiles use detected PAK/XML formats."""
import re

PROFILES = {
    'crysis1-remastered': 'Crysis 1 Remastered',
    'crysis2-remastered': 'Crysis 2 Remastered',
    'crysis3-remastered': 'Crysis 3 Remastered',
}
DEFAULT = 'crysis1-remastered'


def detect(path):
    value = str(path).replace('\\', '/').casefold()
    # Numbered names take precedence over generic ancestor folder names.
    match = re.search(r'crysis[ _-]*([123])(?:\b|[_-]|remaster)', value)
    if match:
        return f'crysis{match[1]}-remastered'
    if re.search(r'crysis[ _-]+remaster', value):
        return DEFAULT
    return None


def title(game_id):
    return PROFILES[game_id]
