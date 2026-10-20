"""Console-free Windows entry point with a readable startup failure dialog."""
from pathlib import Path
import sys

if __name__ == '__main__':
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('CryLocal.Localization.0.2')
        from crylocal import main
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        import ctypes
        import os
        folder = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CryLocal'
        folder.mkdir(parents=True, exist_ok=True)
        log = folder / 'startup-error.log'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None,
            'CryLocal could not start. / Не вдалося запустити CryLocal.\n\n' + str(log),
            'CryLocal', 0x10)
        raise SystemExit(1)
