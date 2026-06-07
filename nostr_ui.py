from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nostr_worker import NostrWorker


VER = "0.1 | 2026-06"
STREAM_CHANNELS = [
    "Public notes",
    "My notes",
    "Reposts",
    "Reactions",
    "Gift wraps for me",
]


def compact(value: Any, left: int = 18, right: int = 8) -> str:
    text = "" if value is None else str(value)
    if len(text) <= left + right + 3:
        return text
    return f"{text[:left]}...{text[-right:]}"


class MainWindow(QWidget):
    action_requested = pyqtSignal(str, object)
    config_changed = pyqtSignal(dict)
    debug_changed = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("py_nostr | Nostr Qt Demo")
        self.resize(1260, 780)
        self.setMinimumSize(980, 620)
        self._config: dict[str, Any] = {}
        self._message_rows: list[dict[str, Any]] = []
        self._build_ui()
        self._apply_theme()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([430, 830])
        root.addWidget(splitter, stretch=1)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("Agama Nostr App")
        title.setObjectName("AppTitle")
        title_row.addWidget(title)
        version = QLabel(f"ver. {VER}")
        version.setObjectName("Version")
        title_row.addWidget(version)
        title_row.addStretch()
        layout.addLayout(title_row)

        layout.addWidget(self._build_keys_box())
        layout.addWidget(self._build_relays_box())
        layout.addWidget(self._build_message_box())
        layout.addWidget(self._build_stream_box())
        layout.addStretch()
        return panel

    def _build_keys_box(self) -> QGroupBox:
        box = QGroupBox("Keys")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.key_combo = QComboBox()
        self.key_combo.currentTextChanged.connect(lambda value: self._set_config("key_env", value))
        row.addWidget(self.key_combo, stretch=1)
        info_btn = QPushButton("Info")
        info_btn.clicked.connect(self._key_info)
        row.addWidget(info_btn)
        layout.addLayout(row)
        self.key_hint = QLabel("Private keys are loaded from .env and never printed.")
        self.key_hint.setWordWrap(True)
        self.key_hint.setObjectName("Muted")
        layout.addWidget(self.key_hint)
        return box

    def _build_relays_box(self) -> QGroupBox:
        box = QGroupBox("Relays")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.relay_combo = QComboBox()
        self.relay_combo.setEditable(True)
        self.relay_combo.currentTextChanged.connect(lambda value: self._set_config("relay", value))
        row.addWidget(self.relay_combo, stretch=1)
        info_btn = QPushButton("Info")
        info_btn.clicked.connect(self._relay_info)
        row.addWidget(info_btn)
        layout.addLayout(row)
        self.relay_hint = QLabel("First three configured relays are preloaded; custom wss:// is allowed.")
        self.relay_hint.setWordWrap(True)
        self.relay_hint.setObjectName("Muted")
        layout.addWidget(self.relay_hint)
        return box

    def _build_message_box(self) -> QGroupBox:
        box = QGroupBox("Message")
        layout = QVBoxLayout(box)
        self.recipient_combo = QComboBox()
        self.recipient_combo.currentTextChanged.connect(lambda value: self._set_config("recipient", value))
        layout.addWidget(self.recipient_combo)

        self.custom_recipient_input = QLineEdit()
        self.custom_recipient_input.setPlaceholderText("Custom recipient npub1... or hex")
        self.custom_recipient_input.textChanged.connect(
            lambda value: self._set_config("custom_recipient", value)
        )
        layout.addWidget(self.custom_recipient_input)

        self.message_input = QTextEdit()
        self.message_input.setPlaceholderText("Message content")
        self.message_input.setMinimumHeight(120)
        layout.addWidget(self.message_input)

        row = QHBoxLayout()
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send_message)
        row.addWidget(send_btn)
        receive_btn = QPushButton("Receive")
        receive_btn.clicked.connect(self._receive_messages)
        row.addWidget(receive_btn)
        row.addStretch()
        layout.addLayout(row)
        return box

    def _build_stream_box(self) -> QGroupBox:
        box = QGroupBox("Events / Stream")
        layout = QVBoxLayout(box)
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(STREAM_CHANNELS)
        self.channel_combo.currentTextChanged.connect(lambda value: self._set_config("stream_channel", value))
        layout.addWidget(self.channel_combo)

        row = QHBoxLayout()
        self.start_stream_button = QPushButton("Start")
        self.start_stream_button.clicked.connect(self._start_stream)
        row.addWidget(self.start_stream_button)
        self.stop_stream_button = QPushButton("Stop")
        self.stop_stream_button.clicked.connect(lambda: self.action_requested.emit("stop_stream", {}))
        self.stop_stream_button.setEnabled(False)
        row.addWidget(self.stop_stream_button)
        row.addStretch()
        layout.addLayout(row)

        reset_btn = QPushButton("Reset setup")
        reset_btn.setProperty("dangerButton", True)
        reset_btn.clicked.connect(lambda: self.action_requested.emit("clear_setup", {}))
        layout.addWidget(reset_btn)
        return box

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setHandleWidth(8)

        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Verbose logs / debug")
        title.setObjectName("Title")
        top.addWidget(title)
        top.addStretch()
        self.status_label = QLabel("Starting")
        self.status_label.setObjectName("Status")
        top.addWidget(self.status_label)
        self.debug_checkbox = QCheckBox("Verbose")
        self.debug_checkbox.setChecked(True)
        self.debug_checkbox.toggled.connect(self.debug_changed.emit)
        top.addWidget(self.debug_checkbox)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_debug)
        top.addWidget(save_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear_debug)
        top.addWidget(clear_btn)
        log_layout.addLayout(top)

        self.debug_box = QTextBrowser()
        self.debug_box.setObjectName("VerboseLog")
        log_layout.addWidget(self.debug_box, stretch=1)
        right_splitter.addWidget(log_panel)

        message_panel = QWidget()
        message_layout = QVBoxLayout(message_panel)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(8)
        messages_title = QLabel("Messages SQLite")
        messages_title.setObjectName("Title")
        message_layout.addWidget(messages_title)
        self.messages_table = QTableWidget(0, 6)
        self.messages_table.setObjectName("MessagesTable")
        self.messages_table.setHorizontalHeaderLabels(["S_R", "datetime", "message", "uid", "event", "relay"])
        self.messages_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.messages_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.messages_table.setAlternatingRowColors(True)
        self.messages_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.messages_table.customContextMenuRequested.connect(self._show_messages_menu)
        self.messages_table.verticalHeader().setVisible(False)
        header = self.messages_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        message_layout.addWidget(self.messages_table, stretch=1)
        right_splitter.addWidget(message_panel)
        right_splitter.setSizes([390, 330])
        layout.addWidget(right_splitter, stretch=1)

        footer = QFrame()
        footer.setObjectName("Footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_label = QLabel("setup.json")
        self.setup_label.setObjectName("Muted")
        footer_layout.addWidget(self.setup_label)
        footer_layout.addStretch()
        layout.addWidget(footer)
        return panel

    def apply_config(self, config: dict) -> None:
        self._config = dict(config)
        self._select_combo_text(self.key_combo, str(config.get("key_env", "")))
        self._select_combo_text(self.relay_combo, str(config.get("relay", "")))
        self._select_combo_text(self.recipient_combo, str(config.get("recipient", "")))
        self._select_combo_text(self.channel_combo, str(config.get("stream_channel", "")))
        self.custom_recipient_input.setText(str(config.get("custom_recipient", "")))
        self.debug_checkbox.setChecked(bool(config.get("verbose", True)))

    def set_key_options(self, keys: list) -> None:
        current = self.key_combo.currentText()
        self.key_combo.blockSignals(True)
        self.key_combo.clear()
        self.key_combo.addItems([str(key) for key in keys])
        self.key_combo.blockSignals(False)
        self._select_combo_text(self.key_combo, self._config.get("key_env") or current)

    def set_relay_options(self, relays: list) -> None:
        current = self.relay_combo.currentText()
        self.relay_combo.blockSignals(True)
        self.relay_combo.clear()
        self.relay_combo.addItems([str(relay) for relay in relays])
        self.relay_combo.blockSignals(False)
        self._select_combo_text(self.relay_combo, self._config.get("relay") or current)

    def set_recipient_options(self, recipients: list) -> None:
        current = self.recipient_combo.currentText()
        self.recipient_combo.blockSignals(True)
        self.recipient_combo.clear()
        self.recipient_combo.addItems([str(recipient) for recipient in recipients])
        self.recipient_combo.blockSignals(False)
        self._select_combo_text(self.recipient_combo, self._config.get("recipient") or current)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_stream_state(self, running: bool) -> None:
        self.start_stream_button.setEnabled(not running)
        self.stop_stream_button.setEnabled(running)

    def set_messages(self, rows: list) -> None:
        self._message_rows = [dict(row) for row in rows]
        self.messages_table.setRowCount(0)
        send_bg = QColor("#173820")
        receive_bg = QColor("#3a2d18")
        tooltip_keys = ["direction", "datetime_text", "content", "uid", "event_id", "relay"]
        for row_data in self._message_rows:
            row = self.messages_table.rowCount()
            self.messages_table.insertRow(row)
            direction = str(row_data.get("direction") or "")
            values = [
                direction,
                str(row_data.get("datetime_text") or ""),
                compact(row_data.get("content"), 90, 24),
                compact(row_data.get("uid"), 12, 6),
                compact(row_data.get("event_id") or row_data.get("wrap_id"), 12, 6),
                compact(row_data.get("relay"), 18, 12),
            ]
            bg = send_bg if direction == "S" else receive_bg
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(row_data.get(tooltip_keys[column]) or value))
                if column == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setBackground(bg)
                self.messages_table.setItem(row, column, item)

    def _show_messages_menu(self, position) -> None:
        item = self.messages_table.itemAt(position)
        if item is None:
            return
        row = item.row()
        if row < 0 or row >= len(self._message_rows):
            return
        self.messages_table.selectRow(row)
        row_data = self._message_rows[row]
        menu = QMenu(self)
        copy_action = menu.addAction("copy msg")
        delete_action = menu.addAction("delete")
        selected = menu.exec(self.messages_table.viewport().mapToGlobal(position))
        if selected == copy_action:
            QApplication.clipboard().setText(str(row_data.get("content") or ""))
            return
        if selected == delete_action:
            self.action_requested.emit(
                "delete_message",
                {
                    "peer_npub": str(row_data.get("peer_npub") or ""),
                    "uid": str(row_data.get("uid") or ""),
                },
            )

    def append_debug(self, text: str, level: str = "info") -> None:
        colors = {
            "info": "#39ff72",
            "debug": "#85c7ff",
            "muted": "#8b96a3",
            "warn": "#ffd166",
            "error": "#ff6b6b",
        }
        color = colors.get(level, colors["info"])
        self.debug_box.append(f'<pre style="color: {color};">{html.escape(text)}</pre>')
        self.debug_box.verticalScrollBar().setValue(self.debug_box.verticalScrollBar().maximum())

    def clear_debug(self) -> None:
        self.debug_box.clear()

    def save_debug(self) -> None:
        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)
        filename = datetime.now().strftime("log_%y%m%d_%H_%M.txt")
        path = data_dir / filename
        path.write_text(self.debug_box.toPlainText(), encoding="utf-8")
        self.append_debug(f"log saved: {path}", "muted")

    def _key_info(self) -> None:
        self.action_requested.emit("key_info", {"key_env": self.key_combo.currentText()})

    def _relay_info(self) -> None:
        self.action_requested.emit("relay_info", {"relay": self.relay_combo.currentText()})

    def _send_message(self) -> None:
        self.action_requested.emit(
            "send_message",
            {
                "key_env": self.key_combo.currentText(),
                "relay": self.relay_combo.currentText(),
                "recipient_env": self.recipient_combo.currentText(),
                "recipient_value": self._recipient_value(),
                "message": self.message_input.toPlainText(),
            },
        )

    def _receive_messages(self) -> None:
        self.action_requested.emit(
            "receive_messages",
            {
                "key_env": self.key_combo.currentText(),
                "relay": self.relay_combo.currentText(),
            },
        )

    def _start_stream(self) -> None:
        self.action_requested.emit(
            "start_stream",
            {
                "key_env": self.key_combo.currentText(),
                "relay": self.relay_combo.currentText(),
                "channel": self.channel_combo.currentText(),
            },
        )

    def _recipient_value(self) -> str:
        custom = self.custom_recipient_input.text().strip()
        if custom:
            return custom
        return ""

    def _set_config(self, key: str, value: str) -> None:
        self._config[key] = value
        self.config_changed.emit({key: value})

    def _select_combo_text(self, combo: QComboBox, value: str) -> None:
        if not value:
            return
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)
            return
        if combo.isEditable():
            combo.setCurrentText(value)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #121417;
                color: #e8e8e8;
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 11pt;
            }
            QLabel#Title {
                font-size: 15pt;
                font-weight: 600;
                color: #ffffff;
            }
            QLabel#AppTitle {
                font-size: 15pt;
                font-weight: 700;
                color: #b56cff;
            }
            QLabel#Version {
                color: #7a828c;
                font-size: 9pt;
                padding-top: 5px;
            }
            QLabel#Status {
                color: #9ad1ff;
                padding: 4px 0;
            }
            QLabel#Muted {
                color: #8b96a3;
            }
            QFrame#Footer {
                background: #121417;
            }
            QGroupBox {
                border: 1px solid #333941;
                border-radius: 6px;
                margin-top: 10px;
                padding: 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                background: #26313d;
                border: 1px solid #3d4a57;
                border-radius: 5px;
                padding: 8px 10px;
                text-align: center;
            }
            QPushButton:hover {
                background: #314153;
            }
            QPushButton:pressed {
                background: #1d2732;
            }
            QPushButton:disabled {
                color: #626b75;
                background: #1a1f25;
                border-color: #2c333a;
            }
            QPushButton[dangerButton="true"] {
                background: #3a2428;
                border-color: #6b343d;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid #58616b;
                border-radius: 3px;
                background: #1b2026;
            }
            QCheckBox::indicator:checked {
                border: 3px solid #58616b;
                background: #39ff14;
            }
            QLineEdit, QComboBox, QTextBrowser, QTextEdit, QTableWidget {
                background: #0f1114;
                border: 1px solid #333941;
                border-radius: 5px;
                padding: 6px;
                color: #e8e8e8;
            }
            QTextBrowser#VerboseLog {
                background: #070b08;
                border-color: #2f5f3b;
                color: #39ff72;
                font-family: Consolas, Cascadia Mono, monospace;
                font-size: 9pt;
            }
            QTableWidget#MessagesTable {
                background: #0d1013;
                alternate-background-color: #121820;
                gridline-color: #2d343c;
                font-family: Consolas, Cascadia Mono, monospace;
                font-size: 9pt;
            }
            QHeaderView::section {
                background: #202833;
                color: #d7e0ea;
                border: 0;
                border-right: 1px solid #343d47;
                padding: 5px 6px;
                font-weight: 600;
            }
            QSplitter::handle {
                background: #262b31;
            }
            """
        )
