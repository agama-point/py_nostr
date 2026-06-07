#!/usr/bin/env python
import argparse
import json
import logging
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime

from tornado import gen
import tornado.ioloop
from tornado.websocket import websocket_connect

from agama_nostr.relays import relays_list
from agama_nostr.tools import get_nostr_key, normalize_nostr_private_key

try:
    from pynostr.base_relay import RelayPolicy
    from pynostr.event import Event
    from pynostr.filters import Filters, FiltersList
    from pynostr.key import PrivateKey, PublicKey
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


WIDTH = 80
logging.getLogger("tornado").setLevel(logging.ERROR)

# Public user/profile/list events. Most list kinds are replaceable and only the
# newest event per kind/d-tag should matter, so the script summarizes that way.
KIND_NAMES = {
    0: "metadata / profile",
    1: "text notes",
    3: "contacts / follows",
    6: "reposts",
    7: "reactions",
    10000: "mute list",
    10001: "pin list",
    10002: "relay list metadata",
    10003: "bookmarks",
    10004: "communities",
    10005: "public chats",
    10006: "blocked relays",
    10007: "search relays",
    10015: "interests",
    10030: "emoji sets",
    30000: "follow sets",
    30001: "generic lists",
    30002: "relay sets",
    30003: "bookmark sets",
    30004: "curation sets",
    30005: "video sets",
    30008: "profile badges",
    30009: "badge definitions",
    30023: "long-form content",
}

PROFILE_AND_LIST_KINDS = [
    0,
    3,
    10000,
    10001,
    10002,
    10003,
    10004,
    10005,
    10006,
    10007,
    10015,
    10030,
    30000,
    30001,
    30002,
    30003,
    30004,
    30005,
    30008,
    30009,
    30023,
]


def line(label=""):
    print()
    print("=" * WIDTH)
    if label:
        print(label)
        print("=" * WIDTH)


def short(value, left=12, right=8):
    value = str(value)
    if len(value) <= left + right + 3:
        return value
    return f"{value[:left]}...{value[-right:]}"


def utc_time(timestamp):
    if not timestamp:
        return "?"
    return datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S UTC")


def tag_value(tag, index=1, default=""):
    if len(tag) > index:
        return tag[index]
    return default


def tag_marker(tag, index=2, default=""):
    if len(tag) > index:
        return tag[index]
    return default


def parse_public_key(value):
    value = str(value).strip()
    if value.startswith("npub1"):
        return PublicKey.from_npub(value)
    if len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        return PublicKey.from_hex(value.lower())
    raise ValueError("User must be npub1... or a 64-character hex public key")


def load_main_public_key():
    private_key_hex = normalize_nostr_private_key(get_nostr_key())
    return PrivateKey.from_hex(private_key_hex).public_key


def probe_relay(relay_url, timeout=5):
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


def select_relay(candidates, timeout=5):
    line("Relay")
    for relay_url in candidates:
        print("testing:", relay_url)
        ok, detail = probe_relay(relay_url, timeout=timeout)
        print("result: ", detail)
        if ok:
            print("selected:", relay_url)
            return relay_url
    return None


def fetch_events(relay_url, public_key_hex, timeout=10, note_limit=10):
    subscription_id = "user-" + uuid.uuid4().hex
    filters = FiltersList(
        [
            Filters(authors=[public_key_hex], kinds=PROFILE_AND_LIST_KINDS, limit=200),
            Filters(authors=[public_key_hex], kinds=[1, 6, 7], limit=note_limit),
        ]
    )

    loop = tornado.ioloop.IOLoop()
    pool = MessagePool(first_response_only=False)
    policy = RelayPolicy()
    notices = []
    raw_count = 0

    def on_message(message_json):
        nonlocal raw_count
        message_type = message_json[0]
        if message_type == RelayMessageType.EVENT:
            raw_count += 1
        elif message_type == RelayMessageType.NOTICE:
            notices.append(message_json[1])

    relay = Relay(
        relay_url,
        pool,
        loop,
        policy,
        timeout=5,
        close_on_eose=True,
        message_callback=on_message,
    )
    relay.add_subscription(subscription_id, filters)

    try:
        loop.run_sync(relay.connect, timeout=timeout)
    except gen.TimeoutError:
        print(f"timeout after {timeout}s - using events received so far")
    finally:
        try:
            if relay.is_connected:
                loop.run_sync(relay.close, timeout=2)
        except Exception:
            pass
        events = [event_msg.event for event_msg in pool.get_all_events()]
        loop.stop()
        loop.close(all_fds=True)

    return events, notices, raw_count


def latest_replaceable(events):
    latest = {}
    for event in events:
        d_tag = ""
        for tag in event.tags:
            if tag and tag[0] == "d":
                d_tag = tag_value(tag)
                break
        key = (event.kind, d_tag)
        if key not in latest or event.created_at > latest[key].created_at:
            latest[key] = event
    return list(latest.values())


def newest_event(events, kind):
    selected = [event for event in events if event.kind == kind]
    if not selected:
        return None
    return max(selected, key=lambda event: event.created_at or 0)


def event_title(event):
    for tag in event.tags:
        if tag and tag[0] in ("title", "name", "d"):
            return tag_value(tag)
    if event.content:
        return event.content.strip().splitlines()[0][:80]
    return ""


def print_profile(event):
    line("Profile")
    if not event:
        print("No kind 0 metadata found on this relay.")
        return

    print("created:", utc_time(event.created_at))
    try:
        metadata = json.loads(event.content or "{}")
    except json.JSONDecodeError:
        print("Invalid profile JSON:")
        print(event.content)
        return

    preferred = [
        "name",
        "display_name",
        "username",
        "about",
        "picture",
        "banner",
        "website",
        "nip05",
        "lud16",
        "lud06",
    ]
    for key in preferred:
        value = metadata.get(key)
        if value:
            print(f"{key:14s}: {value}")

    extra = sorted(key for key in metadata.keys() if key not in preferred)
    if extra:
        print("extra keys     :", ", ".join(extra))


def print_contacts(event):
    line("Contacts / follows")
    if not event:
        print("No kind 3 contacts found on this relay.")
        return

    contacts = [tag for tag in event.tags if tag and tag[0] == "p" and len(tag) > 1]
    print("created:", utc_time(event.created_at))
    print("count:  ", len(contacts))
    for tag in contacts[:30]:
        relay = tag_marker(tag)
        petname = tag_marker(tag, 3)
        suffix = []
        if relay:
            suffix.append(relay)
        if petname:
            suffix.append(petname)
        print("p:", short(tag_value(tag)), (" | " + " / ".join(suffix)) if suffix else "")
    if len(contacts) > 30:
        print(f"... {len(contacts) - 30} more")


def print_relay_list(event):
    line("Relay list")
    if not event:
        print("No kind 10002 relay list found on this relay.")
        return

    relays = [tag for tag in event.tags if tag and tag[0] == "r" and len(tag) > 1]
    print("created:", utc_time(event.created_at))
    print("count:  ", len(relays))
    for tag in relays:
        marker = tag_marker(tag) or "read/write"
        print(f"{marker:10s} {tag_value(tag)}")


def print_list_event(event, max_items=30):
    title = event_title(event)
    header = KIND_NAMES.get(event.kind, f"kind {event.kind}")
    if title:
        header = f"{header}: {title}"
    print()
    print("-" * WIDTH)
    print(header)
    print("created:", utc_time(event.created_at), "id:", short(event.id))

    groups = defaultdict(list)
    for tag in event.tags:
        if tag:
            groups[tag[0]].append(tag)

    if event.content and event.kind not in (0,):
        content = event.content.strip()
        if content:
            print("content:", content[:300])

    printed = 0
    for tag_type in sorted(groups.keys()):
        tags = groups[tag_type]
        print(f"{tag_type} tags ({len(tags)}):")
        for tag in tags[:max_items]:
            print(" ", " | ".join(tag))
        if len(tags) > max_items:
            print(f"  ... {len(tags) - max_items} more")
        printed += len(tags)
    if printed == 0:
        print("no tags")


def print_other_lists(events):
    line("Other public lists")
    excluded = {0, 3, 10002, 1, 6, 7}
    lists = sorted(
        [event for event in latest_replaceable(events) if event.kind not in excluded],
        key=lambda event: (event.kind, event.created_at or 0),
    )
    if not lists:
        print("No additional public list events found.")
        return
    for event in lists:
        print_list_event(event)


def print_recent_activity(events):
    line("Recent public activity")
    activity = sorted(
        [event for event in events if event.kind in (1, 6, 7)],
        key=lambda event: event.created_at or 0,
        reverse=True,
    )
    if not activity:
        print("No recent notes/reposts/reactions found in this query.")
        return

    for event in activity[:20]:
        label = KIND_NAMES.get(event.kind, f"kind {event.kind}")
        print()
        print(f"{label} | {utc_time(event.created_at)} | {short(event.id)}")
        if event.content:
            print(event.content.strip()[:500])
        tag_counts = Counter(tag[0] for tag in event.tags if tag)
        if tag_counts:
            print("tags:", ", ".join(f"{name}:{count}" for name, count in tag_counts.items()))


def print_summary(events, notices, relay_url, public_key):
    line("Summary")
    print("relay: ", relay_url)
    print("npub:  ", public_key.bech32())
    print("hex:   ", public_key.hex())
    print("events:", len(events))
    if notices:
        print("notices:")
        for notice in notices:
            print("-", notice)

    kind_counts = Counter(event.kind for event in events)
    if kind_counts:
        print()
        print("Kinds:")
        for kind, count in sorted(kind_counts.items()):
            print(f"{kind:5d} {count:3d}  {KIND_NAMES.get(kind, '')}")


def main():
    parser = argparse.ArgumentParser(
        description="Read public Nostr profile/list information for the main key from .env."
    )
    parser.add_argument("--user", help="Target npub1... or hex public key. Defaults to NOSTR_KEY public key.")
    parser.add_argument("--relay", help="Use this relay instead of probing the first two configured relays.")
    parser.add_argument("--timeout", type=int, default=12, help="Fetch timeout in seconds. Default: 12.")
    parser.add_argument("--note-limit", type=int, default=10, help="Recent note/repost/reaction limit. Default: 10.")
    args = parser.parse_args()

    try:
        public_key = parse_public_key(args.user) if args.user else load_main_public_key()
    except (RuntimeError, ValueError) as exc:
        print(exc)
        print("Example: copy .env.example to .env and set NOSTR_KEY.")
        return 1

    if args.relay:
        relay_url = args.relay
    else:
        relay_url = select_relay(relays_list[:2], timeout=5)
        if not relay_url:
            print("No reachable relay found among the first two configured relays.")
            return 1

    print()
    print("Fetching user information...")
    events, notices, raw_count = fetch_events(
        relay_url,
        public_key.hex(),
        timeout=args.timeout,
        note_limit=args.note_limit,
    )
    if raw_count and not events:
        print(f"Received {raw_count} raw EVENT messages, but none remained after parsing.")

    print_summary(events, notices, relay_url, public_key)
    print_profile(newest_event(events, 0))
    print_contacts(newest_event(events, 3))
    print_relay_list(newest_event(events, 10002))
    print_other_lists(events)
    print_recent_activity(events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
