#!/usr/bin/env python
import sys
import uuid

from tornado import gen
import tornado.ioloop
from tornado.websocket import websocket_connect

from agama_nostr.relays import relays_list

try:
    from pynostr.base_relay import RelayPolicy
    from pynostr.event import Event, EventKind
    from pynostr.filters import Filters, FiltersList
    from pynostr.message_pool import MessagePool
    from pynostr.message_type import RelayMessageType
    from pynostr.relay import Relay
    from pynostr.utils import get_timestamp
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


def probe_relay(relay_url, timeout=6):
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


def select_first_live_relay():
    line("Relay probe")
    for relay_url in relays_list:
        print("testing:", relay_url)
        ok, detail = probe_relay(relay_url)
        print("result: ", detail)
        if ok:
            print("selected:", relay_url)
            return relay_url
    return None


def stream_relay(relay_url):
    line("Nostr main stream")
    since = get_timestamp()
    subscription_id = "main-stream-" + uuid.uuid4().hex
    filters = FiltersList([
        Filters(
            kinds=[EventKind.TEXT_NOTE],
            since=since,
        )
    ])

    print("relay:           ", relay_url)
    print("subscription id: ", subscription_id)
    print("filter:          ", filters)
    print()
    print("Listening for public kind 1 text notes. Press Ctrl+C to stop.")

    loop = tornado.ioloop.IOLoop()
    message_pool = MessagePool(first_response_only=False)
    policy = RelayPolicy()
    seen = set()

    def on_message(message_json):
        message_type = message_json[0]
        if message_type == RelayMessageType.END_OF_STORED_EVENTS:
            print("[EOSE] relay caught up; waiting for new notes...")
            return
        if message_type == RelayMessageType.NOTICE:
            print("[NOTICE]", message_json)
            return
        if message_type != RelayMessageType.EVENT:
            print("[RELAY RAW]", message_json)
            return

        event = Event.from_dict(message_json[2])
        if event.id in seen:
            return
        seen.add(event.id)

        print()
        print("-" * WIDTH)
        print("date UTC: ", event.date_time())
        print("author:   ", event.pubkey)
        print("event id: ", event.id)
        print("content:")
        print(event.content)

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
        print("Stream interrupted by Ctrl+C.")
    finally:
        try:
            if relay.is_connected:
                loop.run_sync(relay.close, timeout=2)
        except Exception as exc:
            print("Relay close warning:", repr(exc))
        loop.stop()
        loop.close(all_fds=True)
        print("Stream stopped.")


def main():
    relay_url = select_first_live_relay()
    if not relay_url:
        print("No configured relay is reachable.")
        return 1

    stream_relay(relay_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
