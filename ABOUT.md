# py_nostr

## Pravidla spoluprace

- Spolu se bavime cesky.
- Programy, kod, nazvy funkci, komentare v kodu, README pro GitHub a
  uzivatelske texty ve skriptech/aplikacich piseme anglicky, pokud se
  nedomluvime jinak.
- Vsechny soubory, cache, docasna data a generovane vystupy zustavaji pod
  `D:\data_codex`; nepouzivat `C:`.

## O co jde

Aktualizujeme wrapper pro `pynostr` v knihovnach `agama_nostr/`.
Piseme sadu Python skriptu pro testovani klicu, relayu a Nostr
message/event send/read workflow.

## Aktualni stav

...

Generovane vystupy:

- `nostr_user.py` nacita hlavni klic z `.env`, odvozuje verejny klic a
  cte verejne profilove a seznamove informace z prvniho dostupneho relay.
- `nostr_app.py` spousti PyQt6 desktop aplikaci; hlavni UI je v `nostr_ui.py`,
  worker/logika v `nostr_worker.py`.
- `setup.json` drzi zakladni vyber klice, relay, prijemce, stream channel a
  verbose rezim.

## Globalni nastaveni pro praci

```text
CODEX_HOME       D:\data_codex\.codex
XDG_CACHE_HOME   D:\data_codex\.cache
npm_config_cache D:\data_codex\.cache\npm
PIP_CACHE_DIR    D:\data_codex\.cache\pip
TEMP             D:\data_codex\.tmp
TMP              D:\data_codex\.tmp
```

## Poznamky pro Codex

Necommitovat realny mnemonic material z `.env`. Projekt je experiment, ne
produkcni Python klient.
