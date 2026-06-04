from __future__ import annotations

import math
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QSettings, QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication, QIcon, QImage, QImageReader, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QSizePolicy,
    QStyle,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
except Exception:
    QSoundEffect = None
    QMediaPlayer = None
    QAudioOutput = None

from .workers import FfmpegWorker, YtDlpWorker
from .widgets import DropFrame
from .yt_dlp_json import _extract_video_heights, _first_entry_info, _run_yt_dlp_json


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MediaDL")
        self.setFixedSize(1200, 960)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.MSWindowsFixedSizeDialogHint, True)

        self._settings = QSettings("MediaDL", "MediaDL")
        self._migrate_settings()
        self._worker: YtDlpWorker | None = None
        self._available_heights: list[int] = []
        self._height_to_video_kbps: dict[int, int] = {}
        self._available_audio_kbps: list[int] = []
        self._downloaded_files: list[Path] = []
        self._pending_scale_height: int | None = None
        self._pending_scale_container: str | None = None
        self._pending_video_bitrate_kbps: int | None = None
        self._post_worker: FfmpegWorker | None = None
        self._post_src: Path | None = None
        self._post_dst: Path | None = None
        self._last_fetched_url: str = ""
        self._anims: list[QPropertyAnimation] = []
        self._theme = self._settings.value("theme", "dark", str)
        self._animations_enabled = self._settings.value("enable_animations", True, bool)
        self._language = self._settings.value("language", "en", str)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        top = QWidget()
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)

        self.app_title = QLabel("MediaDL")
        self.app_title.setObjectName("AppTitle")
        top_row.addWidget(self.app_title)
        top_row.addStretch(1)

        self.language_combo = QComboBox()
        self.language_combo.setObjectName("LangCombo")
        self.language_combo.addItem("🇺🇸 English", "en")
        self.language_combo.addItem("🇪🇸 Español", "es")
        self.language_combo.addItem("🏴‍☠️ Pirate", "pirate")
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        top_row.addWidget(self.language_combo)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)

        self.download_tab = self._build_download_tab()
        self.convert_tab = self._build_convert_tab()
        self.settings_tab = self._build_settings_tab()
        self.about_tab = self._build_about_tab()

        self.tabs.addTab(self.download_tab, "Download")
        self.tabs.addTab(self.convert_tab, "Convert")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.about_tab, "About")
        self.tabs.currentChanged.connect(self._animate_current_tab)

        self._about_clicks = 0
        self._about_click_reset_timer = QTimer(self)
        self._about_click_reset_timer.setSingleShot(True)
        self._about_click_reset_timer.timeout.connect(self._reset_about_clicks)
        self.about_tab.installEventFilter(self)
        for w in self.about_tab.findChildren(QWidget):
            w.installEventFilter(self)

        self._easter_overlay: QFrame | None = None
        self._easter_timer: QTimer | None = None
        self._easter_hide_timer: QTimer | None = None
        self._easter_t = 0.0
        self._easter_player: QMediaPlayer | None = None
        self._easter_audio: QAudioOutput | None = None

        layout.addWidget(top)
        layout.addWidget(self.tabs)

        self._url_timer = QTimer(self)
        self._url_timer.setSingleShot(True)
        self._url_timer.timeout.connect(self._auto_fetch_formats)

        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(lambda: self.notice.setVisible(False))

        self._sound_effects = {}
        self._sound_cfg = {}
        self._cookies_error_shown = False
        self._not_found_shown = False

        self._restore_settings()
        self._refresh_quality_combo()
        self._refresh_audio_quality_combo()
        self._sync_mode_visibility()
        self._apply_theme(self._theme)
        self._apply_language(self._language)
        self._apply_random_icon()
        self._update_download_enabled()
        self._init_sounds()

    def _migrate_settings(self) -> None:
        try:
            ver = int(self._settings.value("settings_version", 1, int))
        except Exception:
            ver = 1

        if ver >= 4:
            return

        for k in ("sound_error_vol", "sound_cookies_vol", "sound_success_vol"):
            try:
                raw = self._settings.value(k, None)
                if raw is None or raw == "":
                    continue
                v = int(raw)
                if v <= 0:
                    self._settings.setValue(k, 50)
            except Exception:
                pass

        self._settings.setValue("settings_version", 4)

    def _card(self, title_key: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")
        v = QVBoxLayout(frame)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(10)
        header = QWidget()
        header.setObjectName(f"CardHeader_{title_key}")
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        t = QLabel(self._tr(title_key))
        t.setObjectName("CardTitle")
        t.setProperty("tr_key", title_key)
        h.addWidget(t)
        h.addStretch(1)
        v.addWidget(header)
        return frame, v

    def _build_download_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        source_card, source_layout = self._card("source")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Paste a video/playlist URL…")
        self.url_edit.textChanged.connect(self._on_url_changed)

        self.cookies_edit = QLineEdit()
        self.cookies_edit.setPlaceholderText("Optional: cookies.txt (Netscape format)")
        self.cookies_browse_btn = QPushButton("Browse…")
        self.cookies_browse_btn.clicked.connect(self._on_browse_cookies)

        self.url_label = QLabel("URL")
        self.url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cookies_label = QLabel("Cookies")
        self.cookies_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self.url_label, 0, 0)
        grid.addWidget(self.url_edit, 0, 1, 1, 2)
        grid.addWidget(self.cookies_label, 1, 0)
        grid.addWidget(self.cookies_edit, 1, 1)
        grid.addWidget(self.cookies_browse_btn, 1, 2)
        grid.setColumnStretch(1, 1)
        source_layout.addLayout(grid)

        options_card, options_layout = self._card("options")
        grid2 = QGridLayout()
        grid2.setHorizontalSpacing(10)
        grid2.setVerticalSpacing(10)
        self._options_grid = grid2

        self.format_combo = QComboBox()
        model = QStandardItemModel()

        def add_sep(text: str) -> None:
            item = QStandardItem(text)
            item.setEnabled(False)
            model.appendRow(item)

        def add_item(text: str, data: str) -> None:
            item = QStandardItem(text)
            item.setData(data, Qt.ItemDataRole.UserRole)
            model.appendRow(item)

        add_sep("──────── 🎬 VIDEO ────────")
        add_item("🎬 mp4", "video:mp4")
        add_item("🎬 mkv", "video:mkv")
        add_item("🎬 webm", "video:webm")
        add_item("🖼️ gif", "video:gif")
        add_sep("──────── 🎵 AUDIO ────────")
        add_item("🎵 mp3", "audio:mp3")
        add_item("🎵 m4a", "audio:m4a")
        add_item("🎵 opus", "audio:opus")

        self.format_combo.setModel(model)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)

        self.quality_combo = QComboBox()
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)

        self.type_label = QLabel("Video format")
        self.type_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.quality_label = QLabel("Quality")
        self.quality_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_quality_label = QLabel("Audio quality")
        self.audio_quality_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        self.video_bitrate_label = QLabel(self._tr("video_bitrate"))
        self.video_bitrate_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.video_bitrate_label.setProperty("tr_key", "video_bitrate")
        self.video_bitrate_combo = QComboBox()
        self.video_bitrate_combo.addItem(self._tr("auto"), None)
        for k in [2500, 1500, 1000, 800, 600, 450, 350, 250, 180, 120, 80]:
            self.video_bitrate_combo.addItem(f"{k} kbps", int(k))
        self.video_bitrate_combo.currentIndexChanged.connect(self._on_video_bitrate_changed)

        self.include_audio_checkbox = QCheckBox("🔊 Include audio")
        self.include_audio_checkbox.setChecked(True)
        self.include_audio_checkbox.stateChanged.connect(self._sync_mode_visibility)

        self.audio_quality_combo = QComboBox()
        self.audio_quality_combo.currentIndexChanged.connect(self._on_audio_quality_changed)

        grid2.addWidget(self.type_label, 0, 0)
        grid2.addWidget(self.format_combo, 0, 1)
        grid2.addWidget(self.quality_label, 0, 2)
        grid2.addWidget(self.quality_combo, 0, 3)
        grid2.addWidget(self.include_audio_checkbox, 1, 1, 1, 3)
        grid2.addWidget(self.audio_quality_label, 2, 2)
        grid2.addWidget(self.audio_quality_combo, 2, 3)
        grid2.addWidget(self.video_bitrate_label, 3, 2)
        grid2.addWidget(self.video_bitrate_combo, 3, 3)
        grid2.setColumnStretch(1, 1)
        grid2.setColumnStretch(3, 1)
        options_layout.addLayout(grid2)

        actions_card, actions_layout = self._card("output")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("Choose an output folder…")
        self.output_browse_btn = QPushButton("Browse…")
        self.output_browse_btn.clicked.connect(self._on_browse_output_dir)

        self.start_btn = QPushButton("Download")
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.start_btn.clicked.connect(self._on_start)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)

        self.open_folder_btn = QToolButton()
        self.open_folder_btn.setText("Open folder")
        self.open_folder_btn.clicked.connect(self._on_open_output_folder)

        row.addWidget(self.output_dir_edit, stretch=1)
        row.addWidget(self.output_browse_btn)
        row.addWidget(self.open_folder_btn)
        row.addWidget(self.start_btn)
        row.addWidget(self.cancel_btn)
        actions_layout.addLayout(row)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(10)
        self.output_name_label = QLabel(self._tr("output_name"))
        self.output_name_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.output_name_edit = QLineEdit()
        self.output_name_edit.setPlaceholderText(self._tr("output_name_ph"))
        name_row.addWidget(self.output_name_label)
        name_row.addWidget(self.output_name_edit, stretch=1)
        actions_layout.addLayout(name_row)

        progress_card, progress_layout = self._card("progress")
        self.notice = QFrame()
        self.notice.setObjectName("Notice")
        self.notice.setProperty("level", "info")
        notice_row = QHBoxLayout(self.notice)
        notice_row.setContentsMargins(12, 10, 12, 10)
        notice_row.setSpacing(10)
        self.notice_icon = QLabel("")
        self.notice_icon.setObjectName("NoticeIcon")
        self.notice_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.notice_text = QLabel("")
        self.notice_text.setObjectName("NoticeText")
        self.notice_text.setWordWrap(True)
        notice_row.addWidget(self.notice_icon)
        notice_row.addWidget(self.notice_text, stretch=1)
        self.notice.setVisible(False)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.log_container = QFrame()
        self.log_container.setObjectName("LogContainer")
        log_v = QVBoxLayout(self.log_container)
        log_v.setContentsMargins(0, 0, 0, 0)
        log_v.setSpacing(0)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.log.setFont(mono)
        log_v.addWidget(self.log)

        progress_layout.addWidget(self.notice)
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.log_container, stretch=1)

        v.addWidget(source_card)
        v.addWidget(options_card)
        v.addWidget(actions_card)
        v.addWidget(progress_card, stretch=1)
        return w

    def _build_convert_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        file_card, file_layout = self._card("convert")
        file_layout.setSpacing(12)

        self._convert_in_path: Path | None = None
        self._convert_in_kind = "video"

        self.convert_drop = DropFrame(self._tr("drag_to_convert"))
        self.convert_drop.set_icon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.convert_drop.file_dropped.connect(self._on_convert_file_selected)
        self.convert_drop.clicked.connect(self._on_browse_convert_input)

        self.convert_preview = QLabel()
        self.convert_preview.setObjectName("ConvertPreview")
        self.convert_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.convert_preview.setVisible(False)

        out_row = QHBoxLayout()
        out_row.setContentsMargins(0, 0, 0, 0)
        out_row.setSpacing(10)
        self.convert_output_dir_edit = QLineEdit()
        self.convert_output_dir_edit.setPlaceholderText(self._tr("output_ph"))
        self.convert_output_browse_btn = QPushButton(self._tr("browse"))
        self.convert_output_browse_btn.clicked.connect(self._on_browse_convert_output_dir)
        self.convert_output_open_btn = QToolButton()
        self.convert_output_open_btn.setText(self._tr("open_folder"))
        self.convert_output_open_btn.clicked.connect(self._on_open_convert_output_folder)
        out_row.addWidget(self.convert_output_dir_edit, stretch=1)
        out_row.addWidget(self.convert_output_browse_btn)
        out_row.addWidget(self.convert_output_open_btn)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(10)
        self.convert_output_name_label = QLabel(self._tr("output_name"))
        self.convert_output_name_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.convert_output_name_edit = QLineEdit()
        self.convert_output_name_edit.setPlaceholderText(self._tr("output_name_ph"))
        name_row.addWidget(self.convert_output_name_label)
        name_row.addWidget(self.convert_output_name_edit, stretch=1)

        self.convert_format_label = QLabel(self._tr("convert_format"))
        self.convert_format_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.convert_format_combo = QComboBox()
        self.convert_format_combo.currentIndexChanged.connect(self._sync_convert_visibility)

        self.convert_video_quality_label = QLabel(self._tr("quality"))
        self.convert_video_quality_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.convert_video_quality_combo = QComboBox()

        self.convert_audio_quality_label = QLabel(self._tr("convert_quality"))
        self.convert_audio_quality_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.convert_audio_quality_combo = QComboBox()

        self.convert_image_res_label = QLabel(self._tr("resolution"))
        self.convert_image_res_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.convert_image_res_combo = QComboBox()
        self.convert_image_res_combo.addItem(self._tr("original"), None)
        for h in [2048, 1920, 1600, 1280, 1024, 720, 480, 360, 240, 180, 120, 96, 64, 48, 32, 24]:
            self.convert_image_res_combo.addItem(f"{h}px", int(h))

        opts = QGridLayout()
        opts.setHorizontalSpacing(10)
        opts.setVerticalSpacing(10)
        opts.addWidget(self.convert_format_label, 0, 0)
        opts.addWidget(self.convert_format_combo, 0, 1)
        opts.addWidget(self.convert_video_quality_label, 1, 0)
        opts.addWidget(self.convert_video_quality_combo, 1, 1)
        opts.addWidget(self.convert_audio_quality_label, 2, 0)
        opts.addWidget(self.convert_audio_quality_combo, 2, 1)
        opts.addWidget(self.convert_image_res_label, 3, 0)
        opts.addWidget(self.convert_image_res_combo, 3, 1)
        opts.setColumnStretch(1, 1)

        file_layout.addWidget(self.convert_drop)
        file_layout.addWidget(self.convert_preview)
        file_layout.addLayout(out_row)
        file_layout.addLayout(name_row)
        file_layout.addLayout(opts)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)

        self.convert_start_btn = QPushButton(self._tr("convert_start"))
        self.convert_start_btn.setObjectName("PrimaryButton")
        self.convert_start_btn.clicked.connect(self._on_convert_start)

        self.convert_cancel_btn = QPushButton(self._tr("cancel"))
        self.convert_cancel_btn.setEnabled(False)
        self.convert_cancel_btn.clicked.connect(self._on_convert_cancel)

        actions.addStretch(1)
        actions.addWidget(self.convert_start_btn)
        actions.addWidget(self.convert_cancel_btn)
        file_layout.addLayout(actions)

        progress_card, progress_layout = self._card("convert_progress")
        self.convert_progress = QProgressBar()
        self.convert_progress.setRange(0, 100)
        self.convert_progress.setValue(0)

        self.convert_log = QTextEdit()
        self.convert_log.setReadOnly(True)
        self.convert_log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.convert_log.setFont(mono)

        progress_layout.addWidget(self.convert_progress)
        progress_layout.addWidget(self.convert_log, stretch=1)

        v.addWidget(file_card)
        v.addWidget(progress_card, stretch=1)

        self._convert_worker: FfmpegWorker | None = None
        self._set_convert_targets("video")
        self._sync_convert_visibility()
        return w

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        appearance_card, appearance_layout = self._card("appearance")
        grid0 = QGridLayout()
        grid0.setHorizontalSpacing(10)
        grid0.setVerticalSpacing(10)

        self.theme_label = QLabel("Theme")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)

        grid0.addWidget(self.theme_label, 0, 0)
        grid0.addWidget(self.theme_combo, 0, 1)
        grid0.setColumnStretch(1, 1)
        appearance_layout.addLayout(grid0)

        a11y_card, a11y_layout = self._card("accessibility")
        self.animations_checkbox = QCheckBox("Enable animations")
        self.animations_checkbox.setChecked(True)
        self.animations_checkbox.stateChanged.connect(self._on_animations_changed)
        a11y_layout.addWidget(self.animations_checkbox)

        self.animations_hint_label = QLabel(self._tr("animations_hint"))
        self.animations_hint_label.setObjectName("HintText")
        self.animations_hint_label.setWordWrap(True)
        a11y_layout.addWidget(self.animations_hint_label)

        sounds_card, sounds_layout = self._card("sounds")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.sound_err_enable = QCheckBox(self._tr("sound_error"))
        self.sound_err_path = QLineEdit()
        self.sound_err_path.setReadOnly(True)
        self.sound_err_browse = QToolButton()
        self.sound_err_browse.setText(self._tr("browse"))
        self.sound_err_browse.clicked.connect(lambda: self._pick_sound_file("error"))
        self.sound_err_play = QToolButton()
        self.sound_err_play.setAutoRaise(True)
        self.sound_err_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.sound_err_play.clicked.connect(lambda: self._preview_sound("error"))
        self.sound_err_vol = QSlider(Qt.Orientation.Horizontal)
        self.sound_err_vol.setRange(0, 100)
        self.sound_err_vol.setValue(50)
        self.sound_err_vol.valueChanged.connect(lambda v: self._on_sound_changed("error", vol=v))
        self.sound_err_enable.stateChanged.connect(lambda _: self._on_sound_changed("error"))

        self.sound_cookies_enable = QCheckBox(self._tr("sound_cookies"))
        self.sound_cookies_path = QLineEdit()
        self.sound_cookies_path.setReadOnly(True)
        self.sound_cookies_browse = QToolButton()
        self.sound_cookies_browse.setText(self._tr("browse"))
        self.sound_cookies_browse.clicked.connect(lambda: self._pick_sound_file("cookies"))
        self.sound_cookies_play = QToolButton()
        self.sound_cookies_play.setAutoRaise(True)
        self.sound_cookies_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.sound_cookies_play.clicked.connect(lambda: self._preview_sound("cookies"))
        self.sound_cookies_vol = QSlider(Qt.Orientation.Horizontal)
        self.sound_cookies_vol.setRange(0, 100)
        self.sound_cookies_vol.setValue(50)
        self.sound_cookies_vol.valueChanged.connect(lambda v: self._on_sound_changed("cookies", vol=v))
        self.sound_cookies_enable.stateChanged.connect(lambda _: self._on_sound_changed("cookies"))

        self.sound_success_enable = QCheckBox(self._tr("sound_success"))
        self.sound_success_path = QLineEdit()
        self.sound_success_path.setReadOnly(True)
        self.sound_success_browse = QToolButton()
        self.sound_success_browse.setText(self._tr("browse"))
        self.sound_success_browse.clicked.connect(lambda: self._pick_sound_file("success"))
        self.sound_success_play = QToolButton()
        self.sound_success_play.setAutoRaise(True)
        self.sound_success_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.sound_success_play.clicked.connect(lambda: self._preview_sound("success"))
        self.sound_success_vol = QSlider(Qt.Orientation.Horizontal)
        self.sound_success_vol.setRange(0, 100)
        self.sound_success_vol.setValue(50)
        self.sound_success_vol.valueChanged.connect(lambda v: self._on_sound_changed("success", vol=v))
        self.sound_success_enable.stateChanged.connect(lambda _: self._on_sound_changed("success"))

        grid.addWidget(self.sound_err_enable, 0, 0)
        grid.addWidget(self.sound_err_path, 0, 1)
        grid.addWidget(self.sound_err_browse, 0, 2)
        grid.addWidget(self.sound_err_play, 0, 3)
        vol0 = QLabel(self._tr("volume"))
        vol0.setProperty("tr_key", "volume")
        grid.addWidget(vol0, 0, 4)
        grid.addWidget(self.sound_err_vol, 0, 5)

        grid.addWidget(self.sound_cookies_enable, 1, 0)
        grid.addWidget(self.sound_cookies_path, 1, 1)
        grid.addWidget(self.sound_cookies_browse, 1, 2)
        grid.addWidget(self.sound_cookies_play, 1, 3)
        vol1 = QLabel(self._tr("volume"))
        vol1.setProperty("tr_key", "volume")
        grid.addWidget(vol1, 1, 4)
        grid.addWidget(self.sound_cookies_vol, 1, 5)

        grid.addWidget(self.sound_success_enable, 2, 0)
        grid.addWidget(self.sound_success_path, 2, 1)
        grid.addWidget(self.sound_success_browse, 2, 2)
        grid.addWidget(self.sound_success_play, 2, 3)
        vol2 = QLabel(self._tr("volume"))
        vol2.setProperty("tr_key", "volume")
        grid.addWidget(vol2, 2, 4)
        grid.addWidget(self.sound_success_vol, 2, 5)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(5, 1)
        sounds_layout.addLayout(grid)

        if QSoundEffect is None:
            hint = QLabel(self._tr("sound_unavailable"))
            hint.setObjectName("HintText")
            hint.setWordWrap(True)
            sounds_layout.addWidget(hint)

        pref_card, pref_layout = self._card("preferences")
        self.open_after_download_checkbox = QCheckBox(self._tr("open_after_download"))
        self.open_after_download_checkbox.stateChanged.connect(self._on_open_after_download_changed)
        pref_layout.addWidget(self.open_after_download_checkbox)

        self.keep_metadata_checkbox = QCheckBox(self._tr("keep_metadata"))
        self.keep_metadata_checkbox.stateChanged.connect(self._on_keep_metadata_changed)
        pref_layout.addWidget(self.keep_metadata_checkbox)

        v.addWidget(appearance_card)
        v.addWidget(a11y_card)
        v.addWidget(sounds_card)
        v.addWidget(pref_card)
        v.addStretch(1)
        return w

    def _build_about_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        card, layout = self._card("about")
        row = QVBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.about_github_btn = QPushButton(self._tr("github_repo"))
        self.about_github_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/placeholder/repo")))
        self.about_x_btn = QPushButton(self._tr("x_link"))
        self.about_x_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://x.com/4ndere")))

        row.addWidget(self.about_github_btn)
        row.addWidget(self.about_x_btn)
        layout.addLayout(row)

        v.addWidget(card)
        v.addStretch(1)
        return w

    def _build_accessibility_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        appearance_card, appearance_layout = self._card("appearance")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.theme_label = QLabel("Theme")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)

        grid.addWidget(self.theme_label, 0, 0)
        grid.addWidget(self.theme_combo, 0, 1)
        grid.setColumnStretch(1, 1)
        appearance_layout.addLayout(grid)

        a11y_card, a11y_layout = self._card("accessibility")
        self.animations_checkbox = QCheckBox("Enable animations")
        self.animations_checkbox.setChecked(True)
        self.animations_checkbox.stateChanged.connect(self._on_animations_changed)
        a11y_layout.addWidget(self.animations_checkbox)

        hint = QLabel("If you prefer a snappier UI or your PC is under heavy load, disable animations.")
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        a11y_layout.addWidget(hint)

        v.addWidget(appearance_card)
        v.addWidget(a11y_card)
        v.addStretch(1)
        return w

    def _duration(self) -> int:
        return 220 if self._animations_enabled else 0

    def _animate_current_tab(self) -> None:
        w = self.tabs.currentWidget()
        if w is None:
            return
        effect = w.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(effect)

        effect.setOpacity(0.0)
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(self._duration())
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._keep_anim(anim)
        anim.start()

    def _keep_anim(self, anim: QPropertyAnimation) -> None:
        self._anims.append(anim)
        anim.finished.connect(lambda: self._anims.remove(anim) if anim in self._anims else None)

    def eventFilter(self, obj, event) -> bool:
        if (obj is self.about_tab or self.about_tab.isAncestorOf(obj)) and event.type() == QEvent.Type.MouseButtonPress:
            self._about_clicks += 1
            if self._about_clicks == 1:
                self._about_click_reset_timer.start(1800)
            if self._about_clicks >= 5:
                self._about_click_reset_timer.stop()
                self._reset_about_clicks()
                self._trigger_easter_egg()
            return False
        return super().eventFilter(obj, event)

    def _reset_about_clicks(self) -> None:
        self._about_clicks = 0

    def _trigger_easter_egg(self) -> None:
        if self._easter_overlay is None:
            overlay = QFrame(self)
            overlay.setObjectName("EasterEgg")
            overlay.setStyleSheet("background: transparent;")
            overlay.setFixedSize(460, 360)
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lbl = QLabel(overlay)
            lbl.setObjectName("EasterEggImg")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay = QVBoxLayout(overlay)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(lbl)
            self._easter_overlay = overlay

            self._easter_timer = QTimer(self)
            self._easter_timer.setInterval(16)
            self._easter_timer.timeout.connect(self._tick_easter_egg)

            self._easter_hide_timer = QTimer(self)
            self._easter_hide_timer.setSingleShot(True)
            self._easter_hide_timer.timeout.connect(self._hide_easter_egg)

        overlay = self._easter_overlay
        img = overlay.findChild(QLabel, "EasterEggImg")
        icon_path = self._app_root() / "src" / "icons" / "js.png"
        if img is not None:
            if icon_path.exists():
                pix = QPixmap(str(icon_path))
            else:
                pix = self.windowIcon().pixmap(360, 260)
            if not pix.isNull():
                pix = pix.scaled(420, 320, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                img.setPixmap(pix)

        self._easter_t = 0.0
        overlay.setVisible(True)
        overlay.raise_()
        self._center_easter_overlay()

        if self._easter_timer is not None:
            self._easter_timer.start()
        if self._easter_hide_timer is not None:
            self._easter_hide_timer.start(3000)

        if QMediaPlayer is not None and QAudioOutput is not None:
            sound_path = self._app_root() / "src" / "icons" / "js.mp3"
            if sound_path.exists():
                if self._easter_player is None:
                    self._easter_player = QMediaPlayer(self)
                    self._easter_audio = QAudioOutput(self)
                    self._easter_audio.setVolume(0.9)
                    self._easter_player.setAudioOutput(self._easter_audio)
                self._easter_player.setSource(QUrl.fromLocalFile(str(sound_path.resolve())))
                self._easter_player.play()

    def _center_easter_overlay(self) -> None:
        overlay = self._easter_overlay
        if overlay is None:
            return
        r = self.rect()
        x = int((r.width() - overlay.width()) / 2)
        y = int((r.height() - overlay.height()) / 2)
        overlay.move(max(0, x), max(0, y))

    def _tick_easter_egg(self) -> None:
        overlay = self._easter_overlay
        if overlay is None or not overlay.isVisible():
            return
        self._easter_t += 0.18
        amp_x = 14
        amp_y = 10
        dx = int(round(amp_x * math.sin(self._easter_t * 7.0)))
        dy = int(round(amp_y * math.sin(self._easter_t * 9.0 + 1.2)))
        r = self.rect()
        x0 = int((r.width() - overlay.width()) / 2)
        y0 = int((r.height() - overlay.height()) / 2)
        overlay.move(max(0, x0 + dx), max(0, y0 + dy))

    def _hide_easter_egg(self) -> None:
        if self._easter_timer is not None:
            self._easter_timer.stop()
        if self._easter_overlay is not None:
            self._easter_overlay.setVisible(False)
        if self._easter_player is not None:
            self._easter_player.stop()

    def _apply_theme(self, theme: str) -> None:
        self._theme = theme
        is_dark = theme == "dark"
        bg = "#0b1020" if is_dark else "#f6f7fb"
        panel = "#121a33" if is_dark else "#ffffff"
        panel2 = "#0f1730" if is_dark else "#ffffff"
        text = "#e7e9f2" if is_dark else "#141823"
        muted = "#a7aec4" if is_dark else "#5b6275"
        border = "#263155" if is_dark else "#d7d9e3"
        accent = "#6c5ce7"
        accent2 = "#7f74ff"

        qss = f"""
        QWidget {{
            background: {bg};
            color: {text};
            font-size: 13px;
        }}
        QLabel {{
            background: transparent;
            padding: 0px;
        }}
        #AppTitle {{
            font-size: 16px;
            font-weight: 650;
        }}
        QTabWidget::pane {{
            border: 1px solid {border};
            border-top: 0px;
            border-radius: 14px;
            background: {panel2};
            padding: 10px;
            margin-top: -1px;
        }}
        QTabBar {{
            background: transparent;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {muted};
            padding: 10px 14px;
            margin-right: 6px;
            border-radius: 10px;
            border: 1px solid transparent;
        }}
        QTabBar::tab:selected {{
            background: {panel};
            color: {text};
            border: 1px solid {border};
        }}
        QTabBar::tab:hover {{
            color: {text};
        }}
        #Card {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 16px;
        }}
        #CardTitle {{
            font-size: 14px;
            font-weight: 600;
        }}
        #Notice {{
            border: 1px solid {border};
            border-radius: 14px;
        }}
        #Notice[level="error"] {{
            background: {"#2a1020" if is_dark else "#ffe4e8"};
            border: 1px solid {"#6a2034" if is_dark else "#ffb3bf"};
        }}
        #Notice[level="cookies"] {{
            background: {"#231a10" if is_dark else "#fff4e0"};
            border: 1px solid {"#6b4a1b" if is_dark else "#ffd39a"};
        }}
        #Notice[level="success"] {{
            background: {"#0f2220" if is_dark else "#e6fff2"};
            border: 1px solid {"#2d5f55" if is_dark else "#a5f2c7"};
        }}
        #NoticeIcon {{
            min-width: 26px;
        }}
        #HintText {{
            color: {muted};
        }}
        QLineEdit, QComboBox, QTextEdit {{
            background: {"#0c1328" if is_dark else "#ffffff"};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 6px 12px;
            min-height: 34px;
            selection-background-color: {accent};
        }}
        QComboBox {{
            padding-right: 34px;
        }}
        QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
            border: 1px solid {accent2};
        }}
        QComboBox::drop-down {{
            border: 0px;
            width: 26px;
        }}
        QAbstractItemView {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 10px;
            selection-background-color: {accent};
            selection-color: {text};
            outline: 0;
        }}
        QAbstractItemView::item {{
            padding: 6px 10px;
        }}
        QPushButton, QToolButton {{
            background: {"#101a34" if is_dark else "#ffffff"};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 8px 12px;
        }}
        QPushButton:hover, QToolButton:hover {{
            border: 1px solid {accent2};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background: {"#0b1227" if is_dark else "#f1f2f7"};
        }}
        QPushButton#PrimaryButton {{
            background: {accent};
            border: 1px solid {accent};
            color: white;
            font-weight: 600;
        }}
        QPushButton#PrimaryButton:hover {{
            background: {accent2};
            border: 1px solid {accent2};
        }}
        QProgressBar {{
            border: 1px solid {border};
            border-radius: 10px;
            background: {"#0c1328" if is_dark else "#ffffff"};
            height: 16px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            border-radius: 10px;
            background: {accent};
        }}
        QCheckBox {{
            background: transparent;
            padding: 2px 0px;
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1px solid {border};
            background: {"#0c1328" if is_dark else "#ffffff"};
        }}
        QCheckBox::indicator:checked {{
            border: 1px solid {accent2};
            background: {accent};
        }}
        QSlider {{
            background: transparent;
        }}
        QSlider::groove:horizontal {{
            height: 8px;
            border-radius: 4px;
            background: {"#0c1328" if is_dark else "#e5e7f0"};
            border: 1px solid {border};
        }}
        QSlider::sub-page:horizontal {{
            background: {accent};
            border-radius: 4px;
        }}
        QSlider::add-page:horizontal {{
            background: {"#0c1328" if is_dark else "#e5e7f0"};
            border-radius: 4px;
        }}
        QSlider::handle:horizontal {{
            width: 18px;
            margin: -6px 0px;
            border-radius: 9px;
            background: {accent};
            border: 1px solid {accent2};
        }}
        #LogContainer {{
            background: transparent;
        }}
        #DropFrame {{
            background: {panel};
            border: 1px dashed {border};
            border-radius: 16px;
        }}
        #DropTitle {{
            font-size: 14px;
            font-weight: 600;
        }}
        #DropSub {{
            color: {muted};
        }}
        """
        QGuiApplication.instance().setStyleSheet(qss)

        idx = self.theme_combo.findData(theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)

        self.animations_checkbox.setChecked(self._animations_enabled)

    def _icons_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / "icons"

    def _optimized_icons_dir(self) -> Path:
        return self._icons_dir() / "optimized"

    def _ensure_optimized_icons(self) -> list[Path]:
        src_dir = self._icons_dir()
        out_dir = self._optimized_icons_dir()
        if not src_dir.exists():
            return []
        out_dir.mkdir(parents=True, exist_ok=True)

        out: list[Path] = []
        for p in src_dir.iterdir():
            if p.is_dir():
                continue
            if p.suffix.lower() not in {".gif", ".webp", ".png", ".jpg", ".jpeg", ".bmp"}:
                continue
            dst = out_dir / f"{p.stem}.ico"
            try:
                if dst.exists() and dst.stat().st_mtime >= p.stat().st_mtime:
                    out.append(dst)
                    continue
            except Exception:
                pass

            reader = QImageReader(str(p))
            reader.setAutoTransform(True)
            img = reader.read()
            if img.isNull():
                continue
            icon_img = img.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            if icon_img.save(str(dst), "ICO"):
                out.append(dst)
        return out

    def _apply_random_icon(self) -> None:
        candidates = self._ensure_optimized_icons()
        if not candidates:
            src_dir = self._icons_dir()
            if src_dir.exists():
                candidates = [p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}]
        if not candidates:
            return

        picked = random.choice(candidates)
        icon = QIcon(str(picked))
        app = QGuiApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
        self.setWindowIcon(icon)

    def _restore_settings(self) -> None:
        self.url_edit.clear()
        root = self._app_root()
        self._cookies_file_hint = str((root / "cookies.txt").resolve())
        cookies_file = self._settings.value("cookies_file", "", str)
        if cookies_file and Path(cookies_file).exists():
            self.cookies_edit.setText(cookies_file)
        elif Path(self._cookies_file_hint).exists():
            self.cookies_edit.setText(self._cookies_file_hint)
        else:
            self.cookies_edit.clear()

        out_dir = root / "Output"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.output_dir_edit.setText(str(out_dir))
        self.convert_output_dir_edit.setText(str(out_dir))

        self._cookies_dir = self._settings.value("cookies_dir", str(root), str)

        saved_format = self._settings.value("format", "", str)
        if not saved_format:
            mode = self._settings.value("mode", "video", str)
            if mode == "audio":
                saved_format = f"audio:{self._settings.value('audio_format', 'mp3', str)}"
            else:
                saved_format = f"video:{self._settings.value('video_container', 'mp4', str)}"
        idx_format = self.format_combo.findData(saved_format)
        if idx_format >= 0:
            self.format_combo.setCurrentIndex(idx_format)

        self.include_audio_checkbox.setChecked(True)

        aq = self._settings.value("audio_quality_kbps", "", str)
        if aq:
            try:
                aq_i = int(aq)
            except Exception:
                aq_i = None
        else:
            aq_i = None

        self._animations_enabled = self._settings.value("enable_animations", True, bool)
        self._theme = self._settings.value("theme", "dark", str)
        self._language = self._settings.value("language", "en", str)
        idx_theme = self.theme_combo.findData(self._theme)
        if idx_theme >= 0:
            self.theme_combo.setCurrentIndex(idx_theme)
        self.animations_checkbox.setChecked(bool(self._animations_enabled))
        self._refresh_audio_quality_combo()
        if aq_i is not None:
            idx_aq = self.audio_quality_combo.findData(aq_i)
            if idx_aq >= 0:
                self.audio_quality_combo.setCurrentIndex(idx_aq)
        else:
            idx_aq = self.audio_quality_combo.findData(None)
            if idx_aq >= 0:
                self.audio_quality_combo.setCurrentIndex(idx_aq)

        vb = self._settings.value("video_bitrate_kbps", "", str)
        if vb:
            try:
                vb_i = int(vb)
            except Exception:
                vb_i = None
        else:
            vb_i = None
        idx_vb = self.video_bitrate_combo.findData(vb_i)
        if idx_vb >= 0:
            self.video_bitrate_combo.setCurrentIndex(idx_vb)
        idx_lang = self.language_combo.findData(self._language)
        if idx_lang >= 0:
            self.language_combo.setCurrentIndex(idx_lang)


        self.sound_err_enable.setChecked(self._settings.value("sound_error_enabled", True, bool))
        self.sound_err_path.setText(self._settings.value("sound_error_file", "", str))
        self.sound_err_vol.setValue(int(self._settings.value("sound_error_vol", 50, int)))
        self._on_sound_changed("error")

        self.sound_cookies_enable.setChecked(self._settings.value("sound_cookies_enabled", True, bool))
        self.sound_cookies_path.setText(self._settings.value("sound_cookies_file", "", str))
        self.sound_cookies_vol.setValue(int(self._settings.value("sound_cookies_vol", 50, int)))
        self._on_sound_changed("cookies")

        self.sound_success_enable.setChecked(self._settings.value("sound_success_enabled", True, bool))
        self.sound_success_path.setText(self._settings.value("sound_success_file", "", str))
        self.sound_success_vol.setValue(int(self._settings.value("sound_success_vol", 50, int)))

        self.open_after_download_checkbox.setChecked(self._settings.value("open_after_download", True, bool))
        self.keep_metadata_checkbox.setChecked(self._settings.value("keep_metadata", False, bool))
        self._on_sound_changed("success")

        self._sync_mode_visibility()

    def _persist_settings(self) -> None:
        self._settings.setValue("format", str(self.format_combo.currentData()))
        self._settings.setValue("enable_animations", self._animations_enabled)
        self._settings.setValue("include_audio", bool(self.include_audio_checkbox.isChecked()))
        aq = self.audio_quality_combo.currentData()
        self._settings.setValue("audio_quality_kbps", "" if aq is None else str(int(aq)))
        vb = self.video_bitrate_combo.currentData()
        self._settings.setValue("video_bitrate_kbps", "" if vb is None else str(int(vb)))
        self._settings.setValue("theme", self._theme)

        self._settings.setValue("language", self._language)



    def _append_log(self, line: str) -> None:
        self.log.append(line)
        low = line.lower()
        if (not self._cookies_error_shown) and ("cookie" in low) and ("error" in low or "invalid" in low or "no such file" in low or "unable" in low):
            self._cookies_error_shown = True
            self._play_sound("cookies")
            self._show_notice("cookies", self._tr("cookies_error").format(detail=line.strip()), timeout_ms=6500)
        if (not self._not_found_shown) and self._is_not_found_text(low):
            self._not_found_shown = True
            self._play_sound("error")
            self._show_notice("error", self._tr("not_found").format(detail=line.strip()), timeout_ms=6500)
        try:
            raw = line.strip()
            if re.match(r"^[A-Za-z]:\\", raw) or raw.startswith("\\\\"):
                p = Path(raw)
                if p.is_file():
                    self._downloaded_files.append(p)
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        self.cancel_btn.setEnabled(busy)
        self.format_combo.setEnabled(not busy)
        self.quality_combo.setEnabled(not busy)
        self.audio_quality_combo.setEnabled(not busy)
        self.include_audio_checkbox.setEnabled(not busy)
        self.output_dir_edit.setEnabled(not busy)
        self.output_browse_btn.setEnabled(not busy)
        self.cookies_edit.setEnabled(not busy)
        self.cookies_browse_btn.setEnabled(not busy)
        self.language_combo.setEnabled(not busy)
        self._update_download_enabled()

    def _current_format(self) -> tuple[str, str]:
        raw = str(self.format_combo.currentData() or "video:mp4")
        if ":" not in raw:
            return "video", "mp4"
        kind, fmt = raw.split(":", 1)
        kind = kind.strip().lower()
        fmt = fmt.strip().lower()
        if kind not in {"video", "audio"}:
            kind = "video"
        return kind, fmt

    def _on_format_changed(self) -> None:
        self._sync_mode_visibility()
        self._persist_settings()

    def _sync_mode_visibility(self) -> None:
        kind, fmt = self._current_format()
        is_video = kind == "video"
        is_audio = kind == "audio"
        is_gif = is_video and fmt == "gif"

        self.include_audio_checkbox.setVisible(is_video)
        if is_gif:
            self.include_audio_checkbox.setChecked(False)
            self.include_audio_checkbox.setEnabled(False)
        else:
            self.include_audio_checkbox.setEnabled(True)

        self.quality_label.setVisible(not is_audio)
        self.quality_combo.setVisible(not is_audio)

        show_audio_quality = is_audio or (is_video and (not is_gif) and bool(self.include_audio_checkbox.isChecked()))
        self.audio_quality_label.setVisible(show_audio_quality)
        self.audio_quality_combo.setVisible(show_audio_quality)

        if is_audio:
            self._options_grid.addWidget(self.audio_quality_label, 0, 2)
            self._options_grid.addWidget(self.audio_quality_combo, 0, 3)
        else:
            self._options_grid.addWidget(self.audio_quality_label, 2, 2)
            self._options_grid.addWidget(self.audio_quality_combo, 2, 3)

        show_video_bitrate = is_video and (not is_gif)
        self.video_bitrate_label.setVisible(show_video_bitrate)
        self.video_bitrate_combo.setVisible(show_video_bitrate)

        self._refresh_quality_combo()

    def _refresh_audio_quality_combo(self) -> None:
        current = self.audio_quality_combo.currentData()
        self.audio_quality_combo.blockSignals(True)
        try:
            self.audio_quality_combo.clear()
            candidates = self._audio_kbps_candidates()
            max_k = candidates[0] if candidates else 320
            self.audio_quality_combo.addItem(f"{self._tr('best')} (≈{max_k} kbps)", None)
            for k in candidates:
                if int(k) == int(max_k):
                    continue
                self.audio_quality_combo.addItem(f"{k} kbps", int(k))
        finally:
            self.audio_quality_combo.blockSignals(False)

        if current is not None:
            idx = self.audio_quality_combo.findData(current)
            if idx >= 0:
                self.audio_quality_combo.setCurrentIndex(idx)

    def _refresh_quality_combo(self) -> None:
        current = self.quality_combo.currentData()
        self.quality_combo.blockSignals(True)
        try:
            self.quality_combo.clear()
            kind, fmt = self._current_format()
            if kind == "audio":
                self.quality_combo.addItem(self._tr("audio_quality_hint"), None)
            else:
                if fmt == "gif":
                    for p in self._gif_presets():
                        self.quality_combo.addItem(self._gif_preset_label(p), p)
                else:
                    candidates = self._video_quality_candidates()
                    max_h = candidates[0] if candidates else 720
                    self.quality_combo.addItem(f"{self._tr('best')} (≤{max_h}p)", None)
                    for h in candidates:
                        if int(h) == int(max_h):
                            continue
                        kbps = self._height_to_video_kbps.get(h)
                        if kbps:
                            self.quality_combo.addItem(f"{h}p · ~{kbps} kbps", h)
                        else:
                            self.quality_combo.addItem(f"{h}p", h)
        finally:
            self.quality_combo.blockSignals(False)

        if current is not None:
            idx = self.quality_combo.findData(current)
            if idx >= 0:
                self.quality_combo.setCurrentIndex(idx)

    def _on_url_changed(self) -> None:
        if self._worker is not None:
            return
        self._update_download_enabled()
        self._url_timer.start(650)

    def _auto_fetch_formats(self) -> None:
        url = self.url_edit.text().strip()
        if not self._is_valid_url(url):
            return
        if url == self._last_fetched_url:
            return
        self._on_fetch_formats(silent=True)

    def _on_browse_cookies(self) -> None:
        root = self._app_root()
        fallback = str((root / "cookies.txt").resolve())
        start = getattr(self, "_cookies_file_hint", "") or getattr(self, "_cookies_dir", "") or fallback
        path, _ = QFileDialog.getOpenFileName(self, "Select cookies.txt", start, "cookies.txt (*.txt);;Text files (*.txt);;All files (*.*)")
        if path:
            self.cookies_edit.setText(path)
            try:
                self._cookies_dir = str(Path(path).parent)
                self._settings.setValue("cookies_dir", self._cookies_dir)
                self._settings.setValue("cookies_file", str(Path(path)))
            except Exception:
                pass

    def _on_browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_dir_edit.text().strip() or str(Path.home()))
        if path:
            self.output_dir_edit.setText(path)

    def _on_open_output_folder(self) -> None:
        out_dir = self.output_dir_edit.text().strip()
        if out_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out_dir).resolve())))

    def _on_browse_convert_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self._tr("convert_out_pick"), self.convert_output_dir_edit.text().strip() or str(Path.home()))
        if path:
            self.convert_output_dir_edit.setText(path)

    def _on_open_convert_output_folder(self) -> None:
        out_dir = self.convert_output_dir_edit.text().strip()
        if out_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out_dir).resolve())))

    def _app_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _sanitize_output_stem(self, text: str) -> str:
        s = (text or "").strip()
        if not s:
            return ""
        s = re.sub(r'[<>:"/\\\\|?*\\x00-\\x1F]', "_", s)
        s = s.rstrip(" .")
        if len(s) > 140:
            s = s[:140].rstrip(" .")
        return s

    def _is_valid_url(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        u = QUrl(t)
        if not u.isValid():
            return False
        if u.scheme() not in {"http", "https"}:
            return False
        if not u.host():
            return False
        return True

    def _update_download_enabled(self) -> None:
        ok = self._is_valid_url(self.url_edit.text())
        idle = self._worker is None and self._post_worker is None
        self.start_btn.setEnabled(ok and idle)

    def _show_notice(self, level: str, text: str, timeout_ms: int = 4500) -> None:
        try:
            self.notice.setProperty("level", level)
            self.notice.style().unpolish(self.notice)
            self.notice.style().polish(self.notice)
        except Exception:
            pass

        icon_text = "ℹ️"
        if level == "success":
            icon_text = "✅"
        if level == "error":
            icon_text = "⚠️"
        if level == "cookies":
            icon_text = "🍪"

        self.notice_icon.setText(icon_text)
        self.notice_text.setText(text)
        self.notice.setVisible(True)
        self._notice_timer.stop()
        if timeout_ms > 0:
            self._notice_timer.start(timeout_ms)

    def _init_sounds(self) -> None:
        if QSoundEffect is None:
            self._sound_effects = {}
            return

        def get_bool(k: str, default: bool) -> bool:
            return self._settings.value(k, default, bool)

        def get_int(k: str, default: int) -> int:
            return int(self._settings.value(k, default, int))

        def get_str(k: str, default: str = "") -> str:
            return self._settings.value(k, default, str)

        self._sound_cfg = {
            "error": {
                "enabled": get_bool("sound_error_enabled", True),
                "file": get_str("sound_error_file", ""),
                "vol": get_int("sound_error_vol", 50),
            },
            "cookies": {
                "enabled": get_bool("sound_cookies_enabled", True),
                "file": get_str("sound_cookies_file", ""),
                "vol": get_int("sound_cookies_vol", 50),
            },
            "success": {
                "enabled": get_bool("sound_success_enabled", True),
                "file": get_str("sound_success_file", ""),
                "vol": get_int("sound_success_vol", 50),
            },
        }

        self._sound_effects = {}
        for k, cfg in self._sound_cfg.items():
            eff = QSoundEffect(self)
            eff.setLoopCount(1)
            eff.setVolume(max(0.0, min(1.0, float(cfg["vol"]) / 100.0)))
            f = str(cfg["file"] or "").strip()
            if f and Path(f).exists():
                eff.setSource(QUrl.fromLocalFile(str(Path(f).resolve())))
            self._sound_effects[k] = eff

    def _play_sound(self, kind: str) -> None:
        cfg = self._sound_cfg.get(kind)
        if not cfg or not bool(cfg.get("enabled")):
            return
        eff = self._sound_effects.get(kind)
        if eff is None:
            QApplication.beep()
            return
        if eff.source().isEmpty():
            QApplication.beep()
            return
        eff.setVolume(max(0.0, min(1.0, float(cfg["vol"]) / 100.0)))
        eff.play()

    def _sync_convert_visibility(self) -> None:
        ext = str(self.convert_format_combo.currentData())
        in_kind = getattr(self, "_convert_in_kind", "video")
        gif_from_video = ext == "gif" and in_kind == "video"
        is_audio = ext in {"mp3", "m4a", "opus"}
        is_video = ext in {"mp4", "mkv", "webm"} or gif_from_video
        is_image = (ext in {"jpg", "png", "webp", "jfif", "gif"}) and (not gif_from_video)

        show_audio = is_audio or (is_video and not gif_from_video)
        self.convert_audio_quality_label.setVisible(show_audio)
        self.convert_audio_quality_combo.setVisible(show_audio)

        self.convert_video_quality_label.setVisible(is_video)
        self.convert_video_quality_combo.setVisible(is_video)

        self.convert_image_res_label.setVisible(is_image)
        self.convert_image_res_combo.setVisible(is_image)

        self._populate_convert_qualities(ext)

    def _populate_convert_qualities(self, ext: str) -> None:
        if ext in {"mp3", "m4a", "opus"}:
            current = self.convert_audio_quality_combo.currentData()
            self.convert_audio_quality_combo.blockSignals(True)
            try:
                self.convert_audio_quality_combo.clear()
                for k in self._audio_kbps_candidates():
                    self.convert_audio_quality_combo.addItem(f"{k} kbps", int(k))
            finally:
                self.convert_audio_quality_combo.blockSignals(False)
            if current is not None:
                idx = self.convert_audio_quality_combo.findData(current)
                if idx >= 0:
                    self.convert_audio_quality_combo.setCurrentIndex(idx)
            return

        current_v = self.convert_video_quality_combo.currentData()
        self.convert_video_quality_combo.blockSignals(True)
        try:
            self.convert_video_quality_combo.clear()
            in_kind = getattr(self, "_convert_in_kind", "video")
            if ext in {"mp4", "mkv", "webm"} or (ext == "gif" and in_kind == "video"):
                for h in [1080, 720, 480, 360, 240, 180, 144, 120, 96, 72, 48, 36, 24]:
                    self.convert_video_quality_combo.addItem(f"{h}p", int(h))
        finally:
            self.convert_video_quality_combo.blockSignals(False)

        if current_v is not None:
            idx = self.convert_video_quality_combo.findData(current_v)
            if idx >= 0:
                self.convert_video_quality_combo.setCurrentIndex(idx)

    def _on_browse_convert_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self._tr("convert_pick"), "", "All files (*.*)")
        if path:
            self._on_convert_file_selected(path)

    def _on_convert_file_selected(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        self._convert_in_path = p
        kind = self._detect_input_kind(p)
        self._convert_in_kind = kind
        self.convert_drop.set_subtitle(f"{p.name}  •  {kind.upper()}")
        self._update_convert_preview(p)
        self._set_convert_targets(kind)
        if kind == "audio":
            idx = self.convert_format_combo.findData("mp3")
        elif kind == "video":
            idx = self.convert_format_combo.findData("mp4")
        else:
            idx = self.convert_format_combo.findData("png")
        if idx >= 0:
            self.convert_format_combo.setCurrentIndex(idx)

    def _detect_input_kind(self, p: Path) -> str:
        ext = p.suffix.lower().lstrip(".")
        if ext in {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"}:
            return "audio"
        if ext in {"mp4", "mkv", "webm", "mov", "avi", "m4v"}:
            return "video"
        return "image"

    def _set_convert_targets(self, kind: str) -> None:
        current = self.convert_format_combo.currentData()
        self.convert_format_combo.blockSignals(True)
        try:
            self.convert_format_combo.clear()
            if kind == "audio":
                targets = ("mp3", "m4a", "opus")
            elif kind == "image":
                targets = ("jpg", "png", "webp", "jfif", "gif")
            else:
                targets = ("mp4", "mkv", "webm", "mp3", "m4a", "opus", "ogg", "jpg", "png", "webp", "jfif", "gif")
            for ext in targets:
                self.convert_format_combo.addItem(ext, ext)
        finally:
            self.convert_format_combo.blockSignals(False)

        if current is not None:
            idx = self.convert_format_combo.findData(current)
            if idx >= 0:
                self.convert_format_combo.setCurrentIndex(idx)

    def _update_convert_preview(self, p: Path) -> None:
        self.convert_preview.clear()
        self.convert_preview.setVisible(False)
        max_w = 420
        max_h = 240

        kind = self._detect_input_kind(p)
        pix: QPixmap | None = None
        if kind == "image":
            r = QImageReader(str(p))
            r.setAutoTransform(True)
            img = r.read()
            if not img.isNull():
                pix = QPixmap.fromImage(img)
        else:
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                args = [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    "00:00:01.000",
                    "-i",
                    str(p),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ]
                try:
                    res = subprocess.run(args, capture_output=True, check=False)
                    if res.returncode == 0 and res.stdout:
                        img = QImage.fromData(res.stdout, "PNG")
                        if not img.isNull():
                            pix = QPixmap.fromImage(img)
                except Exception:
                    pix = None

        if pix is None or pix.isNull():
            return
        pix = pix.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.convert_preview.setPixmap(pix)
        self.convert_preview.setVisible(True)

    def _convert_out_path(self, in_path: Path, out_dir: Path, ext: str) -> Path:
        name = self._sanitize_output_stem(self.convert_output_name_edit.text())
        stem = name or in_path.stem
        return out_dir / f"{stem}.{ext}"

    def _set_convert_busy(self, busy: bool) -> None:
        self.convert_start_btn.setEnabled(not busy)
        self.convert_cancel_btn.setEnabled(busy)
        self.convert_drop.setEnabled(not busy)
        self.convert_output_dir_edit.setEnabled(not busy)
        self.convert_output_browse_btn.setEnabled(not busy)
        self.convert_output_open_btn.setEnabled(not busy)
        self.convert_output_name_edit.setEnabled(not busy)
        self.convert_format_combo.setEnabled(not busy)
        self.convert_video_quality_combo.setEnabled(not busy)
        self.convert_audio_quality_combo.setEnabled(not busy)
        self.convert_image_res_combo.setEnabled(not busy)

    def _on_convert_start(self) -> None:
        if self._convert_worker is not None:
            return

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            QMessageBox.critical(self, self._tr("ffmpeg_missing_title"), self._tr("ffmpeg_missing_body"))
            return

        in_path = self._convert_in_path
        if in_path is None:
            QMessageBox.warning(self, self._tr("invalid_settings"), self._tr("convert_need_input"))
            return
        out_dir = Path(self.convert_output_dir_edit.text().strip() or "")
        if not out_dir:
            QMessageBox.warning(self, self._tr("invalid_settings"), self._tr("convert_need_output"))
            return
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        ext = str(self.convert_format_combo.currentData())
        out_path = self._convert_out_path(in_path, out_dir, ext)

        args: list[str] = [ffmpeg, "-hide_banner", "-y", "-i", str(in_path)]
        keep_meta = self._settings.value("keep_metadata", False, bool)
        if ext in {"mp3", "m4a", "opus"}:
            if keep_meta:
                args += ["-map_metadata", "0"]
            kbps = self.convert_audio_quality_combo.currentData()
            if kbps is not None:
                args += ["-b:a", f"{int(kbps)}k"]
        if ext in {"mp4", "mkv", "webm"}:
            if keep_meta:
                args += ["-map_metadata", "0"]
            h = self.convert_video_quality_combo.currentData()
            if h is not None:
                args += ["-vf", f"scale=-2:{int(h)}"]
            kbps = self.convert_audio_quality_combo.currentData()
            if kbps is not None:
                args += ["-b:a", f"{int(kbps)}k"]
            if ext == "webm":
                args += ["-c:v", "libvpx-vp9", "-crf", "35", "-b:v", "0", "-c:a", "libopus"]
            else:
                args += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac"]
        if ext in {"jpg", "png", "webp", "jfif", "gif"}:
            in_kind = getattr(self, "_convert_in_kind", self._detect_input_kind(in_path))
            if ext == "gif" and in_kind == "video":
                h = self.convert_video_quality_combo.currentData()
                if h is None:
                    h = 360
                fps = 12
                args = [ffmpeg, "-hide_banner", "-y", "-i", str(in_path)]
                vf = f"fps={int(fps)},scale=-2:{int(h)}:flags=lanczos"
                fc = f"{vf},split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
                args += ["-filter_complex", fc, "-loop", "0"]
            else:
                h = self.convert_image_res_combo.currentData()
                if h is not None:
                    args += ["-vf", f"scale=-2:{int(h)}"]
                args += ["-frames:v", "1"]
                if ext in {"jpg", "jfif"}:
                    args += ["-q:v", "2"]
                if ext == "webp":
                    args += ["-q:v", "80"]
        args.append(str(out_path))

        self.convert_log.clear()
        self.convert_progress.setRange(0, 0)
        self.convert_log.append(" ".join(args))

        self._set_convert_busy(True)
        self._convert_worker = FfmpegWorker(args)
        self._convert_worker.line_received.connect(self.convert_log.append)
        self._convert_worker.finished_with_code.connect(self._on_convert_finished)
        self._convert_worker.start()

    def _on_convert_cancel(self) -> None:
        w = self._convert_worker
        if w is None:
            return
        self.convert_log.append(self._tr("cancelling"))
        w.cancel()

    def _on_convert_finished(self, code: int) -> None:
        self._set_convert_busy(False)
        self.convert_progress.setRange(0, 100)
        self.convert_progress.setValue(100 if code == 0 else 0)
        self._convert_worker = None
        if code == 0:
            self.convert_log.append(self._tr("done"))
        else:
            self.convert_log.append(self._tr("failed").format(code=code))

    def _on_language_changed(self) -> None:
        self._language = str(self.language_combo.currentData())
        self._persist_settings()
        self._apply_language(self._language)

    def _on_theme_changed(self) -> None:
        theme = str(self.theme_combo.currentData())
        self._apply_theme(theme)
        self._persist_settings()

    def _on_animations_changed(self) -> None:
        self._animations_enabled = bool(self.animations_checkbox.isChecked())
        self._persist_settings()

    def _pick_sound_file(self, kind: str) -> None:
        start = str(self._app_root())
        path, _ = QFileDialog.getOpenFileName(self, self._tr("pick_sound"), start, "Audio (*.wav *.mp3 *.ogg);;All files (*.*)")
        if not path:
            return
        p = str(Path(path).resolve())
        if kind == "error":
            self.sound_err_path.setText(p)
        elif kind == "cookies":
            self.sound_cookies_path.setText(p)
        elif kind == "success":
            self.sound_success_path.setText(p)
        self._on_sound_changed(kind)

    def _on_sound_changed(self, kind: str, vol: int | None = None) -> None:
        if kind == "error":
            enabled = bool(self.sound_err_enable.isChecked())
            path = self.sound_err_path.text().strip()
            v = int(self.sound_err_vol.value() if vol is None else vol)
            self._settings.setValue("sound_error_enabled", enabled)
            self._settings.setValue("sound_error_file", path)
            self._settings.setValue("sound_error_vol", v)
            self.sound_err_path.setEnabled(enabled)
            self.sound_err_browse.setEnabled(enabled)
            self.sound_err_vol.setEnabled(enabled)
            self.sound_err_play.setEnabled(True)
        elif kind == "cookies":
            enabled = bool(self.sound_cookies_enable.isChecked())
            path = self.sound_cookies_path.text().strip()
            v = int(self.sound_cookies_vol.value() if vol is None else vol)
            self._settings.setValue("sound_cookies_enabled", enabled)
            self._settings.setValue("sound_cookies_file", path)
            self._settings.setValue("sound_cookies_vol", v)
            self.sound_cookies_path.setEnabled(enabled)
            self.sound_cookies_browse.setEnabled(enabled)
            self.sound_cookies_vol.setEnabled(enabled)
            self.sound_cookies_play.setEnabled(True)
        elif kind == "success":
            enabled = bool(self.sound_success_enable.isChecked())
            path = self.sound_success_path.text().strip()
            v = int(self.sound_success_vol.value() if vol is None else vol)
            self._settings.setValue("sound_success_enabled", enabled)
            self._settings.setValue("sound_success_file", path)
            self._settings.setValue("sound_success_vol", v)
            self.sound_success_path.setEnabled(enabled)
            self.sound_success_browse.setEnabled(enabled)
            self.sound_success_vol.setEnabled(enabled)
            self.sound_success_play.setEnabled(True)

        self._init_sounds()

    def _preview_sound(self, kind: str) -> None:
        if QSoundEffect is None:
            QApplication.beep()
            return
        path = ""
        vol = 50
        if kind == "error":
            path = self.sound_err_path.text().strip()
            vol = int(self.sound_err_vol.value())
        elif kind == "cookies":
            path = self.sound_cookies_path.text().strip()
            vol = int(self.sound_cookies_vol.value())
        elif kind == "success":
            path = self.sound_success_path.text().strip()
            vol = int(self.sound_success_vol.value())

        if not path or not Path(path).exists():
            QApplication.beep()
            return

        eff = self._sound_effects.get(kind)
        if eff is None:
            eff = QSoundEffect(self)
            eff.setLoopCount(1)
            self._sound_effects[kind] = eff
        eff.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        eff.setVolume(max(0.0, min(1.0, float(vol) / 100.0)))
        eff.play()

    def _on_open_after_download_changed(self) -> None:
        self._settings.setValue("open_after_download", bool(self.open_after_download_checkbox.isChecked()))

    def _on_keep_metadata_changed(self) -> None:
        self._settings.setValue("keep_metadata", bool(self.keep_metadata_checkbox.isChecked()))

    def _on_fetch_formats(self, silent: bool = False) -> None:
        url = self.url_edit.text().strip()
        if not url:
            if not silent:
                QMessageBox.warning(self, self._tr("missing_url_title"), self._tr("missing_url_body"))
            return

        self._not_found_shown = False
        cookies = self.cookies_edit.text().strip() or None
        try:
            info = _run_yt_dlp_json(url, cookies)
            info = _first_entry_info(info)
            formats: list[dict[str, Any]] = info.get("formats") or []
            heights = _extract_video_heights(info)
            self._available_heights = heights
            self._height_to_video_kbps = self._collect_video_kbps(formats)
            self._available_audio_kbps = self._collect_audio_kbps(formats)
            self._last_fetched_url = url
            self._refresh_audio_quality_combo()
            self._refresh_quality_combo()
            if heights:
                self._append_log(self._tr("fetched_video") + ": " + ", ".join(str(h) + "p" for h in heights))
            if self._available_audio_kbps:
                self._append_log(self._tr("fetched_audio") + ": " + ", ".join(str(k) + " kbps" for k in self._available_audio_kbps[:8]))
        except Exception as e:
            if not silent:
                msg = str(e).strip()
                self._play_sound("error")
                if self._is_not_found_text(msg.lower()):
                    self._show_notice("error", self._tr("not_found").format(detail=msg), timeout_ms=6500)
                else:
                    self._show_notice("error", f"{self._tr('fetch_failed_title')}: {msg}", timeout_ms=6500)

    def _yt_dlp_args(self, overwrite: bool) -> list[str]:
        url = self.url_edit.text().strip()
        out_dir = self.output_dir_edit.text().strip()
        cookies = self.cookies_edit.text().strip()
        kind, fmt_selected = self._current_format()
        selection = self.quality_combo.currentData()
        custom_name = self._sanitize_output_stem(self.output_name_edit.text())

        if not url:
            raise ValueError(self._tr("url_required"))
        if not out_dir:
            raise ValueError(self._tr("out_required"))

        args: list[str] = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-color",
            "--newline",
            "--progress",
            "--no-warnings",
            "-P",
            out_dir,
        ]

        if custom_name:
            args += ["-o", f"{custom_name}.%(ext)s"]

        if overwrite:
            args += ["--force-overwrites", "--no-continue"]
        else:
            args += ["--no-overwrites"]

        if cookies:
            if not Path(cookies).exists():
                raise ValueError(self._tr("cookies_missing").format(path=cookies))
            args += ["--cookies", cookies]

        self._pending_scale_height = None
        self._pending_scale_container = None
        self._pending_video_bitrate_kbps = None
        self._downloaded_files = []

        if self._settings.value("keep_metadata", False, bool):
            args += ["--add-metadata"]

        args += ["--print", "after_move:filepath"]

        if kind == "audio":
            audio_format = fmt_selected
            kbps = self.audio_quality_combo.currentData()
            if kbps is None:
                fmt = "bestaudio/best"
            else:
                k = int(kbps)
                fmt = f"worstaudio[abr<={k}]/worstaudio/bestaudio[abr<={k}]/bestaudio/worstaudio/best"
            args += [
                "-f",
                fmt,
                "-x",
                "--audio-format",
                audio_format,
            ]
            if kbps is not None:
                args += ["--postprocessor-args", f"FFmpegExtractAudio:-b:a {int(kbps)}k"]
        else:
            container = fmt_selected
            if container == "gif":
                preset = selection
                if not isinstance(preset, dict):
                    preset = self._gif_presets()[1]
                h = int(preset["height"])
                fps = int(preset["fps"])
                fmt = f"bv*[height<={h}]/bv*"
                args += ["-f", fmt, "--recode-video", "gif"]
                vf = f"fps={fps},scale=-1:{h}:flags=neighbor"
                args += ["--postprocessor-args", f"ffmpeg:-vf {vf}"]
            else:
                height = selection if isinstance(selection, int) else None
                if height is None:
                    vsel = "bestvideo/best"
                else:
                    vsel = f"bestvideo[height<={int(height)}]/bestvideo/best"

                if height is not None and (not self._available_heights or not any(h <= height for h in self._available_heights)):
                    self._pending_scale_height = int(height)
                    self._pending_scale_container = container

                vb = self.video_bitrate_combo.currentData()
                if isinstance(vb, int) and vb > 0 and shutil.which("ffmpeg") is not None:
                    self._pending_video_bitrate_kbps = int(vb)

                include_audio = bool(self.include_audio_checkbox.isChecked())
                if include_audio:
                    ffmpeg_ok = shutil.which("ffmpeg") is not None
                    aq = self.audio_quality_combo.currentData()
                    if not ffmpeg_ok:
                        if height is None:
                            fmt = "best"
                        else:
                            fmt = f"best[height<={int(height)}]/best"
                        args += ["-f", fmt, "--remux-video", container]
                    else:
                        abr_filter = "" if aq is None else f"[abr<={int(aq)}]"
                        if container == "mp4":
                            asel = (
                                f"bestaudio{abr_filter}[ext=m4a]/bestaudio[ext=m4a]/"
                                f"bestaudio{abr_filter}[acodec^=mp4a]/bestaudio[acodec^=mp4a]/"
                                f"bestaudio{abr_filter}/bestaudio/best"
                            )
                        elif container == "webm":
                            asel = (
                                f"bestaudio{abr_filter}[acodec*=opus]/bestaudio[acodec*=opus]/"
                                f"bestaudio{abr_filter}[ext=webm]/bestaudio[ext=webm]/"
                                f"bestaudio{abr_filter}/bestaudio/best"
                            )
                        else:
                            asel = "bestaudio/best" if aq is None else f"bestaudio[abr<={int(aq)}]/bestaudio/best"
                        fmt = f"({vsel})+({asel})/best"
                        args += ["-f", fmt, "--merge-output-format", container]
                else:
                    fmt = vsel
                    args += ["-f", fmt, "--remux-video", container]

        args.append(url)
        return args

    def _on_start(self) -> None:
        if self._worker is not None:
            return

        if not self._is_valid_url(self.url_edit.text()):
            self._play_sound("error")
            self._show_notice("error", self._tr("missing_url_body"))
            return

        try:
            overwrite = self._confirm_overwrite_if_needed()
            if overwrite is None:
                return
            args = self._yt_dlp_args(overwrite=overwrite)
        except Exception as e:
            msg = str(e)
            if "cookies" in msg.lower():
                self._play_sound("cookies")
                self._show_notice("cookies", msg)
            else:
                self._play_sound("error")
                self._show_notice("error", msg)
            return

        self._persist_settings()
        self.log.clear()
        self.progress.setValue(0)
        self._cookies_error_shown = False
        self._not_found_shown = False
        self._append_log(self._tr("starting"))
        self._append_log(" ".join(args))

        self._worker = YtDlpWorker(args)
        self._worker.line_received.connect(self._append_log)
        self._worker.progress_changed.connect(self.progress.setValue)
        self._worker.finished_with_code.connect(self._on_worker_finished)
        self._set_busy(True)
        self._worker.start()

    def _confirm_overwrite_if_needed(self) -> bool | None:
        kind, fmt_selected = self._current_format()
        out_dir = self.output_dir_edit.text().strip()
        if not out_dir:
            return False
        url = self.url_edit.text().strip()
        if not url:
            return False

        custom_name = self._sanitize_output_stem(self.output_name_edit.text())
        expected_ext = fmt_selected if kind == "audio" else fmt_selected
        selection = self.quality_combo.currentData()
        if kind == "video" and fmt_selected == "gif":
            expected_ext = "gif"
        if kind == "audio":
            expected_ext = fmt_selected
        if kind == "video":
            expected_ext = fmt_selected

        probe = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-color",
            "--no-warnings",
            "-P",
            out_dir,
            "--skip-download",
            "--print",
            "filename",
            url,
        ]
        if custom_name:
            probe[probe.index(url) : probe.index(url)] = ["-o", f"{custom_name}.%(ext)s"]
        cookies = self.cookies_edit.text().strip()
        if cookies:
            if not Path(cookies).exists():
                raise ValueError(self._tr("cookies_missing").format(path=cookies))
            probe[probe.index(url) : probe.index(url)] = ["--cookies", cookies]

        try:
            res = subprocess.run(probe, capture_output=True, text=True, check=False)
        except Exception:
            return False

        lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return False

        existing: list[Path] = []
        for ln in lines[:200]:
            p = Path(ln)
            if expected_ext and p.suffix.lower().lstrip(".") != expected_ext.lower():
                p = p.with_suffix("." + expected_ext)
            if p.exists():
                existing.append(p)

        if not existing:
            return False

        title = self._tr("overwrite_title")
        body = self._tr("overwrite_body").format(n=len(existing))
        reply = QMessageBox.question(self, title, body, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            return True

        self._show_notice("info", self._tr("overwrite_cancelled"), timeout_ms=4500)
        return None

    def _on_cancel(self) -> None:
        w = self._worker
        if w is None:
            return
        self._append_log(self._tr("cancelling"))
        w.cancel()

    def _on_worker_finished(self, code: int) -> None:
        if code == 0 and self._pending_scale_height is not None and self._pending_scale_container and self._downloaded_files:
            src = self._downloaded_files[-1]
            self._worker = None
            self._start_post_scale(src, self._pending_scale_height)
            return

        if code == 0 and self._pending_video_bitrate_kbps is not None and self._downloaded_files:
            src = self._downloaded_files[-1]
            kbps = int(self._pending_video_bitrate_kbps)
            self._pending_video_bitrate_kbps = None
            self._worker = None
            self._start_post_bitrate(src, kbps)
            return

        self._worker = None
        self._set_busy(False)
        if code == 0:
            self.progress.setValue(100)
            self._append_log(self._tr("done"))
            self._play_sound("success")
            self._show_notice("success", self._tr("download_success"), timeout_ms=3800)
            if self._settings.value("open_after_download", True, bool):
                out_dir = self.output_dir_edit.text().strip()
                if out_dir:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out_dir).resolve())))
        else:
            self._append_log(self._tr("failed").format(code=code))
            self._play_sound("error")
            self._show_notice("error", self._tr("download_failed").format(code=code), timeout_ms=6500)

        QTimer.singleShot(500, lambda: self.progress.setValue(0) if self._worker is None else None)

    def _audio_kbps_candidates(self) -> list[int]:
        base = [320, 256, 224, 192, 160, 128, 96, 64, 48, 32, 24, 16, 12, 8]
        s = set(base)
        avail = list(self._available_audio_kbps or [])
        s.update(avail)
        if avail:
            mx = max(avail)
            return sorted([k for k in s if k <= mx], reverse=True)
        return sorted(s, reverse=True)

    def _start_post_bitrate(self, src: Path, kbps: int) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self._set_busy(False)
            return

        dst = src.with_name(f"{src.stem}.br{src.suffix}")
        self._post_src = src
        self._post_dst = dst

        meta_args: list[str] = []
        if self._settings.value("keep_metadata", False, bool):
            meta_args = ["-map_metadata", "0"]

        suffix = src.suffix.lower()
        if suffix == ".webm":
            v_args = ["-c:v", "libvpx-vp9", "-b:v", f"{int(kbps)}k", "-deadline", "good", "-cpu-used", "4"]
        else:
            v_args = ["-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{int(kbps)}k"]
        a_args = ["-c:a", "copy"]

        args = [ffmpeg, "-hide_banner", "-y", "-i", str(src), *meta_args, *v_args, *a_args, str(dst)]
        self._append_log(f"Post-process: video bitrate {int(kbps)} kbps…")
        self.progress.setRange(0, 0)
        self._post_worker = FfmpegWorker(args)
        self._post_worker.line_received.connect(self._append_log)
        self._post_worker.finished_with_code.connect(self._on_post_bitrate_finished)
        self._set_busy(True)
        self._post_worker.start()

    def _start_post_scale(self, src: Path, height: int) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self._append_log(self._tr("ffmpeg_missing_title") + ": " + self._tr("ffmpeg_missing_body"))
            self._set_busy(False)
            self._pending_scale_height = None
            self._pending_scale_container = None
            return

        dst = src.with_name(f"{src.stem}.scaled{src.suffix}")
        self._post_src = src
        self._post_dst = dst

        suffix = src.suffix.lower()
        if suffix == ".webm":
            v_args = ["-c:v", "libvpx-vp9", "-crf", "35", "-b:v", "0"]
            aq = self.audio_quality_combo.currentData()
            abr = int(aq) if isinstance(aq, int) and aq > 0 else 96
            a_args = ["-c:a", "libopus", "-b:a", f"{abr}k"]
        else:
            v_args = ["-c:v", "libx264", "-preset", "veryfast"]
            a_args = ["-c:a", "copy"]

        args = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(src),
            "-vf",
            f"scale=-2:{int(height)}",
            *v_args,
            *a_args,
            str(dst),
        ]

        self._append_log(f"Post-process: scale to {height}p…")
        self.progress.setRange(0, 0)
        self._post_worker = FfmpegWorker(args)
        self._post_worker.line_received.connect(self._append_log)
        self._post_worker.finished_with_code.connect(self._on_post_scale_finished)
        self._set_busy(True)
        self._post_worker.start()

    def _on_post_scale_finished(self, code: int) -> None:
        src = self._post_src
        dst = self._post_dst
        self._post_worker = None
        self._post_src = None
        self._post_dst = None
        self._pending_scale_height = None
        self._pending_scale_container = None

        self.progress.setRange(0, 100)
        if code != 0 or src is None or dst is None or not dst.exists():
            self._set_busy(False)
            self._append_log(self._tr("failed").format(code=code))
            self._play_sound("error")
            self._show_notice("error", self._tr("download_failed").format(code=code), timeout_ms=6500)
            QTimer.singleShot(500, lambda: self.progress.setValue(0) if self._worker is None else None)
            return

        try:
            tmp_backup = src.with_name(f"{src.stem}.orig{src.suffix}")
            try:
                src.replace(tmp_backup)
            except Exception:
                pass
            dst.replace(src)
            try:
                if tmp_backup.exists():
                    tmp_backup.unlink()
            except Exception:
                pass
        except Exception as e:
            self._append_log(str(e))

        if self._pending_video_bitrate_kbps is not None:
            kbps = int(self._pending_video_bitrate_kbps)
            self._pending_video_bitrate_kbps = None
            self._start_post_bitrate(src, kbps)
            return

        self.progress.setValue(100)
        self._set_busy(False)
        self._append_log(self._tr("done"))
        self._play_sound("success")
        self._show_notice("success", self._tr("download_success"), timeout_ms=3800)
        if self._settings.value("open_after_download", True, bool):
            out_dir = self.output_dir_edit.text().strip()
            if out_dir:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out_dir).resolve())))
        QTimer.singleShot(500, lambda: self.progress.setValue(0) if self._worker is None else None)

    def _on_post_bitrate_finished(self, code: int) -> None:
        src = self._post_src
        dst = self._post_dst
        self._post_worker = None
        self._post_src = None
        self._post_dst = None

        self.progress.setRange(0, 100)
        if code != 0 or src is None or dst is None or not dst.exists():
            self._set_busy(False)
            self._append_log(self._tr("failed").format(code=code))
            self._play_sound("error")
            self._show_notice("error", self._tr("download_failed").format(code=code), timeout_ms=6500)
            QTimer.singleShot(500, lambda: self.progress.setValue(0) if self._worker is None else None)
            return

        try:
            tmp_backup = src.with_name(f"{src.stem}.orig{src.suffix}")
            try:
                src.replace(tmp_backup)
            except Exception:
                pass
            dst.replace(src)
            try:
                if tmp_backup.exists():
                    tmp_backup.unlink()
            except Exception:
                pass
        except Exception as e:
            self._append_log(str(e))

        self.progress.setValue(100)
        self._set_busy(False)
        self._append_log(self._tr("done"))
        self._play_sound("success")
        self._show_notice("success", self._tr("download_success"), timeout_ms=3800)
        if self._settings.value("open_after_download", True, bool):
            out_dir = self.output_dir_edit.text().strip()
            if out_dir:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(out_dir).resolve())))
        QTimer.singleShot(500, lambda: self.progress.setValue(0) if self._worker is None else None)

    def _is_not_found_text(self, low: str) -> bool:
        s = (low or "").lower()
        if not s:
            return False
        if "error" not in s and "http" not in s:
            if not s.startswith("unable to download") and not s.startswith("unsupported url"):
                return False
        markers = (
            "404",
            "not found",
            "video unavailable",
            "this video is private",
            "private video",
            "removed",
            "deleted",
            "does not exist",
            "content is not available",
            "unsupported url",
            "unable to download",
            "no video formats found",
        )
        return any(m in s for m in markers)

    def closeEvent(self, event) -> None:
        self._persist_settings()
        w = self._worker
        if w is not None:
            res = QMessageBox.question(self, self._tr("dl_running_title"), self._tr("dl_running_body"))
            if res != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            w.cancel()
        event.accept()

    def _on_video_format_changed(self) -> None:
        self._sync_mode_visibility()

    def _on_quality_changed(self) -> None:
        self._persist_settings()

    def _on_audio_quality_changed(self) -> None:
        self._persist_settings()

    def _on_video_bitrate_changed(self) -> None:
        self._persist_settings()

    def _collect_video_kbps(self, formats: list[dict[str, Any]]) -> dict[int, int]:
        out: dict[int, int] = {}
        for f in formats:
            vcodec = f.get("vcodec")
            if not vcodec or vcodec == "none":
                continue
            h = f.get("height")
            if not isinstance(h, int) or h <= 0:
                continue
            br = f.get("vbr")
            if br is None:
                br = f.get("tbr")
            if not isinstance(br, (int, float)) or br <= 0:
                continue
            kbps = int(round(float(br)))
            prev = out.get(h)
            if prev is None or kbps > prev:
                out[h] = kbps
        return out

    def _collect_audio_kbps(self, formats: list[dict[str, Any]]) -> list[int]:
        s: set[int] = set()
        for f in formats:
            acodec = f.get("acodec")
            if not acodec or acodec == "none":
                continue
            if f.get("vcodec") and f.get("vcodec") != "none":
                continue
            br = f.get("abr")
            if br is None:
                br = f.get("tbr")
            if not isinstance(br, (int, float)) or br <= 0:
                continue
            s.add(int(round(float(br))))
        return sorted(s, reverse=True)

    def _video_quality_candidates(self) -> list[int]:
        base = [2160, 1440, 1080, 720, 480, 360, 240, 180, 144, 120, 96, 72, 48, 36, 24]
        if not self._available_heights:
            return base
        mx = max(self._available_heights)
        s = set(base).union(self._available_heights)
        return sorted([h for h in s if h <= mx], reverse=True)

    def _gif_presets(self) -> list[dict[str, int | str]]:
        return [
            {"key": "gif_mote", "height": 32, "fps": 4},
            {"key": "gif_toaster", "height": 48, "fps": 6},
            {"key": "gif_mini", "height": 64, "fps": 8},
            {"key": "gif_tiny", "height": 96, "fps": 9},
            {"key": "gif_potato", "height": 120, "fps": 10},
            {"key": "gif_retro", "height": 180, "fps": 12},
            {"key": "gif_classic", "height": 240, "fps": 14},
            {"key": "gif_smooth", "height": 360, "fps": 16},
            {"key": "gif_hd", "height": 480, "fps": 18},
            {"key": "gif_glorious", "height": 720, "fps": 22},rename
        ]

    def _gif_preset_label(self, p: dict[str, Any]) -> str:
        name = self._tr(str(p["key"]))
        return f"{name} · {int(p['height'])}p · {int(p['fps'])} fps"

    def _tr(self, key: str) -> str:
        table: dict[str, dict[str, str]] = {
            "download": {"en": "⬇️ Download", "es": "⬇️ Descargar", "pirate": "⬇️ Plunder"},
            "convert": {"en": "🔁 Convert", "es": "🔁 Convertir", "pirate": "🔁 Transmog"},
            "convert_progress": {"en": "📦 Conversion", "es": "📦 Conversión", "pirate": "📦 Alchemy"},
            "accessibility": {"en": "♿️ Accessibility", "es": "♿️ Accesibilidad", "pirate": "♿️ Cap'n Options"},
            "settings": {"en": "⚙️ Settings", "es": "⚙️ Ajustes", "pirate": "⚙️ Riggin'"},
            "about": {"en": "ℹ️ About", "es": "ℹ️ Acerca de", "pirate": "ℹ️ About Yer Ship"},
            "source": {"en": "🔗 Source", "es": "🔗 Fuente", "pirate": "🔗 Treasure Map"},
            "options": {"en": "🧰 Options", "es": "🧰 Opciones", "pirate": "🧰 Tricks"},
            "output": {"en": "📁 Output", "es": "📁 Salida", "pirate": "📁 Booty Hold"},
            "progress": {"en": "📈 Progress", "es": "📈 Progreso", "pirate": "📈 Voyage"},
            "sounds": {"en": "🔊 Sounds", "es": "🔊 Sonidos", "pirate": "🔊 Noises"},
            "preferences": {"en": "🧩 Preferences", "es": "🧩 Preferencias", "pirate": "🧩 Captain's Picks"},
            "appearance": {"en": "🎨 Appearance", "es": "🎨 Apariencia", "pirate": "🎨 Paint & Polish"},
            "theme": {"en": "Theme", "es": "Tema", "pirate": "Flag Colors"},
            "sound_error": {"en": "⚠️ Error", "es": "⚠️ Error", "pirate": "⚠️ Shipwreck"},
            "sound_cookies": {"en": "🍪 Cookies error", "es": "🍪 Error de cookies", "pirate": "🍪 Crumbs Trouble"},
            "sound_success": {"en": "✅ Success", "es": "✅ Éxito", "pirate": "✅ Haul Complete"},
            "volume": {"en": "Volume", "es": "Volumen", "pirate": "Loudness"},
            "pick_sound": {"en": "Select sound file", "es": "Seleccionar sonido", "pirate": "Pick yer noise"},
            "open_after_download": {
                "en": "📂 Open output folder after download",
                "es": "📂 Abrir carpeta de salida al terminar",
                "pirate": "📂 Open the hold after plunder",
            },
            "keep_metadata": {
                "en": "🏷️ Keep original metadata",
                "es": "🏷️ Mantener metadatos originales",
                "pirate": "🏷️ Keep the old marks",
            },
            "output_name": {"en": "🗒 Rename", "es": "🗒 Renombrar", "pirate": "🗒 Rename"},
            "output_name_ph": {
                "en": "Optional output filename (no extension)",
                "es": "Nombre de salida opcional (sin extensión)",
                "pirate": "Optional booty name (no extension)",
            },
            "github_repo": {"en": "GitHub", "es": "GitHub", "pirate": "GitHub"},
            "x_link": {"en": "🐦 X", "es": "🐦 X", "pirate": "🐦 Treasure Map"},
            "overwrite_title": {"en": "File exists", "es": "Archivo existe", "pirate": "Booty Already There"},
            "overwrite_body": {
                "en": "{n} file(s) already exist in the output folder. Overwrite?",
                "es": "Ya existen {n} archivo(s) en la carpeta de salida. ¿Sobrescribir?",
                "pirate": "{n} loot file(s) already be there. Overwrite 'em?",
            },
            "overwrite_cancelled": {
                "en": "Download cancelled (kept existing files).",
                "es": "Descarga cancelada (se mantuvieron los archivos).",
                "pirate": "Belayed (kept the old booty).",
            },
            "sound_unavailable": {
                "en": "Audio playback is unavailable in this build (QtMultimedia missing).",
                "es": "La reproducción de audio no está disponible en esta versión (falta QtMultimedia).",
                "pirate": "No sound deck be installed on this ship.",
            },
            "type": {"en": "Type", "es": "Tipo", "pirate": "Loot Type"},
            "quality": {"en": "🔍 Quality", "es": "🔍 Calidad", "pirate": "🔍 Shininess"},
            "auto": {"en": "Auto", "es": "Auto", "pirate": "Auto"},
            "video_bitrate": {"en": "🎛️ Video bitrate", "es": "🎛️ Bitrate de video", "pirate": "🎛️ Video Bitrate"},
            "video_format": {"en": "🎞️ Video format", "es": "🎞️ Formato", "pirate": "🎞️ Moving Picture"},
            "audio_format": {"en": "Audio format", "es": "Formato de audio", "pirate": "Sea Shanty Type"},
            "audio_quality": {"en": "🎙 Audio quality", "es": "🎙 Calidad de audio", "pirate": "🎙 Shanty Quality"},
            "include_audio": {"en": "🔊 Include audio", "es": "🔊 Incluir audio", "pirate": "🔊 Keep the Shanty"},
            "url": {"en": "URL", "es": "URL", "pirate": "Map Link"},
            "cookies": {"en": "Cookies", "es": "Cookies", "pirate": "Crumbs"},
            "custom": {"en": "🎚️ Custom", "es": "🎚️ Personalizado", "pirate": "🎚️ Hand-Tuned"},
            "url_ph": {"en": "Paste a video/playlist URL…", "es": "Pega una URL de video/lista…", "pirate": "Paste a map to the loot…"},
            "cookies_ph": {
                "en": "Optional: cookies.txt (Netscape format)",
                "es": "Opcional: cookies.txt (formato Netscape)",
                "pirate": "Optional: cookie crumbs (Netscape)",
            },
            "output_ph": {"en": "Choose an output folder…", "es": "Elige una carpeta de salida…", "pirate": "Pick a hold fer booty…"},
            "fetch": {"en": "Fetch formats", "es": "Buscar formatos", "pirate": "Scout the Seas"},
            "browse": {"en": "Browse…", "es": "Examinar…", "pirate": "Rummage…"},
            "download_btn": {"en": "Download", "es": "Descargar", "pirate": "Plunder"},
            "cancel": {"en": "Cancel", "es": "Cancelar", "pirate": "Belay"},
            "open_folder": {"en": "Open folder", "es": "Abrir carpeta", "pirate": "Open the Hold"},
            "details": {"en": "Details", "es": "Detalles", "pirate": "Tales"},
            "clear": {"en": "Clear", "es": "Limpiar", "pirate": "Swab"},
            "set_downloads": {"en": "Set output to Downloads", "es": "Salida a Descargas", "pirate": "Send to Booty Pile"},
            "reset_qualities": {"en": "Reset fetched qualities", "es": "Reiniciar calidades", "pirate": "Forget the Waters"},
            "enable_animations": {"en": "Enable animations", "es": "Habilitar animaciones", "pirate": "Wavy Movin' Bits"},
            "animations_hint": {
                "en": "If you prefer a snappier UI or your PC is under heavy load, disable animations.",
                "es": "Si prefieres una interfaz más rápida o tu PC está con mucha carga, desactiva las animaciones.",
                "pirate": "If yer rig be sluggish, turn off the fancy wiggles.",
            },
            "best": {"en": "Best", "es": "Mejor", "pirate": "Finest"},
            "missing_url_title": {"en": "Missing URL", "es": "Falta URL", "pirate": "No Map!"},
            "missing_url_body": {"en": "Paste a URL first.", "es": "Pega una URL primero.", "pirate": "Paste yer map link, matey."},
            "fetch_failed_title": {"en": "Fetch failed", "es": "Falló la búsqueda", "pirate": "Scoutin' Failed"},
            "invalid_settings": {"en": "Invalid settings", "es": "Ajustes inválidos", "pirate": "Bad Riggin'"},
            "url_required": {"en": "URL is required", "es": "La URL es obligatoria", "pirate": "Need a map link"},
            "out_required": {"en": "Output folder is required", "es": "La carpeta de salida es obligatoria", "pirate": "Need a hold fer booty"},
            "cookies_missing": {
                "en": "Cookies file not found: {path}",
                "es": "Archivo de cookies no encontrado: {path}",
                "pirate": "No cookie scroll found: {path}",
            },
            "starting": {"en": "Starting download…", "es": "Iniciando descarga…", "pirate": "Settin' sail…"},
            "cancelling": {"en": "Cancelling…", "es": "Cancelando…", "pirate": "Belayin'…"},
            "done": {"en": "Done.", "es": "Listo.", "pirate": "Haul complete."},
            "failed": {"en": "Failed (exit code {code}).", "es": "Falló (código {code}).", "pirate": "Shipwrecked (code {code})."},
            "download_success": {"en": "Download finished.", "es": "Descarga finalizada.", "pirate": "Plunder secured."},
            "download_failed": {"en": "Download failed (code {code}).", "es": "Descarga fallida (código {code}).", "pirate": "Plunder failed (code {code})."},
            "audio_quality_hint": {
                "en": "Use Audio quality →",
                "es": "Usa Calidad de audio →",
                "pirate": "Use Shanty Quality →",
            },
            "cookies_error": {
                "en": "Cookies issue: {detail}",
                "es": "Problema de cookies: {detail}",
                "pirate": "Cookie trouble: {detail}",
            },
            "not_found": {
                "en": "Not found / unavailable: {detail}",
                "es": "No encontrado / no disponible: {detail}",
                "pirate": "No loot found: {detail}",
            },
            "dl_running_title": {"en": "Download running", "es": "Descarga en curso", "pirate": "Plunder Afoot"},
            "dl_running_body": {
                "en": "A download is running. Cancel and exit?",
                "es": "Hay una descarga en curso. ¿Cancelar y salir?",
                "pirate": "Yer plunderin'. Belay and abandon ship?",
            },
            "fetched_video": {"en": "Video qualities", "es": "Calidades de video", "pirate": "Video Treasures"},
            "fetched_audio": {"en": "Audio qualities", "es": "Calidades de audio", "pirate": "Shanty Treasures"},
            "gif_mote": {"en": "Mote", "es": "Mota", "pirate": "Speck"},
            "gif_toaster": {"en": "Toaster", "es": "Tostadora", "pirate": "Toaster"},
            "gif_mini": {"en": "Mini", "es": "Mini", "pirate": "Mini"},
            "gif_tiny": {"en": "Tiny", "es": "Pequeño", "pirate": "Wee"},
            "gif_potato": {"en": "Potato", "es": "Papa", "pirate": "Spud"},
            "gif_retro": {"en": "Retro", "es": "Retro", "pirate": "Old Sea"},
            "gif_classic": {"en": "Classic", "es": "Clásico", "pirate": "Classic"},
            "gif_smooth": {"en": "Smooth-ish", "es": "Suave-ish", "pirate": "Silky-ish"},
            "gif_hd": {"en": "HD-ish", "es": "HD-ish", "pirate": "High Deck"},
            "gif_glorious": {"en": "Glorious", "es": "Glorioso", "pirate": "Glorious"},
            "convert_input": {"en": "Input file", "es": "Archivo de entrada", "pirate": "Loot File"},
            "convert_output": {"en": "📁 Output folder", "es": "📁 Carpeta de salida", "pirate": "📁 Booty Folder"},
            "convert_format": {"en": "🎯 Target format", "es": "🎯 Formato destino", "pirate": "🎯 Target Shape"},
            "convert_quality": {"en": "🎚️ Audio bitrate", "es": "🎚️ Bitrate de audio", "pirate": "🎚️ Shanty Bitrate"},
            "convert_start": {"en": "Convert", "es": "Convertir", "pirate": "Transmog"},
            "convert_in_ph": {"en": "Choose a file to convert…", "es": "Elige un archivo para convertir…", "pirate": "Pick a file to transmog…"},
            "convert_out_ph": {"en": "Choose an output folder…", "es": "Elige una carpeta de salida…", "pirate": "Pick a folder fer booty…"},
            "drag_to_convert": {"en": "🧲 Drag to Convert", "es": "🧲 Arrastra para convertir", "pirate": "🧲 Drag fer Transmog"},
            "resolution": {"en": "🖼️ Resolution", "es": "🖼️ Resolución", "pirate": "🖼️ Tallness"},
            "original": {"en": "Original", "es": "Original", "pirate": "As-is"},
            "convert_pick": {"en": "Select file", "es": "Seleccionar archivo", "pirate": "Pick yer loot"},
            "convert_out_pick": {"en": "Select output folder", "es": "Seleccionar carpeta de salida", "pirate": "Pick the hold"},
            "convert_need_input": {"en": "Select an input file first.", "es": "Selecciona un archivo de entrada.", "pirate": "Pick a loot file first."},
            "convert_need_output": {"en": "Select an output folder.", "es": "Selecciona una carpeta de salida.", "pirate": "Pick a folder fer the booty."},
            "ffmpeg_missing_title": {"en": "ffmpeg not found", "es": "ffmpeg no encontrado", "pirate": "No ffmpeg aboard"},
            "ffmpeg_missing_body": {
                "en": "Install ffmpeg and ensure it's available in PATH to use the Convert tab.",
                "es": "Instala ffmpeg y asegúrate de que esté en el PATH para usar la pestaña Convertir.",
                "pirate": "Ye need ffmpeg in yer PATH fer the alchemy.",
            },
        }
        lang = self._language if self._language in {"en", "es", "pirate"} else "en"
        return table.get(key, {}).get(lang, table.get(key, {}).get("en", key))

    def _apply_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle("descargar videos i sonidos asi bien fasil jejeje")
        self.tabs.setTabText(0, self._tr("download"))
        self.tabs.setTabText(1, self._tr("convert"))
        self.tabs.setTabText(2, self._tr("settings"))
        self.tabs.setTabText(3, self._tr("about"))

        for lbl in self.findChildren(QLabel):
            k = lbl.property("tr_key")
            if isinstance(k, str) and k:
                lbl.setText(self._tr(k))

        self.app_title.setText("el programa:")

        self.url_label.setText(self._tr("url"))
        self.cookies_label.setText(self._tr("cookies"))
        self.type_label.setText(self._tr("video_format"))
        self.quality_label.setText(self._tr("quality"))
        self.audio_quality_label.setText(self._tr("audio_quality"))
        self.video_bitrate_label.setText(self._tr("video_bitrate"))
        self.video_bitrate_combo.setItemText(0, self._tr("auto"))

        self.url_edit.setPlaceholderText(self._tr("url_ph"))
        self.cookies_edit.setPlaceholderText(self._tr("cookies_ph"))
        self.output_dir_edit.setPlaceholderText(self._tr("output_ph"))
        self.output_name_label.setText(self._tr("output_name"))
        self.output_name_edit.setPlaceholderText(self._tr("output_name_ph"))

        self.cookies_browse_btn.setText(self._tr("browse"))
        self.output_browse_btn.setText(self._tr("browse"))
        self.start_btn.setText(self._tr("download_btn"))
        self.cancel_btn.setText(self._tr("cancel"))
        self.open_folder_btn.setText(self._tr("open_folder"))
        self.convert_format_label.setText(self._tr("convert_format"))
        self.convert_audio_quality_label.setText(self._tr("convert_quality"))
        self.convert_video_quality_label.setText(self._tr("quality"))
        self.convert_image_res_label.setText(self._tr("resolution"))
        self.convert_image_res_combo.setItemText(0, self._tr("original"))
        self.convert_start_btn.setText(self._tr("convert_start"))
        self.convert_cancel_btn.setText(self._tr("cancel"))
        self.convert_drop.title_label.setText(self._tr("drag_to_convert"))
        self.convert_output_dir_edit.setPlaceholderText(self._tr("output_ph"))
        self.convert_output_browse_btn.setText(self._tr("browse"))
        self.convert_output_open_btn.setText(self._tr("open_folder"))
        self.convert_output_name_label.setText(self._tr("output_name"))
        self.convert_output_name_edit.setPlaceholderText(self._tr("output_name_ph"))

        self.animations_checkbox.setText(self._tr("enable_animations"))
        self.animations_hint_label.setText(self._tr("animations_hint"))

        self.include_audio_checkbox.setText(self._tr("include_audio"))
        self.theme_label.setText(self._tr("theme"))

        self.sound_err_enable.setText(self._tr("sound_error"))
        self.sound_err_browse.setText(self._tr("browse"))
        self.sound_cookies_enable.setText(self._tr("sound_cookies"))
        self.sound_cookies_browse.setText(self._tr("browse"))
        self.sound_success_enable.setText(self._tr("sound_success"))
        self.sound_success_browse.setText(self._tr("browse"))
        self.open_after_download_checkbox.setText(self._tr("open_after_download"))
        self.keep_metadata_checkbox.setText(self._tr("keep_metadata"))
        self.about_github_btn.setText(self._tr("github_repo"))
        self.about_x_btn.setText(self._tr("x_link"))

        self._refresh_quality_combo()
        self._refresh_audio_quality_combo()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MediaDL")
    app.setOrganizationName("MediaDL")
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()
