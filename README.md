# Makerverse (`#MV3D`)

**Makerverse** is a self-hosted, federated 3D metaverse where worlds, avatars, portals, interactive triggers, and reusable blueprints are authored in plain human-readable Markdown (`#MV3D`).

---

## Architecture & Repository Layout

```text
makerverse/
├── mv3d.py      # #MV3D Markdown parser, validator, frontmatter updater, blueprint/snippet builders & 3D ghost preview geometry
├── server.py    # Async WebSocket multiplayer server (Ed25519 auth, rate limits, world ACLs, snapshots, federation, server plugins)
├── client.py    # Portable Pygame + OpenGL 3.30 3D client (spatial hash physics, 3D Ghost Preview, inspect mode, radio streaming)
└── makerverse_data/  # Auto-created server runtime directory (override with --data-dir PATH)
    ├── config/       # server.json (operator limits/ports), permissions.json, friends.json, world_meta.json, federation.json
    ├── worlds/       # hub.md and custom #MV3D worlds
    ├── avatars/      # Uploaded custom #MV3D avatars
    ├── prefabs/      # Saved #MV3D blueprints
    ├── backups/      # Clean .md world snapshots
    └── plugins/      # Server-side .mv3d command plugins
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

*(Optional)* For world background radio streams (`/radio <url> [name]`), install `ffplay` (FFmpeg), `mpv`, or `vlc`.

### 2. Start the Server

```bash
cd makerverse
python server.py
```

- Listens on `ws://0.0.0.0:8765` by default.
- Automatically initializes `./makerverse_data/` with `config/server.json`, `worlds/hub.md`, permissions, backups, prefabs, and sample server plugins (`plugins/world_tools.mv3d`).
- The **first user to authenticate** on a fresh server is automatically granted `admin` privileges.

### Server Operator Configuration (`makerverse_data/config/server.json`)

On first startup, the server auto-generates `makerverse_data/config/server.json` so operators can configure network binding, player caps, and world creation limits without editing source code:

```json
{
  "host": "0.0.0.0",
  "port": 8765,
  "node_name": "Makerverse Node",
  "node_desc": "Self-Hosted #MV3D Markdown Metaverse Node",
  "default_role": "builder",
  "max_players": 0,
  "max_connections_per_ip": 5,
  "max_total_worlds": 0,
  "max_worlds_per_user": 0,
  "max_world_size_kb": 5120,
  "max_avatar_size_kb": 512,
  "max_prefabs_total": 256,
  "max_auto_backups": 25,
  "auth_timeout_sec": 10.0
}
```

- **`max_players`**: Maximum simultaneous non-admin players allowed (`0` = unlimited).
- **`max_total_worlds` / `max_worlds_per_user`**: Server-wide and per-user custom world creation caps (`0` = unlimited; also stays synced with `/maxworlds` and `/userworldlimit`).
- **`default_role`**: Default role assigned to newly connecting keys (`"builder"` or `"guest"`).
- **CLI Overrides**: `python server.py --data-dir /path/to/data --host 0.0.0.0 --port 8765`

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
| **T** / **Enter** / **/** | Open chat or command prompt (`Ctrl+V` / `Ctrl+C` / `Ctrl+X` clipboard & `Up`/`Down` history) |
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
