#!/usr/bin/env python3
"""
EVE Frontier Sui Chain — on-chain data client.
Queries kill events, assembly state, gate events, characters, and SSU inventory via Sui GraphQL.

No local cache — chain data is live. Use --limit to control scope.
Endpoint: Sui testnet GraphQL (Stillness is the live game, runs on testnet).

Usage:
  python scripts/sui_chain.py kills                        # 20 most recent kills
  python scripts/sui_chain.py kills --limit 50             # more kills
  python scripts/sui_chain.py kills --structure            # structure kills only
  python scripts/sui_chain.py kills --ship                 # ship kills only
  python scripts/sui_chain.py assemblies                   # all assemblies (ONLINE/OFFLINE)
  python scripts/sui_chain.py assemblies --online          # online only
  python scripts/sui_chain.py assemblies --offline         # offline only
  python scripts/sui_chain.py gates                        # all 14 player smart gates + link status
  python scripts/sui_chain.py gate-events                  # recent gate jump events
  python scripts/sui_chain.py gate-events --limit 50       # more jumps
  python scripts/sui_chain.py character 0xWALLET           # look up character by wallet address
  python scripts/sui_chain.py character 2112084665         # look up character by game ID
  python scripts/sui_chain.py network-nodes                # all network nodes with fuel state
  python scripts/sui_chain.py ssu <game_id>                # SSU inventory by game ID
  python scripts/sui_chain.py events killmail              # raw events from module
  python scripts/sui_chain.py events gate --limit 50       # more gate events (all types)
  python scripts/sui_chain.py events assembly              # assembly lifecycle events
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GRAPHQL_URL = "https://graphql.testnet.sui.io/graphql"
JSONRPC_URL = "https://fullnode.testnet.sui.io:443"

WORLD_PKG = "0x28b497559d65ab320d9da4613bf2498d5946b2c0ae3597ccfda3072ce127448c"

# Full event type strings
EVENT_KILL      = f"{WORLD_PKG}::killmail::KillmailCreatedEvent"
EVENT_GATE_JUMP = f"{WORLD_PKG}::gate::JumpEvent"

# Full object type strings
OBJ_ASSEMBLY = f"{WORLD_PKG}::assembly::Assembly"
OBJ_GATE     = f"{WORLD_PKG}::gate::Gate"

# Tenant tag embedded in all game-side IDs
TENANT = "stillness"

session = requests.Session()
session.headers["Content-Type"] = "application/json"


# ---------------------------------------------------------------------------
# Core fetch helpers
# ---------------------------------------------------------------------------

def _graphql(query: str) -> dict:
    """POST a GraphQL query and return the data dict. Raises on errors."""
    r = session.post(GRAPHQL_URL, json={"query": query}, timeout=30)
    r.raise_for_status()
    result = r.json()
    if "errors" in result:
        raise RuntimeError(f"GraphQL error: {result['errors']}")
    return result["data"]


def _rpc(method: str, params: list) -> dict:
    """JSON-RPC 2.0 call. Returns result dict. Raises on errors."""
    r = session.post(JSONRPC_URL, json={
        "jsonrpc": "2.0", "id": 1,
        "method":  method,
        "params":  params,
    }, timeout=30)
    r.raise_for_status()
    result = r.json()
    if "error" in result:
        raise RuntimeError(f"RPC error: {result['error']}")
    return result["result"]


# ---------------------------------------------------------------------------
# Public API functions (importable from other scripts)
# ---------------------------------------------------------------------------

_PAGE_MAX = 50  # GraphQL hard limit per events() call


def get_kills(limit: int = 20, loss_type: str = None) -> list[dict]:
    """
    Recent kill events from the chain.

    Args:
        limit:     Maximum results to return (capped at 50 when no filter).
        loss_type: Optional filter — 'SHIP' or 'STRUCTURE'. When set,
                   paginates backward up to 5 pages (250 events) to find matches.

    Returns list of dicts:
        kill_id, killer_id, victim_id, loss_type, solar_system_id,
        kill_timestamp (unix seconds str), timestamp (ISO 8601), tx_digest
    """
    def _parse_node(node):
        payload = node["contents"]["json"]
        lt = payload.get("loss_type", {})
        lt_str = lt.get("@variant", "UNKNOWN") if isinstance(lt, dict) else str(lt)
        return {
            "kill_id":         payload["key"]["item_id"],
            "killer_id":       payload["killer_id"]["item_id"],
            "victim_id":       payload["victim_id"]["item_id"],
            "loss_type":       lt_str,
            "solar_system_id": payload["solar_system_id"]["item_id"],
            "kill_timestamp":  payload.get("kill_timestamp"),
            "timestamp":       node["timestamp"],
            "tx_digest":       node["transaction"]["digest"],
        }

    if not loss_type:
        # Single page — return the most recent min(limit, PAGE_MAX) kills
        fetch = min(limit, _PAGE_MAX)
        data = _graphql(f"""
        {{
          events(
            last: {fetch},
            filter: {{ type: "{EVENT_KILL}" }}
          ) {{
            nodes {{
              timestamp
              contents {{ json }}
              transaction {{ digest }}
            }}
          }}
        }}
        """)
        return [_parse_node(n) for n in data["events"]["nodes"]]

    # Filtered: paginate backward up to 5 pages to collect enough matches
    kills = []
    cursor = None
    max_pages = 5
    for _ in range(max_pages):
        before_clause = f', before: "{cursor}"' if cursor else ""
        data = _graphql(f"""
        {{
          events(
            last: {_PAGE_MAX}{before_clause},
            filter: {{ type: "{EVENT_KILL}" }}
          ) {{
            pageInfo {{ hasPreviousPage startCursor }}
            nodes {{
              timestamp
              contents {{ json }}
              transaction {{ digest }}
            }}
          }}
        }}
        """)
        page = data["events"]
        for node in page["nodes"]:
            k = _parse_node(node)
            if k["loss_type"] == loss_type.upper():
                kills.append(k)
        if not page["pageInfo"]["hasPreviousPage"]:
            break
        if len(kills) >= limit:
            break
        cursor = page["pageInfo"]["startCursor"]
    # kills are oldest-first within each page; preserve chronological order, return last N
    return kills[-limit:]


def get_gate_jumps(limit: int = 20) -> list[dict]:
    """
    Recent gate jump events (JumpEvent only — player traversals).

    Returns list of dicts:
        source_gate_id, destination_gate_id, character_id,
        timestamp (ISO 8601)
    """
    data = _graphql(f"""
    {{
      events(
        last: {limit},
        filter: {{ type: "{EVENT_GATE_JUMP}" }}
      ) {{
        nodes {{
          timestamp
          contents {{ json }}
        }}
      }}
    }}
    """)
    jumps = []
    for node in data["events"]["nodes"]:
        p = node["contents"]["json"]
        jumps.append({
            "source_gate_id":      p.get("source_gate_id"),
            "source_gate_game_id": p.get("source_gate_key", {}).get("item_id"),
            "dest_gate_id":        p.get("destination_gate_id"),
            "dest_gate_game_id":   p.get("destination_gate_key", {}).get("item_id"),
            "character_id":        p.get("character_id"),
            "character_game_id":   p.get("character_key", {}).get("item_id"),
            "timestamp":           node["timestamp"],
        })
    return jumps


def get_module_events(module: str, limit: int = 20) -> list[dict]:
    """
    Raw events from any world module. module: 'killmail' | 'gate' | 'assembly' | 'turret' | etc.

    Returns list of dicts: timestamp, tx_digest, payload (raw dict from chain)
    """
    data = _graphql(f"""
    {{
      events(
        last: {limit},
        filter: {{ module: "{WORLD_PKG}::{module}" }}
      ) {{
        nodes {{
          timestamp
          contents {{ json }}
          transaction {{ digest }}
        }}
      }}
    }}
    """)
    return [
        {
            "timestamp": node["timestamp"],
            "tx_digest":  node["transaction"]["digest"],
            "payload":    node["contents"]["json"],
        }
        for node in data["events"]["nodes"]
    ]


def get_ssu_transactions(limit: int = 20, direction: str = None) -> list[dict]:
    """
    SSU item deposit and withdrawal events.

    These come from inventory.move (not storage_unit.move), emitted during SSU
    deposit/withdraw calls. Use direction to filter.

    Args:
        limit:     Max results.
        direction: 'deposit' | 'withdraw' | 'burn' | None (all three types mixed).

    Returns list of dicts:
        timestamp, direction, ssu_id, character_id, type_id, quantity
    """
    EVENT_DEPOSIT  = f"{WORLD_PKG}::inventory::ItemDepositedEvent"
    EVENT_WITHDRAW = f"{WORLD_PKG}::inventory::ItemWithdrawnEvent"
    EVENT_BURN     = f"{WORLD_PKG}::inventory::ItemBurnedEvent"

    if direction == "deposit":
        types = [EVENT_DEPOSIT]
    elif direction == "withdraw":
        types = [EVENT_WITHDRAW]
    elif direction == "burn":
        types = [EVENT_BURN]
    else:
        types = [EVENT_DEPOSIT, EVENT_WITHDRAW, EVENT_BURN]

    results = []
    label_map = {
        EVENT_DEPOSIT:  "deposit",
        EVENT_WITHDRAW: "withdraw",
        EVENT_BURN:     "burn",
    }
    for evt_type in types:
        fetch = min(limit, _PAGE_MAX)
        data = _graphql(f"""
        {{
          events(
            last: {fetch},
            filter: {{ type: "{evt_type}" }}
          ) {{
            nodes {{
              timestamp
              contents {{ json }}
            }}
          }}
        }}
        """)
        for node in data["events"]["nodes"]:
            p = node["contents"]["json"]
            results.append({
                "timestamp":    node["timestamp"],
                "direction":    label_map[evt_type],
                "ssu_id":       p.get("assembly_key", {}).get("item_id"),
                "character_id": p.get("character_key", {}).get("item_id"),
                "type_id":      p.get("type_id"),
                "quantity":     p.get("quantity"),
            })

    results.sort(key=lambda x: x["timestamp"])
    return results[-limit:]


def get_kills_since_checkpoint(after_checkpoint: int, limit: int = 50) -> tuple[list[dict], int]:
    """
    Efficient kill poller for alert systems. Fetches kills strictly after a checkpoint.

    Args:
        after_checkpoint: Only return events from checkpoints > this value.
        limit:            Max events per call.

    Returns (kills, max_checkpoint_seen).
    Use max_checkpoint_seen as the next after_checkpoint value.

    Checkpoint rate: ~3.6/second. Poll every 30s => ~108 new checkpoints.
    """
    data = _graphql(f"""
    {{
      events(
        first: {min(limit, _PAGE_MAX)},
        filter: {{
          type: "{EVENT_KILL}",
          afterCheckpoint: {after_checkpoint}
        }}
      ) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          timestamp
          contents {{ json }}
          transaction {{ digest effects {{ checkpoint {{ sequenceNumber }} }} }}
        }}
      }}
    }}
    """)
    kills = []
    max_cp = after_checkpoint
    for node in data["events"]["nodes"]:
        payload = node["contents"]["json"]
        cp_num = int(node["transaction"]["effects"]["checkpoint"]["sequenceNumber"])
        max_cp = max(max_cp, cp_num)
        lt = payload.get("loss_type", {})
        lt_str = lt.get("@variant", "UNKNOWN") if isinstance(lt, dict) else str(lt)
        kills.append({
            "kill_id":         payload["key"]["item_id"],
            "killer_id":       payload["killer_id"]["item_id"],
            "victim_id":       payload["victim_id"]["item_id"],
            "loss_type":       lt_str,
            "solar_system_id": payload["solar_system_id"]["item_id"],
            "kill_timestamp":  payload.get("kill_timestamp"),
            "timestamp":       node["timestamp"],
            "tx_digest":       node["transaction"]["digest"],
            "checkpoint":      cp_num,
        })
    return kills, max_cp


def get_current_checkpoint() -> int:
    """Return the current Sui checkpoint sequence number. Use as starting point for pollers."""
    data = _graphql("{ checkpoint { sequenceNumber } }")
    return int(data["checkpoint"]["sequenceNumber"])


def get_assemblies(status_filter: str = None) -> list[dict]:
    """
    All on-chain Assembly objects with current lifecycle status.

    Args:
        status_filter: Optional — 'ONLINE' | 'OFFLINE'. None returns all.

    Returns list of dicts:
        object_address, game_id, type_id, status (ONLINE/OFFLINE), owner_cap_id
    """
    results = []
    cursor = None
    while True:
        after_clause = f', after: "{cursor}"' if cursor else ""
        data = _graphql(f"""
        {{
          objects(
            first: 50{after_clause},
            filter: {{ type: "{OBJ_ASSEMBLY}" }}
          ) {{
            pageInfo {{ endCursor hasNextPage }}
            nodes {{
              address
              asMoveObject {{
                contents {{ json }}
              }}
            }}
          }}
        }}
        """)
        page = data["objects"]
        for node in page["nodes"]:
            contents = (node.get("asMoveObject") or {}).get("contents", {}).get("json", {})
            status_obj = contents.get("status", {}).get("status", {})
            status_str = (
                status_obj.get("@variant", "UNKNOWN")
                if isinstance(status_obj, dict)
                else str(status_obj)
            )
            if status_filter and status_str != status_filter.upper():
                continue
            results.append({
                "object_address": node["address"],
                "game_id":        contents.get("key", {}).get("item_id"),
                "type_id":        contents.get("type_id"),
                "status":         status_str,
                "owner_cap_id":   contents.get("owner_cap_id"),
            })
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return results


def get_all_gates() -> list[dict]:
    """
    All player Smart Gate objects (only 14 in Stillness as of 2026-04-04).

    Returns list of dicts:
        object_address, game_id, type_id, status, linked_gate_id (Sui ID or None),
        extension (None or extension config), owner_cap_id, metadata_name
    """
    data = _graphql(f"""
    {{
      objects(first: 50, filter: {{ type: "{OBJ_GATE}" }}) {{
        nodes {{
          address
          asMoveObject {{ contents {{ json }} }}
        }}
      }}
    }}
    """)
    gates = []
    for node in data["objects"]["nodes"]:
        g = node["asMoveObject"]["contents"]["json"]
        gates.append({
            "object_address": node["address"],
            "game_id":        g["key"]["item_id"],
            "type_id":        g.get("type_id"),
            "status":         g["status"]["status"].get("@variant", "UNKNOWN"),
            "linked_gate_id": g.get("linked_gate_id"),
            "extension":      g.get("extension"),
            "owner_cap_id":   g.get("owner_cap_id"),
            "metadata_name":  g.get("metadata", {}).get("name", ""),
        })
    return gates


def get_character_by_wallet(wallet: str) -> dict | None:
    """
    Look up a character by their Sui wallet address.

    Returns dict: game_id, name, tribe_id, wallet, owner_cap_id, char_sui_id
    or None if not found.
    """
    # address().objects returns MoveObject directly — no asMoveObject wrapper
    data = _graphql(f"""
    {{
      address(address: "{wallet}") {{
        objects(
          first: 10,
          filter: {{ type: "{WORLD_PKG}::character::PlayerProfile" }}
        ) {{
          nodes {{
            address
            contents {{ json }}
          }}
        }}
      }}
    }}
    """)
    profiles = data["address"]["objects"]["nodes"]
    if not profiles:
        return None
    char_sui_id = profiles[0]["contents"]["json"].get("character_id")
    if not char_sui_id:
        return None
    return _get_character_by_sui_id(char_sui_id)


def _get_character_by_sui_id(char_sui_id: str) -> dict | None:
    """Fetch Character shared object by its Sui object ID."""
    data = _graphql(f"""
    {{
      object(address: "{char_sui_id}") {{
        address
        asMoveObject {{ contents {{ json }} }}
      }}
    }}
    """)
    obj = data.get("object")
    if not obj:
        return None
    c = obj["asMoveObject"]["contents"]["json"]
    return {
        "char_sui_id":  char_sui_id,
        "game_id":      c["key"]["item_id"],
        "name":         c.get("metadata", {}).get("name", ""),
        "tribe_id":     c.get("tribe_id"),
        "wallet":       c.get("character_address"),
        "owner_cap_id": c.get("owner_cap_id"),
    }


def get_network_nodes(limit: int = 50) -> list[dict]:
    """
    Network nodes with fuel state (read directly from object — no events needed).

    Returns list of dicts:
        object_address, game_id, name, status, fuel_qty, fuel_max, fuel_type_id,
        burn_rate_ms, is_burning, last_updated_ms, connected_count
    """
    results = []
    cursor = None
    while len(results) < limit:
        after_clause = f', after: "{cursor}"' if cursor else ""
        fetch = min(50, limit - len(results))
        data = _graphql(f"""
        {{
          objects(
            first: {fetch}{after_clause},
            filter: {{ type: "{WORLD_PKG}::network_node::NetworkNode" }}
          ) {{
            pageInfo {{ endCursor hasNextPage }}
            nodes {{
              address
              asMoveObject {{ contents {{ json }} }}
            }}
          }}
        }}
        """)
        page = data["objects"]
        for node in page["nodes"]:
            c = node["asMoveObject"]["contents"]["json"]
            fuel = c.get("fuel", {})
            results.append({
                "object_address":   node["address"],
                "game_id":          c["key"]["item_id"],
                "name":             c.get("metadata", {}).get("name", ""),
                "status":           c["status"]["status"].get("@variant", "UNKNOWN"),
                "fuel_qty":         int(fuel.get("quantity", 0)),
                "fuel_max":         int(fuel.get("max_capacity", 0)),
                "fuel_type_id":     fuel.get("type_id"),
                "burn_rate_ms":     int(fuel.get("burn_rate_in_ms", 0)),
                "is_burning":       fuel.get("is_burning", False),
                "last_updated_ms":  int(fuel.get("last_updated", 0)),
                "connected_count":  len(c.get("connected_assembly_ids", [])),
            })
        if not page["pageInfo"]["hasNextPage"] or len(results) >= limit:
            break
        cursor = page["pageInfo"]["endCursor"]
    return results


def get_ssu_inventory(game_id: str) -> dict | None:
    """
    Get the inventory of a specific SSU by game ID.

    Scans SSU objects to find the matching game_id, then reads dynamic fields.
    Returns dict: game_id, object_address, status, inventory (list of {type_id, quantity, volume})
    or None if not found.

    Note: Dynamic fields max 20 per request. Fetches first 2 inventory slots.
    """
    cursor = None
    for _ in range(200):  # scan up to 4000 SSUs
        after_clause = f', after: "{cursor}"' if cursor else ""
        data = _graphql(f"""
        {{
          objects(
            first: 20{after_clause},
            filter: {{ type: "{WORLD_PKG}::storage_unit::StorageUnit" }}
          ) {{
            pageInfo {{ endCursor hasNextPage }}
            nodes {{
              address
              asMoveObject {{
                contents {{ json }}
                dynamicFields(first: 2) {{
                  nodes {{
                    name {{ json }}
                    value {{ ... on MoveValue {{ json }} }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """)
        page = data["objects"]
        for node in page["nodes"]:
            c = node["asMoveObject"]["contents"]["json"]
            if c["key"]["item_id"] != str(game_id):
                continue
            # Found it — parse inventory from dynamic fields
            inventory = []
            df = node["asMoveObject"].get("dynamicFields", {}).get("nodes", [])
            for field in df:
                fval = (field.get("value") or {}).get("json") or {}
                for item in (fval.get("items") or {}).get("contents", []):
                    iv = item["value"]
                    inventory.append({
                        "type_id":  iv.get("type_id"),
                        "quantity": iv.get("quantity"),
                        "volume":   iv.get("volume"),
                    })
            return {
                "game_id":        c["key"]["item_id"],
                "object_address": node["address"],
                "status":         c["status"]["status"].get("@variant", "UNKNOWN"),
                "name":           c.get("metadata", {}).get("name", ""),
                "inventory":      inventory,
            }
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return None


# ---------------------------------------------------------------------------
# Enrichment helpers — resolve IDs to human-readable names
# ---------------------------------------------------------------------------

# Lazy-loaded in-process caches (populated on first use per process)
_system_map: dict | None = None
_type_map:   dict | None = None

_CHAR_CACHE_PATH = Path(".cache/characters.sqlite")


def get_system_map() -> dict:
    """
    Returns {solar_system_id_str: system_name} for all systems.
    Loaded from world_api SQLite cache — fast after first fetch.
    """
    global _system_map
    if _system_map is None:
        sys.path.insert(0, str(Path(__file__).parent))
        import world_api
        _system_map = {str(s["id"]): s["name"] for s in world_api.get_systems()}
    return _system_map


def get_type_map() -> dict:
    """
    Returns {type_id_str: type_name} for all item types.
    Loaded from world_api SQLite cache — fast after first fetch.
    """
    global _type_map
    if _type_map is None:
        sys.path.insert(0, str(Path(__file__).parent))
        import world_api
        _type_map = {str(t["id"]): t["name"] for t in world_api.get_types()}
    return _type_map


def build_char_cache(force: bool = False) -> None:
    """
    Scan all on-chain Character objects and persist to local SQLite cache.
    Safe to call repeatedly — only re-scans if forced or cache is missing.
    ~8,000+ characters; takes 30-90s on first run, instant after.
    """
    _CHAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(_CHAR_CACHE_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            game_id TEXT PRIMARY KEY,
            name    TEXT,
            wallet  TEXT,
            tribe_id TEXT
        )
    """)
    if not force:
        count = db.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
        if count > 0:
            db.close()
            return

    print("Building character name cache (one-time scan, ~30-90s)...", file=sys.stderr)
    cursor = None
    total = 0
    for _ in range(300):
        after = f', after: "{cursor}"' if cursor else ""
        data = _graphql(f"""
        {{
          objects(first: 50{after}, filter: {{ type: "{WORLD_PKG}::character::Character" }}) {{
            pageInfo {{ endCursor hasNextPage }}
            nodes {{
              asMoveObject {{ contents {{ json }} }}
            }}
          }}
        }}
        """)
        rows = []
        for node in data["objects"]["nodes"]:
            c = node["asMoveObject"]["contents"]["json"]
            rows.append((
                c["key"]["item_id"],
                c.get("metadata", {}).get("name", ""),
                c.get("character_address", ""),
                str(c.get("tribe_id", "")),
            ))
        if rows:
            db.executemany(
                "INSERT OR REPLACE INTO characters VALUES (?,?,?,?)", rows
            )
            db.commit()
            total += len(rows)
        if not data["objects"]["pageInfo"]["hasNextPage"]:
            break
        cursor = data["objects"]["pageInfo"]["endCursor"]

    db.close()
    print(f"Character cache built: {total} characters.", file=sys.stderr)


def resolve_characters(game_ids: list[str]) -> dict:
    """
    Resolve a list of game IDs to character names using local SQLite cache.
    Call build_char_cache() first if the cache may not exist.
    Returns {game_id: name} — missing IDs map to the raw game_id string.
    """
    if not _CHAR_CACHE_PATH.exists():
        return {gid: gid for gid in game_ids}
    db = sqlite3.connect(_CHAR_CACHE_PATH)
    placeholders = ",".join("?" for _ in game_ids)
    rows = db.execute(
        f"SELECT game_id, name FROM characters WHERE game_id IN ({placeholders})",
        game_ids,
    ).fetchall()
    db.close()
    result = {gid: gid for gid in game_ids}
    result.update({row[0]: row[1] or row[0] for row in rows})
    return result


# ---------------------------------------------------------------------------
# CLI formatting helpers
# ---------------------------------------------------------------------------

def _json(obj):
    print(json.dumps(obj, indent=2))


def _table(rows, headers):
    if not rows:
        print("(no results)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def _fmt_time(ts: str) -> str:
    """ISO 8601 or Unix seconds → readable UTC timestamp."""
    try:
        if "T" in str(ts):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%m-%d %H:%M:%S")
    except Exception:
        return str(ts)[:19]


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_kills(args):
    loss_type = None
    if args.structure:
        loss_type = "STRUCTURE"
    elif args.ship:
        loss_type = "SHIP"

    kills = get_kills(limit=args.limit, loss_type=loss_type)

    if args.json:
        _json(kills)
        return

    # Always resolve system names (world_api cache — free)
    systems = get_system_map()

    # Optionally resolve character names (local SQLite cache)
    char_map = {}
    if args.names:
        build_char_cache()
        game_ids = list({k["killer_id"] for k in kills} | {k["victim_id"] for k in kills})
        char_map = resolve_characters(game_ids)

    def _char(gid):
        return char_map.get(gid, gid)

    rows = [
        (
            k["kill_id"],
            _fmt_time(k["timestamp"]),
            k["loss_type"],
            _char(k["killer_id"]),
            _char(k["victim_id"]),
            systems.get(k["solar_system_id"], k["solar_system_id"]),
        )
        for k in kills
    ]
    headers = ["Kill ID", "Time (UTC)", "Type",
               "Killer" if args.names else "Killer ID",
               "Victim" if args.names else "Victim ID",
               "System"]
    _table(rows, headers)
    print(f"\n{len(rows)} kills")


# Known deployable type IDs for readable assembly summaries (from World API, 2026-04-04)
_KNOWN_TYPES = {
    # Gates
    "84955": "Heavy Gate",
    "88086": "Mini Gate",
    # Turrets / Defense
    "84556": "Smart Turret",
    "92401": "Turret",
    "92279": "Mini Turret",
    "92404": "Heavy Turret",
    # Storage
    "88083": "Storage",
    "88082": "Mini Storage",
    "77917": "Heavy Storage",
    # Industry
    "88063": "Refinery",
    "88064": "Heavy Refinery",
    "88067": "Printer",
    "87119": "Mini Printer",
    "87120": "Heavy Printer",
    "88068": "Assembler",
    "88069": "Mini Berth",
    "88070": "Berth",
    "88071": "Heavy Berth",
    "91978": "Nursery",
    # Hangars / Shelter
    "88093": "Shelter",
    "88094": "Heavy Shelter",
    "91871": "Nest",
    # Infrastructure
    "88092": "Network Node",
    "90184": "Relay",
    "87160": "Refuge",
    "87161": "Field Refinery",
    "87162": "Field Printer",
    "87566": "Field Storage",
    # Misc
    "88098": "Monolith 1",
    "88099": "Monolith 2",
    "88100": "Wall 1",
    "88101": "Wall 2",
    "89775": "SEER I",
    "89776": "SEER II",
    "89777": "HARBINGER I",
    "89778": "HARBINGER II",
    "89779": "RAINMAKER II",
    "89780": "RAINMAKER I",
    "85291": "Deployable Beacon",
}


def cmd_assemblies(args):
    status_filter = None
    if args.online:
        status_filter = "ONLINE"
    elif args.offline:
        status_filter = "OFFLINE"

    print("Querying all on-chain assemblies (paginating)...", file=sys.stderr)
    assemblies = get_assemblies(status_filter=status_filter)

    if args.json:
        _json(assemblies)
        return

    if args.summary:
        # Count by type_id x status
        from collections import Counter
        counts = Counter()
        for a in assemblies:
            counts[(a["type_id"], a["status"])] += 1
        rows = []
        for (tid, st), n in sorted(counts.items(), key=lambda x: -x[1]):
            name = _KNOWN_TYPES.get(str(tid), f"type {tid}")
            rows.append((name, tid, st, n))
        _table(rows, ["Type", "Type ID", "Status", "Count"])
        print(f"\n{len(assemblies)} total assemblies")
        return

    rows = [
        (a["game_id"], a["type_id"], a["status"], a["object_address"][:20] + "...")
        for a in assemblies
    ]
    _table(rows, ["Game ID", "Type ID", "Status", "Object Address"])

    # Summary footer by type
    from collections import Counter
    by_type = Counter()
    for a in assemblies:
        tid = str(a["type_id"])
        by_type[_KNOWN_TYPES.get(tid, f"type {tid}")] += 1
    print(f"\n{len(rows)} assemblies: " + ", ".join(f"{n}x {t}" for t, n in by_type.most_common(8)))


def cmd_gates(args):
    gates = get_all_gates()

    if args.json:
        _json(gates)
        return

    rows = [
        (
            g["game_id"],
            g["status"],
            "YES" if g["linked_gate_id"] else "no",
            "YES" if g["extension"] else "null",
            g["metadata_name"] or "(unnamed)",
        )
        for g in gates
    ]
    _table(rows, ["Game ID", "Status", "Linked", "Extension", "Name"])
    print(f"\n{len(gates)} player gates total")


def cmd_gate_events(args):
    jumps = get_gate_jumps(limit=args.limit)

    if args.json:
        _json(jumps)
        return

    # Build gate name map for readable output (only 14 gates — fast)
    gate_names = {}
    try:
        for g in get_all_gates():
            label = g["metadata_name"] or f"Gate {g['game_id']}"
            gate_names[g["game_id"]] = label
    except Exception:
        pass  # name resolution best-effort

    def _gate_label(game_id):
        name = gate_names.get(game_id, "")
        return f"{game_id} ({name})" if name and name != f"Gate {game_id}" else str(game_id)

    rows = [
        (
            _fmt_time(j["timestamp"]),
            j["character_game_id"],
            _gate_label(j["source_gate_game_id"]),
            _gate_label(j["dest_gate_game_id"]),
        )
        for j in jumps
    ]
    _table(rows, ["Time (UTC)", "Character ID", "Source Gate", "Dest Gate"])
    print(f"\n{len(rows)} gate jumps")


def cmd_character(args):
    query = args.query
    if query.startswith("0x"):
        char = get_character_by_wallet(query)
        if not char:
            print(f"No character found for wallet {query}", file=sys.stderr)
            sys.exit(1)
    else:
        # Game ID — scan characters to find match
        print(f"Scanning characters for game ID {query}...", file=sys.stderr)
        cursor = None
        char = None
        for _ in range(300):
            after = f', after: "{cursor}"' if cursor else ""
            data = _graphql(f'{{ objects(first:50{after}, filter:{{type:"{WORLD_PKG}::character::Character"}}) {{ pageInfo {{ endCursor hasNextPage }} nodes {{ address asMoveObject {{ contents {{ json }} }} }} }} }}')
            for node in data["objects"]["nodes"]:
                c = node["asMoveObject"]["contents"]["json"]
                if c["key"]["item_id"] == str(query):
                    char = {
                        "char_sui_id":  node["address"],
                        "game_id":      c["key"]["item_id"],
                        "name":         c.get("metadata", {}).get("name", ""),
                        "tribe_id":     c.get("tribe_id"),
                        "wallet":       c.get("character_address"),
                        "owner_cap_id": c.get("owner_cap_id"),
                    }
                    break
            if char or not data["objects"]["pageInfo"]["hasNextPage"]:
                break
            cursor = data["objects"]["pageInfo"]["endCursor"]
        if not char:
            print(f"No character found with game ID {query}", file=sys.stderr)
            sys.exit(1)

    if args.json:
        _json(char)
        return

    print(f"\n  Name:       {char['name']}")
    print(f"  Game ID:    {char['game_id']}")
    print(f"  Tribe ID:   {char['tribe_id']}")
    print(f"  Wallet:     {char['wallet']}")
    print(f"  Owner Cap:  {char['owner_cap_id']}")
    print(f"  Sui Object: {char['char_sui_id']}")


def cmd_network_nodes(args):
    print("Querying network nodes...", file=sys.stderr)
    nodes = get_network_nodes(limit=args.limit)

    if args.json:
        _json(nodes)
        return

    def _fuel_str(n):
        hrs = n["fuel_qty"] / max(n["burn_rate_ms"] / 3_600_000, 0.001)
        return f"{n['fuel_qty']}/{n['fuel_max']} ({hrs:.0f}h)"

    rows = [
        (
            n["game_id"],
            n["status"],
            n["connected_count"],
            _fuel_str(n),
            n["name"] or "(unnamed)",
        )
        for n in nodes
    ]
    _table(rows, ["Game ID", "Status", "Conns", "Fuel (qty/max, hours)", "Name"])
    print(f"\n{len(rows)} network nodes")


def cmd_ssu(args):
    print(f"Scanning SSUs for game ID {args.game_id}...", file=sys.stderr)
    result = get_ssu_inventory(args.game_id)

    if not result:
        print(f"SSU {args.game_id} not found", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _json(result)
        return

    print(f"\n  SSU #{result['game_id']}  '{result['name']}'  {result['status']}")
    print(f"  Sui: {result['object_address']}")
    inv = result["inventory"]
    if not inv:
        print("  (no items in accessible inventory slots)")
    else:
        print(f"\n  {len(inv)} item types:\n")
        rows = [(i["type_id"], i["quantity"], i["volume"]) for i in inv]
        _table(rows, ["Type ID", "Qty", "Vol/unit"])


def cmd_ssu_events(args):
    direction = None
    if args.deposit:
        direction = "deposit"
    elif args.withdraw:
        direction = "withdraw"
    elif args.burn:
        direction = "burn"
    txns = get_ssu_transactions(limit=args.limit, direction=direction)
    if args.json:
        _json(txns)
        return
    rows = [
        (_fmt_time(t["timestamp"]), t["direction"], t["ssu_id"], t["character_id"], t["type_id"], t["quantity"])
        for t in txns
    ]
    _table(rows, ["Time (UTC)", "Direction", "SSU ID", "Char ID", "Type ID", "Qty"])
    print(f"\n{len(rows)} events")


def cmd_char_cache(args):
    if args.rebuild:
        build_char_cache(force=True)
        return
    if _CHAR_CACHE_PATH.exists():
        db = sqlite3.connect(_CHAR_CACHE_PATH)
        count = db.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
        db.close()
        size_kb = _CHAR_CACHE_PATH.stat().st_size // 1024
        print(f"Character cache: {count} characters, {size_kb} KB ({_CHAR_CACHE_PATH})")
    else:
        print("Character cache not built yet. Run: python scripts/sui_chain.py char-cache --rebuild")


def cmd_events(args):
    events = get_module_events(module=args.module, limit=args.limit)

    if args.json:
        _json(events)
        return

    rows = [
        (
            _fmt_time(e["timestamp"]),
            e["tx_digest"][:20] + "...",
            json.dumps(e["payload"])[:70],
        )
        for e in events
    ]
    _table(rows, ["Time (UTC)", "Tx Digest", "Payload (truncated)"])
    print(f"\n{len(rows)} events from module '{args.module}'")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="EVE Frontier Sui Chain — on-chain data client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    p = sub.add_parser("kills", help="Recent kill events from chain")
    p.add_argument("--limit",     type=int, default=20, metavar="N",
                   help="Max results (default: 20)")
    p.add_argument("--structure", action="store_true", help="Structure kills only")
    p.add_argument("--ship",      action="store_true", help="Ship kills only")
    p.add_argument("--names",     action="store_true",
                   help="Resolve character IDs to names (uses local cache, builds on first run)")
    p.add_argument("--json",      action="store_true", help="Output raw JSON")

    p = sub.add_parser("assemblies", help="All on-chain assemblies with status")
    p.add_argument("--online",   action="store_true", help="Online assemblies only")
    p.add_argument("--offline",  action="store_true", help="Offline assemblies only")
    p.add_argument("--summary",  action="store_true", help="Count by type instead of full list")
    p.add_argument("--json",     action="store_true", help="Output raw JSON")

    p = sub.add_parser("gates", help="All player Smart Gate objects (status, link, extension)")
    p.add_argument("--json", action="store_true", help="Output raw JSON")

    p = sub.add_parser("gate-events", help="Recent gate jump traversal events")
    p.add_argument("--limit", type=int, default=20, metavar="N",
                   help="Max results (default: 20)")
    p.add_argument("--json",  action="store_true", help="Output raw JSON")

    p = sub.add_parser("character", help="Look up a character by wallet address or game ID")
    p.add_argument("query", metavar="WALLET_OR_GAME_ID", help="0x... wallet or integer game ID")
    p.add_argument("--json", action="store_true", help="Output raw JSON")

    p = sub.add_parser("network-nodes", help="Network nodes with fuel state")
    p.add_argument("--limit", type=int, default=50, metavar="N",
                   help="Max results (default: 50)")
    p.add_argument("--json",  action="store_true", help="Output raw JSON")

    p = sub.add_parser("ssu", help="SSU inventory by game ID")
    p.add_argument("game_id", metavar="GAME_ID", help="SSU game ID (integer)")
    p.add_argument("--json", action="store_true", help="Output raw JSON")

    p = sub.add_parser("ssu-events", help="SSU item deposit/withdraw events")
    p.add_argument("--limit",     type=int, default=20, metavar="N")
    p.add_argument("--deposit",   action="store_true", help="Deposits only")
    p.add_argument("--withdraw",  action="store_true", help="Withdrawals only")
    p.add_argument("--burn",      action="store_true", help="Burns only")
    p.add_argument("--json",      action="store_true")

    p = sub.add_parser("char-cache", help="Manage local character name cache")
    p.add_argument("--rebuild", action="store_true", help="Force full rescan from chain")

    p = sub.add_parser("events", help="Raw events from any world module")
    p.add_argument("module",  metavar="MODULE",
                   help="Module name: killmail | gate | assembly | turret | character | ...")
    p.add_argument("--limit", type=int, default=20, metavar="N",
                   help="Max results (default: 20)")
    p.add_argument("--json",  action="store_true", help="Output raw JSON")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "kills":         cmd_kills,
        "assemblies":    cmd_assemblies,
        "gates":         cmd_gates,
        "gate-events":   cmd_gate_events,
        "character":     cmd_character,
        "network-nodes": cmd_network_nodes,
        "ssu":           cmd_ssu,
        "ssu-events":    cmd_ssu_events,
        "char-cache":    cmd_char_cache,
        "events":        cmd_events,
    }
    try:
        dispatch[args.cmd](args)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
