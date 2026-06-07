from time import sleep
from agama_nostr.client import Client 
from agama_nostr.tools import get_nostr_key, print_head


nostr_client = Client(get_nostr_key())

#print_head("my relays list")
#nostr_client.print_myrelays_list()

try:
    npub1 = get_nostr_key("NOSTR_PUB")
except RuntimeError as exc:
    print(exc)
    print("Add NOSTR_PUB=npub1... to .env.")
    raise SystemExit(1)

nostr_client.set_filter_meta(npub1)
nostr_client.set_subscription_id()

nostr_client.single_relay_event()
nostr_client.message_pool_events()
