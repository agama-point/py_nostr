from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
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


VER = "0.2 | 2026-06"
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


QR_ECC_CODEWORDS_PER_BLOCK_L = [
    0,
    7, 10, 15, 20, 26, 18, 20, 24, 30, 18,
    20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
    28, 28, 28, 30, 30, 26, 28, 30, 30, 30,
    30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
]
QR_NUM_ERROR_CORRECTION_BLOCKS_L = [
    0,
    1, 1, 1, 1, 1, 2, 2, 2, 2, 4,
    4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
    8, 9, 9, 10, 12, 12, 12, 13, 14, 15,
    16, 17, 18, 19, 19, 20, 21, 22, 24, 25,
]


def _qr_alignment_positions(version: int) -> list[int]:
    if version == 1:
        return []
    count = version // 7 + 2
    step = 26 if version == 32 else ((version * 4 + count * 2 + 1) // (count * 2 - 2)) * 2
    result = [6]
    position = version * 4 + 10
    for _ in range(count - 1):
        result.insert(1, position)
        position -= step
    return result


def _qr_raw_codewords(version: int) -> int:
    modules = (16 * version + 128) * version + 64
    if version >= 2:
        align_count = version // 7 + 2
        modules -= (25 * align_count - 10) * align_count - 55
    if version >= 7:
        modules -= 36
    return modules // 8


def _qr_data_codewords(version: int) -> int:
    return (
        _qr_raw_codewords(version)
        - QR_ECC_CODEWORDS_PER_BLOCK_L[version] * QR_NUM_ERROR_CORRECTION_BLOCKS_L[version]
    )


def _qr_bits_append(bits: list[int], value: int, length: int) -> None:
    for i in reversed(range(length)):
        bits.append((value >> i) & 1)


def _qr_gf_multiply(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _qr_reed_solomon_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        result.append(0)
        for j in range(degree):
            result[j] = _qr_gf_multiply(result[j], root)
            if j + 1 < len(result):
                result[j] ^= result[j + 1]
        root = _qr_gf_multiply(root, 0x02)
    return result[:degree]


def _qr_reed_solomon_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coeff in enumerate(divisor):
            result[i] ^= _qr_gf_multiply(coeff, factor)
    return result


def _qr_add_ecc(data: list[int], version: int) -> list[int]:
    raw_codewords = _qr_raw_codewords(version)
    block_count = QR_NUM_ERROR_CORRECTION_BLOCKS_L[version]
    ecc_len = QR_ECC_CODEWORDS_PER_BLOCK_L[version]
    short_block_len = raw_codewords // block_count
    short_data_len = short_block_len - ecc_len
    short_block_count = block_count - raw_codewords % block_count
    divisor = _qr_reed_solomon_divisor(ecc_len)

    blocks = []
    offset = 0
    for i in range(block_count):
        data_len = short_data_len + (0 if i < short_block_count else 1)
        block_data = data[offset : offset + data_len]
        offset += data_len
        pad = [None] if i < short_block_count else []
        blocks.append(block_data + pad + _qr_reed_solomon_remainder(block_data, divisor))

    result = []
    max_len = max(len(block) for block in blocks)
    for i in range(max_len):
        for block in blocks:
            if i < len(block) and block[i] is not None:
                result.append(block[i])
    return result


def _qr_format_bits(mask: int) -> int:
    data = (1 << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _qr_version_bits(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | rem


def make_qr_matrix(text: str) -> list[list[bool]]:
    data_bytes = text.encode("utf-8")
    version = 0
    for candidate in range(1, 41):
        count_bits = 8 if candidate <= 9 else 16
        if 4 + count_bits + len(data_bytes) * 8 <= _qr_data_codewords(candidate) * 8:
            version = candidate
            break
    if version == 0:
        raise ValueError("Message is too long for one QR code")

    bits: list[int] = []
    _qr_bits_append(bits, 0x4, 4)
    _qr_bits_append(bits, len(data_bytes), 8 if version <= 9 else 16)
    for byte in data_bytes:
        _qr_bits_append(bits, byte, 8)

    capacity_bits = _qr_data_codewords(version) * 8
    _qr_bits_append(bits, 0, min(4, capacity_bits - len(bits)))
    while len(bits) % 8:
        bits.append(0)

    data = []
    for i in range(0, len(bits), 8):
        data.append(int("".join(str(bit) for bit in bits[i : i + 8]), 2))
    pad = 0xEC
    while len(data) < _qr_data_codewords(version):
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    codewords = _qr_add_ecc(data, version)

    size = version * 4 + 17
    modules: list[list[bool | None]] = [[None] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def set_module(x: int, y: int, value: bool, is_function: bool = True) -> None:
        if 0 <= x < size and 0 <= y < size:
            modules[y][x] = value
            function[y][x] = is_function

    def draw_finder(cx: int, cy: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                dist = max(abs(dx), abs(dy))
                set_module(cx + dx, cy + dy, dist != 2 and dist != 4)

    def draw_alignment(cx: int, cy: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                set_module(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    draw_finder(3, 3)
    draw_finder(size - 4, 3)
    draw_finder(3, size - 4)
    for i in range(8, size - 8):
        set_module(6, i, i % 2 == 0)
        set_module(i, 6, i % 2 == 0)
    alignment_positions = _qr_alignment_positions(version)
    for y in alignment_positions:
        for x in alignment_positions:
            if modules[y][x] is None:
                draw_alignment(x, y)
    set_module(8, size - 8, True)

    for i in range(15):
        set_module(8, i if i < 6 else i + 1 if i < 8 else size - 15 + i, False)
        set_module(size - 1 - i if i < 8 else 15 - i, 8, False)
    if version >= 7:
        bits_version = _qr_version_bits(version)
        for i in range(18):
            bit = ((bits_version >> i) & 1) != 0
            set_module(size - 11 + i % 3, i // 3, bit)
            set_module(i // 3, size - 11 + i % 3, bit)

    bit_index = 0
    direction = -1
    x = size - 1
    while x > 0:
        if x == 6:
            x -= 1
        for y_offset in range(size):
            y = size - 1 - y_offset if direction == -1 else y_offset
            for dx in range(2):
                xx = x - dx
                if function[y][xx]:
                    continue
                bit = False
                if bit_index < len(codewords) * 8:
                    bit = ((codewords[bit_index >> 3] >> (7 - (bit_index & 7))) & 1) != 0
                    bit_index += 1
                if (xx + y) % 2 == 0:
                    bit = not bit
                set_module(xx, y, bit, False)
        direction *= -1
        x -= 2

    format_bits = _qr_format_bits(0)
    for i in range(15):
        bit = ((format_bits >> i) & 1) != 0
        set_module(8, i if i < 6 else i + 1 if i < 8 else size - 15 + i, bit)
        set_module(size - 1 - i if i < 8 else 15 - i, 8, bit)

    return [[cell is True for cell in row] for row in modules]


def qr_matrix_pixmap(matrix: list[list[bool]], scale: int, border: int = 4) -> QPixmap:
    size = len(matrix)
    image_size = (size + border * 2) * scale
    image = QImage(image_size, image_size, QImage.Format.Format_RGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.fillRect(0, 0, image_size, image_size, QColor("#ffffff"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#000000"))
    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if value:
                painter.drawRect((x + border) * scale, (y + border) * scale, scale, scale)
    painter.end()
    return QPixmap.fromImage(image)


def qr_pixmap(text: str, scale: int | None = None, border: int = 4) -> QPixmap:
    matrix = make_qr_matrix(text)
    size = len(matrix)
    if scale is None:
        scale = max(3, min(8, 900 // (size + border * 2)))
    return qr_matrix_pixmap(matrix, scale, border)


class QrCodeWindow(QWidget):
    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message
        self._matrix = make_qr_matrix(message)
        self._border = 4
        self._last_scale = 0

        self.setWindowTitle("Message QR")
        self.setMinimumSize(300, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(240, 240)
        layout.addWidget(self.image_label, stretch=1)

        self.text_box = QTextEdit()
        self.text_box.setReadOnly(True)
        self.text_box.setPlainText(message)
        self.text_box.setMaximumHeight(110)
        layout.addWidget(self.text_box)

        initial_size = min(900, max(360, (len(self._matrix) + self._border * 2) * 6 + 36))
        self.resize(initial_size, initial_size + 150)
        self._refresh_qr()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_qr()

    def _refresh_qr(self) -> None:
        available = min(self.image_label.width(), self.image_label.height())
        modules = len(self._matrix) + self._border * 2
        scale = max(1, available // modules)
        if scale == self._last_scale and self.image_label.pixmap() is not None:
            return
        self._last_scale = scale
        self.image_label.setPixmap(qr_matrix_pixmap(self._matrix, scale, self._border))


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
        self._qr_windows: list[QWidget] = []
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
        hint_label = QLabel("| right_click: Show QR / Copy / Delete message")
        hint_label.setObjectName("Muted")
        footer_layout.addWidget(hint_label)
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
        show_qr_action = menu.addAction("Show QR")
        copy_action = menu.addAction("Copy message")
        delete_action = menu.addAction("Delete message")
        selected = menu.exec(self.messages_table.viewport().mapToGlobal(position))
        if selected == show_qr_action:
            self.show_message_qr(str(row_data.get("content") or ""))
            return
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

    def show_message_qr(self, message: str) -> None:
        if not message:
            self.append_debug("QR skipped: message is empty", "warn")
            return
        try:
            window = QrCodeWindow(message)
        except ValueError as exc:
            self.append_debug(f"QR skipped: {exc}", "warn")
            return

        window.show()
        self._qr_windows.append(window)

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
