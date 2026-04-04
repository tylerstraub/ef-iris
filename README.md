# ef-iris — EVE Frontier IRIS Skills

Claude Code workspace with two skills for querying live EVE Frontier game data:

- **world-api** — CCP's authoritative game data (item types, ore mappings, systems, ships, tribes). Cache-first SQLite, essentially free after first run.
- **sui-chain** — Live Sui blockchain queries (kills, gate state, assembly status, character lookup, SSU inventory, network node fuel).

## Setup

**Requirements:** Python 3.9+, pip

```bash
git clone <this-repo>
cd ef-iris
pip install -r requirements.txt
```

That's it. No API keys, no accounts. Both skills hit public endpoints.

First run of `world-api` fetches ~24,500 systems and caches them locally (`~30s`). All subsequent calls are instant SQLite reads.

First run of `sui-chain char-cache` builds a local character name cache (~8,000 players, ~30–90s). Optional — only needed for resolving character IDs to names in kill feeds.

## Usage

Open this folder in Claude Code. The skills activate automatically. Just ask questions:

> "Show me the last 20 kills"
> "What does a Comet body yield?"
> "Which smart gates are currently online?"
> "Look up character by wallet 0x..."
> "What's the item type ID for D1 Fuel?"

Claude will call the right skill and script for you.

## Direct CLI Use

Both scripts also work standalone from the project root:

```bash
python scripts/world_api.py types --category Asteroid
python scripts/world_api.py system O58-BSK
python scripts/sui_chain.py kills --names
python scripts/sui_chain.py gates
```

See each script's `--help` or the skill docs in `.claude/skills/` for the full command reference.

## What Gets Cached

| Cache | Location | TTL |
|-------|----------|-----|
| World API data (types, systems, ships) | `.cache/world_api.sqlite` | 7 days |
| Tribes | `.cache/world_api.sqlite` | 1 hour |
| Character names | `.cache/char_cache.sqlite` | Manual rebuild |

`.cache/` is gitignored — each user builds their own local cache.

## Source

These skills are maintained in [SByFrontier](https://github.com/TylerSByFrontier) and synced here via `scripts/sync-ef-iris.py`. Do not edit `scripts/` or `.claude/skills/` directly in this repo — changes will be overwritten on next sync.
