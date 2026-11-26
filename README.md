# Makerverse (`#MV3D`)

A self-hosted, decentralized 3D metaverse platform inspired by 1990s Active Worlds and VRML97, built around human-readable **`#MV3D` Markdown** worlds and avatars, **Ed25519** cryptographic identities, real-time WebSocket multiplayer, and cross-server portal federation.

## Live Public Demo Server

Want to jump in right away without hosting your own node first? Connect to the live community demo server:

* **WebSocket Address:** `ws://whitenode45.ddns.net:8765`
* **Node Directory & Status Page:** `http://whitenode45.ddns.net:8766/`

```bash
python3 client.py ws://whitenode45.ddns.net:8765 --nick YourName
```

*(Or from inside any running client, type `/hop whitenode45.ddns.net:8765`.)*

## Repository Layout & Architecture

| File / Folder | Role | Key Responsibilities |
| :--- | :--- | :--- |
| **`mv3d.py`** | Scene & Format Engine | Parses `#MV3D` frontmatter + Markdown node trees, resolves `@def`/`@use` blueprints, computes quaternion transforms, validates uploads, mutates placed snippets, and enforces media SSRF rules. |
| **`server.py`** | Headless Multiplayer Node | Runs the `asyncio` WebSocket server (`ws://0.0.0.0:8765`), Ed25519 challenge-response auth, per-world ACLs, persistent trigger states, snapshot backups & diffs, federation probing, and a cached HTTP node directory & status page (`http://0.0.0.0:8766/`). |
| **`client.py`** | 3D OpenGL Client | Renders `#MV3D` worlds and custom avatars via OpenGL 3.3 Core shaders, spatial-hash AABB physics, 3D ghost placement (`[B]`, including portals and blueprints), `[I]` inspect tools, `Tab` command autocomplete, zero-asset synthesized terminal/step/proximity/warp SFX, `F12` screenshots, push-to-talk spatial voice chat, and live `/watch` hot-reloading. |
| **`build.py`** | Standalone Binary Builder | Compiles `server.py` and `client.py` (bundling `mv3d.py`, `PyNaCl`, and `_cffi_backend`) into standalone single-file executables (`./dist/makerverse-server` and `./dist/makerverse-client`) via PyInstaller. |
| **`examples/`** | Sample `#MV3D` Assets | Ready-to-load sample world (`demo_gallery.md`) and multi-expression avatar (`cyber_avatar.md`) for testing `/upload` or `/watch`. |
| **`requirements.txt`** | Python Dependencies | Lists core server & 3D client packages (`websockets`, `PyNaCl`, `pygame-ce`, `PyOpenGL`, `numpy`). |
| **`.gitignore`** | Git Ignore Rules | Keeps build artifacts (`build/`, `dist/`, `*.spec`), local data directories (`makerverse_data/`, `makerverse_client_data/`, `.makerverse/`), and keys out of version control. |

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt

# Optional: push-to-talk spatial voice chat in client.py
pip install sounddevice
```

### 2. Start a Node

```bash
python3 server.py --host 0.0.0.0 --port 8765 --status-port 8766
```

### 3. Launch the 3D Client

```bash
python3 client.py ws://localhost:8765 --nick YourName
```

Use `--portable` to store identity keys, bookmarks, waypoints, and world caches inside `./makerverse_client_data/` next to `client.py`.

## Building Standalone Binaries (`build.py`)

```bash
pip install pyinstaller
python3 build.py --clean
```

## Controls & In-World Commands

### Keybindings

| Key | Action |
| :--- | :--- |
| `W A S D` / Arrows | Move (`Space` jump / fly up, `Shift` fly down) |
| `T` / `/` / `Enter` | Open chat / command input (`Tab` autocomplete, `Up`/`Down` history, `Ctrl+V`/`C`/`X` clipboard) |
| `B` | Toggle 3D Ghost Placement Preview & cycle shapes (`LMB` place, `Scroll` distance, `[` `]` scale, `R` rotate) |
| `I` | Toggle Inspect Mode (`PgUp`/`PgDn` nudge Y, `R` rotate 45 deg, `-`/`=` scale, `Del` remove) |
| `F` | Interact with nearby `[F]` trigger switch or embedded media screen |
| `G` | Toggle 0.5m grid snap |
| `V` | Cycle camera (`first-person` -> `third-person` -> `front-view`) |
| `1`–`4` | Avatar emotes (`wave`, `dance`, `spin`, `jump`) |
| `Left/Right Alt` | Hold for Push-to-Talk spatial voice chat (enable with `/voice`) |
| `F12` | Save PNG screenshot of the 3D viewport to `exports/` |
| `H` / `Esc` | Toggle HUD visibility / release mouse grab |

### Slash Commands

* **Navigation & Federation:** `/worlds [query]`, `/world <name>`, `/spawn`, `/hop <bookmark|ws://host:port> [world]`, `/back`, `/servers`, `/bookmark <name> <url>`, `/unbookmark <name|url>`, `/nodes`, `/linknode <ws://host:port> [label]`, `/unlinknode <url|label>`
* **World Building:** `/createworld <name> [template] [desc]`, `/templates`, `/ghost [shape|portal|prefab|off] [color] [scale]`, `/place <shape> [color] [scale]`, `/placeportal <world|server/world> [color] [radius]`, `/moveobj`, `/rotateobj`, `/scaleobj`, `/paint`, `/cloneobj`, `/delobj`, `/undo`, `/cleanworld`, `/snap [m]`
* **Blueprints & Live Reload:** `/saveprefab <name> [count]`, `/stamp <name> [scale]`, `/delprefab <name>`, `/prefabs`, `/watch <world|avatar> <name> <file.md>`, `/unwatch`, `/upload <world|avatar> [name] <file.md>`
* **World Metadata, Captures & Backups:** `/worldinfo [world]`, `/title`, `/desc`, `/tags`, `/sky <hex>`, `/fog <hex>`, `/radio <url|off>`, `/setspawn`, `/clearspawn`, `/backup [label]`, `/backups`, `/diff [file.md]`, `/restore <file.md>`, `/export`, `/screenshot`, `/chatlog [n]`, `/clear`, `/rate <1-5> [comment]`, `/topworlds`
* **Audio, Identity, Social & ACLs:** `/sfx`, `/voice`, `/mute`, `/nick <name>`, `/key`, `/avatar <name>`, `/avatars`, `/mood <expr>`, `/friends`, `/addfriend <u>`, `/accept <u>`, `/msg <u> <text>`, `/whisper <u> <text>`, `/goto <u>`, `/block <u>`, `/unblock <u>`, `/blocked`, `/waypoint`, `/waypoints`, `/delwaypoint`, `/clearwaypoints`, `/tpwp`, `/perms`, `/setrole <u> <admin|builder|guest>`, `/acl`, `/privateworld <on|off>`, `/allowbuilder <u>`, `/allowvisitor <u>`, `/fly`, `/cam`, `/help`, `/quit`