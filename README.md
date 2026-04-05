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

> **Platform note:** Tested on Windows 11 / Python 3.13. Should work on macOS/Linux — substitute `python3` for `python` in all commands if needed. No OS-specific dependencies.

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
python scripts/world_api.py system EQN-M88
python scripts/sui_chain.py kills --names
python scripts/sui_chain.py gates
```

See the skill docs in `.claude/skills/` for the full command reference.

## What Gets Cached

| Cache | Location | TTL |
|-------|----------|-----|
| World API data (types, systems, ships) | `.cache/world_api.sqlite` | 7 days |
| Tribes | `.cache/world_api.sqlite` | 1 hour |
| Character names | `.cache/characters.sqlite` | Manual rebuild |

`.cache/` is gitignored — each user builds their own local cache.

## Source

`scripts/` and `.claude/skills/` are synced from an upstream private repo. Do not edit them directly here — changes will be overwritten on next sync.
