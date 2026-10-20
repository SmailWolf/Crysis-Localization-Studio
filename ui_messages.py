"""Localize standard dialog buttons independently of the operating system."""
from PySide6.QtWidgets import QMessageBox as BaseMessageBox, QDialogButtonBox
from i18n import tr


def localize_buttons(box):
    enum = QDialogButtonBox if isinstance(box, QDialogButtonBox) else BaseMessageBox
    for name, label in [('Ok', 'Гаразд'), ('Cancel', 'Скасувати'), ('Save', 'Зберегти'),
                        ('Discard', 'Не зберігати'), ('Close', 'Закрити'), ('Yes', 'Так'), ('No', 'Ні')]:
        button = box.button(getattr(enum, name))
        if button:
            button.setText(tr(label))


class QMessageBox(BaseMessageBox):
    @staticmethod
    def _show(parent, title, text, icon, buttons=None, default=None):
        box = BaseMessageBox(parent)
        box.setWindowTitle(title)
        box.setText(str(text))
        box.setIcon(icon)
        box.setStandardButtons(buttons if buttons is not None else BaseMessageBox.Ok)
        if default is not None:
            box.setDefaultButton(default)
        localize_buttons(box)
        return box.exec()

    @staticmethod
    def information(parent, title, text):
        return QMessageBox._show(parent, title, text, BaseMessageBox.Information)

    @staticmethod
    def warning(parent, title, text):
        return QMessageBox._show(parent, title, text, BaseMessageBox.Warning)

    @staticmethod
    def critical(parent, title, text):
        return QMessageBox._show(parent, title, text, BaseMessageBox.Critical)

    @staticmethod
    def question(parent, title, text, buttons, default=None):
        return QMessageBox._show(parent, title, text, BaseMessageBox.Question, buttons, default)
