#!/usr/bin/env python3
"""
EVE Frontier World API — authoritative data client with local SQLite cache.
Primary data source for all static game data. Cache-first: API is only hit
when data is absent or expired. Stillness (live) environment by default.

Cache location: .cache/world_api.sqlite (project root)
TTLs: types/systems/ships/constellations = 7 days | tribes = 1 hour

Note: list endpoints omit descriptions for many item types; use individual
      lookups (get_type_by_id, get_ship_by_id) when descriptions are needed.

Usage:
  python scripts/world_api.py types                         # category summary
  python scripts/world_api.py types --category Asteroid     # filter by category
  python scripts/world_api.py types --group "Comet Ores"    # filter by group
  python scripts/world_api.py types --search fuel           # partial name search
  python scripts/world_api.py type 77811                    # single type by ID
  python scripts/world_api.py type "Hydrated Sulfide Matrix" # by exact name
  python scripts/world_api.py type fuel                     # partial — shows suggestions
  python scripts/world_api.py system O58-BSK                # system by name
  python scripts/world_api.py system 30020654               # system by ID
  python scripts/world_api.py ships                         # list all ship hulls
  python scripts/world_api.py ship 87847                    # full ship stats by ID
  python scripts/world_api.py tribes                        # all player tribes
  python scripts/world_api.py tribe ICA                     # tribe by short name
  python scripts/world_api.py ores                          # body type → ore map
  python scripts/world_api.py cache-info                    # cache stats
  python scripts/world_api.py cache-clear                   # wipe cache
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import requests_cache

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE = "https://world-api-stillness.live.tech.evefrontier.com"
BASE_UTOPIA = "https://world-api-utopia.uat.pub.evefrontier.com"

CACHE_PATH = Path(__file__).parent.parent / ".cache" / "world_api"

TTL_7D = 7 * 24 * 3600   # types, systems, ships, constellations
TTL_1H = 3600             # tribes (player corps register/update)
TTL_1D = 86400            # config, anything else

URLS_EXPIRE = {
    f"{BASE}/v2/tribes*":         TTL_1H,
    f"{BASE}/v2/solarsystems*":   TTL_7D,
    f"{BASE}/v2/types*":          TTL_7D,
    f"{BASE}/v2/ships*":          TTL_7D,
    f"{BASE}/v2/constellations*": TTL_7D,
    "*": TTL_1D,
}

session = requests_cache.CachedSession(
    str(CACHE_PATH),
    backend="sqlite",
    urls_expire_after=URLS_EXPIRE,
)


# ---------------------------------------------------------------------------
# Core fetch helpers
# ---------------------------------------------------------------------------

def _get(path, params=None, base=BASE):
    r = session.get(f"{base}{path}", params=params or {})
    r.raise_for_status()
    return r.json()


def _get_all(path, base=BASE, page_size=500):
    """Fetch all pages of a paginated collection endpoint."""
    results = []
    offset = 0
    total = None
    while total is None or offset < total:
        data = _get(path, params={"limit": page_size, "offset": offset}, base=base)
        results.extend(data["data"])
        total = data["metadata"]["total"]
        offset += len(data["data"])
        if not data["data"]:
            break
    return results


# ---------------------------------------------------------------------------
# Public API functions (importable from other scripts)
# ---------------------------------------------------------------------------

def get_types(category=None, group=None, search=None):
    """All item types, optionally filtered by category, group, or name search (contains)."""
    types = _get_all("/v2/types")
    if category:
        types = [t for t in types if t["categoryName"].lower() == category.lower()]
    if group:
        types = [t for t in types if t["groupName"].lower() == group.lower()]
    if search:
        types = [t for t in types if search.lower() in t["name"].lower()]
    return types


def get_type_by_id(type_id):
    """Single type by numeric ID. Includes full description."""
    return _get(f"/v2/types/{int(type_id)}")


def get_type_by_name(name):
    """Find type(s) by exact name — case-insensitive, returns list."""
    name_lower = name.lower()
    return [t for t in get_types() if t["name"].lower() == name_lower]


def get_systems():
    """All 24,502 solar systems. ~50 cached pages; instant after first run."""
    return _get_all("/v2/solarsystems")


def get_system_by_id(system_id):
    """Single system by numeric ID — includes gateLinks (CCP gates only)."""
    return _get(f"/v2/solarsystems/{int(system_id)}")


def get_system_by_name(name):
    """Find system(s) by exact name — searches full cached list, returns with gateLinks."""
    name_lower = name.lower()
    matches = [s for s in get_systems() if s["name"].lower() == name_lower]
    if len(matches) == 1:
        # Fetch detail to include gateLinks
        return [get_system_by_id(matches[0]["id"])]
    return matches


def get_ships():
    """All ship hulls (catalog only — use get_ship_by_id for full stats)."""
    return _get_all("/v2/ships")


def get_ship_by_id(ship_id):
    """Full ship stats: slots, HP, fuel capacity, physics, damage resistances."""
    return _get(f"/v2/ships/{int(ship_id)}")


def get_tribes():
    """All registered tribes. 1-hour TTL."""
    return _get_all("/v2/tribes")


def get_tribe_by_name(name):
    """Find tribe by full name or short name — case-insensitive."""
    name_lower = name.lower()
    return [
        t for t in get_tribes()
        if t["name"].lower() == name_lower or t["nameShort"].lower() == name_lower
    ]


def get_constellations():
    """All constellations, each including their solar system list."""
    return _get_all("/v2/constellations")


def get_config():
    """API config — returns podPublicSigningKey for EVE Vault POD verification."""
    data = _get("/config")
    # API returns a list with one entry
    return data[0] if isinstance(data, list) and data else data


# ---------------------------------------------------------------------------
# Derived lookups
# ---------------------------------------------------------------------------

def get_ore_map():
    """
    Body type → {body, ores} mapping for all asteroid types.

    Two group layouts in the data:
      Standard: body item in group "Slag" + ore items in group "Slag Ores"
      Flat:     all items share one group name (Rift, Synthetic Hermetite,
                Deep-Core Carbon Asteroid) — no distinct body type item.

    Body type items have IDs in the 91370–91389 range.
    """
    asteroids = get_types(category="Asteroid")

    BODY_ID_MIN, BODY_ID_MAX = 91370, 91390
    body_by_group = {
        t["groupName"]: t for t in asteroids
        if BODY_ID_MIN <= t["id"] <= BODY_ID_MAX
    }

    ores_by_body = {}
    for t in asteroids:
        if t["groupName"].endswith(" Ores"):
            key = t["groupName"].replace(" Ores", "")
            ores_by_body.setdefault(key, []).append(t)

    # Groups with no "[Body] Ores" counterpart — all items are ores
    skip = set(body_by_group) | {g + " Ores" for g in body_by_group} | {"Natural Resources"}
    flat_groups = {}
    for t in asteroids:
        if t["groupName"] not in skip:
            flat_groups.setdefault(t["groupName"], []).append(t)

    result = {}
    for group, body in body_by_group.items():
        result[group] = {"body": body, "ores": ores_by_body.get(group, [])}
    for group, items in flat_groups.items():
        result[group] = {"body": None, "ores": items}

    return result


# ---------------------------------------------------------------------------
# Cache utilities
# ---------------------------------------------------------------------------

def cache_info():
    """Cache statistics — entry counts and per-endpoint TTL breakdown."""
    responses = session.cache.responses
    keys = list(responses.keys())
    now = datetime.now(timezone.utc)

    expired = []
    by_endpoint = {}
    for key in keys:
        resp = responses[key]
        path = resp.url.replace(BASE, "").split("?")[0]
        by_endpoint.setdefault(path, {"count": 0, "expires": None})
        by_endpoint[path]["count"] += 1
        if resp.expires and (by_endpoint[path]["expires"] is None
                             or resp.expires < by_endpoint[path]["expires"]):
            by_endpoint[path]["expires"] = resp.expires
        if resp.is_expired:
            expired.append(key)

    return {
        "cache_file": str(CACHE_PATH) + ".sqlite",
        "total_entries": len(keys),
        "expired_entries": len(expired),
        "fresh_entries": len(keys) - len(expired),
        "endpoints": by_endpoint,
    }


def cache_clear():
    session.cache.clear()


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


def _fmt_ttl(expires):
    """Human-readable TTL remaining: '6d 23h', '52m', 'EXPIRED'."""
    if expires is None:
        return "never"
    now = datetime.now(timezone.utc)
    delta = (expires - now).total_seconds()
    if delta <= 0:
        return "EXPIRED"
    if delta >= 86400:
        return f"{int(delta // 86400)}d {int((delta % 86400) // 3600)}h"
    if delta >= 3600:
        return f"{int(delta // 3600)}h {int((delta % 3600) // 60)}m"
    return f"{int(delta // 60)}m"


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_types(args):
    types = get_types(category=args.category, group=args.group, search=args.search)
    if args.json:
        _json(types)
        return

    # No filters: show category summary instead of 392-row wall
    if not args.category and not args.group and not args.search:
        from collections import Counter
        cats = Counter(t["categoryName"] for t in types)
        print(f"\n{len(types)} types across {len(cats)} categories:\n")
        for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"  {cat:<18} {count:>3}")
        print(f"\nFilter with --category, --group, or --search to see items.")
        return

    rows = [(t["id"], t["categoryName"], t["groupName"], t["name"]) for t in types]
    rows.sort(key=lambda r: (r[1], r[2], r[3]))
    if rows:
        _table(rows, ["ID", "Category", "Group", "Name"])
    print(f"\n{len(rows)} types")


def cmd_type(args):
    """Look up by numeric ID, exact name, or partial name (contains)."""
    try:
        type_id = int(args.query)
    except ValueError:
        type_id = None

    if type_id is not None:
        try:
            _json(get_type_by_id(type_id))
            return
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                print(f"No type found with ID: {type_id}", file=sys.stderr)
            else:
                print(f"API error: {e}", file=sys.stderr)
            sys.exit(1)

    # Exact match first
    matches = get_type_by_name(args.query)
    if matches:
        _json(matches if len(matches) > 1 else matches[0])
        return

    # Fall back to contains search
    suggestions = get_types(search=args.query)
    if not suggestions:
        print(f"No type found: {args.query!r}", file=sys.stderr)
        sys.exit(1)
    if len(suggestions) == 1:
        _json(get_type_by_id(suggestions[0]["id"]))
        return
    # Multiple partial matches — show a table of options
    rows = [(t["id"], t["categoryName"], t["groupName"], t["name"]) for t in suggestions]
    rows.sort(key=lambda r: (r[1], r[2], r[3]))
    print(f"No exact match for {args.query!r}. Did you mean one of these?\n")
    _table(rows, ["ID", "Category", "Group", "Name"])
    print(f"\n{len(rows)} matches -- use the ID or exact name to look up.")
    sys.exit(1)


def cmd_system(args):
    """Look up by numeric ID or exact name."""
    try:
        system_id = int(args.query)
    except ValueError:
        system_id = None

    if system_id is not None:
        try:
            _json(get_system_by_id(system_id))
            return
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                print(f"No system found with ID: {system_id}", file=sys.stderr)
            else:
                print(f"API error: {e}", file=sys.stderr)
            sys.exit(1)

    matches = get_system_by_name(args.query)
    if not matches:
        print(f"No system found: {args.query!r}", file=sys.stderr)
        sys.exit(1)
    _json(matches if len(matches) > 1 else matches[0])


def cmd_ships(args):
    ships = get_ships()
    if args.json:
        _json(ships)
        return
    rows = [(s["id"], s["className"], s["name"]) for s in ships]
    rows.sort(key=lambda r: (r[1], r[2]))
    _table(rows, ["ID", "Class", "Name"])
    print(f"\nUse 'ship <id>' for full stats (slots, HP, fuel capacity, physics)")


def cmd_ship(args):
    """Full stats for a single ship by ID."""
    try:
        ship_id = int(args.query)
    except ValueError:
        print(f"Ship ID must be numeric (got: {args.query!r})", file=sys.stderr)
        sys.exit(1)
    try:
        ship = get_ship_by_id(ship_id)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            print(f"No ship found with ID: {ship_id}", file=sys.stderr)
        else:
            print(f"API error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _json(ship)
        return

    print(f"\n{ship['name']}  [{ship['className']}]  ID: {ship['id']}")
    print(f"  {ship.get('description', '').splitlines()[0][:80]}" if ship.get('description') else "")
    slots = ship.get("slots", {})
    print(f"\n  Slots     high={slots.get('high',0)}  med={slots.get('medium',0)}  low={slots.get('low',0)}")
    hp = ship.get("health", {})
    print(f"  HP        shield={hp.get('shield',0)}  armor={hp.get('armor',0)}  structure={hp.get('structure',0)}")
    print(f"  Fuel cap  {ship.get('fuelCapacity', 'N/A')}")
    phys = ship.get("physics", {})
    print(f"  Max vel   {phys.get('maximumVelocity', 'N/A')} m/s")
    print(f"  CPU       {ship.get('cpuOutput', 'N/A')}  PG {ship.get('powergridOutput', 'N/A')}")
    cap = ship.get("capacitor", {})
    print(f"  Capacitor {cap.get('capacity', 'N/A')} (recharge {cap.get('rechargeRate', 'N/A')})")


def cmd_tribes(args):
    tribes = get_tribes()
    if not args.all:
        tribes = [t for t in tribes if t["id"] >= 98000000]
    if args.json:
        _json(tribes)
        return
    rows = [(t["id"], t["nameShort"], t["name"], f"{t['taxRate']*100:.0f}%") for t in tribes]
    rows.sort(key=lambda r: r[1].lower())
    _table(rows, ["ID", "Short", "Name", "Tax"])
    print(f"\n{len(rows)} tribes")


def cmd_tribe(args):
    matches = get_tribe_by_name(args.query)
    if not matches:
        print(f"No tribe found: {args.query!r}", file=sys.stderr)
        sys.exit(1)
    _json(matches if len(matches) > 1 else matches[0])


def cmd_ores(args):
    ore_map = get_ore_map()
    if args.json:
        _json(ore_map)
        return
    rows = []
    for body_name, entry in sorted(ore_map.items()):
        body_id   = entry["body"]["id"] if entry["body"] else "(flat)"
        ore_names = ", ".join(o["name"] for o in entry["ores"]) or "(unknown)"
        ore_ids   = ", ".join(str(o["id"]) for o in entry["ores"]) or "-"
        rows.append((body_id, body_name, ore_names, ore_ids))
    _table(rows, ["Body ID", "Body Type", "Ore(s)", "Ore ID(s)"])


def cmd_cache_info(_args):
    info = cache_info()
    print(f"  {'Cache file':<22} {info['cache_file']}")
    print(f"  {'Total entries':<22} {info['total_entries']}")
    print(f"  {'Fresh':<22} {info['fresh_entries']}")
    print(f"  {'Expired':<22} {info['expired_entries']}")
    print()
    print(f"  {'Endpoint':<40}  {'Entries':>7}  {'Expires in':>12}")
    print(f"  {'-'*40}  {'-'*7}  {'-'*12}")
    for path, ep in sorted(info["endpoints"].items()):
        ttl_str = _fmt_ttl(ep["expires"])
        print(f"  {path:<40}  {ep['count']:>7}  {ttl_str:>12}")


def cmd_cache_clear(_args):
    cache_clear()
    print("Cache cleared.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="EVE Frontier World API — cached data client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    p = sub.add_parser("types", help="List item types (no args = category summary)")
    p.add_argument("--category", metavar="NAME", help="Filter by category (e.g. Asteroid)")
    p.add_argument("--group",    metavar="NAME", help="Filter by group (e.g. 'Comet Ores')")
    p.add_argument("--search",   metavar="TEXT", help="Partial name search (contains)")
    p.add_argument("--json",     action="store_true", help="Output raw JSON")

    p = sub.add_parser("type", help="Look up a type by ID or exact name")
    p.add_argument("query", metavar="ID_OR_NAME")

    p = sub.add_parser("system", help="Look up a solar system by name or ID")
    p.add_argument("query", metavar="NAME_OR_ID")

    p = sub.add_parser("ships", help="List all ship hulls")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("ship", help="Full stats for one ship by ID")
    p.add_argument("query", metavar="ID")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("tribes", help="List player tribes")
    p.add_argument("--all",  action="store_true", help="Include NPC corps")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("tribe", help="Look up a tribe by name or short name")
    p.add_argument("query", metavar="NAME_OR_SHORT")

    p = sub.add_parser("ores", help="Body type → ore mapping")
    p.add_argument("--json", action="store_true")

    sub.add_parser("cache-info",  help="Show cache stats and TTL breakdown")
    sub.add_parser("cache-clear", help="Wipe the local cache")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "types":       cmd_types,
        "type":        cmd_type,
        "system":      cmd_system,
        "ships":       cmd_ships,
        "ship":        cmd_ship,
        "tribes":      cmd_tribes,
        "tribe":       cmd_tribe,
        "ores":        cmd_ores,
        "cache-info":  cmd_cache_info,
        "cache-clear": cmd_cache_clear,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
