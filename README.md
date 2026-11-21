# Makerverse (`#MV3D`)

**A self-hosted, decentralized 3D Markdown metaverse engine and multiplayer client — the spiritual and technical successor to the `cyberx87` metaverse.**

Makerverse replaces proprietary binary world formats with **`#MV3D` (Markdown 3D)**: human-readable `.md` files with YAML-style frontmatter and hierarchical Markdown headings that compile directly into interactive 3D worlds, custom avatars, reusable blueprints, portals, and triggers.

---

## Successor to `cyberx87`

Built as the next-generation evolution of the **cyberx87** metaverse, Makerverse carries forward the lightweight, creator-owned, self-hostable ethos of `cyberx87` while introducing:
- **Pure Markdown Scene Graph (`#MV3D`)**: Author worlds, avatars, and prefabs in plain `.md` files using any text editor—or build live in-world and export straight back to Markdown.
- **Ed25519 Cryptographic Identity**: Passwordless public-key authentication (`identity.key.hex`) that travels with you across federated nodes.
- **Modern OpenGL 3.30 Core Renderer**: Hardware-accelerated 3D rendering with dynamic fog, sky gradients, 3D sign billboards, spatial hash collision/raycasting, and first/third/front camera modes.
- **Cross-Server Portals & Federation**: Seamless node-to-node server hopping (`/hop`), bookmarking, and portals with target spawn coordinates and server URLs.

---

## Features

- **Worlds & Templates**: Create worlds from 8 built-in themes (`flat`, `cybercity`, `forest`, `desert`, `crystal`, `fortress`, `island`, `space`) or upload custom `.md` scenes.
- **Live In-World Building & Blueprints**:
  - Place primitives (`box`, `pillar`, `sphere`, `cone`, `arch`, `platform`, `wall`), move, rotate, scale, paint, clone, delete, and undo edits in real time.
  - Save multi-block creations into reusable `# @def` blueprints (`/saveprefab`) and stamp them anywhere (`/stamp`).
  - Toggle grid & 45° angle snapping (`/snap`, `[G]`) and hot-reload local `.md` files automatically on save (`/watch`).
- **Interactive `#MV3D` Mechanics**:
  - Proximity and `[F]`-interact triggers (`toggle_glow`, `move_offset`) with `once` or `toggle` modes and configurable cooldowns.
  - World portals supporting local world transitions, custom target spawn coordinates/yaw, and cross-server hops.
  - Streaming world radio (`/radio`) with SSRF/private-IP protection and local mute toggle.
- **Social, Discovery & Moderation**:
  - Cryptographic Ed25519 identities, persistent nicknames, custom `.md` avatars, and emotes (`wave`, `dance`, `spin`, `jump`).
  - Friends list, direct whispers (`/msg`), friend teleport (`/goto`), local player blocking (`/block`), 3D HUD waypoints (`/waypoint`), and world search/ratings (`/worlds`, `/rate`, `/topworlds`).
  - Role-based permissions (`admin`, `builder`, `guest`), per-world ACLs (private worlds, co-builders, visitor whitelists), automatic snapshots, backups, and `.md` exports.
- **Portable Client Architecture**:
  - Cross-platform support (Windows, macOS, Linux) with automatic font/audio player fallbacks, resizable OpenGL window, and `--portable` / `--data-dir` self-contained storage.

---

## Project Structure

```text
makerverse/
├── mv3d.py      # #MV3D Markdown parser, validator, math utilities, templates & blueprint tools
├── server.py    # Async WebSocket metaverse server, Ed25519 auth, ACLs, backups & federation
└── client.py    # Pygame + OpenGL 3.30 3D client, physics/raycast engine, HUD & live file watcher
```

---

## Quick Start

### 1. Requirements
- **Python 3.10+**
- Install dependencies:
  ```bash
  pip install websockets pynacl numpy pygame PyOpenGL
  ```
- *(Optional, for world radio streams)*: `ffplay`, `mpv`, or `vlc` available on your `PATH`.

### 2. Run the Server
```bash
python server.py
```
- Starts the WebSocket server on `ws://0.0.0.0:8765`.
- Automatically initializes `./makerverse_data/` with the default `hub.md` world, permissions, backups, prefabs, and sample plugins.
- **Note**: The first Ed25519 key to authenticate on a fresh server is automatically granted `admin` privileges.

### 3. Run the Client
```bash
python client.py
```
Optional CLI flags and environment variables:
```bash
# Connect to a specific server and set a nickname
python client.py --server ws://your-server:8765 --nick CyberBuilder

# Run in portable mode (stores keys, cache, and exports in ./makerverse_client_data/)
python client.py --portable

# Custom client data directory
python client.py --data-dir /path/to/data
```
Supported environment variables: `MAKERVERSE_SERVER`, `MAKERVERSE_DATA_DIR`.

---

## Controls

| Key | Action |
| :--- | :--- |
| `W A S D` / `Arrow Keys` | Move |
| `Mouse` | Look around |
| `Space` | Jump (or ascend in Fly mode) |
| `Left Shift` | Descend in Fly mode |
| `T` / `Enter` | Open chat |
| `/` | Open command prompt |
| `F` | Interact with nearby switch / trigger |
| `I` | Toggle Inspect Mode (highlights targeted object & coordinates) |
| `R` | Rotate targeted placed object by `45°` (while in Inspect Mode) |
| `G` | Toggle `0.5m` grid & `45°` angle snapping |
| `V` | Cycle camera view (`First-Person` → `Third-Person` → `Front`) |
| `1` – `4` | Trigger emotes (`1`: Wave, `2`: Dance, `3`: Spin, `4`: Jump) |
| `H` | Toggle HUD visibility |
| `Esc` | Release / recapture mouse cursor |

---

## In-Game Commands

### Navigation, Discovery & Social
- `/worlds [query]` — List or filter accessible worlds by name, tag, description, or creator
- `/world <name>` — Join a world on the current server
- `/worldinfo [world]` — View world metadata, creator, spawn coordinates, and ratings
- `/rate <1-5> [note]` / `/topworlds` — Rate the current world or view top-rated worlds
- `/spawn` — Teleport back to the current world's spawn point
- `/waypoint <name> [x y z]` / `/delwaypoint <name>` / `/waypoints` — Manage 3D HUD waypoints
- `/hop <bookmark|ws://host:port> [world]` / `/back` — Jump to another Makerverse server or return
- `/servers` / `/bookmark <name> <url>` / `/unbookmark <name>` — Manage server bookmarks
- `/friends` / `/addfriend <user>` / `/accept <user>` / `/goto <user>` — Friend system & teleport
- `/msg <user> <text>` — Send a private whisper to an online friend
- `/block <user>` / `/unblock <user>` / `/blocked` — Locally mute chat and whispers from a user
- `/nick <name>` / `/key` / `/fly` / `/cam` / `/emote <wave|dance|spin|jump>`

### Building, Blueprints & Authoring
- `/createworld <name> [template] [desc]` — Create a new world from a template
- `/place <shape> [color] [scale]` — Place `box`, `pillar`, `sphere`, `cone`, `arch`, `platform`, or `wall`
- `/moveobj <dx> <dy> <dz>` — Move targeted placed object (aim with `[I]`)
- `/rotateobj <degrees>` — Rotate targeted placed object around Y-axis
- `/scaleobj <multiplier>` — Scale targeted placed object
- `/paint <color|#hex>` — Repaint targeted placed object
- `/cloneobj [dx dy dz]` — Duplicate targeted placed object
- `/delobj [id]` / `/undo` — Delete targeted placed object or undo last world edit
- `/snap [meters|off]` — Set or toggle grid snapping
- `/saveprefab <name> [count]` / `/stamp <name> [scale]` / `/prefabs` — Save and stamp reusable blueprints
- `/setspawn [x y z] [yaw]` / `/clearspawn` — Set or clear the world's spawn point
- `/watch <world|avatar> <name> <file.md>` / `/unwatch` — Live hot-reload a local `.md` file on save
- `/upload <world|avatar> <name> <file.md>` — Upload a `#MV3D` world or avatar file
- `/avatars` / `/avatar <name|default>` — List or equip custom avatars
- `/backup [label]` / `/backups` / `/restore <file.md>` / `/export` — Manage world snapshots & local `.md` exports

### World Settings, ACLs & Server Admin
- `/desc [world] <text|clear>` / `/sky <hex>` / `/fog <hex>` — Customize world description & atmosphere
- `/radio <stream_url|off>` / `/mute` — Set world audio stream or toggle local mute
- `/acl [world]` / `/privateworld <on|off>` — Inspect or toggle private world visibility
- `/allowbuilder <user>` / `/removebuilder <user>` — Manage world co-builders
- `/allowvisitor <user>` / `/revokevisitor <user>` — Manage private world visitor whitelist
- `/perms` / `/admin <user>` / `/grant <user>` / `/ban <user>` / `/defaultrole <guest|builder>` — Server administration

---

## `#MV3D` Format Example

Worlds and avatars are standard Markdown files. Save the example below as `my_world.md` and load it with `/upload world my_world my_world.md` or `/watch world my_world my_world.md`:

```markdown
---
title: "Cyberx87 Memorial Plaza"
description: "A neon sanctuary built in #MV3D"
tags: [cyberpunk, plaza, hub]
sky: ["#050a17", "#140b2e"]
fog: { color: "#0b132b", range: 160 }
spawn: { pos: [0, 2.0, 12.0], yaw: 0 }
---

# Node: PlazaFloor
* **pos**: [0, -0.05, 0]
* **box**: { size: [120, 0.1, 120], color: "#0a0f1c", glow: "#030812" }

# @def NeonPillar
* **cylinder**: { radius: 0.45, height: 4.5, color: "#00ffd9", glow: "#007a68" }
  ## Part: Cap
  * **pos**: [0, 2.4, 0]
  * **box**: { size: [1.2, 0.25, 1.2], color: "#ff26bf", glow: "#ff26bf" }

# Node: LeftPillar
* **pos**: [-5, 2.25, -4]
* **@use**: NeonPillar

# Node: RightPillar
* **pos**: [5, 2.25, -4]
* **@use**: NeonPillar

# Node: WelcomeSign
* **pos**: [0, 3.2, -6]
* **sign**: { text: "WELCOME TO MAKERVERSE", color: "#00ffd9", glow: "#005544" }

# Node: HubPortal
* **pos**: [0, 1.2, -10]
* **portal**: { to: "hub", radius: 2.0, color: "#ff26bf" }
```