---
name: sui-chain
description: >
  Query the EVE Frontier Sui blockchain for live on-chain data — kill events, smart gate
  state and jump traversals, assembly ONLINE/OFFLINE status, character name/wallet lookup,
  SSU inventory contents, network node fuel levels. Use when Commander asks about kills,
  who died, gate activity, which assemblies are online, or any live chain state.
  Complements world-api (static data) and evedatacore (community index).
allowed-tools:
  - Bash
---

# sui-chain: EVE Frontier On-Chain Data Client

Live Sui blockchain queries for Stillness (live game, runs on Sui testnet).
No local cache — chain data is real-time. All commands run from project root.
Client lives at `scripts/sui_chain.py`. Full research reference: `docs/research/sui-chain-queries.md`.

**What it covers:** kill events, gate jump traversals, assembly state (ONLINE/OFFLINE),
all player smart gates + extension state, character names + wallet lookup,
SSU inventory contents, network node fuel state, SSU deposit/withdraw events.

**What it does NOT cover:** live ship positions (game server side), ship hold inventory,
decoded spatial coordinates (location_hash is CCP-proprietary), crafting recipes.
For static game data (item type names, ore mappings, systems), use the `world-api` skill instead.

---

## Cross-Reference with world-api

The chain stores IDs. The World API stores what those IDs mean. Use both together:

| Chain field | Example value | Resolve with world-api |
|---|---|---|
| `type_id` (kills, SSU events, inventory) | `88335` | `python scripts/world_api.py type 88335` → "D1 Fuel" |
| `solar_system_id` (kills) | `30016295` | `python scripts/world_api.py system 30016295` → system name |
| `tribe_id` (characters) | `98000424` | `python scripts/world_api.py tribes` → tribe name |
| `type_id` (assemblies) | `88063` | `python scripts/world_api.py type 88063` → "Refinery" |

**Gate topology requires both sources:**
- CCP NPC gates (static backbone) → world-api `system <name>` returns `gateLinks`
- Player smart gates (dynamic shortcuts) → `python scripts/sui_chain.py gates`
- Full routing = NPC backbone + player gate overlay

**Enrichment functions (built-in, no manual world_api import needed):**
```python
import sys
sys.path.insert(0, 'scripts')
import sui_chain

# System name map — lazy-loaded from world_api cache, free after first call
systems = sui_chain.get_system_map()   # {system_id_str: name}
types   = sui_chain.get_type_map()     # {type_id_str: name}

# Character names — SQLite cache, build once then instant
sui_chain.build_char_cache()           # no-op if cache exists; ~30-90s on first run
names = sui_chain.resolve_characters(['2112081168', '2112078261'])
# returns {'2112081168': 'agent-raw', '2112078261': 'OMGEA'}

# Full enriched kill feed
kills = sui_chain.get_kills(limit=20)
for k in kills:
    k['system_name'] = systems.get(k['solar_system_id'], k['solar_system_id'])
    # k['killer_name'] = names.get(k['killer_id'], k['killer_id'])  # after resolve_characters
```

---

## CLI Quick Reference

```bash
# Kill events (system names always resolved via world-api cache)
python scripts/sui_chain.py kills                    # 20 most recent kills (SHIP + STRUCTURE)
python scripts/sui_chain.py kills --limit 50         # more
python scripts/sui_chain.py kills --structure        # structure kills only
python scripts/sui_chain.py kills --ship             # ship kills only
python scripts/sui_chain.py kills --names            # resolve character IDs to names (SQLite cache)
python scripts/sui_chain.py kills --json             # raw JSON

# Character name cache (8451 characters, ~1 MB SQLite)
python scripts/sui_chain.py char-cache               # show cache status
python scripts/sui_chain.py char-cache --rebuild     # force rescan from chain (~30-90s)

# Smart Gates (only 14 exist in all of Stillness)
python scripts/sui_chain.py gates                    # all gates: status, linked, extension
python scripts/sui_chain.py gate-events              # recent gate jump traversals
python scripts/sui_chain.py gate-events --limit 50

# Assemblies
python scripts/sui_chain.py assemblies               # all Assembly objects (ONLINE/OFFLINE)
python scripts/sui_chain.py assemblies --online
python scripts/sui_chain.py assemblies --offline
python scripts/sui_chain.py assemblies --summary     # count by type (fastest overview)

# Characters (8050+ players, all public)
python scripts/sui_chain.py character 0xWALLET       # lookup by Sui wallet address
python scripts/sui_chain.py character 2112084665     # lookup by game ID (slower — scans)

# Network Nodes (3322 total)
python scripts/sui_chain.py network-nodes            # first 50 with fuel state
python scripts/sui_chain.py network-nodes --limit 20

# SSU Inventory
python scripts/sui_chain.py ssu 1000003307091        # inventory by game ID (slow — scans)

# SSU Item Events
python scripts/sui_chain.py ssu-events               # recent deposits + withdrawals + burns
python scripts/sui_chain.py ssu-events --deposit     # deposits only
python scripts/sui_chain.py ssu-events --withdraw    # withdrawals only
python scripts/sui_chain.py ssu-events --burn        # burns only
python scripts/sui_chain.py ssu-events --limit 50

# Raw module events (exploration)
python scripts/sui_chain.py events killmail          # all kill events
python scripts/sui_chain.py events gate              # all gate events (jumps, links, creates)
python scripts/sui_chain.py events assembly          # assembly lifecycle events
python scripts/sui_chain.py events turret            # turret lifecycle events
python scripts/sui_chain.py events character         # character creation events
python scripts/sui_chain.py events network_node      # fuel burn/deposit events
python scripts/sui_chain.py events storage_unit      # SSU lifecycle events
```

---

## Key Package + Object IDs (Stillness / Live Game)

| Object | ID |
|--------|-----|
| World Package | `0x28b497559d65ab320d9da4613bf2498d5946b2c0ae3597ccfda3072ce127448c` |
| Object Registry | `0x454a9aa3d37e1d08d3c9181239c1b683781e4087fbbbd48c935d54b6736fd05c` |
| Killmail Registry | `0x7fd9a32d0bbe7b1cfbb7140b1dd4312f54897de946c399edb21c3a12e52ce283` |
| Location Registry | `0xc87dca9c6b2c95e4a0cbe1f8f9eeff50171123f176fbfdc7b49eef4824fc596b` |
| Gate Config | `0xd6d9230faec0230c839a534843396e97f5f79bdbd884d6d5103d0125dc135827` |

Endpoints: GraphQL `https://graphql.testnet.sui.io/graphql` · JSON-RPC `https://fullnode.testnet.sui.io:443`

---

## Importing in Other Scripts

```python
import sys
sys.path.insert(0, 'scripts')
import sui_chain

# Kill stream
kills = sui_chain.get_kills(limit=50)
kills = sui_chain.get_kills(limit=20, loss_type='STRUCTURE')

# Checkpoint-based poll loop (efficient — no reprocessing)
cp = sui_chain.get_current_checkpoint()
kills, cp = sui_chain.get_kills_since_checkpoint(cp)  # returns (kills, new_checkpoint)

# All player smart gates (only 14)
gates = sui_chain.get_all_gates()
# gate dict keys: game_id, status, linked_gate_id, extension, owner_cap_id, metadata_name

# Character lookup
char = sui_chain.get_character_by_wallet('0xWALLET')
# returns: { game_id, name, tribe_id, wallet, owner_cap_id, char_sui_id }

# Network nodes with fuel
nodes = sui_chain.get_network_nodes(limit=100)
# node dict keys: game_id, name, status, fuel_qty, fuel_max, burn_rate_ms, is_burning,
#                 last_updated_ms, connected_count

# SSU item events (direction-aware)
events = sui_chain.get_ssu_transactions(limit=50, direction='deposit')
events = sui_chain.get_ssu_transactions(limit=50, direction='withdraw')
events = sui_chain.get_ssu_transactions(limit=50)  # all

# Assembly state
assemblies = sui_chain.get_assemblies(status_filter='ONLINE')

# Gate jump events
jumps = sui_chain.get_gate_jumps(limit=20)
```

---

## Key Data Shapes

### Kill Event
```json
{
  "kill_id":         "8777",
  "killer_id":       "2112078868",
  "victim_id":       "2112078261",
  "loss_type":       "STRUCTURE",
  "solar_system_id": "30016295",
  "kill_timestamp":  "1775338299",
  "timestamp":       "2026-04-04T21:31:52.950Z",
  "tx_digest":       "CGgQjLtd..."
}
```

### Gate Object
```json
{
  "game_id":        "1000003399417",
  "status":         "ONLINE",
  "linked_gate_id": "0x558793c2...",
  "extension":      null,
  "owner_cap_id":   "0x...",
  "metadata_name":  "TEST Gate Please Ignore"
}
```
`extension: null` = no extension installed (default `jump()` allowed).
When populated: `{ "name": "0xPKG::module::WitnessStruct" }`.

### Character
```json
{
  "char_sui_id": "0xcdf46dbc...",
  "game_id":     "2112084665",
  "name":        "PlayerName",
  "tribe_id":    98000424,
  "wallet":      "0xWALLET...",
  "owner_cap_id":"0x..."
}
```

### Network Node
```json
{
  "game_id":         "1000002266218",
  "name":            "Voidwalker Nexus 1",
  "status":          "ONLINE",
  "fuel_qty":        1088,
  "fuel_max":        100000,
  "burn_rate_ms":    3600000,
  "is_burning":      true,
  "last_updated_ms": 1775339238605,
  "connected_count": 5
}
```
`burn_rate_ms = 3600000` = 1 unit burned per hour. `fuel_qty / (burn_rate_ms / 3_600_000)` = hours remaining.

---

## Checkpoint-Based Polling (Alert Pattern)

Sui checkpoints advance at ~3.6/sec. Poll every 30s = ~108 new checkpoints.
`afterCheckpoint` is the correct mechanism — it survives restarts and avoids reprocessing.

```python
import time, sui_chain

checkpoint = sui_chain.get_current_checkpoint()

while True:
    time.sleep(30)
    kills, checkpoint = sui_chain.get_kills_since_checkpoint(checkpoint)
    for k in kills:
        if k['loss_type'] == 'STRUCTURE':
            # fire Discord webhook for structure destroyed alert
            pass
```

---

## Rate Limits and Hard Constraints

| Constraint | Limit | Notes |
|-----------|-------|-------|
| Events per query | 50 max | Paginate with cursor |
| Objects per query (with dynamicFields) | 20 max | Use `first: 20` |
| Objects per query (no dynamicFields) | 50 max | |
| Filter objects by content | Not supported | Paginate + filter client-side |
| Checkpoint rate | ~3.6/sec | 0.28s per checkpoint |

---

## Object Counts (Stillness, 2026-04-04 snapshot)

| Type | Count | Notes |
|------|-------|-------|
| Character | 8,050+ | All players, names + wallets public |
| NetworkNode | 3,322 | Infrastructure |
| Turret | 2,623 | 2 have active extensions (pkg `0x9029...`) |
| StorageUnit | 1,547 | Full inventory in dynamic fields |
| **Gate** | **14** | **No extensions installed — virgin territory** |

Stillness game launch: **2026-03-11 15:33 UTC**.

---

## SSU Inventory Notes

Inventory is stored in dynamic fields (one per inventory slot / access tier).
Max 20 objects per GraphQL request when including `dynamicFields`.
`get_ssu_inventory(game_id)` scans all SSUs to find by game ID — can be slow (1547 SSUs / 20 per page = up to 78 requests). For production use, derive Sui object ID directly via `ObjectRegistry` instead.

SSU deposit vs withdrawal direction:
- `inventory::ItemDepositedEvent` = deposit
- `inventory::ItemWithdrawnEvent` = withdrawal
- Both have identical field schemas (no direction field — it's in the type)
- A single game action may emit both (e.g., moving items between inventory slots)

---

## Extension Field Notes

All assemblies have `extension: Option<TypeName>`. When populated it stores the witness type name string of the authorized extension package. Checking for extensions:

```python
gates = sui_chain.get_all_gates()
extended = [g for g in gates if g['extension'] is not None]
# extension value: {"name": "0xPKG::module::WitnessStruct"}
```

Once a gate extension is authorized, `jump()` is disabled for that gate — all traversals require a `JumpPermit` issued by the extension (`jump_with_permit()`). This is a commitment: installing an extension changes the gate's behavior for all users.
