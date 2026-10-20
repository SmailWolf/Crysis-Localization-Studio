#!/usr/bin/env python3
"""CryLocal: GUI by default, optional command-line workflow."""
from i18n import tr
import argparse
import json
from pathlib import Path
import sys
import paths
import core
import games


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=tr('CryLocal — утиліта локалізації Crysis Remastered'))
    sub = parser.add_subparsers(dest='command')
    gui = sub.add_parser('gui', help=tr('Віконний редактор'))
    gui.add_argument('--project')
    gui.add_argument('--language', choices=['uk', 'en'], help='Interface language / Мова інтерфейсу')
    gui.add_argument('--screenshot', help=argparse.SUPPRESS)
    scan = sub.add_parser('scan', help=tr('Знайти мовні пакети'))
    scan.add_argument('game')
    create = sub.add_parser('new', help=tr('Створити проєкт з PAK'))
    create.add_argument('pak')
    create.add_argument('project')
    create.add_argument('--game', choices=list(games.PROFILES))
    for name in ('export', 'import'):
        p = sub.add_parser(name, help='CSV ' + name)
        p.add_argument('project')
        p.add_argument('csv')
    check = sub.add_parser('check', help=tr('Перевірити службові вставки'))
    check.add_argument('project')
    build = sub.add_parser('build', help=tr('Зібрати повний мовний PAK'))
    build.add_argument('project')
    build.add_argument('output')
    build.add_argument('--fonts-from')
    args = parser.parse_args()
    if args.command in (None, 'gui'):
        if getattr(args, 'language', None):
            import i18n
            i18n.set_language(args.language)
            # Overrides preferences for this invocation, without changing them.
            import os
            os.environ['CRYLOCAL_LANGUAGE'] = args.language
        try:
            from app import run
        except ImportError as exc:
            parser.error(tr('Для віконного редактора встановіть PySide6: python3 -m pip install PySide6. CLI працює без нього. ') + str(exc))
        default = paths.resources_dir() / 'projects' / 'crysis1-uk.crylocal.json'
        import platform
        if platform.system() == 'Windows' and default.exists():
            target = paths.data_dir() / 'projects' / default.name
            if not target.exists():
                core.atomic_bytes(target, default.read_bytes())
            default = target
        remembered = paths.load_preferences().get('last_project')
        fallback = remembered if remembered and Path(remembered).is_file() else (str(default) if default.exists() else None)
        project = getattr(args, 'project', None) or fallback
        return run(project, getattr(args, 'screenshot', None))
    if args.command == 'scan':
        for p in core.discover(args.game):
            print(p)
        return 0
    if args.command == 'new':
        project = core.new_project(args.pak, args.game)
        core.save_project(project, args.project)
        print(tr('Створено проєкт: {0} рядків; {1} XML; пропущено {2}').format(len(project['entries']), len(project['files']), len(project['skipped'])))
        return 0
    project = core.load_project(args.project)
    if args.command == 'export':
        core.export_csv(project, args.csv)
        print(tr('CSV збережено'))
    elif args.command == 'import':
        n = core.import_csv(project, args.csv)
        core.save_project(project, args.project)
        print(tr('Імпортовано {0} рядків').format(n))
    elif args.command == 'check':
        issues = core.validate(project)
        for _, key, reason in issues:
            print(f'{key}: {reason}')
        print(tr('Помилок: {0}').format(len(issues)))
        return 1 if issues else 0
    elif args.command == 'build':
        result = core.build(project, args.output, args.fonts_from)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (core.LocalizerError, OSError, ValueError, KeyError) as exc:
        print(tr('Помилка: {0}').format(exc), file=sys.stderr)
        raise SystemExit(2)
