from __future__ import annotations

import sys

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from nostr_ui import MainWindow, NostrWorker


def main() -> int:
    app = QApplication(sys.argv)

    worker_thread = QThread()
    worker = NostrWorker()
    worker.moveToThread(worker_thread)

    window = MainWindow()
    window.action_requested.connect(worker.run_action)
    window.config_changed.connect(worker.update_config)
    window.debug_changed.connect(worker.set_debug)
    worker.log_signal.connect(window.append_debug)
    worker.status_signal.connect(window.set_status)
    worker.config_signal.connect(window.apply_config)
    worker.keys_signal.connect(window.set_key_options)
    worker.relays_signal.connect(window.set_relay_options)
    worker.recipients_signal.connect(window.set_recipient_options)
    worker.stream_state_signal.connect(window.set_stream_state)
    worker_thread.started.connect(worker.initialize)

    app.aboutToQuit.connect(worker.shutdown)
    app.aboutToQuit.connect(worker_thread.quit)
    worker_thread.finished.connect(worker.deleteLater)

    worker_thread.start()
    window.show()
    exit_code = app.exec()
    worker_thread.wait(3000)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
