#!/usr/bin/env python
import sys

from tornado import gen
import tornado.ioloop

from agama_nostr.relays import relays_list
from agama_nostr.tools import get_nostr_key, normalize_nostr_private_key

try:
    from pynostr.base_relay import RelayPolicy
    from pynostr.event import Event
    from pynostr.key import PrivateKey
    from pynostr.message_pool import MessagePool
    from pynostr.message_type import RelayMessageType
    from pynostr.relay import Relay
except ModuleNotFoundError as exc:
    print(f"Missing Python package: {exc.name}")
    print(f"Python executable: {sys.executable}")
    print()
    print("Install requirements with the same Python used to run this script:")
    print(f'"{sys.executable}" -m pip install -r requirements.txt')
    raise SystemExit(1) from exc


WIDTH = 72


def line(label=""):
    print()
    print("=" * WIDTH)
    if label:
        print(label)
        print("=" * WIDTH)


def prompt_note():
    line("Publish text note")
    text = input("Text to publish (len < 2 => 'test'): ")
    if len(text.strip()) < 2:
        return "test"
    return text


def publish_to_relay(relay_url, event, timeout=12):
    loop = tornado.ioloop.IOLoop()
    message_pool = MessagePool(first_response_only=False)
    status = {
        "relay": relay_url,
        "sent": False,
        "ok": None,
        "detail": "",
    }

    def on_message(message_json):
        print("[RELAY RAW]", relay_url, message_json)
        if message_json[0] == RelayMessageType.OK and message_json[1] == event.id:
            status["ok"] = bool(message_json[2])
            status["detail"] = str(message_json[3])
            loop.add_callback(relay.close)
        elif message_json[0] == RelayMessageType.NOTICE:
            status["detail"] = str(message_json)

    relay = Relay(
        relay_url,
        message_pool,
        loop,
        RelayPolicy(),
        timeout=5,
        close_on_eose=True,
        message_callback=on_message,
    )
    relay.publish(event.to_message())

    try:
        loop.run_sync(relay.connect, timeout=timeout)
    except gen.TimeoutError:
        status["detail"] = f"timeout after {timeout}s"
    finally:
        status["sent"] = relay.num_sent_events > 0
        try:
            if relay.is_connected:
                loop.run_sync(relay.close, timeout=2)
        except Exception as exc:
            status["detail"] = f"{status['detail']} close warning: {exc!r}".strip()
        loop.stop()
        loop.close(all_fds=True)

    return status


def main():
    try:
        nostr_sec = get_nostr_key("NOSTR_KEY")
    except RuntimeError as exc:
        print(exc)
        print("Example: copy .env.example to .env and set NOSTR_KEY.")
        return 1

    private_key = PrivateKey.from_hex(normalize_nostr_private_key(nostr_sec))
    note = prompt_note()

    event = Event(content=note)
    event.sign(private_key.hex())

    line("Event debug")
    print("author npub:       ", private_key.public_key.bech32())
    print("author hex:        ", private_key.public_key.hex())
    print("event id:          ", event.id)
    print("event kind:        ", event.kind)
    print("created_at:        ", event.created_at)
    print("content length:    ", len(note))
    print("content preview:   ", repr(note))
    print("relays:            ", ", ".join(relays_list))

    statuses = []
    for relay_url in relays_list:
        line(f"Publish to {relay_url}")
        statuses.append(publish_to_relay(relay_url, event))

    line("Publish result")
    ok_count = sum(1 for status in statuses if status["ok"] is True)
    rejected = [status for status in statuses if status["ok"] is False]
    unknown = [status for status in statuses if status["ok"] is None]

    print("event id:          ", event.id)
    print("confirmed relays:  ", f"{ok_count}/{len(statuses)}")
    for status in statuses:
        print(
            f"{status['relay']}: sent={status['sent']} "
            f"ok={status['ok']} detail={status['detail']!r}"
        )

    if ok_count:
        print("Publish confirmed by at least one relay.")
        return 0
    if rejected:
        print("Publish rejected by relay(s).")
        return 2
    if unknown:
        print("Publish status unknown: no relay confirmed OK.")
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
