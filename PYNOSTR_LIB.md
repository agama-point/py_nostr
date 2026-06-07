# pynostr Library Notes

`pynostr` is a Python library for working with the Nostr protocol. It provides
helpers for Nostr keys, events, filters, relay connections, relay managers,
metadata, and encrypted direct messages.

This project currently uses:

```text
pynostr==0.7.0
```

As of the latest PyPI check, `0.7.0` is the newest available version.

## Sources

- PyPI: https://pypi.org/project/pynostr/
- GitHub: https://github.com/holgern/pynostr
- Nostr protocol repository: https://github.com/nostr-protocol/nostr
- NIPs: https://github.com/nostr-protocol/nips

## Main Modules

Common imports used by this wrapper:

```python
from pynostr.key import PrivateKey, PublicKey
from pynostr.event import Event, EventKind
from pynostr.filters import Filters, FiltersList
from pynostr.relay_manager import RelayManager
from pynostr.relay import Relay
from pynostr.message_pool import MessagePool
from pynostr.message_type import RelayMessageType, ClientMessageType
from pynostr.base_relay import RelayPolicy
from pynostr.encrypted_dm import EncryptedDirectMessage
from pynostr.utils import get_public_key, get_relay_list, get_timestamp
```

## Keys

Create a new private key:

```python
private_key = PrivateKey()
public_key = private_key.public_key
```

Load an existing private key:

```python
private_key = PrivateKey.from_hex(private_key_hex)
```

Useful key methods:

```python
private_key.hex()
private_key.bech32()
private_key.nsec()
PrivateKey.from_hex(...)
PrivateKey.from_nsec(...)

public_key.hex()
public_key.bech32()
public_key.npub()
PublicKey.from_hex(...)
PublicKey.from_npub(...)
```

## Events

Create and sign a text note:

```python
event = Event(content="Hello Nostr")
event.sign(private_key.hex())
```

Useful event methods:

```python
event.compute_id()
event.sign(private_key_hex)
event.verify()
event.to_dict()
event.to_message()
event.serialize()
event.date_time()
Event.from_dict(...)
```

Useful tag helpers:

```python
event.add_tag(...)
event.add_event_ref(event_id)
event.add_pubkey_ref(pubkey_hex)
event.has_event_ref(event_id)
event.has_pubkey_ref(pubkey_hex)
event.get_tag_list(...)
event.clear_tags()
```

Common event kinds:

```python
EventKind.TEXT_NOTE
EventKind.SET_METADATA
EventKind.CONTACTS
EventKind.RECOMMEND_RELAY
EventKind.ENCRYPTED_DIRECT_MESSAGE
EventKind.DELETE
EventKind.REACTION
EventKind.REPOSTS
EventKind.LONG_FORM_CONTENT
EventKind.RELAY_LIST_METADATA
EventKind.ZAP_REQUEST
```

## Filters

Build filters for subscriptions:

```python
filters = FiltersList([
    Filters(kinds=[EventKind.TEXT_NOTE], limit=10)
])
```

Filter by author:

```python
filters = FiltersList([
    Filters(
        authors=[public_key_hex],
        kinds=[EventKind.TEXT_NOTE],
        limit=20,
    )
])
```

Useful filter methods:

```python
filters.to_json_array()
FiltersList.from_json_array(...)
filter.to_dict()
Filters.from_dict(...)
filter.matches(event)
```

## Relay Manager

`RelayManager` manages multiple relays and is the easiest way to publish events
to several relays at once.

```python
relay_manager = RelayManager(timeout=5)
relay_manager.add_relay("wss://relay.damus.io")
relay_manager.add_relay("wss://nos.lol")

relay_manager.publish_event(event)
relay_manager.run_sync()
```

Useful relay manager methods:

```python
add_relay(url)
add_relay_list(relay_list)
remove_relay(url)
publish_event(event)
publish_message(message)
add_subscription_on_all_relays(subscription_id, filters)
close_subscription_on_all_relays(subscription_id)
open_connections(...)
close_connections()
close_all_relay_connections()
connection_statuses()
get_relay_information(url)
run_sync()
```

## Single Relay

Use `Relay` when a script needs direct control over one relay.

```python
relay = Relay(relay_url, message_pool, io_loop, policy, timeout=5)
relay.add_subscription(subscription_id, filters)
relay.publish(event.to_message())
```

Useful relay methods:

```python
add_subscription(subscription_id, filters)
update_subscription(subscription_id, filters)
close_subscription(subscription_id)
publish(message)
connect()
close()
is_connected()
update_metadata()
check_nip(nip_number)
```

## Message Pool

Relay messages are collected in a message pool.

Common usage:

```python
while message_pool.has_events():
    event_msg = message_pool.get_event()
    print(event_msg.event.content)
```

Common pool checks used by this project:

```python
has_events()
get_event()
get_all_events()
has_notices()
get_notice()
has_ok_notices()
get_ok_notice()
```

## Metadata

Metadata events use:

```python
EventKind.SET_METADATA
```

Typical filter:

```python
filters = FiltersList([
    Filters(authors=[author_hex], kinds=[EventKind.SET_METADATA])
])
```

The event content is usually a JSON string with fields such as:

```text
name
display_name
about
picture
banner
website
nip05
lud16
```

## Encrypted Direct Messages

Encrypted direct messages use:

```python
EventKind.ENCRYPTED_DIRECT_MESSAGE
EncryptedDirectMessage
```

Basic flow:

```python
dm = EncryptedDirectMessage()
dm.encrypt(
    sender_private_key.hex(),
    cleartext_content="Hello",
    recipient_pubkey=recipient_public_key.hex(),
)

dm_event = dm.to_event()
dm_event.sign(sender_private_key.hex())
```

Useful direct message methods:

```python
encrypt(...)
decrypt(...)
to_event()
from_event(event)
from_npub(...)
```

## Notes For agama_nostr

The local wrapper in `agama_nostr.client.Client` uses `pynostr` mainly for:

- loading and printing Nostr keys
- publishing text note events
- filtering text notes and metadata events
- reading relay message pools
- sending encrypted direct messages
- querying relay metadata through NIP-11 helpers

Keep `requirements.txt` pinned while this wrapper is being modernized. `pynostr`
is a relatively small library and its API can change between minor releases.
