from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from pprint import pformat
from typing import Any

import tornado.ioloop
from tornado import gen
from tornado.websocket import websocket_connect

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from agama_nostr import nip17
from agama_nostr.relays import relays_list
from agama_nostr.tools import (
    get_nostr_key,
    get_relay_information,
    load_env_file,
    normalize_nostr_private_key,
)
from pynostr.base_relay import RelayPolicy
from pynostr.event import Event
from pynostr.filters import Filters, FiltersList
from pynostr.key import PrivateKey, PublicKey
from pynostr.message_type import RelayMessageType
from pynostr.relay import Relay


SETUP_PATH = Path("setup.json")
DEFAULT_SETUP = {
    "key_env": "NOSTR_KEY",
    "relay": relays_list[0] if relays_list else "",
    "recipient": "NOSTR_PUB2",
    "custom_recipient": "",
    "stream_channel": "Public notes",
    "verbose": True,
}

STREAM_CHANNELS = {
    "Public notes": {"kinds": [1], "authors": None, "pubkey_refs": None},
    "My notes": {"kinds": [1], "authors": "self", "pubkey_refs": None},
    "Reposts": {"kinds": [6], "authors": None, "pubkey_refs": None},
    "Reactions": {"kinds": [7], "authors": None, "pubkey_refs": None},
    "Gift wraps for me": {"kinds": [nip17.KIND_GIFT_WRAP], "authors": None, "pubkey_refs": "self"},
}


def utc_time(timestamp: int | None) -> str:
    if not timestamp:
        return "?"
    return datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S UTC")


def short(value: str, left: int = 12, right: int = 8) -> str:
    value = str(value)
    if len(value) <= left + right + 3:
        return value
    return f"{value[:left]}...{value[-right:]}"


def load_setup() -> dict[str, Any]:
    if not SETUP_PATH.exists():
        save_setup(DEFAULT_SETUP)
        return dict(DEFAULT_SETUP)
    try:
        data = json.loads(SETUP_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    setup = dict(DEFAULT_SETUP)
    setup.update({key: value for key, value in data.items() if key in setup})
    return setup


def save_setup(setup: dict[str, Any]) -> None:
    SETUP_PATH.write_text(json.dumps(setup, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_public_key(value: str) -> PublicKey:
    value = str(value).strip()
    if value.startswith("npub1"):
        return PublicKey.from_npub(value)
    if len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        return PublicKey.from_hex(value.lower())
    raise ValueError("Recipient must be npub1... or a 64-character hex public key")


def env_keys(prefix: str) -> list[str]:
    load_env_file()
    keys = sorted(name for name in os.environ if name.startswith(prefix))
    preferred = [prefix] if prefix in keys else []
    return preferred + [name for name in keys if name not in preferred]


class NostrWorker(QObject):
    log_signal = pyqtSignal(str, str)
    status_signal = pyqtSignal(str)
    config_signal = pyqtSignal(dict)
    keys_signal = pyqtSignal(list)
    relays_signal = pyqtSignal(list)
    recipients_signal = pyqtSignal(list)
    stream_state_signal = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.config = load_setup()
        self.debug_enabled = bool(self.config.get("verbose", True))
        self._stream_stop = threading.Event()
        self._stream_thread: threading.Thread | None = None
        self._receive_stop = threading.Event()
        self._receive_thread: threading.Thread | None = None

    @pyqtSlot()
    def initialize(self) -> None:
        load_env_file()
        self.config_signal.emit(dict(self.config))
        self.keys_signal.emit(env_keys("NOSTR_KEY"))
        self.recipients_signal.emit(env_keys("NOSTR_PUB"))
        self.relays_signal.emit(relays_list[:3])
        self.status_signal.emit("Ready")
        self.log("Nostr Qt app initialized.")
        self.log(f"Setup file: {SETUP_PATH.resolve()}", "muted")

    @pyqtSlot(bool)
    def set_debug(self, enabled: bool) -> None:
        self.debug_enabled = enabled
        self.config["verbose"] = bool(enabled)
        save_setup(self.config)

    @pyqtSlot(dict)
    def update_config(self, patch: dict) -> None:
        self.config.update(patch)
        save_setup(self.config)

    @pyqtSlot()
    def shutdown(self) -> None:
        self.stop_stream()
        self.stop_receive()

    @pyqtSlot(str, object)
    def run_action(self, action: str, payload: object = None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        try:
            if action == "key_info":
                self.key_info(str(payload.get("key_env") or self.config["key_env"]))
            elif action == "relay_info":
                self.relay_info(str(payload.get("relay") or self.config["relay"]))
            elif action == "send_message":
                self.send_message(payload)
            elif action == "receive_messages":
                self.start_receive(payload)
            elif action == "start_stream":
                self.start_stream(payload)
            elif action == "stop_stream":
                self.stop_stream()
            elif action == "clear_setup":
                self.config = dict(DEFAULT_SETUP)
                save_setup(self.config)
                self.config_signal.emit(dict(self.config))
                self.log("Setup reset to defaults.")
            else:
                self.log(f"Unknown action: {action}", "warn")
        except Exception as exc:
            self.status_signal.emit("Error")
            self.log(f"{type(exc).__name__}: {exc}", "error")

    def log(self, text: str, level: str = "info") -> None:
        if level == "debug" and not self.debug_enabled:
            return
        self.log_signal.emit(str(text), level)

    def private_key_from_env(self, key_env: str | None = None) -> PrivateKey:
        raw = get_nostr_key(key_env or str(self.config["key_env"]))
        return PrivateKey.from_hex(normalize_nostr_private_key(raw))

    def key_info(self, key_env: str) -> None:
        self.status_signal.emit("Reading key")
        key = self.private_key_from_env(key_env)
        self.log_section("Key info")
        self.log(f"env:  {key_env}")
        self.log(f"npub: {key.public_key.bech32()}")
        self.log(f"hex:  {key.public_key.hex()}")
        self.log("Private key is loaded but never printed by the app.", "muted")
        self.status_signal.emit("Key loaded")

    def relay_info(self, relay_url: str) -> None:
        self.status_signal.emit("Testing relay")
        self.log_section("Relay info")
        self.log(f"url: {relay_url}")
        metadata = get_relay_information(relay_url, timeout=4)
        if metadata:
            self.log("NIP-11 metadata:")
            self.log(pformat(metadata, width=90, sort_dicts=True))
        else:
            self.log("No NIP-11 metadata returned.", "warn")

        ok, detail = self.probe_relay(relay_url)
        self.log(f"websocket: {detail}", "info" if ok else "warn")
        self.status_signal.emit("Relay OK" if ok else "Relay issue")

    def probe_relay(self, relay_url: str, timeout: int = 6) -> tuple[bool, str]:
        loop = tornado.ioloop.IOLoop()
        try:
            ws = loop.run_sync(
                lambda: gen.with_timeout(loop.time() + timeout, websocket_connect(relay_url)),
                timeout=timeout + 1,
            )
            ws.close()
            return True, "websocket OK"
        except Exception as exc:
            return False, repr(exc)
        finally:
            loop.stop()
            loop.close(all_fds=True)

    def send_message(self, payload: dict[str, Any]) -> None:
        relay_url = str(payload.get("relay") or self.config["relay"])
        recipient_value = str(payload.get("recipient_value") or "").strip()
        recipient_env = str(payload.get("recipient_env") or self.config.get("recipient") or "").strip()
        if recipient_value.startswith("NOSTR_PUB"):
            recipient_env = recipient_value
            recipient_value = ""
        if not recipient_value and recipient_env:
            recipient_value = get_nostr_key(recipient_env)
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("Message is empty")
        if not recipient_value:
            raise ValueError("Recipient is empty")

        sender_key = self.private_key_from_env(str(payload.get("key_env") or self.config["key_env"]))
        recipient = parse_public_key(recipient_value)
        self.status_signal.emit("Sending message")
        self.log_section("Send NIP-17 message")
        self.log(f"relay:     {relay_url}")
        self.log(f"recipient: {recipient.bech32()}")
        self.log(f"message bytes: {len(message.encode('utf-8'))}")

        rumor, seal, wrap = nip17.make_gift_wrap(
            sender_key,
            recipient.hex(),
            message,
            relay_url=relay_url,
        )
        _, sender_seal, sender_wrap = nip17.make_sender_copy(
            sender_key,
            recipient.hex(),
            message,
            relay_url=relay_url,
            rumor=rumor,
        )
        self.log(f"rumor id:       {rumor['id']}", "debug")
        self.log(f"recipient wrap: {wrap.id}")
        self.log(f"sender copy:    {sender_wrap.id}")
        statuses = self.publish_events(relay_url, [wrap, sender_wrap])
        self.log(pformat(statuses, width=90, sort_dicts=True))
        ok_count = sum(1 for status in statuses.values() if status.get("ok") is True)
        self.status_signal.emit(f"Sent {ok_count}/{len(statuses)}")

    def publish_events(self, relay_url: str, events: list[Event]) -> dict[str, dict[str, Any]]:
        loop = tornado.ioloop.IOLoop()
        policy = RelayPolicy()
        statuses = {event.id: {"ok": None, "detail": "", "sent": False} for event in events}

        def on_message(message_json):
            self.log(f"[PUBLISH RAW] {message_json}", "debug")
            if message_json[0] == RelayMessageType.OK:
                event_id = message_json[1]
                if event_id in statuses:
                    statuses[event_id]["ok"] = bool(message_json[2])
                    statuses[event_id]["detail"] = str(message_json[3])
                    if all(status["ok"] is not None for status in statuses.values()):
                        loop.add_callback(relay.close)
            elif message_json[0] == RelayMessageType.NOTICE:
                self.log(f"[PUBLISH NOTICE] {message_json}", "warn")

        relay = Relay(
            relay_url,
            message_pool=None,
            io_loop=loop,
            policy=policy,
            timeout=5,
            close_on_eose=True,
            message_callback=on_message,
        )
        for event in events:
            relay.publish(event.to_message())

        try:
            loop.run_sync(relay.connect, timeout=12)
        except gen.TimeoutError:
            self.log("Publish timeout after 12s.", "warn")
        finally:
            for event in events:
                statuses[event.id]["sent"] = relay.num_sent_events > 0
            try:
                if relay.is_connected:
                    loop.run_sync(relay.close, timeout=2)
            except Exception as exc:
                self.log(f"Relay close warning: {exc!r}", "warn")
            loop.stop()
            loop.close(all_fds=True)
        return statuses

    def start_receive(self, payload: dict[str, Any]) -> None:
        if self._receive_thread and self._receive_thread.is_alive():
            self.log("Receive is already running.", "warn")
            return
        self._receive_stop.clear()
        self._receive_thread = threading.Thread(
            target=self.receive_loop,
            args=(dict(payload),),
            daemon=True,
        )
        self._receive_thread.start()

    def stop_receive(self) -> None:
        self._receive_stop.set()

    def receive_loop(self, payload: dict[str, Any]) -> None:
        relay_url = str(payload.get("relay") or self.config["relay"])
        key = self.private_key_from_env(str(payload.get("key_env") or self.config["key_env"]))
        my_pub_hex = key.public_key.hex()
        self.log_section("Receive NIP-17 messages")
        self.log(f"relay: {relay_url}")
        self.log(f"npub:  {key.public_key.bech32()}")
        self.status_signal.emit("Receiving")
        self.read_events(
            relay_url,
            FiltersList([Filters(kinds=[nip17.KIND_GIFT_WRAP], pubkey_refs=[my_pub_hex])]),
            close_on_eose=True,
            stop_event=self._receive_stop,
            decrypt_key=key,
            subscription_prefix="dm-receive",
        )
        self.status_signal.emit("Receive stopped")

    def start_stream(self, payload: dict[str, Any]) -> None:
        if self._stream_thread and self._stream_thread.is_alive():
            self.log("Stream is already running.", "warn")
            return
        self._stream_stop.clear()
        self._stream_thread = threading.Thread(
            target=self.stream_loop,
            args=(dict(payload),),
            daemon=True,
        )
        self._stream_thread.start()
        self.stream_state_signal.emit(True)

    def stop_stream(self) -> None:
        self._stream_stop.set()
        self.stream_state_signal.emit(False)

    def stream_loop(self, payload: dict[str, Any]) -> None:
        relay_url = str(payload.get("relay") or self.config["relay"])
        channel = str(payload.get("channel") or self.config["stream_channel"])
        key = self.private_key_from_env(str(payload.get("key_env") or self.config["key_env"]))
        channel_def = STREAM_CHANNELS.get(channel, STREAM_CHANNELS["Public notes"])
        filters_kwargs = {"kinds": channel_def["kinds"]}
        if channel_def["authors"] == "self":
            filters_kwargs["authors"] = [key.public_key.hex()]
        if channel_def["pubkey_refs"] == "self":
            filters_kwargs["pubkey_refs"] = [key.public_key.hex()]
        filters = FiltersList([Filters(**filters_kwargs)])

        self.log_section("Event stream")
        self.log(f"relay:   {relay_url}")
        self.log(f"channel: {channel}")
        self.log(f"filter:  {filters}")
        self.status_signal.emit("Streaming")
        self.read_events(
            relay_url,
            filters,
            close_on_eose=False,
            stop_event=self._stream_stop,
            decrypt_key=key if channel == "Gift wraps for me" else None,
            subscription_prefix="stream",
        )
        self.status_signal.emit("Stream stopped")
        self.stream_state_signal.emit(False)

    def read_events(
        self,
        relay_url: str,
        filters: FiltersList,
        close_on_eose: bool,
        stop_event: threading.Event,
        decrypt_key: PrivateKey | None,
        subscription_prefix: str,
    ) -> None:
        loop = tornado.ioloop.IOLoop()
        policy = RelayPolicy()
        seen: set[str] = set()
        subscription_id = f"{subscription_prefix}-{uuid.uuid4().hex}"
        relay: Relay | None = None

        def on_message(message_json):
            self.log(f"[RELAY RAW] {message_json}", "debug")
            message_type = message_json[0]
            if message_type == RelayMessageType.END_OF_STORED_EVENTS:
                self.log("[EOSE] relay caught up.")
                return
            if message_type == RelayMessageType.NOTICE:
                self.log(f"[NOTICE] {message_json[1]}", "warn")
                return
            if message_type != RelayMessageType.EVENT:
                return
            event = Event.from_dict(message_json[2])
            if event.id in seen:
                return
            seen.add(event.id)
            self.log_event(event, decrypt_key)

        def poll_stop():
            if stop_event.is_set() and relay is not None and relay.is_connected:
                loop.add_callback(relay.close)
            elif not stop_event.is_set():
                loop.call_later(0.25, poll_stop)

        relay = Relay(
            relay_url,
            message_pool=None,
            io_loop=loop,
            policy=policy,
            timeout=5,
            close_on_eose=close_on_eose,
            message_callback=on_message,
        )
        relay.add_subscription(subscription_id, filters)
        loop.call_later(0.25, poll_stop)
        try:
            loop.run_sync(relay.connect)
        except Exception as exc:
            self.log(f"Relay loop stopped: {exc!r}", "warn")
        finally:
            try:
                if relay.is_connected:
                    loop.run_sync(relay.close, timeout=2)
            except Exception as exc:
                self.log(f"Relay close warning: {exc!r}", "warn")
            loop.stop()
            loop.close(all_fds=True)

    def log_event(self, event: Event, decrypt_key: PrivateKey | None = None) -> None:
        self.log("")
        self.log("-" * 80)
        self.log(f"kind: {event.kind} | date: {utc_time(event.created_at)} | id: {short(event.id)}")
        self.log(f"author: {short(event.pubkey or '')}")
        if event.tags:
            self.log("tags: " + json.dumps(event.tags[:12], ensure_ascii=False))
        if decrypt_key and event.kind == nip17.KIND_GIFT_WRAP:
            try:
                seal, rumor = nip17.unwrap_gift_wrap(decrypt_key, event)
                self.log(f"seal sender: {seal.pubkey}")
                self.log(f"dm clear:    {rumor.get('content')}")
                return
            except Exception as exc:
                self.log(f"decrypt error: {exc!r}", "error")
        if event.content:
            self.log(event.content[:2000])

    def log_section(self, title: str) -> None:
        self.log("")
        self.log("=" * 80)
        self.log(title)
        self.log("=" * 80)
