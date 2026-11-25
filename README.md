# Makerverse (`#MV3D`)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-00ffd9?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Protocol: #MV3D v2](https://img.shields.io/badge/protocol-%23MV3D%20v2-ff4d94?style=flat-square)](#mv3d-markdown-format)
[![Identity: Ed25519](https://img.shields.io/badge/identity-Ed25519-00e6b8?style=flat-square)](#cryptographic-identity--permissions)
[![Platforms: OpenBSD / FreeBSD / Linux / Windows / macOS](https://img.shields.io/badge/OS-OpenBSD%20%7C%20FreeBSD%20%7C%20Linux%20%7C%20Win%20%7C%20macOS-8fa6c2?style=flat-square)](#quick-start)

A self-hosted, decentralized 3D metaverse platform inspired by 1990s Active Worlds and VRML97, built around human-readable **`#MV3D` Markdown** worlds and avatars, **Ed25519** cryptographic identities, real-time WebSocket multiplayer, and cross-server portal federation.

---

## Repository Layout & Architecture

Makerverse keeps its core stack compact and portable across Unix-like systems (OpenBSD, FreeBSD, Linux) and desktops:

| File / Folder | Role | Key Responsibilities |
| :--- | :--- | :--- |
| **`mv3d.py`** | Scene & Format Engine | Parses `#MV3D` YAML frontmatter + Markdown node trees, resolves `@def`/`@use` blueprints, computes quaternion transforms, validates uploads, mutates placed snippets, and enforces media SSRF rules. |
| **`server.py`** | Headless Multiplayer Node | Runs the `asyncio` WebSocket server (`ws://0.0.0.0:8765`), Ed25519 challenge-response auth, per-world ACLs, persistent trigger states, snapshot backups, federation probing, and a cached HTTP node directory & status page (`http://0.0.0.0:8766/`). |
| **`client.py`** | 3D OpenGL Client | Renders `#MV3D` worlds and custom avatars via OpenGL 3.3 Core shaders, spatial-hash AABB physics, 3D ghost placement (`[B]`), `[I]` inspect/nudge/rotate/scale/delete tools, `Tab` command autocomplete, zero-asset synthesized terminal & warp SFX, `F12` screenshots, push-to-talk spatial voice chat, and live `/watch` hot-reloading. |
| **`build.py`** | Standalone Binary Builder | Compiles `server.py` and `client.py` (bundling `mv3d.py`, `PyNaCl`, and `_cffi_backend`) into standalone single-file executables (`./dist/makerverse-server` and `./dist/makerverse-client`) via PyInstaller. |
| **`test_makerverse.py`** | Smoke & Regression Suite | Exercises `#MV3D` parsing, snippet mutations, `@def` pruning, SSRF guards, persistent trigger reconciliation, and the HTTP status/directory endpoints. |
| **`examples/`** | Sample `#MV3D` Assets | Ready-to-load sample world (`demo_gallery.md`) and multi-expression avatar (`cyber_avatar.md`) for testing `/upload` or `/watch`. |
| **`requirements.txt`** | Python Dependencies | Lists core server & 3D client packages (`websockets`, `PyNaCl`, `pygame-ce`, `PyOpenGL`, `numpy`). |

---

## Quick Start

### 1. Install Dependencies

Install from `requirements.txt` or pick the minimal package set for your target:

```bash
# Install both server and 3D client dependencies
pip install -r requirements.txt

# Or install minimal headless server dependencies only:
pip install websockets PyNaCl

# Optional: push-to-talk spatial voice chat in client.py
pip install sounddevice
```

### 2. Start a Node

```bash
python3 server.py --host 0.0.0.0 --port 8765 --status-port 8766
```

* **WebSocket endpoint:** `ws://localhost:8765`
* **Public Node Directory & Web Status Page:** `http://localhost:8766/` (JSON feed at `/status.json` and `/health`)
* **Persistent storage:** `./makerverse_data/` (override with `--data-dir /path/to/data` or `MAKERVERSE_SERVER_DATA_DIR`)

### 3. Launch the 3D Client

```bash
python3 client.py ws://localhost:8765 --nick YourName
```

Use `--portable` to store identity keys, bookmarks, waypoints, and world caches inside `./makerverse_client_data/` next to `client.py`.

---

## Building Standalone Binaries (`build.py`)

Bundle `server.py` and `client.py` (along with `mv3d.py`, `PyNaCl`, and `cffi`/`_cffi_backend`) into self-contained single-file executables using PyInstaller:

```bash
pip install pyinstaller

# Clean previous build artifacts and build both ./dist/makerverse-server and ./dist/makerverse-client
python3 build.py --clean

# Or build a single target
python3 build.py --server-only
python3 build.py --client-only
```

When run as a compiled binary, `makerverse-server` defaults to storing `./makerverse_data/` alongside the executable unless `--data-dir` or `MAKERVERSE_SERVER_DATA_DIR` is specified.

---

## `#MV3D` Markdown Format

Worlds and avatars are plain `.md` files with optional YAML frontmatter and hierarchical `# Node` / `## Part` headings (see `examples/demo_gallery.md` and `examples/cyber_avatar.md`):

```markdown
---
title: "Arctic Crystal Cavern"
description: "Glacial ice floor with cyan & violet crystal spires"
tags: [crystal, arctic, makerverse]
sky: ["#0f2e61", "#3d7ab3"]
fog: { color: "#153652", range: 160 }
spawn: { pos: [0, 2.0, 12.0], yaw: 0 }
---

# Node: Ground
* **pos**: [0, -0.05, 0]
* **box**: { size: [120, 0.1, 120], color: "#add1eb", glow: "#05050a" }

# @def CrystalPillar
* **cylinder**: { radius: 0.45, height: 4.0, color: "#59ebff", glow: "#1a8cff" }

# Node: PlazaPillar
* **pos**: [4.0, 2.0, -6.0]
* **@use**: CrystalPillar

# Node: GateDoor
* **pos**: [0, 1.5, -10.0]
* **box**: { size: [3.0, 3.0, 0.4], color: "#00ffd9", glow: "#004433" }

# Node: GateSwitch
* **pos**: [-2.5, 1.0, -8.0]
* **trigger**: { action: "move_offset", target: "GateDoor", offset: [0, 3.2, 0], radius: 3.0, mode: "toggle", interact: true, label: "Toggle Gate" }

# Node: FederationPortal
* **pos**: [8.0, 1.2, -8.0]
* **portal**: { to: "hub", server: "ws://peer.example.org:8765", radius: 2.0, color: "#ff26d1" }
```

### Supported Primitives & Blocks
* **Geometry:** `box`, `sphere`, `cylinder`, `cone`, `grid` (heightmap terrain), `mesh` (indexed vertex/face geometry), `sign` (3D floating billboard).
* **Blueprints:** `# @def <Name>` reusable templates instantiated anywhere with `* **@use**: <Name>` (supports nested relative transforms and scaling).
* **Interactivity:** `trigger` (`toggle_glow` or `move_offset`, proximity or `[F]` interact, persisted in `world_meta.json` across sessions), `portal` (local world or cross-server `ws://host:port` hop), and `media` / `screen` / `embed` (`[F]` safe browser launcher + `media_sync`).
* **Avatar Expressions:** Tag sub-parts with `[expr:happy]`, `[expr:angry]`, `[expr:surprised]`, or `[expr:smirk]` and switch moods live with `/mood <expr>`.

---

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
* **Navigation & Federation:** `/worlds [query]`, `/world <name>`, `/spawn`, `/hop <bookmark|ws://host:port> [world]`, `/back`, `/servers`, `/bookmark <name> <url>`, `/nodes`, `/linknode <ws://host:port> [label]`, `/unlinknode <url|label>`
* **World Building:** `/createworld <name> [template] [desc]`, `/templates`, `/ghost [shape|off] [color] [scale]`, `/place <shape> [color] [scale]`, `/placeportal <world|server/world> [color] [radius]`, `/moveobj`, `/rotateobj`, `/scaleobj`, `/paint`, `/cloneobj`, `/delobj`, `/undo`, `/cleanworld`
* **Blueprints & Live Reload:** `/saveprefab <name> [count]`, `/stamp <name> [scale]`, `/delprefab <name>`, `/prefabs`, `/watch <world|avatar> <name> <file.md>`, `/unwatch`
* **World Metadata, Captures & Backups:** `/worldinfo [world]`, `/title`, `/desc`, `/tags`, `/sky <hex>`, `/fog <hex>`, `/radio <url|off>`, `/setspawn`, `/clearspawn`, `/backup [label]`, `/backups`, `/restore <file.md>`, `/export`, `/screenshot`, `/chatlog [n]`, `/rate <1-5> [comment]`, `/topworlds`
* **Audio, Identity, Social & ACLs:** `/sfx` (toggle synthesized typing & warp audio cues), `/voice`, `/mute`, `/nick <name>`, `/avatar <name>`, `/avatars`, `/mood <expr>`, `/friends`, `/addfriend <u>`, `/accept <u>`, `/msg <u> <text>`, `/goto <u>`, `/perms`, `/setrole <u> <admin|builder|guest>`, `/acl`, `/privateworld <on|off>`, `/allowbuilder <u>`, `/allowvisitor <u>`

---

## Cryptographic Identity & Permissions

* Each client generates a local **Ed25519** keypair (`identity.key.hex`) and authenticates via single-use challenge-response nonces.
* The first key to connect to a fresh server automatically claims **`admin`** rank; subsequent users receive `default_role` (`builder` by default, configurable in `makerverse_data/config/server.json` or via `/defaultrole`).
* World creators own their worlds and can grant per-world `co_builders` and private `whitelist` access without needing server-wide admin privileges.

---

## Running Tests

```bash
python3 test_makerverse.py
```

Runs headless verification of `#MV3D` parsing, transforms, `@def` pruning, SSRF URL filtering, persistent trigger state reconciliation, and the HTTP status/directory JSON & HTML endpoints.