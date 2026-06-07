import sys

from pynostr.key import PrivateKey

from agama_nostr.client import Client
from agama_nostr.tools import (
    get_nostr_key,
    mnemonic_to_private_key_hex,
    private_key_to_mnemonic,
    print_head,
)


def print_key_info(private_key):
    client = Client(private_key, relays=False)
    client.print_keys_info()
    print("BIP39 mnemonic:")
    print(private_key_to_mnemonic(private_key))


def load_env_key():
    try:
        private_key = get_nostr_key()
    except RuntimeError as exc:
        print(exc)
        print("Example: copy .env.example to .env and set NOSTR_KEY.")
        return

    print_head("NOSTR_KEY from .env")
    print_key_info(private_key)


def generate_key():
    print_head("new key")
    private_key = PrivateKey()
    print_key_info(private_key.bech32())
    print()
    print("Save this value to .env as NOSTR_KEY if you want to use it:")
    print(f"NOSTR_KEY={private_key.bech32()}")


def mnemonic_to_key():
    print_head("mnemonic to key")
    words = input("BIP39 mnemonic: ").strip()
    if not words:
        print("No mnemonic entered.")
        return

    try:
        key_hex = mnemonic_to_private_key_hex(words)
    except ValueError as exc:
        print(exc)
        return

    print_key_info(key_hex)


def show_menu():
    print()
    print("nostr_keys.py")
    print("1. load .env")
    print("2. create/generate new key")
    print("3. mnemonic -> key")
    print("0. exit")


def main():
    while True:
        show_menu()
        try:
            choice = input("> ").strip()
        except EOFError:
            print()
            return 0
        if choice == "1":
            load_env_key()
        elif choice == "2":
            generate_key()
        elif choice == "3":
            mnemonic_to_key()
        elif choice == "0":
            return 0
        else:
            print("Unknown option.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ModuleNotFoundError as exc:
        print(f"Missing Python package: {exc.name}")
        print(f"Python executable: {sys.executable}")
        print(f'"{sys.executable}" -m pip install -r requirements.txt')
        raise SystemExit(1)
