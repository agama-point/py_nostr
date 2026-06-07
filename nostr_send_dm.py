#!/usr/bin/env python
import sys

from agama_nostr.tools import get_nostr_key

try:
    from agama_nostr.client import Client
except ModuleNotFoundError as exc:
    print(f"Missing Python package: {exc.name}")
    print(f"Python executable: {sys.executable}")
    print()
    print("Install requirements with the same Python used to run this script:")
    print(f'"{sys.executable}" -m pip install -r requirements.txt')
    print()
    print("In VS Code select interpreter:")
    raise SystemExit(1) from exc


def main():
    try:
        sender_key = get_nostr_key("NOSTR_KEY")
        recipient_str = get_nostr_key("NOSTR_PUB2")
    except RuntimeError as exc:
        print(exc)
        print("Add NOSTR_KEY=nsec1... and NOSTR_PUB2=npub1... to .env.")
        return 1

    nostr_client = Client(sender_key, False)
    msg = "test message 1"

    # recipient_str = input("recipient (npub or nip05): ")
    # msg = input("message: ")

    print("="*39)
    nostr_client.send_direct_message(recipient_str, msg, relay="R")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
