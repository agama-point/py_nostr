#!/usr/bin/env python
import sys
import json
import uuid
from pprint import pformat

import tornado.ioloop
from tornado import gen
from tornado.websocket import websocket_connect

from agama_nostr import nip17
from agama_nostr.relays import relays_list
from agama_nostr.tools import get_nostr_key, get_relay_information, short_str

try:
    from agama_nostr.client import Client
    from pynostr.event import Event, EventKind
    from pynostr.filters import Filters, FiltersList
    from pynostr.message_pool import MessagePool
    from pynostr.message_type import RelayMessageType
    from pynostr.relay import Relay
    from pynostr.base_relay import RelayPolicy
    from pynostr.utils import get_public_key, get_timestamp
except ModuleNotFoundError as exc:
    print(f"Missing Python package: {exc.name}")
    print(f"Python executable: {sys.executable}")
    print()
    print("Install requirements with the same Python used to run this script:")
    print(f'"{sys.executable}" -m pip install -r requirements.txt')
    print()
    print("In VS Code select interpreter:")
    print(r"D:\data_codex\py_nostr\venv\Scripts\python.exe")
    raise SystemExit(1) from exc


WIDTH = 72
DEFAULT_RELAY_ALIAS = "R"
DEFAULT_RELAY_URL = relays_list[0]
RECIPIENT_ENV_KEYS = {
    "2": "NOSTR_PUB2",
    "3": "NOSTR_PUB3",
}


def line(label=""):
    print()
    print("=" * WIDTH)
    if label:
        print(label)
        print("=" * WIDTH)


def load_recipients():
    recipients = {}
    missing = []

    for choice, env_key in RECIPIENT_ENV_KEYS.items():
        try:
            recipients[choice] = {
                "choice": choice,
                "env_key": env_key,
                "value": get_nostr_key(env_key),
            }
        except RuntimeError:
            missing.append(env_key)

    if missing:
        print("Missing recipient config:", ", ".join(missing))
        print("Add these keys to .env, for example:")
        for key in missing:
            print(f"{key}=npub1...")
        return None

    return recipients


def print_recipient_debug(recipients):
    line("Configured recipients")
    for choice, recipient in recipients.items():
        try:
            public_key = get_public_key(recipient["value"])
            print(f"{choice}. {recipient['env_key']}")
            print(f"   npub: {public_key.bech32()}")
            print(f"   hex:  {public_key.hex()}")
        except Exception as exc:
            print(f"{choice}. {recipient['env_key']} is invalid: {exc}")


def select_recipient(recipients):
    line("Select recipient")
    for choice, recipient in recipients.items():
        print(f"{choice}. {recipient['env_key']} {short_str(recipient['value'], 14)}")

    while True:
        choice = input("Recipient 2 or 3: ").strip()
        if choice in recipients:
            return recipients[choice]
        print("Please type 2 or 3.")


def prompt_message():
    print()
    msg = input("Message text (len < 2 => 'test'): ")
    if len(msg.strip()) < 2:
        return "test"
    return msg


def print_relay_metadata(relay_url):
    print("relay alias:       ", DEFAULT_RELAY_ALIAS)
    print("relay url:         ", relay_url)
    print("NIP-11 metadata:   ", relay_url.replace("wss://", "https://", 1))
    print()
    print("Fetching relay metadata...")

    relay_data = get_relay_information(relay_url, timeout=4)
    if relay_data:
        print(pformat(relay_data, width=WIDTH, sort_dicts=True))
    else:
        print("No NIP-11 metadata returned, or request failed.")
    return relay_data


def test_relay(relay_url=DEFAULT_RELAY_URL):
    line("Test relay")
    relay_data = print_relay_metadata(relay_url)
    loop = tornado.ioloop.IOLoop()

    try:
        print()
        print("Opening websocket...")
        ws = loop.run_sync(
            lambda: gen.with_timeout(loop.time() + 6, websocket_connect(relay_url)),
            timeout=7,
        )
        print("websocket protocol: ", getattr(ws, "protocol", None))
        print("websocket selected: ", getattr(ws, "selected_subprotocol", None))
        ws.close()
    except Exception as exc:
        print("Relay websocket test failed:", repr(exc))
        loop.close(all_fds=True)
        return False

    loop.close(all_fds=True)
    print()
    if relay_data:
        print(f"Relay {relay_url} is OK.")
    else:
        print(f"Relay {relay_url} websocket is OK, but NIP-11 metadata was not available.")
    return True


def send_message(nostr_client, recipients):
    recipient = select_recipient(recipients)
    msg = prompt_message()
    recipient_pubkey = get_public_key(recipient["value"])
    inbox_relays = discover_dm_relays(recipient_pubkey.hex())
    publish_relays = inbox_relays or [DEFAULT_RELAY_URL]

    line("Send NIP-17 DM")
    print("recipient env key: ", recipient["env_key"])
    print("recipient raw:     ", recipient["value"])
    print("recipient hex:     ", recipient_pubkey.hex())
    print("cleartext length:  ", len(msg))
    print("cleartext preview: ", repr(msg))
    print()
    if inbox_relays:
        print("recipient inbox relays:", ", ".join(inbox_relays))
    else:
        print("recipient inbox relays: not found, falling back to configured relay")
    print()
    print("Creating NIP-17 gift wraps: rumor kind 14 -> seal kind 13 -> gift wrap kind 1059...")

    relay_for_tags = publish_relays[0]
    recipient_rumor, recipient_seal, recipient_wrap = nip17.make_gift_wrap(
        nostr_client.private_key,
        recipient_pubkey.hex(),
        msg,
        relay_url=relay_for_tags,
    )
    _, sender_seal, sender_wrap = nip17.make_sender_copy(
        nostr_client.private_key,
        recipient_pubkey.hex(),
        msg,
        relay_url=relay_for_tags,
        rumor=recipient_rumor,
    )

    print("-" * 39)
    print("[NIP17 DEBUG] sender npub:         ", nostr_client.public_key.bech32())
    print("[NIP17 DEBUG] sender hex:          ", nostr_client.public_key.hex())
    print("[NIP17 DEBUG] rumor id:            ", recipient_rumor["id"])
    print("[NIP17 DEBUG] rumor kind:          ", recipient_rumor["kind"])
    print("[NIP17 DEBUG] rumor tags:          ", recipient_rumor["tags"])
    print("[NIP17 DEBUG] seal id:             ", recipient_seal.id)
    print("[NIP17 DEBUG] seal kind:           ", recipient_seal.kind)
    print("[NIP17 DEBUG] seal pubkey:         ", recipient_seal.pubkey)
    print("[NIP17 DEBUG] seal encrypted bytes:", len(recipient_seal.content))
    print("[NIP17 DEBUG] wrap id:             ", recipient_wrap.id)
    print("[NIP17 DEBUG] wrap kind:           ", recipient_wrap.kind)
    print("[NIP17 DEBUG] wrap pubkey random:  ", recipient_wrap.pubkey)
    print("[NIP17 DEBUG] wrap tags:           ", recipient_wrap.tags)
    print("[NIP17 DEBUG] wrap encrypted bytes:", len(recipient_wrap.content))
    print("[NIP17 DEBUG] sender-copy wrap id: ", sender_wrap.id)

    statuses_by_relay = {}
    for relay_url in publish_relays:
        statuses_by_relay[relay_url] = publish_events(
            relay_url,
            [recipient_wrap, sender_wrap],
            label="NIP-17 gift wrap",
        )

    flat_statuses = [
        (relay_url, event_id, status)
        for relay_url, statuses in statuses_by_relay.items()
        for event_id, status in statuses.items()
    ]
    ok_count = sum(1 for _, _, status in flat_statuses if status.get("ok") is True)
    rejected = [status for _, _, status in flat_statuses if status.get("ok") is False]

    print()
    print("recipient wrap id: ", recipient_wrap.id)
    print("sender copy id:    ", sender_wrap.id)
    print("relays used:       ", ", ".join(publish_relays))
    print("confirmed writes:  ", f"{ok_count}/{len(flat_statuses)}")
    for relay_url, event_id, status in flat_statuses:
        print(f"status {relay_url} {event_id}: ok={status.get('ok')} detail={status.get('detail')!r}")
    if rejected:
        print("Send rejected by relay for at least one wrap.")
    elif ok_count == len(flat_statuses):
        print("NIP-17 send confirmed by relay.")
    else:
        print("NIP-17 send status unknown or failed for at least one wrap.")


def discover_dm_relays(recipient_pubkey_hex):
    line("Discover NIP-17 inbox relays")
    print("Looking for recipient kind 10050 DM relay list...")
    found_events = []

    for relay_url in relays_list:
        print("query relay:", relay_url)
        loop = tornado.ioloop.IOLoop()
        message_pool = MessagePool(first_response_only=False)
        policy = RelayPolicy()
        subscription_id = "dm-relays-" + uuid.uuid4().hex
        filters = FiltersList([
            Filters(
                authors=[recipient_pubkey_hex],
                kinds=[10050],
                limit=1,
            )
        ])

        def on_message(message_json):
            print("[DISCOVER RAW]", message_json)
            if message_json[0] == RelayMessageType.EVENT:
                found_events.append(Event.from_dict(message_json[2]))

        relay = Relay(
            relay_url,
            message_pool,
            loop,
            policy,
            timeout=4,
            close_on_eose=True,
            message_callback=on_message,
        )
        relay.add_subscription(subscription_id, filters)
        try:
            loop.run_sync(relay.connect, timeout=7)
        except gen.TimeoutError:
            print("discover timeout:", relay_url)
        finally:
            try:
                if relay.is_connected:
                    loop.run_sync(relay.close, timeout=2)
            except Exception as exc:
                print("discover close warning:", repr(exc))
            loop.stop()
            loop.close(all_fds=True)

    if not found_events:
        print("No kind 10050 event found.")
        return []

    latest = max(found_events, key=lambda event: event.created_at)
    inbox_relays = []
    for tag in latest.tags:
        if len(tag) >= 2 and tag[0] == "relay" and tag[1].startswith("wss://"):
            if tag[1] not in inbox_relays:
                inbox_relays.append(tag[1])

    print("kind 10050 event id:", latest.id)
    print("kind 10050 created: ", latest.created_at)
    print("inbox relays:       ", ", ".join(inbox_relays) if inbox_relays else "(empty)")
    return inbox_relays


def publish_events(relay_url, events, label="event"):
    line(f"Publish {label}")
    loop = tornado.ioloop.IOLoop()
    message_pool = MessagePool(first_response_only=False)
    policy = RelayPolicy()
    statuses = {
        event.id: {"ok": None, "detail": "", "sent": False}
        for event in events
    }

    def on_message(message_json):
        print("[PUBLISH RAW]", message_json)
        if message_json[0] == RelayMessageType.OK:
            event_id = message_json[1]
            if event_id in statuses:
                statuses[event_id]["ok"] = bool(message_json[2])
                statuses[event_id]["detail"] = str(message_json[3])
                if all(status["ok"] is not None for status in statuses.values()):
                    loop.add_callback(relay.close)
        elif message_json[0] == RelayMessageType.NOTICE:
            print("[PUBLISH NOTICE]", message_json)

    relay = Relay(
        relay_url,
        message_pool,
        loop,
        policy,
        timeout=5,
        close_on_eose=True,
        message_callback=on_message,
    )
    for event in events:
        print("publishing:", event.id, "kind:", event.kind)
        relay.publish(event.to_message())

    try:
        loop.run_sync(relay.connect, timeout=12)
    except gen.TimeoutError:
        print("Publish timeout after 12s.")
    finally:
        for event in events:
            statuses[event.id]["sent"] = relay.num_sent_events > 0
        try:
            if relay.is_connected:
                loop.run_sync(relay.close, timeout=2)
        except Exception as exc:
            print("Relay close warning:", repr(exc))
        loop.stop()
        loop.close(all_fds=True)

    return statuses


def receive_messages(nostr_client, relay_url=DEFAULT_RELAY_URL):
    line("Recieve NIP-17 DMs")
    my_pub_hex = nostr_client.public_key.hex()
    my_pub_npub = nostr_client.public_key.bech32()
    since = get_timestamp()
    subscription_id = "dm-receive"
    filters = FiltersList([
        Filters(
            kinds=[nip17.KIND_GIFT_WRAP],
            pubkey_refs=[my_pub_hex],
            since=since,
        )
    ])

    print("my npub:           ", my_pub_npub)
    print("my hex:            ", my_pub_hex)
    print("relay:             ", relay_url)
    print("subscription id:   ", subscription_id)
    print("filter:            ", filters)
    print()
    print("Listening for NIP-17 gift wraps. Press Ctrl+C to stop.")

    loop = tornado.ioloop.IOLoop()
    message_pool = MessagePool(first_response_only=False)
    policy = RelayPolicy()

    def on_message(message_json):
        print()
        print("[RELAY RAW]", message_json)
        message_type = message_json[0]

        if message_type == RelayMessageType.END_OF_STORED_EVENTS:
            print("[RECEIVE] EOSE received, still listening for new messages...")
            return
        if message_type == RelayMessageType.NOTICE:
            print("[RECEIVE] NOTICE:", message_json)
            return
        if message_type != RelayMessageType.EVENT:
            return

        gift_wrap = Event.from_dict(message_json[2])
        print("[WRAP] id:        ", gift_wrap.id)
        print("[WRAP] created_at:", gift_wrap.created_at)
        print("[WRAP] date UTC:  ", gift_wrap.date_time())
        print("[WRAP] random pub:", gift_wrap.pubkey)
        print("[WRAP] tags:      ", gift_wrap.tags)
        print("[WRAP] encrypted: ", gift_wrap.content)

        try:
            seal, rumor = nip17.unwrap_gift_wrap(nostr_client.private_key, gift_wrap)
            print("[SEAL] id:        ", seal.id)
            print("[SEAL] sender hex:", seal.pubkey)
            print("[RUMOR] id:       ", rumor.get("id"))
            print("[RUMOR] kind:     ", rumor.get("kind"))
            print("[RUMOR] tags:     ", rumor.get("tags"))
            print("[DM CLEAR]        ", rumor.get("content"))
        except Exception as exc:
            print("[DM DECRYPT ERROR] ", repr(exc))

    relay = Relay(
        relay_url,
        message_pool,
        loop,
        policy,
        timeout=5,
        close_on_eose=False,
        message_callback=on_message,
    )
    relay.add_subscription(subscription_id, filters)

    try:
        loop.run_sync(relay.connect)
    except KeyboardInterrupt:
        print()
        print("Receive interrupted by Ctrl+C.")
    finally:
        try:
            if relay.is_connected:
                loop.run_sync(relay.close, timeout=2)
        except Exception as exc:
            print("Relay close warning:", repr(exc))
        loop.stop()
        loop.close(all_fds=True)
        print("Receive stopped.")


def print_menu():
    line("Nostr DM terminal app")
    print(f"Relay: {DEFAULT_RELAY_URL}")
    print()
    print("1. test relay")
    print("2. send")
    print("3. recieve")
    print("0. exit")


def main():
    try:
        sender_key = get_nostr_key("NOSTR_KEY")
    except RuntimeError as exc:
        print(exc)
        print("Add NOSTR_KEY=nsec1... to .env.")
        return 1

    recipients = load_recipients()
    if not recipients:
        return 1

    print_recipient_debug(recipients)
    nostr_client = Client(sender_key, False)

    while True:
        print_menu()
        action = input("Choice: ").strip()

        if action == "0":
            print("Bye.")
            return 0
        if action == "1":
            test_relay()
            continue
        if action == "2":
            send_message(nostr_client, recipients)
            continue
        if action == "3":
            receive_messages(nostr_client)
            continue

        print("Unknown choice. Type 1, 2, 3, or 0.")


if __name__ == "__main__":
    raise SystemExit(main())
