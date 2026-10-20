"""Optional PySide6 desktop interface; CLI/core have no external dependencies."""
import i18n
from i18n import tr
import paths
import games
from pathlib import Path
from datetime import datetime
import json
import sys
import traceback

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QThread, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QTableView, QPlainTextEdit, QSplitter, QFileDialog, QMessageBox,
    QProgressBar, QDialog, QDialogButtonBox, QHeaderView, QFormLayout, QSpinBox,
)
import core
import runtime
from ui_messages import QMessageBox, localize_buttons
from translate import Translator, DEFAULTS, LANGUAGES, LANGUAGE_LABELS


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, action):
        super().__init__()
        self.action = action

    def run(self):
        try:
            self.done.emit(self.action())
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelWorker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object)
    item = Signal(object)

    def __init__(self, action):
        super().__init__()
        self.action = action

    def report(self, done, total, label):
        self.progress.emit((done, total, label))

    def run(self):
        try:
            self.done.emit(self.action(self))
        except Exception as exc:
            self.failed.emit(str(exc))


class TaskDialog(QDialog):
    def __init__(self, parent, title, cancel):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.resize(620, 190)
        self.cancel = cancel
        layout = QVBoxLayout(self)
        self.label = QLabel(tr('Підготовка…'))
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.button = QPushButton(tr('Скасувати'))
        self.button.clicked.connect(self.reject)
        layout.addWidget(self.button)

    def reject(self):
        self.cancel()
        self.button.setEnabled(False)
        self.label.setText(tr('Завершення поточної операції… Уже отримані переклади збережуться.'))

    def update_progress(self, value):
        done, total, label = value
        if self.button.isEnabled():
            self.label.setText(label)
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(round(done / total * 1000))
            self.bar.setFormat(f'{done:,} / {total:,}')
        else:
            self.bar.setRange(0, 0)


class Entries(QAbstractTableModel):
    HEADERS = ['Стан', 'Ключ', 'Оригінал', 'Переклад']

    def __init__(self, records):
        super().__init__()
        self.records = records

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 4

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return tr(self.HEADERS[section])

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        entry = self.records[index.row()]
        if role == Qt.DisplayRole:
            return ['●' if entry['translation'] else '○', entry['key'],
                    entry['source'].replace('\n', ' ↵ '), entry['translation'].replace('\n', ' ↵ ')][index.column()]
        if role == Qt.ForegroundRole and index.column() == 0:
            return QColor('#68e0ba' if entry['translation'] else '#65738a')
        if role == Qt.ToolTipRole:
            return f"{entry['file']}\n{entry['key']} · {entry['field']}\n{entry['context']}"


class Filter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.query, self.file, self.state = '', '', 0
        self.setDynamicSortFilter(False)

    def filterAcceptsRow(self, row, parent):
        e = self.sourceModel().records[row]
        return ((not self.file or self.file == e['file'])
                and (self.state != 1 or not e['translation'])
                and (self.state != 2 or bool(e['translation']))
                and (not self.query or self.query in '\n'.join([e['key'], e['source'], e['translation'], e['context']]).casefold()))


class Window(QMainWindow):
    def __init__(self, project_path=None, language=None):
        i18n.set_language(language or i18n.preferred_language())
        super().__init__()
        self.project, self.path, self.current = None, None, None
        self.setWindowIcon(QIcon(str(paths.resources_dir() / 'icon.svg')))
        self.dirty, self.editor_dirty = False, False
        self.worker = None
        self.engine = runtime.Runtime(paths.data_dir() / 'runtime')
        self.setWindowTitle(tr('CryLocal • локалізація Crysis Remastered'))
        self.resize(1240, 850)
        container = QWidget()
        self.setCentralWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(26, 22, 26, 18)
        layout.setSpacing(14)
        title = QLabel('CryLocal')
        title.setObjectName('title')
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        title_row.addWidget(QLabel(tr('Мова інтерфейсу')))
        self.interface_language = QComboBox()
        self.interface_language.addItem('English', 'en')
        self.interface_language.addItem('Українська', 'uk')
        self.interface_language.setCurrentIndex(self.interface_language.findData(i18n.language()))
        self.interface_language.setMinimumWidth(150)
        title_row.addWidget(self.interface_language)
        layout.addLayout(title_row)
        caption = QLabel(tr('Майстерня перекладу / Crysis Remastered'))
        caption.setObjectName('muted')
        game_row = QHBoxLayout()
        game_row.addWidget(caption, 1)
        game_row.addWidget(QLabel(tr('Мова перекладу')))
        self.target_language = QComboBox()
        self.target_language.setObjectName('target_language')
        for code, name in LANGUAGES.items():
            self.target_language.addItem(LANGUAGE_LABELS[code], code)
        remembered_target = paths.load_preferences().get('target_language', 'uk')
        self.target_language.setCurrentIndex(max(0, self.target_language.findData(remembered_target)))
        self.target_language.setMinimumWidth(175)
        game_row.addWidget(self.target_language)
        game_row.addWidget(QLabel(tr('Гра')))
        self.game_choice = QComboBox()
        for game_id, name in games.PROFILES.items():
            self.game_choice.addItem(name, game_id)
        remembered_game = paths.load_preferences().get('last_game_id', games.DEFAULT)
        self.game_choice.setCurrentIndex(max(0, self.game_choice.findData(remembered_game)))
        self.game_choice.setMinimumWidth(255)
        game_row.addWidget(self.game_choice)
        layout.addLayout(game_row)
        tools = QHBoxLayout()
        self.buttons = []
        for label, action in [(tr('Новий проєкт'), self.new), (tr('Відкрити'), self.open),
                              (tr('Зберегти'), self.save), (tr('Експорт CSV'), self.export),
                              (tr('Імпорт CSV'), self.import_csv), (tr('Перевірити'), self.check),
                              (tr('Зібрати PAK'), self.build)]:
            b = QPushButton(label)
            b.clicked.connect(action)
            if label == tr('Зібрати PAK'):
                b.setObjectName('primary')
            tools.addWidget(b)
            self.buttons.append(b)
        layout.addLayout(tools)
        model_tools = QHBoxLayout()
        self.model_status = QLabel('TranslateGemma 4B')
        self.model_status.setObjectName('muted')
        model_tools.addWidget(self.model_status, 1)
        for label, action in [(tr('Завантажити 4B'), self.download_model), (tr('Запустити'), self.start_model),
                              (tr('Зупинити'), self.stop_model), (tr('Налаштування'), self.model_settings),
                              (tr('Перекласти рядок'), self.translate_one), (tr('Перекласти групу'), self.translate_batch)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            model_tools.addWidget(button)
        layout.addLayout(model_tools)
        self.summary = QLabel(tr('Відкрийте мовний PAK або готовий проєкт, щоб почати переклад.'))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        source_row = QHBoxLayout()
        self.pak_location = QLabel('')
        self.pak_location.setObjectName('muted')
        self.pak_location.setWordWrap(True)
        source_row.addWidget(self.pak_location, 1)
        self.locate_button = QPushButton(tr('Знайти PAK…'))
        self.locate_button.clicked.connect(self.locate_pak)
        source_row.addWidget(self.locate_button)
        layout.addLayout(source_row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        layout.addWidget(self.bar)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('Пошук за ключем, оригіналом, перекладом чи контекстом…'))
        self.search.textChanged.connect(self.filter)
        self.files = QComboBox()
        self.files.setMinimumWidth(255)
        self.files.currentIndexChanged.connect(self.filter)
        self.states = QComboBox()
        self.states.addItems([tr('Усі рядки'), tr('Без перекладу'), tr('Перекладені')])
        self.states.currentIndexChanged.connect(self.filter)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.files)
        filters.addWidget(self.states)
        layout.addLayout(filters)
        split = QSplitter(Qt.Vertical)
        self.table = QTableView()
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        split.addWidget(self.table)
        edit = QWidget()
        el = QVBoxLayout(edit)
        el.setContentsMargins(0, 12, 0, 0)
        self.detail = QLabel(tr('Виберіть рядок для редагування'))
        self.detail.setWordWrap(True)
        self.detail.setObjectName('muted')
        el.addWidget(self.detail)
        columns = QHBoxLayout()
        self.translation_label = None
        for label, attr in [(tr('Оригінал'), 'source'), (tr('Переклад'), 'translation')]:
            box = QVBoxLayout()
            heading = QLabel(label)
            box.addWidget(heading)
            if attr == 'translation':
                self.translation_label = heading
            widget = QPlainTextEdit()
            widget.setMinimumHeight(100)
            if attr == 'source':
                widget.setReadOnly(True)
            else:
                widget.setPlaceholderText(tr('Введіть переклад. Збережіть %s, {0}, @ключі та інші службові вставки.'))
                widget.textChanged.connect(self.editor_changed)
            setattr(self, attr, widget)
            box.addWidget(widget)
            columns.addLayout(box)
        el.addLayout(columns)
        actions = QHBoxLayout()
        for label, action in [(tr('Копіювати оригінал'), self.copy_source), (tr('Вставити переклад'), self.paste_translation)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            actions.addWidget(button)
        self.hint = QLabel(tr('Ctrl+S — зберегти · Ctrl+Enter — далі'))
        self.hint.setObjectName('muted')
        actions.addWidget(self.hint, 1)
        apply = QPushButton(tr('Застосувати → наступний'))
        apply.clicked.connect(self.next)
        actions.addWidget(apply)
        el.addLayout(actions)
        split.addWidget(edit)
        split.setSizes([430, 220])
        layout.addWidget(split, 1)
        self.footer = QLabel(tr('Crysis 1: перевірка на локальних файлах · Crysis 2/3: визначення формату, ще не перевірено'))
        self.footer.setObjectName('muted')
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)
        QShortcut(QKeySequence.Save, self, activated=self.save)
        QShortcut(QKeySequence('Ctrl+Return'), self, activated=self.next)
        if project_path:
            self.attach(core.load_project(project_path), Path(project_path))
        self.refresh_model_status()
        self.refresh_pak_location()
        self.capture_translations()
        self.interface_language.currentIndexChanged.connect(self.change_language)
        self.game_choice.currentIndexChanged.connect(self.change_game)
        self.target_language.currentIndexChanged.connect(self.change_target_language)

    def sync_game_choice(self):
        if self.project:
            self.game_choice.blockSignals(True)
            self.game_choice.setCurrentIndex(self.game_choice.findData(self.project['game_id']))
            self.game_choice.blockSignals(False)

    def change_game(self, *args):
        chosen = self.game_choice.currentData()
        if self.project and chosen != self.project['game_id']:
            # A different game needs its own PAK; never relabel loaded strings.
            self.new_for_game(chosen)
        elif not self.project:
            paths.save_preferences({'last_game_id': chosen})

    def change_target_language(self, *args):
        code = self.target_language.currentData()
        if not code:
            return
        if self.project:
            previous = self.project.get('target_language', 'uk')
            self.commit()
            if previous != code:
                translations = self.project.setdefault('translations', {})
                translations[previous] = {entry['id']: entry['translation']
                                          for entry in self.project['entries'] if entry['translation']}
                selected = translations.get(code, {})
                for entry in self.project['entries']:
                    entry['translation'] = selected.get(entry['id'], '')
                self.refresh_entries()
            self.project['target_language'] = code
            self.project.setdefault('translator', {})['target_language'] = code
            self.dirty = True
            self.setWindowModified(True)
        try:
            paths.save_preferences({'target_language': code})
        except OSError as exc:
            QMessageBox.warning(self, tr('Не вдалося зберегти мову перекладу'), str(exc))
        self.update_target_language_label()

    def update_target_language_label(self):
        code = self.target_language.currentData()
        if self.translation_label:
            self.translation_label.setText(f"{tr('Переклад')} · {LANGUAGE_LABELS.get(code, code)}")
        if self.project:
            self.stats()

    def new_for_game(self, game_id):
        if not self.unsaved():
            self.sync_game_choice()
            return
        title = tr('Виберіть мовний PAK для {0}').format(games.title(game_id))
        pak, _ = QFileDialog.getOpenFileName(self, title, paths.game_directory(self.project, game_id), tr('Мовні пакети (*.pak)'))
        if not pak:
            self.sync_game_choice()
            return
        self.busy(lambda: core.new_project(pak, game_id), self.new_done, self.sync_game_choice)

    def capture_translations(self):
        self._text_bindings = []
        for kind, getter, setter in [(QLabel, 'text', 'setText'), (QPushButton, 'text', 'setText'),
                                     (QLineEdit, 'placeholderText', 'setPlaceholderText'),
                                     (QPlainTextEdit, 'placeholderText', 'setPlaceholderText')]:
            for widget in self.findChildren(kind):
                key = i18n.source_key(getattr(widget, getter)())
                if key:
                    self._text_bindings.append((widget, setter, key))
        self._combo_bindings = []
        for combo in (self.states,):
            for index in range(combo.count()):
                key = i18n.source_key(combo.itemText(index))
                if key:
                    self._combo_bindings.append((combo, index, key))

    def change_language(self, *args):
        self.commit()
        i18n.set_language(self.interface_language.currentData())
        for widget, setter, key in self._text_bindings:
            getattr(widget, setter)(tr(key))
        for combo, index, key in self._combo_bindings:
            combo.blockSignals(True)
            combo.setItemText(index, tr(key))
            combo.blockSignals(False)
        if self.project:
            self.files.blockSignals(True)
            self.files.setItemText(0, tr('Усі файли'))
            self.files.blockSignals(False)
            self.model.headerDataChanged.emit(Qt.Horizontal, 0, 3)
            self.stats()
            self.select(self.table.currentIndex())
            if self.project.get('skipped'):
                self.footer.setText(tr('Пропущено непідтримуваних XML: {0}. Причини збережено у проєкті.').format(len(self.project['skipped'])))
        else:
            self.setWindowTitle(tr('CryLocal • локалізація Crysis Remastered'))
        self.refresh_model_status()
        self.refresh_pak_location()
        self.update_target_language_label()
        self.statusBar().clearMessage()
        try:
            paths.save_preferences({'interface_language': i18n.language()})
        except OSError as exc:
            QMessageBox.warning(self, tr('Не вдалося зберегти мову інтерфейсу'), str(exc))

    def refresh_pak_location(self):
        self.locate_button.setEnabled(self.project is not None)
        if not self.project:
            self.pak_location.clear()
        elif not Path(self.project['source_pak']).is_file():
            self.pak_location.setText(tr('Джерельний PAK не знайдено. Виберіть його на цьому комп’ютері.'))
        else:
            self.pak_location.setText(tr('Джерельний PAK') + ': ' + self.project['source_pak'])

    def locate_pak(self):
        if not self.project:
            return
        self.commit()
        chosen, _ = QFileDialog.getOpenFileName(self, tr('Виберіть мовний PAK'), paths.game_directory(self.project), tr('Мовні пакети (*.pak)'))
        if not chosen:
            return
        def verify():
            core.check_source_pak(self.project, chosen)
            return chosen
        def done(value):
            self.project['source_pak'] = str(Path(value).resolve())
            self.dirty = True
            self.refresh_pak_location()
            paths.save_preferences({'last_game_directory': str(Path(value).parent)})
            self.save()
            self.statusBar().showMessage(tr('Шлях до PAK оновлено'), 4000)
        self.busy(verify, done)

    def copy_source(self):
        QApplication.clipboard().setText(self.source.toPlainText())
        self.statusBar().showMessage(tr('Оригінал скопійовано'), 2500)

    def paste_translation(self):
        if self.current is not None:
            self.translation.setPlainText(QApplication.clipboard().text())
            self.translation.setFocus()

    def refresh_model_status(self):
        if self.engine.process and self.engine.process.poll() is None:
            self.model_status.setText(tr('TranslateGemma 4B · працює локально'))
        elif self.engine.installed():
            self.model_status.setText(tr('TranslateGemma 4B · завантажено'))
        else:
            self.model_status.setText(tr('TranslateGemma 4B · не завантажено'))

    def model_task(self, title, action, on_done=None, on_item=None, on_finish=None):
        self.commit()
        self.centralWidget().setEnabled(False)
        self.worker = ModelWorker(action)
        worker = self.worker
        dialog = TaskDialog(self, title, worker.requestInterruption)
        worker.progress.connect(dialog.update_progress)
        if on_item:
            worker.item.connect(on_item)
        def finish():
            dialog.accept()
            self.centralWidget().setEnabled(True)
            self.refresh_model_status()
            self.stats()
            if on_finish:
                on_finish()
        def done(result):
            finish()
            if on_done:
                on_done(result)
        def failed(message):
            finish()
            QMessageBox.warning(self, title, message)
        worker.done.connect(done)
        worker.failed.connect(failed)
        dialog.show()
        worker.start()

    def download_model(self):
        self.model_task(tr('Завантаження TranslateGemma 4B · 2,49 ГБ'),
                        lambda w: self.engine.install(w.report, w.isInterruptionRequested),
                        lambda _: QMessageBox.information(self, tr('Модель готова'), tr('TranslateGemma 4B завантажено й перевірено.\nДля перекладу натисніть «Перекласти рядок» або «Перекласти групу».')))

    def start_model(self):
        self.model_task(tr('Запуск локальної моделі'),
                        lambda w: self.engine.start(w.report, w.isInterruptionRequested))

    def stop_model(self):
        self.engine.stop()
        self.refresh_model_status()

    def model_settings(self):
        if not self.project:
            QMessageBox.information(self, tr('Проєкт'), tr('Спочатку відкрийте проєкт перекладу.'))
            return
        settings = dict(DEFAULTS, **self.project.get('translator', {}))
        dialog = QDialog(self)
        dialog.setWindowTitle('TranslateGemma 4B')
        dialog.resize(700, 340)
        layout = QVBoxLayout(dialog)
        note = QLabel(tr('Вбудований режим сам запускає завантажену модель.\nЗовнішній сервер можна використати, якщо модель уже працює в LM Studio чи llama.cpp.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        provider = QComboBox()
        providers = [(tr('Вбудована модель (CPU)'), 'builtin'), ('LM Studio / llama.cpp', 'openai'),
                     ('Ollama', 'ollama'), (tr('Hugging Face / vLLM (структурований шаблон)'), 'hf')]
        for label, value in providers:
            provider.addItem(label, value)
        provider.setCurrentIndex(max(0, provider.findData(settings['provider'])))
        language = QComboBox()
        for code, name in LANGUAGES.items():
            language.addItem(LANGUAGE_LABELS[code], code)
        language.setCurrentIndex(max(0, language.findData(settings['source_language'])))
        address = QLineEdit(settings['base_url'])
        model = QLineEdit(settings['model'])
        form.addRow(tr('Режим'), provider)
        form.addRow(tr('Мова оригіналу'), language)
        form.addRow(tr('Адреса зовнішнього сервера'), address)
        form.addRow(tr('Назва моделі на сервері'), model)
        def changed():
            builtin = provider.currentData() == 'builtin'
            address.setEnabled(not builtin)
            model.setEnabled(not builtin)
            if provider.currentData() == 'openai' and address.text() == DEFAULTS['base_url']:
                address.setText('http://127.0.0.1:1234')
            if provider.currentData() == 'ollama':
                address.setText('http://127.0.0.1:11434')
                model.setText('translategemma:4b')
        provider.currentIndexChanged.connect(changed)
        changed()
        layout.addLayout(form)
        license_label = QLabel(tr('Модель: Google TranslateGemma, GGUF від mradermacher. Ліцензія Gemma.\nСередовище: llama.cpp, MIT. Завантаження з Hugging Face та GitHub.\nПісля завантаження вбудований переклад працює без інтернету.'))
        license_label.setWordWrap(True)
        license_label.setObjectName('muted')
        layout.addWidget(license_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        localize_buttons(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.project['translator'] = dict(settings, provider=provider.currentData(),
                source_language=language.currentData(), base_url=address.text().strip(), model=model.text().strip())
            self.dirty = True
            self.save()

    def translator(self, worker):
        settings = dict(DEFAULTS, **self.project.get('translator', {}))
        settings['target_language'] = self.project.get('target_language', settings.get('target_language', 'uk'))
        if settings['provider'] == 'builtin':
            settings['base_url'] = self.engine.start(worker.report, worker.isInterruptionRequested)
            settings['model'] = 'translategemma-4b'
        return Translator(settings)

    def translate_one(self):
        if self.current is None:
            return
        self.commit()
        source = self.project['entries'][self.current]['source']
        def task(worker):
            translator = self.translator(worker)
            if worker.isInterruptionRequested():
                raise runtime.Cancelled(tr('Переклад скасовано'))
            worker.report(0, 0, tr('Переклад вибраного рядка…'))
            return translator.translate(source)
        def result(value):
            self.translation.setPlainText(value)
            self.translation.setFocus()
            self.statusBar().showMessage(tr('Переклад моделі готовий до редагування. Застосуйте або збережіть його.'), 7000)
        self.model_task(tr('Переклад рядка'), task, result)

    def translate_batch(self):
        if not self.project:
            return
        self.commit()
        selected = sorted({self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()})
        visible = [self.proxy.mapToSource(self.proxy.index(i, 0)).row() for i in range(self.proxy.rowCount())]
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('Пакетний переклад'))
        layout = QVBoxLayout(dialog)
        note = QLabel(tr('Перекладаються лише порожні рядки. Наявні переклади збережуться.\nМожна відфільтрувати потрібний файл або виділити рядки через Ctrl / Shift.\nПеред початком збережеться резервна копія проєкту.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        scope = QComboBox()
        scope.addItem(tr('Виділені рядки ({0})').format(len(selected)), selected)
        scope.addItem(tr('Результати пошуку ({0})').format(len(visible)), visible)
        if len(selected) < 2:
            scope.setCurrentIndex(1)
        limit = QSpinBox()
        limit.setRange(1, max(1, len(visible)))
        limit.setValue(min(50, max(1, len(visible))))
        form.addRow(tr('Які рядки'), scope)
        form.addRow(tr('Не більше рядків за раз'), limit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        localize_buttons(buttons)
        buttons.button(QDialogButtonBox.Ok).setText(tr('Почати переклад'))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        rows = [r for r in scope.currentData() if not self.project['entries'][r]['translation']][:limit.value()]
        if not rows:
            QMessageBox.information(self, tr('Переклад'), tr('У вибраній групі немає порожніх перекладів.'))
            return
        if not self.save():
            return
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = self.path.with_name(self.path.stem + '.before-ai-' + stamp + '.json')
        recovery = self.path.with_name(self.path.stem + '.ai-' + stamp + '.jsonl')
        try:
            core.save_project(self.project, backup)
        except Exception as exc:
            QMessageBox.critical(self, tr('Резервна копія'), str(exc))
            return
        count = [0]
        def item(data):
            row, value = data
            self.project['entries'][row]['translation'] = value
            count[0] += 1
            self.dirty = True
            with recovery.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'id': self.project['entries'][row]['id'], 'translation': value}, ensure_ascii=False) + '\n')
            if count[0] % 10 == 0:
                core.save_project(self.project, self.path)
        def task(worker):
            translator = self.translator(worker)
            failures = []
            for number, row in enumerate(rows, 1):
                if worker.isInterruptionRequested():
                    break
                entry = self.project['entries'][row]
                worker.report(number - 1, len(rows), f"{number} / {len(rows)} · {entry['key']}")
                try:
                    value = translator.translate(entry['source'])
                except core.LocalizerError as exc:
                    failures.append(f"{entry['key']}: {exc}")
                    # Connection/model errors should not trigger thousands of retries.
                    if 'HTTP' in str(exc) or tr('Немає відповіді') in str(exc):
                        break
                    continue
                worker.item.emit((row, value))
            return failures
        def finish():
            core.save_project(self.project, self.path)
            self.dirty = False
            self.refresh_entries()
        def done(failures):
            self.show_text(tr('Пакетний переклад завершено'), tr('Отримано й збережено перекладів: {0}\nРезервна копія: {1}\n\n').format(count[0], backup.name) + (tr('Рядки без результату:\n') + '\n'.join(failures) if failures else tr('Перевірте результат і за потреби відредагуйте текст.')))
        self.model_task(tr('Пакетний переклад'), task, done, item, finish)

    def editor_changed(self):
        if self.current is not None:
            self.editor_dirty = True
            self.dirty = True
            self.setWindowModified(True)

    def commit(self):
        if self.current is not None and self.editor_dirty:
            self.project['entries'][self.current]['translation'] = self.translation.toPlainText()
            self.editor_dirty = False
            index = self.model.index(self.current, 0)
            self.model.dataChanged.emit(index, self.model.index(self.current, 3))
            self.stats()

    def attach(self, project, path=None):
        self.current = None
        self.editor_dirty = False
        self.project, self.path = project, path
        self.project.setdefault('game_id', games.detect(project.get('source_pak', '')) or games.DEFAULT)
        target = project.get('target_language', project.get('translator', {}).get(
            'target_language', paths.load_preferences().get('target_language', 'uk')))
        if target not in LANGUAGES:
            target = {'ukrainian': 'uk', 'english': 'en', 'russian': 'ru'}.get(target, 'uk')
        project['target_language'] = target
        project.setdefault('translator', {})['target_language'] = project['target_language']
        self.sync_game_choice()
        self.target_language.blockSignals(True)
        self.target_language.setCurrentIndex(max(0, self.target_language.findData(project['target_language'])))
        self.target_language.blockSignals(False)
        self.dirty = False
        self.source.clear()
        self.translation.clear()
        self.model = Entries(project['entries'])
        self.proxy = Filter()
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)
        self.table.selectionModel().currentRowChanged.connect(self.select)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 255)
        self.table.setColumnWidth(2, 410)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.files.blockSignals(True)
        self.files.clear()
        self.files.addItem(tr('Усі файли'), '')
        for name in sorted(project['files']):
            self.files.addItem(Path(name).name, name)
        self.files.blockSignals(False)
        self.filter()
        self.stats()
        self.setWindowTitle(f"CryLocal • {games.title(project['game_id'])} • {Path(project['source_pak']).name}[*]")
        self.update_target_language_label()
        self.setWindowModified(False)
        self.refresh_pak_location()
        if self.proxy.rowCount():
            self.table.selectRow(0)

    def stats(self):
        if not self.project:
            return
        entries = self.project['entries']
        n = sum(bool(e['translation']) for e in entries)
        self.summary.setText(tr('{0}   •   {1:,} рядків   •   {2:,} перекладено   •   {3} XML-файлів').format(Path(self.project['source_pak']).name, len(entries), n, len(self.project['files'])))
        self.bar.setRange(0, len(entries))
        self.bar.setValue(n)

    def refresh_entries(self):
        # Changes to text do not rearrange the source model. A proper reset also
        # clears proxy indexes before restoring the current source row.
        row = self.current
        self.current = None
        self.editor_dirty = False
        self.model.beginResetModel()
        self.model.endResetModel()
        self.filter()
        if self.proxy.rowCount():
            index = self.proxy.mapFromSource(self.model.index(row, 0)) if row is not None else QModelIndex()
            self.table.selectRow(index.row() if index.isValid() else 0)

    def select(self, index, previous=QModelIndex()):
        self.commit()
        if not index.isValid():
            self.current = None
            self.source.clear()
            self.translation.clear()
            return
        self.current = self.proxy.mapToSource(index).row()
        e = self.project['entries'][self.current]
        self.source.setPlainText(e['source'])
        self.translation.blockSignals(True)
        self.translation.setPlainText(e['translation'])
        self.translation.blockSignals(False)
        self.editor_dirty = False
        self.detail.setText(f"{e['key']}  ·  {e['field']}  ·  {e['file']}\n{e['context']}")

    def filter(self, *args):
        if not self.project:
            return
        self.commit()
        self.proxy.query = self.search.text().casefold()
        self.proxy.file = self.files.currentData() or ''
        self.proxy.state = self.states.currentIndex()
        self.proxy.invalidate()

    def next(self):
        if not self.project:
            return
        idx = self.table.currentIndex()
        row = idx.row()
        eid = self.project['entries'][self.current]['id'] if self.current is not None else None
        self.commit()
        self.proxy.invalidate()
        count = self.proxy.rowCount()
        if not count:
            return
        # If the previous row vanished under the filter, keep the same position.
        if row < count:
            current_entry = self.model.records[self.proxy.mapToSource(self.proxy.index(row, 0)).row()]
            if current_entry['id'] == eid:
                row += 1
        self.table.selectRow(min(row, count - 1))
        self.translation.setFocus()

    def unsaved(self):
        self.commit()
        if not self.dirty:
            return True
        choice = QMessageBox.question(self, tr('Незбережені зміни'), tr('Зберегти проєкт перед продовженням?'),
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if choice == QMessageBox.Cancel:
            return False
        if choice == QMessageBox.Save:
            return self.save()
        return True

    def busy(self, action, on_done, on_failed=None):
        self.centralWidget().setEnabled(False)
        self.bar.setRange(0, 0)
        self.worker = Worker(action)
        self.worker.done.connect(on_done)
        def failed(message):
            if on_failed:
                on_failed()
            QMessageBox.critical(self, tr('Не вдалося виконати дію'), message)
        self.worker.failed.connect(failed)
        self.worker.finished.connect(self.idle)
        self.worker.start()

    def idle(self):
        self.centralWidget().setEnabled(True)
        self.bar.setRange(0, 1)
        self.stats()

    def new(self):
        self.new_for_game(self.game_choice.currentData())

    def new_done(self, project):
        self.attach(project)
        self.dirty = True
        self.save()
        if project.get('skipped'):
            self.footer.setText(tr('Пропущено непідтримуваних XML: {0}. Причини збережено у проєкті.').format(len(project['skipped'])))

    def open(self):
        if not self.unsaved():
            return
        path, _ = QFileDialog.getOpenFileName(self, tr('Відкрити проєкт'), '', tr('Проєкт CryLocal (*.json)'))
        if path:
            try:
                self.attach(core.load_project(path), Path(path))
            except Exception as exc:
                QMessageBox.critical(self, tr('Помилка проєкту'), str(exc))

    def save(self):
        if not self.project:
            return False
        self.commit()
        default_name = self.project['game_id'].replace('-remastered', '') + '-' + self.project.get('target_language', 'uk') + '.crylocal.json'
        path = str(self.path) if self.path else QFileDialog.getSaveFileName(self, tr('Зберегти проєкт'), default_name, tr('Проєкт (*.json)'))[0]
        if not path:
            return False
        try:
            core.save_project(self.project, path)
            self.path, self.dirty = Path(path), False
            try:
                directories = paths.load_preferences().get('game_directories', {})
                directories[self.project['game_id']] = str(Path(self.project['source_pak']).parent)
                paths.save_preferences({'last_project': str(self.path.resolve()),
                                        'last_game_id': self.project['game_id'], 'game_directories': directories})
            except OSError:
                pass
            self.setWindowModified(False)
            self.statusBar().showMessage(tr('Проєкт збережено'), 4000)
            return True
        except Exception as exc:
            QMessageBox.critical(self, tr('Не вдалося зберегти'), str(exc))
            return False

    def export(self):
        if not self.project:
            return
        self.commit()
        path, _ = QFileDialog.getSaveFileName(self, tr('Експорт текстів'), 'ukrainian.csv', 'CSV (*.csv)')
        if path:
            try:
                core.export_csv(self.project, path)
                self.statusBar().showMessage(tr('CSV збережено. Редагуйте колонку translation.'), 6000)
            except Exception as exc:
                QMessageBox.critical(self, tr('Помилка експорту'), str(exc))

    def import_csv(self):
        if not self.project:
            return
        self.commit()
        path, _ = QFileDialog.getOpenFileName(self, tr('Імпорт перекладів'), '', 'CSV (*.csv)')
        if path:
            try:
                n = core.import_csv(self.project, path)
                self.refresh_entries()
                self.dirty = True
                self.stats()
                self.statusBar().showMessage(tr('Імпортовано {0} рядків. Збережіть проєкт.').format(n), 6000)
            except Exception as exc:
                QMessageBox.critical(self, tr('Помилка імпорту'), str(exc))

    def check(self):
        if not self.project:
            return
        self.commit()
        issues = core.validate(self.project)
        if issues:
            self.show_text(tr('Перевірка перекладу'), '\n\n'.join(f'{key}: {reason}' for _, key, reason in issues))
        else:
            QMessageBox.information(self, tr('Перевірка'), tr('Помилок службових вставок та XML-символів не знайдено.\nЯкість перекладу та вигляд тексту перевіряються окремо у грі.'))

    def show_text(self, title, text):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(820, 500)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit(text)
        edit.setReadOnly(True)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        localize_buttons(buttons)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def build(self):
        if not self.project:
            return
        self.commit()
        if core.validate(self.project):
            self.check()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('Зібрати мовний пакет'))
        dialog.resize(680, 250)
        layout = QVBoxLayout(dialog)
        label = QLabel(tr('Створюється повна копія базового PAK з перекладеним текстом.\nОзвучення збережеться. Неперекладені рядки залишаться як у базовому пакеті.\nПереклад сам не додає гліфи до шрифту. Щоб були українські Ґ Є І Ї або білоруська Ў, виберіть сумісний PAK тієї самої гри/версії зі шрифтом, де є ці літери. CryLocal замінить шрифтові ресурси; сумісність треба перевірити у грі.'))
        label.setWordWrap(True)
        layout.addWidget(label)
        row = QHBoxLayout()
        donor = QLineEdit()
        donor.setPlaceholderText(tr('PAK зі шрифтом, що містить потрібні літери — необов’язково'))
        browse = QPushButton(tr('Вибрати…'))
        browse.clicked.connect(lambda: donor.setText(QFileDialog.getOpenFileName(dialog, tr('PAK зі шрифтами'), str(Path(self.project['source_pak']).parent), 'PAK (*.pak)')[0]))
        row.addWidget(donor, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        localize_buttons(buttons)
        buttons.button(QDialogButtonBox.Ok).setText(tr('Зібрати…'))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        default = str((self.path.parent if self.path else Path.cwd()) / 'build' / Path(self.project['source_pak']).name)
        path, _ = QFileDialog.getSaveFileName(self, tr('Новий PAK — окремо від гри'), default, 'PAK (*.pak)')
        if not path:
            return
        self.busy(lambda: core.build(self.project, path, donor.text() or None),
                  lambda result: self.show_text(tr('Мовний пакет створено'), tr('{0}\n\nПерекладено: {1} / {2}\nШрифтових ресурсів замінено: {3}\n\n{4}\n\nНаявність потрібних літер і роботу пакета потрібно перевірити у грі.').format(result['output'], result['translated_entries'], result['total_entries'], len(result['font_assets_replaced']), result['installation'])))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            event.ignore()
            return
        if self.unsaved():
            self.engine.stop()
            event.accept()
        else:
            event.ignore()


STYLE = '''
QWidget { background: #111923; color: #e8edf5; font-family: "Segoe UI", "DejaVu Sans"; font-size: 13px; }
QLabel#title { font-size: 34px; font-weight: 700; color: #72e2bb; }
QLabel#muted { color: #92a2b9; font-size: 12px; }
QPushButton { background: #233246; border: 1px solid #34465d; padding: 10px 13px; border-radius: 7px; }
QPushButton:hover { background: #30435c; }
QPushButton#primary { background: #65d5af; color: #10271e; border-color: #65d5af; font-weight: 700; }
QLineEdit, QComboBox, QPlainTextEdit { background: #192432; border: 1px solid #34465d; border-radius: 6px; padding: 8px; selection-background-color: #325f72; }
QPlainTextEdit { font-size: 14px; }
QTableView { background: #141f2c; alternate-background-color: #192635; border: 1px solid #304054; gridline-color: #26364a; selection-background-color: #284b5b; selection-color: #ffffff; }
QHeaderView::section { background: #223246; color: #adbed3; padding: 10px; border: 0px; border-bottom: 1px solid #3c506b; }
QProgressBar { background: #29384a; border: 0; border-radius: 2px; }
QProgressBar::chunk { background: #65d5af; border-radius: 2px; }
QSplitter::handle { background: #34465d; height: 2px; }
QStatusBar { color: #92a2b9; }
QToolTip { background: #233246; color: #e8edf5; border: 1px solid #435b79; }
'''


def run(project=None, screenshot=None):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Window(project)
    app.aboutToQuit.connect(window.engine.stop)
    window.show()
    if screenshot:
        app.processEvents()
        window.grab().save(str(screenshot))
        return 0
    return app.exec()
