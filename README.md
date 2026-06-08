# agama_nostr

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab.svg)
![Nostr](https://img.shields.io/badge/Nostr-protocol-8e44ad.svg)
![pynostr](https://img.shields.io/badge/pynostr-0.7.0-2f855a.svg)
![Purpose](https://img.shields.io/badge/purpose-education%20%26%20testing-orange.svg)

Small experimental Python wrapper around `pynostr`.

This project builds on an older Agora-era codebase:
[agora3/agora-py-nostr](https://github.com/agora3/agora-py-nostr), developed
for Agora experiments during 2021-2023.

This project is still a work in progress. Treat scripts that publish events or send
DMs as real Nostr actions: they use the private key from `.env`.

## Install

```bash
git clone https://github.com/agama-point/py_nostr.git
cd py_nostr

python -m venv venv
# Windows PowerShell:
.\venv\Scripts\Activate.ps1
# Linux/macOS:
# source venv/bin/activate

python -m pip install -r requirements.txt
copy .env.example .env
```

Always run scripts with the same Python interpreter that installed the
requirements. On Windows this is usually:

```powershell
.\venv\Scripts\Activate.ps1
python nostr_1_keys.py

# or explicitly:
.\venv\Scripts\python.exe nostr_1_keys.py
.\venv\Scripts\python.exe -m pip show python-dotenv
```

Avoid `python3` on Windows unless you have verified it points into this venv.

If you want to use a specific Python installation, install the requirements with
that exact interpreter too:

```powershell
C:\Users\Yenda\AppData\Local\Programs\Python\Python313\python.exe -m pip install -r requirements.txt
C:\Users\Yenda\AppData\Local\Programs\Python\Python313\python.exe nostr_1_keys.py
```

Edit `.env` and set your Nostr private key:

```dotenv
NOSTR_KEY=nsec1_or_64_char_hex_private_key
```

`NOSTR_KEY` can be either:

- a 64-character hex private key
- an `nsec1...` private key

Do not commit `.env`. It is ignored by git.

## Generate a Key

To generate a new temporary Nostr keypair:

```bash
python nostr_key_gen.py
```

Save the printed private key to `.env` as `NOSTR_KEY` if you want to use it.

For interactive key work, including BIP39 mnemonic export/import:

```bash
python nostr_keys.py
```

The mnemonic conversion maps the raw 32-byte Nostr private key to a 24-word
BIP39 mnemonic and back. This is a local backup/export format, not a special
Nostr protocol requirement.

## First Tests

### 1. Local key smoke test

```bash
python nostr_1_keys.py
```

This loads `NOSTR_KEY` from `.env`, creates a local client without connecting to
relays, prints key information, and generates one temporary keypair.

### 2. Publish a text event

```bash
python nostr_2_event.py
```

This connects to configured relays and publishes a text note. Run it only with a
key you are comfortable using publicly.

## Basic Usage

```python
from agama_nostr.client import Client
from agama_nostr.tools import get_nostr_key

nc = Client(get_nostr_key())
nc.publish_event("Hello Nostr from agama_nostr")
```

For local-only key handling:

```python
from agama_nostr.client import Client
from agama_nostr.tools import get_nostr_key

nc = Client(get_nostr_key(), relays=False)
nc.print_keys_info()
```

## Dependencies

The current wrapper is built around:

- `pynostr==0.7.0`
- `cryptography==48.0.0`
- `bech32==1.2.0` for `nsec1...` private key decoding
- `mnemonic==0.21` for BIP39 mnemonic export/import
- `python-dotenv==1.2.2` for `.env` loading
- `requests==2.34.2` for NIP-11 relay metadata
- `rich==15.0.0` for nicer console tables; plain text fallback is used when it is missing
- `tornado==6.5.6` used by `pynostr` relay handling

`secp256k1` is not listed directly. On Windows it requires native build tooling
and is not imported by this codebase; `pynostr` uses `coincurve` instead.

## Project Notes

Main package:

```text
agama_nostr/
  client.py
  tools.py
  relays.py
  logger.py
```

Useful scripts:

```text
nostr_1_keys.py       local key smoke test
nostr_keys.py         interactive key console
nostr_2_event.py      publish a text note
nostr_dm.py           interactive NIP-17/NIP-44 DM console: test relay, send, recieve
nostr_main_stream.py  listen to the public kind 1 main stream
nostr_key_gen.py      generate a new keypair
nostr_publish_event.py older publish example
```


---

[agama-point/agama_linky_sandbox](https://github.com/agama-point/agama_linky_sandbox)

`agama_linky_sandbox` is a local testing and learning sandbox for [hynek-jina/linky](https://github.com/hynek-jina/linky), focused on key derivation, local tooling, and small protocol experiments around Linky's Nostr, Cashu, and Evolu integrations.

🔗 [agama-point/py_nostr](https://github.com/agama-point/py_nostr)

`py_nostr` is a small experimental Python wrapper around `pynostr` for working with Nostr keys, events, relays, publishing, user metadata, and direct messages. It is useful for local protocol experiments, but scripts that publish events or send DMs perform real Nostr actions when configured with a private key.

🔗 [agama-point/py_cashu](https://github.com/agama-point/py_cashu)

`py_cashu` is an educational Python project for exploring Cashu ecash flows: mints, Lightning invoices, blind signatures, proofs, bearer tokens, wallet seed material, and token transfers. It is a console and desktop experiment for understanding the protocol, not a production wallet.

🔗 [octopusengine/py_evolu](https://github.com/octopusengine/py_evolu)

`py_evolu` is a Python experiment around Evolu local-first data, owner mnemonics, SQLite storage, backup export, restore, and relay sync. Because Evolu has no official Python client, it uses a small TypeScript sidecar with the official Evolu packages.

---



## References

- https://github.com/holgern/pynostr
- https://github.com/jeffthibault/python-nostr
- https://github.com/monty888/monstr
