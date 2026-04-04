---
name: world-api
description: >
  Query the EVE Frontier World API for authoritative game data — item types, ore mappings,
  solar systems, ship stats, tribes, crafting materials. Use when Commander asks about item
  IDs, what a body type yields, system info, ship fitting stats, or corp lookups.
  Prefer this over manual lookups or web searches for any data the World API covers.
allowed-tools:
  - Bash
---

# world-api: EVE Frontier World API Client

Authoritative CCP-provided game data. Cache-first (SQLite, `.cache/world_api.sqlite`).
Client lives at `scripts/world_api.py`. All commands run from project root.

**What it covers:** item types, ore/body mappings, solar systems + CCP gate topology,
ship hull stats, player tribes, crafting materials, deployable structures.

**What it does NOT cover:** live SSU inventory, player smart gates, kill events,
character assets, crafting recipes. Those require the Sui chain layer.

---

## Cross-Reference with sui-chain

World API is the reference dictionary. The Sui chain is the live state machine.
Use world-api to give meaning to raw IDs returned from the chain:

| Question | Start with | Then enrich with |
|---|---|---|
| "What got destroyed?" | `sui-chain kills` → `type_id` | `world_api.py type <id>` → item name |
| "Where did the kill happen?" | `sui-chain kills` → `solar_system_id` | `world_api.py system <id>` → system name |
| "What corp is this player in?" | `sui-chain character <wallet>` → `tribe_id` | `world_api.py tribes` → tribe name |
| "What's in this SSU?" | `sui-chain ssu <id>` → `type_id` list | `world_api.py type <id>` for each |
| "Full route between two systems?" | `world_api.py system` → NPC gate links | + `sui-chain gates` → player gate shortcuts |

**world-api is cache-first (SQLite) — essentially free after the first call.**
All type and system lookups are local reads once the cache is populated.

---

## CLI Quick Reference

```bash
# Item types
python scripts/world_api.py types                          # category summary
python scripts/world_api.py types --category Asteroid      # filter by category
python scripts/world_api.py types --category Deployable    # structures
python scripts/world_api.py types --search fuel            # partial name search
python scripts/world_api.py type 77811                     # by numeric ID
python scripts/world_api.py type "Hydrated Sulfide Matrix" # exact name
python scripts/world_api.py type fuel                      # partial — shows suggestions

# Ore mapping
python scripts/world_api.py ores                           # all body type → ore mappings

# Solar systems
python scripts/world_api.py system EQN-M88                 # by name (returns gateLinks)
python scripts/world_api.py system 30020654                # by numeric ID

# Ships
python scripts/world_api.py ships                          # all hulls
python scripts/world_api.py ship 87847                     # full stats (slots, HP, fuel, physics)

# Tribes
python scripts/world_api.py tribes                         # all player tribes
python scripts/world_api.py tribe ICA                      # by name or short name

# Cache
python scripts/world_api.py cache-info                     # entry counts + TTL per endpoint
python scripts/world_api.py cache-clear                    # wipe and force re-fetch
```

---

## Key Item Type IDs (Common Reference)

| Item | ID |
|---|---|
| D1 Fuel | 88335 |
| Hydrated Sulfide Matrix | 77811 |
| Platinum-Palladium Matrix | 77810 |
| Feldspar Crystals | 77800 |
| Fossilized Exotronics | 83818 |
| Refuge | 87160 |
| Network Node | 88092 |
| Storage | 88083 |
| Smart Turret | 84556 |
| Heavy Gate | 84955 |
| Reflex | 87847 |

---

## Body Type → Ore (Quick Ref)

For the full live table: `python scripts/world_api.py ores`

| Body | Ore | Known Use |
|---|---|---|
| Comet | Hydrated Sulfide Matrix | → D1 Fuel |
| Slag | Platinum-Palladium Matrix | → Refuge, structures |
| Char | Feldspar Crystals | → Portable Field Refinery |
| Dewdrop | Methane Ice Shards | Unknown |
| Ember | Primitive Kerogen Matrix | Unknown |
| Glint | Aromatic Carbon Veins | Unknown |
| Ingot | Iridosmine Nodules | Unknown |
| Soot | Tholin Nodules | Unknown |
| Rift | 5× Crude Matter variants | Unknown |
| Synthetic Hermetite | 4× Core Hermetite variants | Unknown |
| Deep-Core Carbon Asteroid | Deep-Core Carbon Ore | Unknown |

---

## Importing in Other Scripts

```python
import sys
sys.path.insert(0, 'scripts')
import world_api

# Common patterns
types     = world_api.get_types(category='Asteroid')
item      = world_api.get_type_by_id(77811)
item      = world_api.get_type_by_name('D1 Fuel')[0]
system    = world_api.get_system_by_name('EQN-M88')[0]   # includes gateLinks
ship      = world_api.get_ship_by_id(87847)
tribe     = world_api.get_tribe_by_name('ICA')[0]
ore_map   = world_api.get_ore_map()
```

---

## Cache Behavior

| Endpoint group | TTL |
|---|---|
| types, systems, ships, constellations | 7 days |
| tribes | 1 hour |
| config | 1 day |

Run `cache-clear` if you suspect stale data after a game update.
First run of `get_systems()` fetches ~50 pages (~24,502 systems); instant from cache after.

---

## Coordinate System Note

Solar system `location {x, y, z}` values are in **meters** at universe scale (~10¹⁹ m range).
In-system gate positions inside `gateLinks` are also meters but at AU scale (~10⁹–10¹¹ m).
Python handles these natively as integers. JavaScript requires `BigInt` for precision.
