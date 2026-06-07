from __future__ import annotations

import json
import os
import sqlite3
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
DATA_DIR = Path("data")
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


def compact_time(timestamp: int | None) -> str:
    if not timestamp:
        return "?"
    return datetime.fromtimestamp(timestamp).strftime("%y%m%d|%H:%M")


def one_line(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def short(value: str, left: int = 12, right: int = 8) -> str:
    value = str(value)
    if len(value) <= left + right + 3:
        return value
    return f"{value[:left]}...{value[-right:]}"


def peer_db_name(public_key: PublicKey) -> str:
    npub = public_key.bech32()
    return f"{npub[:15]}_{npub[-6:]}.sqlite"


def peer_db_path(public_key: PublicKey) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / peer_db_name(public_key)


def public_key_from_hex(value: str) -> PublicKey:
    return PublicKey.from_hex(str(value).lower())


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
    messages_signal = pyqtSignal(list)

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
        self.refresh_messages_for_config()
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
        if {"recipient", "custom_recipient"} & set(patch):
            self.refresh_messages_for_config()

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
            elif action == "delete_message":
                self.delete_message(payload)
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

    def resolve_recipient_value(self, recipient_env: str | None = None, recipient_value: str | None = None) -> str:
        value = str(recipient_value or "").strip()
        env_name = str(recipient_env or self.config.get("recipient") or "").strip()
        if value.startswith("NOSTR_PUB"):
            env_name = value
            value = ""
        if not value and env_name:
            value = get_nostr_key(env_name)
        return value

    def refresh_messages_for_config(self) -> None:
        try:
            value = self.resolve_recipient_value(
                str(self.config.get("recipient") or ""),
                str(self.config.get("custom_recipient") or ""),
            )
            if not value:
                self.messages_signal.emit([])
                return
            self.emit_messages(parse_public_key(value))
        except Exception:
            self.messages_signal.emit([])

    def ensure_message_db(self, peer: PublicKey) -> Path:
        db_path = peer_db_path(peer)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT NOT NULL UNIQUE,
                    direction TEXT NOT NULL CHECK(direction IN ('S', 'R')),
                    created_at INTEGER,
                    datetime_text TEXT,
                    content TEXT NOT NULL,
                    peer_npub TEXT NOT NULL,
                    peer_hex TEXT NOT NULL,
                    sender_hex TEXT,
                    recipient_hex TEXT,
                    rumor_id TEXT,
                    event_id TEXT,
                    wrap_id TEXT,
                    sender_wrap_id TEXT,
                    seal_id TEXT,
                    relay TEXT,
                    inserted_at INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_uid ON messages(uid)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at DESC)")
        return db_path

    def store_message(
        self,
        peer: PublicKey,
        direction: str,
        content: str,
        created_at: int | None,
        relay_url: str,
        sender_hex: str | None = None,
        recipient_hex: str | None = None,
        rumor_id: str | None = None,
        event_id: str | None = None,
        wrap_id: str | None = None,
        sender_wrap_id: str | None = None,
        seal_id: str | None = None,
    ) -> bool:
        db_path = self.ensure_message_db(peer)
        uid = rumor_id or event_id or uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{direction}|{created_at}|{sender_hex}|{recipient_hex}|{content}",
        ).hex
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    uid, direction, created_at, datetime_text, content, peer_npub, peer_hex,
                    sender_hex, recipient_hex, rumor_id, event_id, wrap_id, sender_wrap_id,
                    seal_id, relay, inserted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    direction,
                    created_at,
                    compact_time(created_at),
                    content,
                    peer.bech32(),
                    peer.hex(),
                    sender_hex,
                    recipient_hex,
                    rumor_id,
                    event_id,
                    wrap_id,
                    sender_wrap_id,
                    seal_id,
                    relay_url,
                    int(datetime.now().timestamp()),
                ),
            )
            inserted = cursor.rowcount > 0
        self.emit_messages(peer)
        return inserted

    def load_messages(self, peer: PublicKey, limit: int = 300) -> list[dict[str, Any]]:
        db_path = self.ensure_message_db(peer)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT direction, datetime_text, content, uid, peer_npub, rumor_id, event_id, wrap_id,
                       sender_wrap_id, seal_id, relay
                FROM messages
                ORDER BY COALESCE(created_at, inserted_at) DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def emit_messages(self, peer: PublicKey) -> None:
        self.messages_signal.emit(self.load_messages(peer))

    def delete_message(self, payload: dict[str, Any]) -> None:
        peer_npub = str(payload.get("peer_npub") or "").strip()
        uid = str(payload.get("uid") or "").strip()
        if not peer_npub or not uid:
            self.log("delete skipped: missing peer or uid", "warn")
            return
        peer = parse_public_key(peer_npub)
        db_path = self.ensure_message_db(peer)
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("DELETE FROM messages WHERE uid = ?", (uid,))
            deleted = cursor.rowcount
        self.emit_messages(peer)
        self.log(f"message delete: {short(uid)} from {db_path} ({deleted} row)", "muted")

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
        recipient_env = str(payload.get("recipient_env") or self.config.get("recipient") or "").strip()
        recipient_value = self.resolve_recipient_value(recipient_env, str(payload.get("recipient_value") or ""))
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
        inserted = self.store_message(
            recipient,
            "S",
            message,
            int(rumor.get("created_at") or datetime.now().timestamp()),
            relay_url,
            sender_hex=sender_key.public_key.hex(),
            recipient_hex=recipient.hex(),
            rumor_id=str(rumor.get("id") or ""),
            event_id=wrap.id,
            wrap_id=wrap.id,
            sender_wrap_id=sender_wrap.id,
            seal_id=seal.id,
        )
        self.log(
            f"message db: {peer_db_path(recipient)} ({'inserted' if inserted else 'duplicate'})",
            "muted",
        )
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
            self.log_event(event, decrypt_key, relay_url)

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

    def log_event(
        self,
        event: Event,
        decrypt_key: PrivateKey | None = None,
        relay_url: str = "",
    ) -> None:
        self.log("-" * 48, "muted")
        date_text = compact_time(event.created_at)
        event_id = event.id or ""
        author = event.pubkey or ""

        if decrypt_key and event.kind == nip17.KIND_GIFT_WRAP:
            try:
                seal, rumor = nip17.unwrap_gift_wrap(decrypt_key, event)
                content = one_line(rumor.get("content"), 1200 if self.debug_enabled else 500)
                sender = str(rumor.get("pubkey") or seal.pubkey or "")
                rumor_id = str(rumor.get("id") or "")
                self.log(content or "(empty message)")
                if self.debug_enabled:
                    self.log(
                        f"{date_text} / from {sender} / rumor {rumor_id} / wrap {event_id} / seal {seal.id}",
                        "muted",
                    )
                    if rumor.get("tags"):
                        self.log("tags " + json.dumps(rumor.get("tags"), ensure_ascii=False), "debug")
                else:
                    self.log(
                        f"{date_text} / from {short(sender)} / id {short(rumor_id or event_id)}",
                        "muted",
                    )
                self.store_unwrapped_message(decrypt_key, event, seal, rumor, relay_url)
                return
            except Exception as exc:
                self.log(
                    f"{date_text} / gift-wrap {short(event_id)} / from {short(author)} / decrypt error",
                    "warn",
                )
                self.log(f"{exc!r}", "error")
                return

        content = one_line(event.content, 1200 if self.debug_enabled else 500)
        self.log(content or f"(kind {event.kind}, no content)")
        if self.debug_enabled:
            self.log(
                f"{date_text} / kind {event.kind} / from {author} / id {event_id}",
                "muted",
            )
            if event.tags:
                self.log("tags " + json.dumps(event.tags[:12], ensure_ascii=False), "debug")
        else:
            self.log(
                f"{date_text} / from {short(author)} / kind {event.kind} / id {short(event_id)}",
                "muted",
            )

    def store_unwrapped_message(
        self,
        my_key: PrivateKey,
        wrap_event: Event,
        seal: Event,
        rumor: dict[str, Any],
        relay_url: str,
    ) -> None:
        my_pub_hex = my_key.public_key.hex()
        sender_hex = str(rumor.get("pubkey") or seal.pubkey or "")
        recipient_hex = ""
        for tag in rumor.get("tags") or []:
            if tag and tag[0] == "p" and len(tag) > 1:
                recipient_hex = str(tag[1])
                break

        if sender_hex == my_pub_hex:
            direction = "S"
            peer_hex = recipient_hex
        else:
            direction = "R"
            peer_hex = sender_hex
            recipient_hex = recipient_hex or my_pub_hex
        if not peer_hex:
            self.log("message db skipped: peer key not found in rumor", "warn")
            return

        peer = public_key_from_hex(peer_hex)
        inserted = self.store_message(
            peer,
            direction,
            str(rumor.get("content") or ""),
            int(rumor.get("created_at") or wrap_event.created_at or datetime.now().timestamp()),
            relay_url,
            sender_hex=sender_hex,
            recipient_hex=recipient_hex,
            rumor_id=str(rumor.get("id") or ""),
            event_id=wrap_event.id,
            wrap_id=wrap_event.id,
            seal_id=seal.id,
        )
        self.log(
            f"message db: {peer_db_path(peer)} ({'inserted' if inserted else 'duplicate'})",
            "muted",
        )

    def log_section(self, title: str) -> None:
        self.log("=" * 80)
        self.log(title)
        self.log("=" * 80)
