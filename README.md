# Makerverse (`#MV3D`)

**Makerverse** is a self-hosted, federated 3D metaverse where worlds, avatars, portals, interactive triggers, and reusable blueprints are authored in plain human-readable Markdown (`#MV3D`).

---

## Architecture & Repository Layout

```text
makerverse_github_repo/
├── build_binaries.py          # Cross-platform PyInstaller binary builder (Linux & Windows)
├── README.md                  # Documentation, quickstart, controls & #MV3D authoring guide
├── examples/                  # Ready-to-use #MV3D starter files for new creators & server admins
│   ├── worlds/
│   │   ├── neon_courtyard.md  # Starter plaza with @def/@use lamp templates, interactive triggers, signs & hub portal
│   │   └── sky_observatory.md # Elevated observatory deck with multi-part telescope & custom 3D mesh prism
│   ├── avatars/
│   │   ├── cyber_bot.md       # Articulated droid avatar with @expr mood visors (/mood happy, angry, surprised, smirk)
│   │   └── crystal_warden.md  # Floating geometric crystal sentinel avatar
│   ├── prefabs/
│   │   └── neon_gazebo.md     # Reusable 4-pillar gazebo blueprint for /stamp neon_gazebo
│   └── plugins/
│       └── custom_commands.mv3d # Example server slash commands (/beacon and /motd)
└── makerverse/
    ├── mv3d.py                # #MV3D Markdown parser, validator, frontmatter updater, blueprint/snippet builders & 3D ghost preview
    ├── server.py              # Async WebSocket multiplayer server (Ed25519 auth, rate limits, world ACLs, snapshots, federation)
    ├── client.py              # Portable Pygame + OpenGL 3.30 3D client (spatial hash physics, 3D Ghost Preview, inspect mode)
    └── makerverse_data/       # Auto-created server runtime directory (override with --data-dir PATH)
        ├── config/            # server.json (operator limits/ports), permissions.json, friends.json, world_meta.json, federation.json
        ├── worlds/            # hub.md and custom #MV3D worlds
        ├── avatars/           # Uploaded custom #MV3D avatars
        ├── prefabs/           # Saved #MV3D blueprints
        ├── backups/           # Clean .md world snapshots
        └── plugins/           # Server-side .mv3d command plugins
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

---

## Starter Examples & Creating `#MV3D` Content (`examples/`)

The `examples/` folder ships with ready-to-upload worlds, custom avatars, a reusable blueprint, and a sample server plugin so new users and server operators can start building right away.

### 1. Upload & Live-Edit Starter Worlds

From the client chat prompt (`T` or `/`), upload either example world directly to your server:

```text
/upload world neon_courtyard examples/worlds/neon_courtyard.md
/world neon_courtyard

/upload world sky_observatory examples/worlds/sky_observatory.md
/world sky_observatory
```

To edit a world `.md` file in your text editor and see changes appear live in-game every time you save:

```text
/watch world neon_courtyard examples/worlds/neon_courtyard.md
# Stop watching when finished:
/unwatch
```

### 2. Upload & Equip Custom Avatars (with Dynamic Mood Expressions)

Avatars are automatically normalized to human height (`~1.85m`) and centered at their feet when uploaded:

```text
/upload avatar cyber_bot examples/avatars/cyber_bot.md
/avatar cyber_bot
```

Press **`V`** to switch to Third-Person or Front view, then test `cyber_bot.md`'s `@expr` visor parts with:

```text
/mood happy
/mood angry
/mood surprised
/mood neutral
```

You can also try the geometric sentinel avatar:

```text
/upload avatar crystal_warden examples/avatars/crystal_warden.md
/avatar crystal_warden
```

### 3. Using Blueprints (`examples/prefabs/neon_gazebo.md`) & Server Plugins (`examples/plugins/custom_commands.mv3d`)

- **Blueprints / Prefabs**: Copy `examples/prefabs/neon_gazebo.md` into `makerverse_data/prefabs/neon_gazebo.md`, then run `/prefabs` and `/stamp neon_gazebo` inside any world you own to stamp a 4-pillar neon gazebo in front of you.
- **Server Slash Commands**: Copy `examples/plugins/custom_commands.mv3d` into `makerverse_data/plugins/custom_commands.mv3d` to add `/beacon` (spawns a glowing tower for builders) and `/motd <message>` (broadcasts a server banner for admins).

### 4. `#MV3D` Authoring Quick Reference

Every `#MV3D` file consists of optional YAML-style frontmatter (`---`) followed by Markdown scene nodes:

- **Frontmatter Keys (Worlds)**:
  - `title: "My World"` — Display title shown in `/worldinfo`.
  - `description: "Short summary"` — Shown in `/worlds` search results.
  - `tags: [plaza, sci-fi]` — Up to 8 searchable discovery tags.
  - `sky: ["#050f1c", "#0f3d47"]` — Sky color or vertical gradient list.
  - `fog: { color: "#08242e", range: 150 }` — Distance fog color and falloff range in meters.
  - `spawn: { pos: [0, 2.0, 12.0], yaw: 0 }` — Default arrival coordinates and facing angle.
  - `radio: { url: "https://example.com/stream.mp3", name: "Synth FM" }` — Optional background audio stream.
- **Scene Hierarchy & Reusable Templates**:
  - `# Node: MyObject` defines a top-level object; `## Part: SubPart` nests child parts relative to their parent's position, rotation, and scale.
  - `# @def LampPost` defines a reusable group that can be instanced anywhere with `* **@use**: LampPost`.
- **Transforms & Primitives**:
  - `* **pos**: [x, y, z]`, `* **rot**: [rx, ry, rz]` *(Euler degrees)*, `* **scale**: [sx, sy, sz]`.
  - `* **box**: { size: [w, h, d], color: "#00ffd9", glow: "#005544" }`
  - `* **sphere**: { radius: 1.0, color: "cyan", glow: "#004433" }`
  - `* **cylinder**: { radius: 0.5, height: 3.0, color: "#70a8ff" }`
  - `* **cone**: { radius: 1.0, height: 2.5, color: "#ff26bf" }`
  - `* **sign**: { text: "HELLO WORLD", color: "#00ffd9" }` — Floating HUD billboard label.
  - `* **portal**: { to: "hub", radius: 2.0, color: "#00ffd9" }` — Local or cross-server (`server: "ws://host:8765"`) teleport ring.
  - `* **trigger**: { action: "toggle_glow", target: "MyObject", mode: "toggle", interact: true, label: "Toggle Light" }` — Proximity or `[F]`-activated interaction (`toggle_glow` or `move_offset`).
  - `* **@expr**: [happy, smirk]` — Restricts an avatar part to render only during matching `/mood` states.
