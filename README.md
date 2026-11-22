# Makerverse (`#MV3D`)

**Makerverse** is a self-hosted, federated 3D metaverse where worlds, avatars, portals, interactive triggers, and reusable blueprints are authored in plain human-readable Markdown (`#MV3D`).

---

## Architecture & Repository Layout

```text
makerverse/
├── mv3d.py       # #MV3D Markdown parser, validator, frontmatter updater, blueprint/snippet builders & 3D ghost preview geometry
├── server.py     # Async WebSocket multiplayer server (Ed25519 auth, rate limits, world ACLs, snapshots, federation, server plugins)
└── client.py     # Portable Pygame + OpenGL 3.30 3D client (spatial hash physics, 3D Ghost Preview, inspect mode, radio streaming)
```

---

## Key Features

- **Markdown-Native 3D Worlds & Avatars (`#MV3D`)**: Build scenes and custom normalized 3D avatars using Markdown headers (`# Node:`, `## Part:`), reusable `@def` / `@use` templates, `box`, `sphere`, `cylinder`, `cone`, `grid`, `mesh`, and floating `sign` billboards.
- **3D Placement Ghost Preview**: Toggle a live, pulsing translucent preview (`[B]` or `/ghost`) before placing geometry into a world, with real-time distance (`Mouse Wheel` / `-` / `=`), scale (`[` / `]`), `45°` yaw rotation (`R`), grid snapping (`G`), and one-click commit (`Left-Click`).
- **Live Collaborative World Building**: Place, move, rotate, scale, repaint, clone, and delete objects in-world with automatic snapshots and `/undo` history.
- **Blueprints / Prefabs**: Extract recently placed objects into reusable `#MV3D` prefab files (`/saveprefab`) and stamp them anywhere (`/stamp`).
- **Cross-World & Federated Portals**: Link worlds locally or across federated WebSocket nodes (`/hop`, `/placeportal`) with optional target spawn coordinates and yaw overrides.
- **Interactive Triggers & Proximity Events**: Trigger glow toggles or smooth spatial offsets automatically on entry or via `[F]` interaction prompts.
- **Cryptographic Identity & Granular Permissions**: Passwordless Ed25519 keypair authentication (`identity.key.hex`), portable avatar syncing across servers, role-based access (`admin`, `builder`, `guest`), per-world privacy/whitelists, and local mute/block lists.
- **Hot-Reload File Watching**: Edit `.md` worlds or avatars in your favorite text editor and hot-reload live into the server with `/watch`.

---

## Quickstart

### 1. Install Dependencies

Requires **Python 3.10+** and an OpenGL 3.3-capable GPU/driver for the desktop client.

```bash
pip install websockets pynacl numpy pygame PyOpenGL
```

*(Optional)* For world background radio streams (`/radio <url>`), install `ffplay` (FFmpeg), `mpv`, or `vlc`.

### 2. Start the Server

```bash
cd makerverse
python server.py
```

- Listens on `ws://0.0.0.0:8765` by default.
- Automatically initializes `./makerverse_data/` with the default `hub.md` world, permissions, backups, prefabs, and sample server plugins (`world_tools.mv3d`).
- The **first user to authenticate** on a fresh server is automatically granted `admin` privileges.

### 3. Launch the 3D Client

```bash
cd makerverse
python client.py
```

Optional CLI flags:

```bash
python client.py ws://localhost:8765 --nick YourName --portable
# Or specify a custom local profile/cache directory:
python client.py --data-dir /path/to/data
```

---

## Controls

| Key / Input | Action |
| :--- | :--- |
| **W A S D** / **Arrow Keys** | Move |
| **Mouse** | Look around (`Esc` toggles mouse grab / cancels active Ghost Preview) |
| **Space** / **Left Shift** | Jump (or ascend / descend in `/fly` mode) |
| **T** / **Enter** / **/** | Open chat or command prompt (`Ctrl+V` / `Ctrl+C` / `Ctrl+X` clipboard supported) |
| **B** | Toggle **3D Placement Ghost Preview** on, or cycle shapes (`box`, `pillar`, `sphere`, `cone`, `arch`, `platform`, `wall`) |
| **Left-Click** *(in Ghost mode)* | Commit ghost object to the world |
| **Mouse Wheel** *(in Ghost mode)* | Adjust ghost placement distance (`2.0m` – `25.0m`) |
| **[** / **]** *(in Ghost mode)* | Decrease / increase ghost scale (`0.2x` – `20.0x`) |
| **-** / **=** *(in Ghost mode)* | Step ghost distance closer / farther |
| **R** | Rotate Ghost Preview `+45°` (or rotate targeted placed object `+45°` in Inspect mode) |
| **X** *(in Ghost mode)* | Turn off Ghost Preview |
| **G** | Toggle `0.5m` grid & `45°` yaw snapping |
| **I** | Toggle **Inspect Mode** (wireframe AABB targeting & placed object ID readout) |
| **F** | Activate nearby interactive trigger |
| **V** | Cycle camera perspective (`First-Person` → `Third-Person` → `Front`) |
| **1 – 4** | Perform emotes (`wave`, `dance`, `spin`, `jump`) |
| **H** | Toggle HUD visibility |

---

## In-Game Commands

### 3D Building, Ghost Preview & Blueprints
- `/ghost [shape|off] [color] [scale]` *(alias `/preview`)* — Toggle or configure the 3D Placement Ghost Preview.
- `/place <shape> [color] [scale]` — Place a primitive (`box`, `pillar`, `sphere`, `cone`, `arch`, `platform`, `wall`) in front of you (syncs with active ghost settings).
- `/placeportal <world|server/world> [color] [radius] [@x,y,z] [yaw=deg]` — Place a local or cross-server portal.
- `/moveobj <dx> <dy> <dz>` — Move targeted placed object (`[I]` inspect mode).
- `/scaleobj <multiplier>` — Scale targeted placed object.
- `/rotateobj <degrees>` — Rotate targeted placed object.
- `/paint <color|#hex>` — Repaint targeted placed object.
- `/cloneobj [dx dy dz]` — Duplicate targeted placed object.
- `/delobj [id]` — Delete targeted placed object (or by hex tag ID).
- `/undo` — Revert the last world edit using automatic snapshots.
- `/snap [meters|off]` — Set grid snapping step (e.g., `/snap 0.5` or `/snap off`).
- `/saveprefab <name> [count]` — Save the last `N` placed blocks as a reusable blueprint.
- `/prefabs` — List available blueprints.
- `/stamp <name> [scale]` — Stamp a saved blueprint in front of you.

### Worlds, Navigation & Live Editing
- `/worlds [query]` — List or search accessible worlds by name, description, owner, or `#tag`.
- `/world <name>` — Join a world.
- `/templates` — List available world templates (`flat`, `cybercity`, `forest`, `desert`, `crystal`, `fortress`, `island`, `space`).
- `/createworld <name> [template] [description]` — Create a new world from built-in templates (`flat`, `cybercity`, `forest`, `desert`, `crystal`, `fortress`, `island`, `space`).
- `/worldinfo [world]` — View world metadata, author, ratings, spawn point, and object counts.
- `/spawn` — Return to the current world's spawn point.
- `/setspawn [x y z] [yaw]` / `/clearspawn` — Set or reset the world spawn point.
- `/title [world] <text|clear>` — Update world title.
- `/desc [world] <text|clear>` — Update world description.
- `/tags [world] <t1,t2|clear>` — Update world discovery tags.
- `/sky <color|#hex>` / `/fog <color|#hex>` — Update world sky and fog colors.
- `/radio <stream_url|off>` / `/mute` — Set world audio stream or mute locally.
- `/rate <1-5> [comment]` / `/topworlds` — Rate a world or view highest-rated worlds.
- `/waypoint <name> [x y z]` / `/waypoints` / `/delwaypoint <name>` — Manage local HUD navigation markers.
- `/watch <world|avatar> <name> <file.md>` / `/unwatch` — Live-reload a local `.md` file on save.
- `/upload <world|avatar> [name] <file.md>` — Upload a world or custom avatar `.md` file.
- `/backup [label]` / `/backups` / `/restore <file.md>` / `/export` — Manage server backups and export world Markdown to `./exports/`.

### Avatars, Social & Federation
- `/avatar [name]` / `/avatars` — Equip or list custom `#MV3D` avatars.
- `/mood <neutral|happy|smirk|angry|surprised|reset>` — Set avatar facial expression state.
- `/nick <name>` — Update your display nickname.
- `/key` — Display your Ed25519 public key and short ID.
- `/friends` / `/addfriend <user>` / `/accept <user>` / `/msg <user> <text>` / `/goto <user>` — Manage friends, private whispers, and friend teleports.
- `/block <user>` / `/unblock <user>` / `/blocked` — Mute chat and whispers from a user locally.
- `/hop <bookmark|ws://host:port> [world]` / `/back` — Travel between federated Makerverse servers.
- `/servers` / `/bookmark <name> <url>` / `/unbookmark <name>` — Manage server bookmarks.

### Administration & World Access Control
- `/perms` / `/admin <u>` / `/unadmin <u>` / `/grant <u>` / `/ungrant <u>` / `/ban <u>` / `/unban <u>` — Manage server roles and bans.
- `/defaultrole <guest|builder>` / `/maxworlds <n>` / `/userworldlimit <n>` — Configure server-wide builder defaults and quotas.
- `/acl [world]` / `/privateworld <on|off>` / `/allowbuilder <u>` / `/removebuilder <u>` / `/allowvisitor <u>` / `/revokevisitor <u>` — Manage per-world co-builders and private whitelists.
- `/delworld <world>` — Delete a non-hub world (admin only).

---

## `#MV3D` Markdown Format Example

```markdown
---
title: "Neon Courtyard"
description: "A compact #MV3D plaza with a portal and interactive pillar"
tags: [plaza, demo]
sky: ["#050f1c", "#0f3d47"]
fog: { color: "#08242e", range: 150 }
spawn: { pos: [0, 2.0, 10.0], yaw: 0 }
---

# Node: PlazaFloor
* **pos**: [0, -0.05, 0]
* **box**: { size: [40, 0.1, 40], color: "#0a121c", glow: "#030f17" }

# Node: BeaconPillar
* **pos**: [0, 2.0, -6.0]
* **cylinder**: { radius: 0.5, height: 4.0, color: "#00ffd9", glow: "#00b399" }

# Node: PillarSwitch
* **pos**: [2.5, 0.5, -5.0]
* **box**: { size: [0.6, 1.0, 0.6], color: "#ff26bf", glow: "#660044" }
* **trigger**: { action: "toggle_glow", target: "BeaconPillar", mode: "toggle", interact: true, label: "Toggle Beacon" }

# Node: HubPortal
* **pos**: [-4.0, 1.2, -6.0]
* **portal**: { to: "hub", radius: 2.0, color: "#00ffd9" }
```
