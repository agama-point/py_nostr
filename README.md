# agama_nostr

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

## Links

- https://github.com/holgern/pynostr
- https://github.com/jeffthibault/python-nostr
- https://github.com/monty888/monstr

---

Agora_Zero: `npub1ag0ra0shs0sd24wqwqdceu2yzj3uj5xa53ge2vstz0nyf49ez68qqq2jgj`
