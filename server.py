import asyncio
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import time
import urllib.parse
from pathlib import Path
import websockets
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

from mv3d import (
    build_default_hub_md,
    build_placed_mv3d_snippet,
    compute_md_sha256,
    build_stamped_prefab_snippet,
    extract_placed_blueprint_md,
    mutate_placed_md_snippet,
    parse_color,
    parse_mv3d,
    remove_placed_md_snippet,
    update_mv3d_frontmatter,
    validate_mv3d_document,
)

HOST = "0.0.0.0"
PORT = 8765
DATA_DIR = Path("./makerverse_data")
WORLDS_DIR = DATA_DIR / "worlds"
AVATARS_DIR = DATA_DIR / "avatars"
SERVER_PLUGINS_DIR = DATA_DIR / "plugins"
PERMS_FILE = DATA_DIR / "permissions.json"
FRIENDS_FILE = DATA_DIR / "friends.json"
WORLD_META_FILE = DATA_DIR / "world_meta.json"
FEDERATION_FILE = DATA_DIR / "federation.json"
BACKUPS_DIR = DATA_DIR / "backups"
PREFABS_DIR = DATA_DIR / "prefabs"
MAX_PREFAB_SIZE = 256 * 1024

MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_AVATAR_SIZE = 512 * 1024
MAX_AUTO_BACKUPS = 25
AUTH_TIMEOUT_SEC = 10.0
MAX_CONNECTIONS_PER_IP = 5
MAX_JSON_DEPTH = 12
MAX_USED_NONCES = 4096
MAX_FRIENDS_PER_USER = 200
MAX_PENDING_PER_USER = 100
MAX_PREFABS_TOTAL = 256

RATE_LIMITS = {
    "move": (30, 1.0),
    "chat": (6, 4.0),
    "upload": (4, 30.0),
    "world_backup": (6, 15.0),
    "world_acl": (12, 10.0),
    "world_edit": (25, 10.0),
    "prefab_action": (12, 10.0),
    "friend_action": (10, 10.0),
    "set_world_meta": (10, 10.0),
    "get_world_info": (15, 10.0),
    "rate_world": (10, 15.0),
    "join_world": (15, 10.0),
    "federation": (6, 15.0),
    "get_avatar": (15, 10.0),
    "set_avatar": (10, 10.0),
    "set_nick": (8, 10.0),
    "default": (25, 5.0),
}


def _atomic_write_text(path: Path, text: str, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass
        os.replace(tmp_path, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _read_json_safe(path: Path, default: dict) -> dict:
    if not path.exists():
        _atomic_write_text(path, json.dumps(default, indent=2))
        return json.loads(json.dumps(default))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return json.loads(json.dumps(default))


def _check_json_depth(obj, depth: int = 0) -> bool:
    if depth > MAX_JSON_DEPTH:
        return False
    if isinstance(obj, dict):
        return all(_check_json_depth(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return all(_check_json_depth(v, depth + 1) for v in obj)
    return True


def _is_finite_num(val) -> bool:
    if isinstance(val, bool):
        return False
    try:
        return math.isfinite(float(val))
    except (TypeError, ValueError):
        return False


def _is_strict_int(val) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _safe_child_path(base_dir: Path, filename: str) -> Path | None:
    base_res = base_dir.resolve()
    cand = (base_res / filename).resolve()
    if cand.parent != base_res:
        return None
    return cand


async def is_safe_public_stream_url_async(url: str) -> tuple[bool, str]:
    if not url or len(url) > 300 or any(ch in url for ch in ('"', "'", "\n", "\r", "\x00", " ")):
        return False, "Invalid radio stream URL."
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False, "Malformed radio stream URL."
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False, "Radio stream URL must start with http:// or https://."
    host = parsed.hostname.strip().lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False, "Localhost and .local stream URLs are blocked."
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return False, f"Could not resolve stream host '{host}'."
    if not infos:
        return False, f"No DNS records for stream host '{host}'."
    for info in infos:
        try:
            ip_obj = ipaddress.ip_address(info[4][0])
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
                or ip_obj.is_unspecified
            ):
                return False, "Private, loopback, link-local, or unspecified stream addresses are blocked."
        except ValueError:
            return False, "Invalid resolved IP address for stream host."
    return True, ""


def validate_ws_peer_url(raw_url: str) -> tuple[bool, str, str]:
    url = (raw_url or "").strip()
    if not url:
        return False, "", "Federated server URL cannot be empty."
    if not url.startswith(("ws://", "wss://")):
        url = f"ws://{url}"
    if len(url) > 200 or any(ch in url for ch in ('"', "'", "\n", "\r", "\x00", " ")):
        return False, "", "Federated server URL contains invalid characters."
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False, "", "Malformed federated server URL."
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        return False, "", "Federated server URL must specify a valid host."
    port = parsed.port or (443 if parsed.scheme == "wss" else 8765)
    return True, f"{parsed.scheme}://{parsed.hostname.lower()}:{port}", ""


def sanitize_name(name: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in ("_", "-")).strip()
    return cleaned[:32] or "unnamed"


def sanitize_plugin_arg(raw_arg: str) -> str:
    return re.sub(r'[\r\n\x00\{\}\[\]"]', "", str(raw_arg or "")).strip()[:120]


def validate_hex_color(raw_color: str) -> str:
    c = str(raw_color or "").strip()
    return c.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", c) else "#00ffcc"


def read_md_safe(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_world_meta() -> dict:
    return _read_json_safe(WORLD_META_FILE, {})


def save_world_meta(data: dict):
    _atomic_write_text(WORLD_META_FILE, json.dumps(data, indent=2))


def can_edit_world(world_name: str, w_entry: dict, pubkey_hex: str, role: str) -> bool:
    if role == "admin":
        return True
    if world_name.lower() == "hub" or role != "builder":
        return False
    owner = w_entry.get("owner", "")
    return bool(owner) and (owner == pubkey_hex or pubkey_hex in w_entry.get("co_builders", []))


def can_visit_world(world_name: str, w_entry: dict, pubkey_hex: str, role: str) -> bool:
    if role == "admin" or world_name.lower() == "hub" or not w_entry.get("private", False):
        return True
    owner = w_entry.get("owner", "")
    return bool(owner) and (
        owner == pubkey_hex
        or pubkey_hex in w_entry.get("co_builders", [])
        or pubkey_hex in w_entry.get("whitelist", [])
    )


def get_world_spawn(world_name: str, w_entry: dict) -> tuple[list[float], float]:
    sp = w_entry.get("spawn_pos")
    yaw = float(w_entry.get("spawn_yaw", 0.0) or 0.0)
    if isinstance(sp, list) and len(sp) >= 3:
        return [float(sp[0]), float(sp[1]), float(sp[2])], yaw
    if world_name.lower() == "hub":
        return [0.0, 2.25, 13.5], 0.0
    return [0.0, 0.4, 12.0], 0.0


def load_federation() -> dict:
    data = _read_json_safe(FEDERATION_FILE, {
        "node_name": "Makerverse Node",
        "node_desc": "Self-Hosted #MV3D Markdown Metaverse Node",
        "peers": {},
    })
    data.setdefault("node_name", "Makerverse Node")
    data.setdefault("node_desc", "Self-Hosted #MV3D Markdown Metaverse Node")
    data.setdefault("peers", {})
    return data


def save_federation(data: dict):
    _atomic_write_text(FEDERATION_FILE, json.dumps(data, indent=2))


def create_world_snapshot(world_name: str, label: str = "") -> str | None:
    safe_world = sanitize_name(world_name)
    md_path = _safe_child_path(WORLDS_DIR, f"{safe_world}.md")
    if md_path is None or not md_path.exists():
        return None
    w_backup_dir = (BACKUPS_DIR.resolve() / safe_world).resolve()
    if w_backup_dir.parent != BACKUPS_DIR.resolve():
        return None
    w_backup_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    clean_lbl = sanitize_name(label) if label.strip() else ""
    base_stem = f"{safe_world}_{ts}{f'_{clean_lbl}' if clean_lbl else ''}"
    snap_md = w_backup_dir / f"{base_stem}.md"
    counter = 1
    while snap_md.exists():
        snap_md = w_backup_dir / f"{base_stem}_{counter}.md"
        counter += 1
    _atomic_write_text(snap_md, read_md_safe(md_path))
    _atomic_write_text(snap_md.with_suffix(".meta.json"), json.dumps(load_world_meta().get(safe_world, {}), indent=2))
    if clean_lbl == "auto":
        for old_md in sorted(w_backup_dir.glob("*_auto*.md"), reverse=True)[MAX_AUTO_BACKUPS:]:
            try:
                old_md.unlink(missing_ok=True)
                old_md.with_suffix(".meta.json").unlink(missing_ok=True)
            except Exception:
                pass
    return snap_md.name


def list_world_snapshots(world_name: str) -> list[dict]:
    safe_world = sanitize_name(world_name)
    w_backup_dir = (BACKUPS_DIR.resolve() / safe_world).resolve()
    if w_backup_dir.parent != BACKUPS_DIR.resolve() or not w_backup_dir.exists():
        return []
    return [
        {"file": fp.name, "bytes": fp.stat().st_size, "mtime": int(fp.stat().st_mtime)}
        for fp in sorted(w_backup_dir.glob("*.md"), reverse=True)
    ]


def ensure_hub_world():
    hub_path = WORLDS_DIR / "hub.md"
    if not hub_path.exists():
        _atomic_write_text(hub_path, build_default_hub_md())
        print("[*] Initialized Makerverse makerverse_data/worlds/hub.md")


def load_server_plugins() -> dict:
    SERVER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    sample = SERVER_PLUGINS_DIR / "world_tools.mv3d"
    if not sample.exists():
        _atomic_write_text(
            sample,
            "# Server-side human-readable plugins (makerverse_data/plugins/)\n"
            "COMMAND: /pillar\n"
            "DESC: Spawn a glowing cyan pillar in front of you\n"
            "ROLE: builder\n"
            "ACTION: spawn_md\n"
            'VALUE: # Node: PluginPillar\n* **pos**: [{x}, 2.0, {z_front}]\n* **cylinder**: {{ radius: 0.5, height: 4.0, color: "#00ffd9", glow: "#00b399" }}\n'
            "---\n"
            "COMMAND: /announce\n"
            "DESC: Broadcast a server-wide banner message\n"
            "ROLE: admin\n"
            "ACTION: broadcast\n"
            "VALUE: [ANNOUNCEMENT] {args}\n",
        )

    cmds = {}
    for fp in sorted(SERVER_PLUGINS_DIR.glob("*.mv3d")):
        raw = fp.read_text(encoding="utf-8", errors="replace")
        for block in raw.split("---"):
            entry = {}
            for line in block.splitlines():
                s = line.strip()
                if not s or s.startswith("#") or ":" not in s:
                    continue
                k, v = s.split(":", 1)
                entry[k.strip().lower()] = v.strip()
            cname = entry.get("command", "").lower()
            if cname:
                cmds[cname if cname.startswith("/") else "/" + cname] = entry
    return cmds


def load_perms():
    perms = _read_json_safe(PERMS_FILE, {
        "default_role": "builder",
        "admins": [],
        "builders": [],
        "banned": [],
        "max_total_worlds": 0,
        "max_worlds_per_user": 0,
        "user_avatars": {},
        "avatar_owners": {},
        "nick_registry": {},
    })
    for k, dv in (
        ("default_role", "builder"),
        ("admins", []),
        ("builders", []),
        ("banned", []),
        ("max_total_worlds", 0),
        ("max_worlds_per_user", 0),
        ("user_avatars", {}),
        ("avatar_owners", {}),
        ("nick_registry", {}),
    ):
        perms.setdefault(k, dv)
    return perms


def save_perms(data):
    _atomic_write_text(PERMS_FILE, json.dumps(data, indent=2))


def load_friends() -> dict:
    data = _read_json_safe(FRIENDS_FILE, {"friends": {}, "pending": {}})
    data.setdefault("friends", {})
    data.setdefault("pending", {})
    return data


def save_friends(data: dict):
    _atomic_write_text(FRIENDS_FILE, json.dumps(data, indent=2))


CLIENTS = {}
IP_CONNECTIONS: dict[str, int] = {}
USED_NONCES: dict[str, float] = {}
UPLOAD_LOCK = asyncio.Lock()


def _prune_used_nonces(now: float):
    for n in [k for k, exp in USED_NONCES.items() if exp <= now]:
        USED_NONCES.pop(n, None)
    if len(USED_NONCES) > MAX_USED_NONCES:
        for old_n in list(USED_NONCES.keys())[: len(USED_NONCES) - MAX_USED_NONCES]:
            USED_NONCES.pop(old_n, None)


def allocate_unique_nick(desired_nick: str, pubkey_hex: str, exclude_ws=None) -> str:
    base = sanitize_name(desired_nick)[:24] or f"Maker_{pubkey_hex[:6]}"
    active_nicks = {st["nick"].lower() for ws, st in CLIENTS.items() if ws != exclude_ws}
    if base.lower() not in active_nicks:
        return base
    tagged = f"{base[:19]}_{pubkey_hex[:4]}"
    return tagged if tagged.lower() not in active_nicks else f"{base[:15]}_{pubkey_hex[:8]}"


def remember_user_nick(pubkey_hex: str, nick: str):
    perms = load_perms()
    reg = perms.setdefault("nick_registry", {})
    if reg.get(pubkey_hex) != nick:
        reg[pubkey_hex] = nick
        save_perms(perms)


def get_role(pubkey_hex: str) -> str:
    perms = load_perms()
    if pubkey_hex in perms.get("banned", []):
        return "banned"
    if not perms.get("admins"):
        perms["admins"].append(pubkey_hex)
        save_perms(perms)
        return "admin"
    if pubkey_hex in perms.get("admins", []):
        return "admin"
    if pubkey_hex in perms.get("builders", []):
        return "builder"
    default_r = str(perms.get("default_role", "builder")).strip().lower()
    return default_r if default_r in ("guest", "builder") else "builder"


def resolve_user_key(identifier: str, fdata: dict, owner_key: str) -> tuple[str | None, str]:
    ident = identifier.strip().lower()
    if not ident:
        return None, ""
    if re.fullmatch(r"[0-9a-f]{64}", ident):
        for st in CLIENTS.values():
            if st["pubkey"] == ident:
                return ident, st["nick"]
        reg = load_perms().get("nick_registry", {})
        return ident, reg.get(ident, ident[:8])

    online_matches = [
        st for st in CLIENTS.values()
        if st["short_id"].lower() == ident or st["nick"].lower() == ident
    ]
    if len(online_matches) == 1:
        return online_matches[0]["pubkey"], online_matches[0]["nick"]
    if len(online_matches) > 1:
        return None, ""

    reg = load_perms().get("nick_registry", {})
    known_keys = (
        set(fdata.get("friends", {}).get(owner_key, []))
        | set(fdata.get("pending", {}).get(owner_key, []))
        | set(reg.keys())
    )
    exact_matches = []
    prefix_matches = []
    for k in known_keys:
        saved_nick = reg.get(k, "")
        if k == ident or (saved_nick and saved_nick.lower() == ident):
            exact_matches.append((k, saved_nick or k[:8]))
        elif len(ident) >= 6 and k.lower().startswith(ident):
            prefix_matches.append((k, saved_nick or k[:8]))

    if len(exact_matches) == 1:
        return exact_matches[0]
    if not exact_matches and len(prefix_matches) == 1:
        return prefix_matches[0]
    return None, ""


def _check_rate_limit(buckets: dict, msg_type: str, now: float) -> bool:
    bucket_key = msg_type if msg_type in RATE_LIMITS else "default"
    limit, window = RATE_LIMITS[bucket_key]
    history = buckets.setdefault(bucket_key, [])
    cutoff = now - window
    while history and history[0] < cutoff:
        history.pop(0)
    if len(history) >= limit:
        return False
    history.append(now)
    return True


async def broadcast_world(world: str, payload: dict, exclude_ws=None):
    msg = json.dumps(payload)
    for ws, state in list(CLIENTS.items()):
        if state.get("world") == world and ws != exclude_ws:
            try:
                await ws.send(msg)
            except Exception:
                pass


async def probe_federated_node(peer_url: str, timeout: float = 2.5) -> dict:
    try:
        async with websockets.connect(peer_url, open_timeout=timeout, close_timeout=1.0) as ws:
            await asyncio.wait_for(ws.recv(), timeout=timeout)
            await ws.send(json.dumps({"type": "node_info"}))
            info = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if info.get("type") == "node_info_ok":
                return {
                    "online": True,
                    "node_name": str(info.get("node_name", ""))[:48],
                    "node_desc": str(info.get("node_desc", ""))[:120],
                    "online_count": int(info.get("online_count", 0)),
                    "worlds": info.get("worlds", [])[:12],
                }
    except Exception:
        pass
    return {"online": False}


def summarize_world_ratings(w_entry: dict) -> tuple[float, int, dict[int, int]]:
    ratings = w_entry.get("ratings")
    dist = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    if not isinstance(ratings, dict) or not ratings:
        return 0.0, 0, dist
    total = count = 0
    for item in ratings.values():
        if not isinstance(item, dict):
            continue
        raw_st = item.get("stars", 0)
        if _is_strict_int(raw_st) and 1 <= raw_st <= 5:
            dist[raw_st] += 1
            total += raw_st
            count += 1
    return (round(total / count, 2) if count else 0.0), count, dist


def _effective_world_meta(target: str, raw_entry: dict | None, md_text: str = "") -> tuple[dict, dict, bool]:
    w_entry = dict(raw_entry or {})
    parsed_scene = parse_mv3d(md_text) if md_text else {}

    title_raw = w_entry.get("title") or parsed_scene.get("title") or ("Neo-Genesis Grid" if target.lower() == "hub" else target)
    w_entry["title"] = re.sub(r"[\x00-\x1f\x7f]", " ", str(title_raw)).strip()[:64] or target

    if "description" in w_entry and w_entry["description"] is not None:
        desc_raw = str(w_entry["description"])
    else:
        desc_raw = str(parsed_scene.get("world_info_desc", ""))
    w_entry["description"] = re.sub(r"[\x00-\x1f\x7f]", " ", desc_raw).strip()[:240]

    raw_tags = w_entry.get("tags") if "tags" in w_entry else parsed_scene.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    elif not isinstance(raw_tags, list):
        raw_tags = []
    w_entry["tags"] = [sanitize_name(str(t)) for t in raw_tags[:8] if sanitize_name(str(t))]

    if "radio_url" in w_entry and w_entry["radio_url"] is not None:
        r_url = str(w_entry["radio_url"]).strip()
        r_name = str(w_entry.get("radio_name") or r_url).strip()
    else:
        r_url = str(parsed_scene.get("radio_url", "")).strip()
        r_name = str(parsed_scene.get("radio_name") or r_url).strip()
    w_entry["radio_url"] = re.sub(r"[\x00-\x1f\x7f]", "", r_url)[:300]
    w_entry["radio_name"] = re.sub(r"[\x00-\x1f\x7f]", " ", r_name).strip()[:64] if w_entry["radio_url"] else ""

    if isinstance(w_entry.get("spawn_pos"), list) and len(w_entry["spawn_pos"]) >= 3 and all(_is_finite_num(v) for v in w_entry["spawn_pos"][:3]):
        sp_pos, sp_yaw = get_world_spawn(target, w_entry)
        spawn_custom = True
    elif isinstance(parsed_scene.get("spawn_pos"), list) and len(parsed_scene["spawn_pos"]) >= 3:
        sp_pos = [float(parsed_scene["spawn_pos"][0]), float(parsed_scene["spawn_pos"][1]), float(parsed_scene["spawn_pos"][2])]
        sp_yaw = float(parsed_scene.get("spawn_yaw", 0.0) or 0.0)
        w_entry["spawn_pos"] = sp_pos
        w_entry["spawn_yaw"] = sp_yaw
        spawn_custom = True
    else:
        sp_pos, sp_yaw = get_world_spawn(target, w_entry)
        spawn_custom = False

    return w_entry, parsed_scene, spawn_custom


def build_world_info_payload(target: str, meta: dict, perms: dict) -> dict:
    md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
    md_text = read_md_safe(md_path) if (md_path and md_path.exists()) else ""
    w_entry, parsed_scene, spawn_custom = _effective_world_meta(target, meta.get(target, {}), md_text)
    sp_pos, sp_yaw = get_world_spawn(target, w_entry)

    owner_key = str(w_entry.get("owner", "")).strip()
    nick_reg = perms.get("nick_registry", {})
    online_by_key = {st["pubkey"]: st for st in CLIENTS.values()}

    def _resolve_member(pk: str, fallback_nick: str = "") -> dict:
        on_st = online_by_key.get(pk)
        return {
            "pubkey": pk,
            "short_id": pk[:8] if pk else "",
            "nick": (on_st["nick"] if on_st else "") or nick_reg.get(pk) or fallback_nick or (pk[:8] if pk else "system"),
            "online": bool(on_st),
        }

    file_mtime = int(md_path.stat().st_mtime) if (md_path and md_path.exists()) else 0
    created_ts = int(w_entry.get("created_at", 0) or 0) or file_mtime
    updated_ts = int(w_entry.get("updated_at", 0) or 0) or file_mtime

    avg_rat, cnt_rat, _ = summarize_world_ratings(w_entry)
    return {
        "type": "world_info",
        "avg_rating": avg_rat,
        "rating_count": cnt_rat,
        "world": target,
        "title": w_entry["title"],
        "description": w_entry["description"],
        "tags": w_entry["tags"],
        "private": bool(w_entry.get("private", False)),
        "is_system": target.lower() == "hub" and not owner_key,
        "owner": _resolve_member(owner_key, w_entry.get("owner_nick", "")) if owner_key else None,
        "co_builders": [_resolve_member(k) for k in w_entry.get("co_builders", [])],
        "whitelist": [_resolve_member(k) for k in w_entry.get("whitelist", [])],
        "radio_url": w_entry["radio_url"],
        "radio_name": w_entry["radio_name"],
        "spawn_pos": sp_pos,
        "spawn_yaw": sp_yaw,
        "spawn_custom": spawn_custom,
        "created_at": created_ts,
        "updated_at": updated_ts,
        "bytes": md_path.stat().st_size if (md_path and md_path.exists()) else 0,
        "sha256": compute_md_sha256(md_text) if md_text else "",
        "object_count": len(parsed_scene.get("objects", [])),
        "portal_count": len(parsed_scene.get("portals", [])),
        "trigger_count": len(parsed_scene.get("triggers", [])),
        "players": [
            {"nick": st["nick"], "short_id": st["short_id"], "role": st["role"]}
            for st in CLIENTS.values()
            if st.get("world") == target
        ],
        "snapshots_count": len(list_world_snapshots(target)),
    }


def _sanitize_spawn_override(raw_sp, raw_yaw=None) -> tuple[list[float] | None, float | None]:
    sp_out = None
    if isinstance(raw_sp, list) and len(raw_sp) >= 3 and all(_is_finite_num(v) for v in raw_sp[:3]):
        sp_out = [
            max(-500.0, min(500.0, round(float(raw_sp[0]), 3))),
            max(-100.0, min(500.0, round(float(raw_sp[1]), 3))),
            max(-500.0, min(500.0, round(float(raw_sp[2]), 3))),
        ]
    yaw_out = None
    if raw_yaw is not None and _is_finite_num(raw_yaw):
        yaw_out = round(float(raw_yaw) % 360.0, 2)
    return sp_out, yaw_out


async def send_world_transition(
    websocket,
    client_state: dict,
    target: str,
    w_meta: dict,
    client_hash: str = "",
    spawn_override: list[float] | None = None,
    yaw_override: float | None = None,
):
    md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
    if md_path is None or not md_path.exists():
        return
    md_text = read_md_safe(md_path)
    w_meta, _, _ = _effective_world_meta(target, w_meta, md_text)
    sp_pos, sp_yaw = get_world_spawn(target, w_meta)
    if spawn_override is not None:
        sp_pos = list(spawn_override)
    if yaw_override is not None:
        sp_yaw = float(yaw_override)
    client_state["world"] = target
    client_state["pos"] = list(sp_pos)
    client_state["rot"] = [0.0, 1.0, 0.0, round(math.radians(-sp_yaw), 3)]

    md_hash = compute_md_sha256(md_text)
    msg = {
        "type": "world_cached" if (client_hash and client_hash == md_hash) else "world_data",
        "world": target,
        "md_hash": md_hash,
        "description": w_meta.get("description", ""),
        "radio_url": w_meta.get("radio_url", ""),
        "radio_name": w_meta.get("radio_name", ""),
    }
    if msg["type"] == "world_data":
        msg["md"] = md_text
    if w_meta.get("spawn_pos") or spawn_override is not None:
        msg["spawn_pos"] = sp_pos
        msg["spawn_yaw"] = sp_yaw
    elif yaw_override is not None:
        msg["spawn_yaw"] = sp_yaw
    await websocket.send(json.dumps(msg))

    peers = [{k: v for k, v in st.items() if k != "pubkey"} for ws_p, st in CLIENTS.items() if ws_p != websocket and st["world"] == target]
    await websocket.send(json.dumps({"type": "peers", "peers": peers}))
    await broadcast_world(target, {"type": "peer_join", "peer": {k: v for k, v in client_state.items() if k != "pubkey"}}, exclude_ws=websocket)


def _install_transferred_avatar(raw_name: str, raw_md: str, pubkey_hex: str, role: str) -> tuple[str, str]:
    av_name = sanitize_name(raw_name)
    if not av_name or av_name.lower() == "default" or not raw_md:
        return "default", ""
    if len(raw_md.encode("utf-8", errors="replace")) > MAX_AVATAR_SIZE:
        return "default", ""
    ok_md, _, _ = validate_mv3d_document(raw_md, kind="avatar")
    if not ok_md:
        return "default", ""
    perms = load_perms()
    av_owners = perms.setdefault("avatar_owners", {})
    existing_owner = av_owners.get(av_name, "")
    if existing_owner and existing_owner != pubkey_hex and role != "admin":
        av_name = f"{av_name[:20]}_{pubkey_hex[:6]}"
        existing_owner = av_owners.get(av_name, "")
        if existing_owner and existing_owner != pubkey_hex and role != "admin":
            return "default", ""
    if not existing_owner:
        av_owners[av_name] = pubkey_hex
    av_path = _safe_child_path(AVATARS_DIR, f"{av_name}.md")
    if av_path is None:
        return "default", ""
    _atomic_write_text(av_path, raw_md)
    perms["user_avatars"][pubkey_hex] = av_name
    save_perms(perms)
    return av_name, raw_md


def _link_mutual_friends(fdata: dict, key_a: str, key_b: str):
    fdata["pending"].setdefault(key_a, [])
    fdata["pending"].setdefault(key_b, [])
    fdata["friends"].setdefault(key_a, [])
    fdata["friends"].setdefault(key_b, [])
    fdata["pending"][key_a] = [k for k in fdata["pending"][key_a] if k != key_b]
    fdata["pending"][key_b] = [k for k in fdata["pending"][key_b] if k != key_a]
    if key_b not in fdata["friends"][key_a]:
        fdata["friends"][key_a].append(key_b)
    if key_a not in fdata["friends"][key_b]:
        fdata["friends"][key_b].append(key_a)


async def handler(websocket):
    remote_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
    if IP_CONNECTIONS.get(remote_ip, 0) >= MAX_CONNECTIONS_PER_IP:
        await websocket.close(code=1008, reason="Too many concurrent connections from IP.")
        return
    IP_CONNECTIONS[remote_ip] = IP_CONNECTIONS.get(remote_ip, 0) + 1

    nonce = secrets.token_hex(24)
    nonce_issued = time.time()
    client_state = None
    rate_buckets: dict[str, list[float]] = {}

    try:
        await websocket.send(json.dumps({"type": "challenge", "nonce": nonce}))
        try:
            first_raw = await asyncio.wait_for(websocket.recv(), timeout=AUTH_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            await websocket.send(json.dumps({"type": "error", "msg": "Authentication handshake timed out."}))
            await websocket.close(code=1008)
            return

        async def _iter_messages():
            yield first_raw
            async for m in websocket:
                yield m

        async for raw_msg in _iter_messages():
            now = time.time()
            try:
                data = json.loads(raw_msg)
            except (json.JSONDecodeError, TypeError):
                await websocket.send(json.dumps({"type": "error", "msg": "Malformed JSON payload."}))
                continue
            if not isinstance(data, dict) or not _check_json_depth(data):
                await websocket.send(json.dumps({"type": "error", "msg": "Invalid or overly nested JSON payload."}))
                continue

            msg_type = str(data.get("type", ""))

            if msg_type == "node_info" and client_state is None:
                fed = load_federation()
                meta = load_world_meta()
                worlds_list = sorted(f.stem for f in WORLDS_DIR.glob("*.md") if not meta.get(f.stem, {}).get("private", False))
                await websocket.send(json.dumps({
                    "type": "node_info_ok",
                    "node_name": fed.get("node_name", "Makerverse Node"),
                    "node_desc": fed.get("node_desc", ""),
                    "online_count": len(CLIENTS),
                    "worlds": worlds_list,
                    "peers": [
                        {"url": u, "label": m.get("label", u), "default_world": m.get("default_world", "hub")}
                        for u, m in fed.get("peers", {}).items()
                    ],
                }))
                await websocket.close()
                return

            if msg_type == "auth":
                _prune_used_nonces(now)
                if client_state is not None or nonce in USED_NONCES or (now - nonce_issued) > AUTH_TIMEOUT_SEC:
                    await websocket.send(json.dumps({"type": "error", "msg": "Authentication challenge expired or replayed."}))
                    await websocket.close(code=1008)
                    return
                USED_NONCES[nonce] = now + 60.0

                pubkey_hex = str(data.get("pubkey", "")).strip().lower()
                sig_hex = str(data.get("signature", "")).strip().lower()
                if not re.fullmatch(r"[0-9a-f]{64}", pubkey_hex) or not re.fullmatch(r"[0-9a-f]{128}", sig_hex):
                    await websocket.close(code=1008)
                    return
                try:
                    VerifyKey(bytes.fromhex(pubkey_hex)).verify(nonce.encode("utf-8"), bytes.fromhex(sig_hex))
                except (ValueError, BadSignatureError):
                    await websocket.close(code=1008)
                    return

                role = get_role(pubkey_hex)
                if role == "banned":
                    await websocket.send(json.dumps({"type": "error", "msg": "Key is banned from this server."}))
                    await websocket.close(code=1008)
                    return

                for old_ws, old_st in list(CLIENTS.items()):
                    if old_ws != websocket and old_st["pubkey"] == pubkey_hex:
                        CLIENTS.pop(old_ws, None)
                        await broadcast_world(old_st["world"], {"type": "peer_leave", "short_id": old_st["short_id"]}, exclude_ws=old_ws)
                        try:
                            await old_ws.send(json.dumps({"type": "error", "msg": "Disconnected: signed in from another session."}))
                            await old_ws.close(code=1008)
                        except Exception:
                            pass

                saved_nick = load_perms().get("nick_registry", {}).get(pubkey_hex, "")
                nick = allocate_unique_nick(str(data.get("nick") or saved_nick or f"Maker_{pubkey_hex[:6]}"), pubkey_hex, exclude_ws=websocket)
                remember_user_nick(pubkey_hex, nick)

                color = validate_hex_color(data.get("color", "#00ffcc"))
                meta = load_world_meta()
                world = sanitize_name(str(data.get("world", "hub")))
                w_cand = _safe_child_path(WORLDS_DIR, f"{world}.md")
                if w_cand is None or not w_cand.exists() or not can_visit_world(world, meta.get(world, {}), pubkey_hex, role):
                    world = "hub"

                req_avatar = sanitize_name(str(data.get("avatar", "")))
                carried_md = str(data.get("avatar_md", ""))
                avatar_synced = False
                if req_avatar and req_avatar.lower() != "default" and carried_md:
                    async with UPLOAD_LOCK:
                        inst_name, inst_md = _install_transferred_avatar(req_avatar, carried_md, pubkey_hex, role)
                        if inst_name != "default" and inst_md:
                            req_avatar, avatar_synced = inst_name, True
                        else:
                            req_avatar = sanitize_name(load_perms().get("user_avatars", {}).get(pubkey_hex, "default"))
                elif not req_avatar or req_avatar.lower() == "default":
                    req_avatar = sanitize_name(load_perms().get("user_avatars", {}).get(pubkey_hex, "default"))

                av_cand = _safe_child_path(AVATARS_DIR, f"{req_avatar}.md")
                if req_avatar != "default" and (av_cand is None or not av_cand.exists()):
                    req_avatar = "default"

                md_text = read_md_safe(WORLDS_DIR / f"{world}.md")
                w_meta, _, _ = _effective_world_meta(world, meta.get(world, {}), md_text)
                sp_pos, sp_yaw = get_world_spawn(world, w_meta)
                ov_sp, ov_yaw = _sanitize_spawn_override(data.get("spawn_pos"), data.get("spawn_yaw"))
                if ov_sp is not None:
                    sp_pos = ov_sp
                if ov_yaw is not None:
                    sp_yaw = ov_yaw
                client_state = {
                    "pubkey": pubkey_hex,
                    "short_id": pubkey_hex[:8],
                    "nick": nick,
                    "color": color,
                    "role": role,
                    "world": world,
                    "pos": list(sp_pos),
                    "rot": [0.0, 1.0, 0.0, round(math.radians(-sp_yaw), 3)],
                    "avatar": req_avatar,
                }
                CLIENTS[websocket] = client_state

                worlds_list = [f.stem for f in WORLDS_DIR.glob("*.md") if can_visit_world(f.stem, meta.get(f.stem, {}), pubkey_hex, role)]
                avatars_list = sorted(f.stem for f in AVATARS_DIR.glob("*.md"))
                fed = load_federation()
                print(f"[AUTH] {nick} ({role}) -> '{world}' [avatar: {req_avatar}] ({len(md_text)} bytes)")

                auth_payload = {
                    "type": "auth_ok",
                    "role": role,
                    "nick": nick,
                    "short_id": client_state["short_id"],
                    "avatar": req_avatar,
                    "avatar_synced": avatar_synced,
                    "node_name": fed.get("node_name", "Makerverse Node"),
                    "world": world,
                    "md_hash": compute_md_sha256(md_text),
                    "description": w_meta.get("description", ""),
                    "radio_url": w_meta.get("radio_url", ""),
                    "radio_name": w_meta.get("radio_name", ""),
                    "md": md_text,
                    "worlds": worlds_list,
                    "avatars": avatars_list,
                }
                if w_meta.get("spawn_pos") or ov_sp is not None:
                    auth_payload["spawn_pos"] = sp_pos
                    auth_payload["spawn_yaw"] = sp_yaw
                elif ov_yaw is not None:
                    auth_payload["spawn_yaw"] = sp_yaw
                await websocket.send(json.dumps(auth_payload))

                peers = [{k: v for k, v in st.items() if k != "pubkey"} for ws_p, st in CLIENTS.items() if ws_p != websocket and st["world"] == world]
                await websocket.send(json.dumps({"type": "peers", "peers": peers}))
                pj = {"type": "peer_join", "peer": {k: v for k, v in client_state.items() if k != "pubkey"}}
                if avatar_synced and req_avatar != "default":
                    pj["avatar_md"] = read_md_safe(AVATARS_DIR / f"{req_avatar}.md")
                await broadcast_world(world, pj, exclude_ws=websocket)

                fdata = load_friends()
                my_friends = set(fdata.get("friends", {}).get(pubkey_hex, []))
                pending_in = fdata.get("pending", {}).get(pubkey_hex, [])
                if pending_in:
                    await websocket.send(json.dumps({
                        "type": "info",
                        "msg": f"You have {len(pending_in)} pending friend request(s). Type /friends to view."
                    }))
                for peer_ws, peer_st in list(CLIENTS.items()):
                    if peer_ws != websocket and peer_st["pubkey"] in my_friends:
                        try:
                            await peer_ws.send(json.dumps({
                                "type": "info",
                                "msg": f"[FRIEND] {nick} ({client_state['short_id']}) came online in '{world}'."
                            }))
                        except Exception:
                            pass
                continue

            if not client_state:
                await websocket.close(code=1008)
                return

            if not _check_rate_limit(rate_buckets, msg_type, now):
                if msg_type != "move":
                    await websocket.send(json.dumps({"type": "error", "msg": f"Rate limit exceeded for '{msg_type}'."}))
                continue

            if msg_type == "move":
                raw_pos, raw_rot = data.get("pos"), data.get("rot")
                if isinstance(raw_pos, list) and len(raw_pos) >= 3 and all(_is_finite_num(v) for v in raw_pos[:3]):
                    vals = [float(raw_pos[0]), float(raw_pos[1]), float(raw_pos[2])]
                    if all(-10000.0 <= v <= 10000.0 for v in vals):
                        client_state["pos"] = vals
                if isinstance(raw_rot, list) and len(raw_rot) >= 4 and all(_is_finite_num(v) for v in raw_rot[:4]):
                    rvals = [float(raw_rot[0]), float(raw_rot[1]), float(raw_rot[2]), float(raw_rot[3])]
                    if all(-1000.0 <= v <= 1000.0 for v in rvals):
                        client_state["rot"] = rvals
                await broadcast_world(client_state["world"], {
                    "type": "peer_move",
                    "short_id": client_state["short_id"],
                    "pos": client_state["pos"],
                    "rot": client_state["rot"],
                }, exclude_ws=websocket)

            elif msg_type == "chat":
                text = re.sub(r"[\x00-\x1f\x7f]", "", str(data.get("text", "")))[:300].strip()
                if not text:
                    continue
                if text.startswith("/emote "):
                    em_name = sanitize_name(text.split(" ", 1)[1])
                    if em_name in ("wave", "dance", "spin", "jump"):
                        await broadcast_world(client_state["world"], {
                            "type": "emote",
                            "short_id": client_state["short_id"],
                            "nick": client_state["nick"],
                            "emote": em_name,
                        })
                    else:
                        await websocket.send(json.dumps({"type": "error", "msg": "Supported emotes: wave, dance, spin, jump."}))
                    continue

                if text.startswith("/"):
                    srv_plugins = load_server_plugins()
                    cmd_tok, _, cmd_args = text.partition(" ")
                    spec = srv_plugins.get(cmd_tok.lower())
                    if spec:
                        req_role = spec.get("role", "guest").lower()
                        if req_role == "admin" and client_state["role"] != "admin":
                            await websocket.send(json.dumps({"type": "error", "msg": f"{cmd_tok} requires admin role."}))
                            continue
                        if req_role == "builder" and client_state["role"] not in ("admin", "builder"):
                            await websocket.send(json.dumps({"type": "error", "msg": f"{cmd_tok} requires builder role."}))
                            continue

                        act = spec.get("action", "broadcast").lower()
                        safe_args = sanitize_plugin_arg(cmd_args)
                        px, py, pz = client_state["pos"]
                        rendered = spec.get("value", "").format(
                            nick=client_state["nick"],
                            world=client_state["world"],
                            args=safe_args,
                            x=round(px, 2),
                            y=round(py, 2),
                            z=round(pz, 2),
                            z_front=round(pz - 4.0, 2),
                        )
                        w_meta = load_world_meta().get(client_state["world"], {})
                        if act == "spawn_md":
                            if not can_edit_world(client_state["world"], w_meta, client_state["pubkey"], client_state["role"]):
                                await websocket.send(json.dumps({"type": "error", "msg": "Permission denied."}))
                                continue
                            async with UPLOAD_LOCK:
                                wpath = _safe_child_path(WORLDS_DIR, f"{client_state['world']}.md")
                                if wpath and wpath.exists():
                                    create_world_snapshot(client_state["world"], label="auto")
                                    updated = read_md_safe(wpath).rstrip() + f"\n\n{rendered}\n"
                                    _atomic_write_text(wpath, updated)
                                    await broadcast_world(client_state["world"], {
                                        "type": "world_data",
                                        "world": client_state["world"],
                                        "md_hash": compute_md_sha256(updated),
                                        "md": updated,
                                        "description": w_meta.get("description", ""),
                                        "radio_url": w_meta.get("radio_url", ""),
                                        "radio_name": w_meta.get("radio_name", ""),
                                        "in_place": True,
                                    })
                        elif act == "broadcast":
                            for peer_ws in list(CLIENTS.keys()):
                                try:
                                    await peer_ws.send(json.dumps({"type": "info", "msg": rendered[:300]}))
                                except Exception:
                                    pass
                        continue

                await broadcast_world(client_state["world"], {
                    "type": "chat",
                    "short_id": client_state["short_id"],
                    "nick": client_state["nick"],
                    "color": client_state["color"],
                    "text": text,
                })

            elif msg_type == "join_world":
                target = sanitize_name(str(data.get("world", "hub")))
                md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
                if md_path is None or not md_path.exists():
                    await websocket.send(json.dumps({"type": "error", "msg": f"World '{target}' does not exist."}))
                    continue
                w_meta = load_world_meta().get(target, {})
                if not can_visit_world(target, w_meta, client_state["pubkey"], client_state["role"]):
                    await websocket.send(json.dumps({"type": "error", "msg": f"Access denied: '{target}' is private."}))
                    continue
                old_world = client_state["world"]
                if old_world != target:
                    client_state["world"] = target
                    await broadcast_world(old_world, {"type": "peer_leave", "short_id": client_state["short_id"]}, exclude_ws=websocket)
                ov_sp, ov_yaw = _sanitize_spawn_override(data.get("spawn_pos"), data.get("spawn_yaw"))
                await send_world_transition(
                    websocket,
                    client_state,
                    target,
                    w_meta,
                    str(data.get("cached_hash", "")).strip().lower(),
                    spawn_override=ov_sp,
                    yaw_override=ov_yaw,
                )

            elif msg_type == "list_worlds":
                meta = load_world_meta()
                nick_reg = load_perms().get("nick_registry", {})
                worlds_list = sorted(f.stem for f in WORLDS_DIR.glob("*.md") if can_visit_world(f.stem, meta.get(f.stem, {}), client_state["pubkey"], client_state["role"]))
                descriptions = {w: f"{'[PRIVATE] ' if meta.get(w, {}).get('private') else ''}{meta.get(w, {}).get('description', '')}".strip() for w in worlds_list}
                tags_map = {w: meta.get(w, {}).get("tags", []) for w in worlds_list if meta.get(w, {}).get("tags")}
                owners_map = {
                    w: nick_reg.get(meta.get(w, {}).get("owner", ""), meta.get(w, {}).get("owner_nick", ""))
                    for w in worlds_list
                    if meta.get(w, {}).get("owner")
                }
                ratings_map = {w: {"avg": r[0], "count": r[1]} for w in worlds_list if (r := summarize_world_ratings(meta.get(w, {})))[1] > 0}
                await websocket.send(json.dumps({
                    "type": "worlds_list",
                    "worlds": worlds_list,
                    "descriptions": descriptions,
                    "tags": tags_map,
                    "owners": owners_map,
                    "ratings": ratings_map,
                    "current": client_state["world"],
                }))

            elif msg_type == "get_world_info":
                target = sanitize_name(str(data.get("world") or client_state["world"]))
                md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
                if md_path is None or not md_path.exists():
                    await websocket.send(json.dumps({"type": "error", "msg": f"World '{target}' does not exist."}))
                    continue
                meta = load_world_meta()
                if not can_visit_world(target, meta.get(target, {}), client_state["pubkey"], client_state["role"]):
                    await websocket.send(json.dumps({"type": "error", "msg": f"Access denied: '{target}' is private."}))
                    continue
                await websocket.send(json.dumps(build_world_info_payload(target, meta, load_perms())))

            elif msg_type == "delete_world":
                if client_state["role"] != "admin":
                    await websocket.send(json.dumps({"type": "error", "msg": "Admin privileges required."}))
                    continue
                target = sanitize_name(str(data.get("world", "")))
                md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
                if not target or target.lower() == "hub" or md_path is None or not md_path.exists():
                    await websocket.send(json.dumps({"type": "error", "msg": f"Cannot delete '{target}'."}))
                    continue
                async with UPLOAD_LOCK:
                    create_world_snapshot(target, label="pre_delete")
                    md_path.unlink()
                    meta = load_world_meta()
                    meta.pop(target, None)
                    save_world_meta(meta)
                    hub_meta = meta.get("hub", {})
                    for peer_ws, peer_st in list(CLIENTS.items()):
                        if peer_st["world"] == target:
                            await send_world_transition(peer_ws, peer_st, "hub", hub_meta)
                await websocket.send(json.dumps({"type": "info", "msg": f"Deleted world '{target}'."}))

            elif msg_type == "world_edit":
                action = str(data.get("action", ""))
                target = sanitize_name(str(data.get("world") or client_state["world"]))
                md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
                if md_path is None or not md_path.exists():
                    await websocket.send(json.dumps({"type": "error", "msg": f"World '{target}' does not exist."}))
                    continue

                async with UPLOAD_LOCK:
                    meta = load_world_meta()
                    w_entry = meta.get(target, {})
                    if not can_edit_world(target, w_entry, client_state["pubkey"], client_state["role"]):
                        await websocket.send(json.dumps({"type": "error", "msg": f"Permission denied on '{target}'."}))
                        continue

                    def _world_update_msg(md_body: str) -> dict:
                        msg = {
                            "type": "world_data",
                            "world": target,
                            "md_hash": compute_md_sha256(md_body),
                            "description": w_entry.get("description", ""),
                            "radio_url": w_entry.get("radio_url", ""),
                            "radio_name": w_entry.get("radio_name", ""),
                            "md": md_body,
                            "in_place": True,
                        }
                        if w_entry.get("spawn_pos"):
                            msg["spawn_pos"] = w_entry["spawn_pos"]
                            msg["spawn_yaw"] = float(w_entry.get("spawn_yaw", 0.0) or 0.0)
                        return msg

                    if action in ("set_spawn", "clear_spawn"):
                        cur_md = read_md_safe(md_path)
                        create_world_snapshot(target, label="auto")
                        if action == "set_spawn":
                            raw_c = (data.get("x", 0.0), data.get("y", 0.0), data.get("z", 0.0), data.get("yaw", 0.0))
                            if not all(_is_finite_num(v) for v in raw_c):
                                await websocket.send(json.dumps({"type": "error", "msg": "Invalid spawn coordinates."}))
                                continue
                            sx = max(-500.0, min(500.0, round(float(raw_c[0]), 3)))
                            sy = max(-100.0, min(500.0, round(float(raw_c[1]), 3)))
                            sz = max(-500.0, min(500.0, round(float(raw_c[2]), 3)))
                            syaw = round(float(raw_c[3]) % 360.0, 2)
                            w_entry["spawn_pos"], w_entry["spawn_yaw"] = [sx, sy, sz], syaw
                            updated_md = update_mv3d_frontmatter(cur_md, {"spawn": f"{{ pos: [{sx}, {sy}, {sz}], yaw: {syaw} }}"})
                            info_txt = f"Set spawn for '{target}' to ({sx}, {sy}, {sz}) yaw={syaw}."
                        else:
                            w_entry.pop("spawn_pos", None)
                            w_entry.pop("spawn_yaw", None)
                            updated_md = update_mv3d_frontmatter(cur_md, {"spawn": None})
                            info_txt = f"Cleared custom spawn for '{target}'."
                        meta[target] = w_entry
                        save_world_meta(meta)
                        _atomic_write_text(md_path, updated_md)
                        await broadcast_world(target, _world_update_msg(updated_md))
                        await websocket.send(json.dumps({"type": "info", "msg": info_txt}))

                    elif action == "place":
                        shape = str(data.get("shape", "box")).strip().lower()
                        if shape not in ("box", "pillar", "sphere", "cone", "arch", "platform", "wall"):
                            await websocket.send(json.dumps({"type": "error", "msg": "Invalid shape."}))
                            continue
                        raw_p = (data.get("x", 0.0), data.get("y", 0.0), data.get("z", -4.0), data.get("scale", 1.0), data.get("yaw", 0.0))
                        if not all(_is_finite_num(v) for v in raw_p):
                            await websocket.send(json.dumps({"type": "error", "msg": "Invalid numeric values."}))
                            continue
                        px = max(-500.0, min(500.0, float(raw_p[0])))
                        py = max(-100.0, min(500.0, float(raw_p[1])))
                        pz = max(-500.0, min(500.0, float(raw_p[2])))
                        scale = max(0.2, min(20.0, float(raw_p[3])))
                        yaw_deg = float(raw_p[4])
                        cur_md = read_md_safe(md_path)
                        if len(cur_md.encode("utf-8", errors="replace")) > MAX_FILE_SIZE - 4096:
                            await websocket.send(json.dumps({"type": "error", "msg": "World size limit reached."}))
                            continue
                        create_world_snapshot(target, label="auto")
                        tid = secrets.token_hex(3)
                        snippet = build_placed_mv3d_snippet(
                            shape, px, py, pz, scale, parse_color(data.get("color", "cyan")), client_state["nick"], tag_id=tid, yaw_deg=yaw_deg
                        )
                        updated_md = cur_md.rstrip() + f"\n\n{snippet}\n"
                        _atomic_write_text(md_path, updated_md)
                        await broadcast_world(target, _world_update_msg(updated_md))
                        await websocket.send(json.dumps({"type": "info", "msg": f"Placed {shape} [{tid}] at ({px:.1f}, {py:.1f}, {pz:.1f})."}))

                    elif action == "mutate_placed":
                        tag_id = re.sub(r"[^a-fA-F0-9]", "", str(data.get("tag_id", ""))).lower()
                        op = str(data.get("op", "")).strip().lower()
                        raw_m = (data.get("dx", 0.0), data.get("dy", 0.0), data.get("dz", 0.0), data.get("yaw_delta", 0.0), data.get("scale_mult", 1.0))
                        if not all(_is_finite_num(v) for v in raw_m):
                            await websocket.send(json.dumps({"type": "error", "msg": "Invalid numeric offset or scale."}))
                            continue
                        cur_md = read_md_safe(md_path)
                        if op == "clone" and len(cur_md.encode("utf-8", errors="replace")) > MAX_FILE_SIZE - 4096:
                            await websocket.send(json.dumps({"type": "error", "msg": "World size limit reached."}))
                            continue
                        create_world_snapshot(target, label="auto")
                        updated_md, ok_mut, status_msg = mutate_placed_md_snippet(
                            cur_md,
                            tag_id,
                            op=op,
                            dx=max(-200.0, min(200.0, float(raw_m[0]))),
                            dy=max(-200.0, min(200.0, float(raw_m[1]))),
                            dz=max(-200.0, min(200.0, float(raw_m[2]))),
                            yaw_delta_deg=float(raw_m[3]),
                            rgb=parse_color(data.get("color", "cyan")) if op == "paint" else None,
                            scale_mult=float(raw_m[4]),
                        )
                        if not ok_mut:
                            await websocket.send(json.dumps({"type": "error", "msg": status_msg}))
                            continue
                        _atomic_write_text(md_path, updated_md)
                        await broadcast_world(target, _world_update_msg(updated_md))
                        await websocket.send(json.dumps({"type": "info", "msg": status_msg}))

                    elif action == "delete_placed":
                        tag_id = re.sub(r"[^a-fA-F0-9]", "", str(data.get("tag_id", ""))).lower()
                        cur_md = read_md_safe(md_path)
                        create_world_snapshot(target, label="auto")
                        updated_md, removed = remove_placed_md_snippet(cur_md, tag_id)
                        if not removed:
                            await websocket.send(json.dumps({"type": "error", "msg": f"Placed object '{tag_id}' not found."}))
                            continue
                        _atomic_write_text(md_path, updated_md)
                        await broadcast_world(target, _world_update_msg(updated_md))
                        await websocket.send(json.dumps({"type": "info", "msg": f"Removed [{tag_id}]."}))

                    elif action == "undo":
                        w_backup_dir = BACKUPS_DIR / target
                        auto_snaps = sorted(w_backup_dir.glob("*_auto*.md"), reverse=True) if w_backup_dir.exists() else []
                        if not auto_snaps:
                            await websocket.send(json.dumps({"type": "error", "msg": "Nothing to undo."}))
                            continue
                        latest = auto_snaps[0]
                        restored_md = read_md_safe(latest)
                        _atomic_write_text(md_path, restored_md)
                        latest.unlink(missing_ok=True)
                        latest.with_suffix(".meta.json").unlink(missing_ok=True)
                        parsed_undo = parse_mv3d(restored_md)
                        if parsed_undo.get("spawn_pos") is not None:
                            w_entry["spawn_pos"] = list(parsed_undo["spawn_pos"])
                            w_entry["spawn_yaw"] = float(parsed_undo.get("spawn_yaw", 0.0) or 0.0)
                        else:
                            w_entry.pop("spawn_pos", None)
                            w_entry.pop("spawn_yaw", None)
                        meta[target] = w_entry
                        save_world_meta(meta)
                        await broadcast_world(target, _world_update_msg(restored_md))
                        await websocket.send(json.dumps({"type": "info", "msg": f"Undid last change in '{target}'."}))

            elif msg_type == "upload":
                if client_state["role"] not in ("admin", "builder"):
                    await websocket.send(json.dumps({"type": "error", "msg": "Builder role required."}))
                    continue
                kind = str(data.get("kind", "")).strip().lower()
                if kind not in ("world", "avatar"):
                    await websocket.send(json.dumps({"type": "error", "msg": "Invalid upload kind."}))
                    continue
                name = sanitize_name(str(data.get("name", "custom")))
                content = str(data.get("content", "")).lstrip("\ufeff").replace("\r\n", "\n")
                size_limit = MAX_AVATAR_SIZE if kind == "avatar" else MAX_FILE_SIZE
                if len(content.encode("utf-8", errors="replace")) > size_limit:
                    await websocket.send(json.dumps({"type": "error", "msg": "File size limit exceeded."}))
                    continue
                ok_md, md_err, _ = validate_mv3d_document(content, kind=kind)
                if not ok_md:
                    await websocket.send(json.dumps({"type": "error", "msg": md_err}))
                    continue

                if kind == "world":
                    async with UPLOAD_LOCK:
                        meta = load_world_meta()
                        w_entry = meta.get(name, {})
                        w_path = _safe_child_path(WORLDS_DIR, f"{name}.md")
                        if w_path is None or name.lower() == "hub":
                            await websocket.send(json.dumps({"type": "error", "msg": "Cannot overwrite hub or invalid path."}))
                            continue
                        is_new = not w_path.exists()
                        if not is_new and not can_edit_world(name, w_entry, client_state["pubkey"], client_state["role"]):
                            await websocket.send(json.dumps({"type": "error", "msg": f"Access denied on '{name}'."}))
                            continue
                        if is_new and client_state["role"] != "admin":
                            perms = load_perms()
                            max_total = int(perms.get("max_total_worlds", 0) or 0)
                            max_per_user = int(perms.get("max_worlds_per_user", 0) or 0)
                            all_custom = [f.stem for f in WORLDS_DIR.glob("*.md") if f.stem.lower() != "hub"]
                            if max_total > 0 and len(all_custom) >= max_total:
                                await websocket.send(json.dumps({"type": "error", "msg": "Server world limit reached."}))
                                continue
                            if max_per_user > 0 and sum(1 for w in all_custom if meta.get(w, {}).get("owner") == client_state["pubkey"]) >= max_per_user:
                                await websocket.send(json.dumps({"type": "error", "msg": "Personal world limit reached."}))
                                continue
                        if not is_new:
                            create_world_snapshot(name, label="auto")
                        if not w_entry.get("owner"):
                            w_entry["owner"] = client_state["pubkey"]
                            w_entry["owner_nick"] = client_state["nick"]
                            w_entry["created_at"] = int(time.time())
                        w_entry["updated_at"] = int(time.time())
                        parsed_up = parse_mv3d(content)
                        if data.get("description"):
                            w_entry["description"] = re.sub(r'[\x00-\x1f\x7f"]', " ", str(data["description"])).strip()[:240]
                        elif parsed_up.get("world_info_desc"):
                            w_entry["description"] = re.sub(r'[\x00-\x1f\x7f"]', " ", str(parsed_up["world_info_desc"])).strip()[:240]
                        if parsed_up.get("title"):
                            w_entry["title"] = re.sub(r'[\x00-\x1f\x7f"]', " ", str(parsed_up["title"])).strip()[:64]
                        if parsed_up.get("tags"):
                            w_entry["tags"] = [sanitize_name(t) for t in parsed_up["tags"][:8] if sanitize_name(t)]
                        if parsed_up.get("spawn_pos") is not None:
                            w_entry["spawn_pos"] = list(parsed_up["spawn_pos"])
                            w_entry["spawn_yaw"] = float(parsed_up.get("spawn_yaw", 0.0) or 0.0)
                        else:
                            w_entry.pop("spawn_pos", None)
                            w_entry.pop("spawn_yaw", None)
                        meta[name] = w_entry
                        save_world_meta(meta)
                        _atomic_write_text(w_path, content)
                        worlds_list = [f.stem for f in WORLDS_DIR.glob("*.md") if can_visit_world(f.stem, meta.get(f.stem, {}), client_state["pubkey"], client_state["role"])]
                    if not is_new:
                        await broadcast_world(name, {
                            "type": "world_data",
                            "world": name,
                            "md_hash": compute_md_sha256(content),
                            "description": w_entry.get("description", ""),
                            "radio_url": w_entry.get("radio_url", ""),
                            "radio_name": w_entry.get("radio_name", ""),
                            "md": content,
                            "in_place": True,
                        }, exclude_ws=websocket)
                    await websocket.send(json.dumps({"type": "upload_ok", "kind": "world", "name": name, "worlds": worlds_list}))

                elif kind == "avatar":
                    if name.lower() == "default":
                        await websocket.send(json.dumps({"type": "error", "msg": "'default' is reserved."}))
                        continue
                    av_path = _safe_child_path(AVATARS_DIR, f"{name}.md")
                    if av_path is None:
                        continue
                    async with UPLOAD_LOCK:
                        perms = load_perms()
                        av_owners = perms.setdefault("avatar_owners", {})
                        if av_owners.get(name) and av_owners[name] != client_state["pubkey"] and client_state["role"] != "admin":
                            await websocket.send(json.dumps({"type": "error", "msg": f"Avatar '{name}' belongs to another user."}))
                            continue
                        av_owners.setdefault(name, client_state["pubkey"])
                        _atomic_write_text(av_path, content)
                        client_state["avatar"] = name
                        perms["user_avatars"][client_state["pubkey"]] = name
                        save_perms(perms)
                    await websocket.send(json.dumps({"type": "upload_ok", "kind": "avatar", "name": name, "md": content}))
                    await broadcast_world(client_state["world"], {"type": "peer_avatar", "short_id": client_state["short_id"], "avatar": name, "md": content}, exclude_ws=websocket)

            elif msg_type == "get_avatar":
                name = sanitize_name(str(data.get("name", "")))
                av_path = _safe_child_path(AVATARS_DIR, f"{name}.md")
                if av_path and av_path.exists():
                    await websocket.send(json.dumps({"type": "avatar_data", "name": name, "md": read_md_safe(av_path)}))

            elif msg_type == "set_avatar":
                name = sanitize_name(str(data.get("name", "default")))
                if name.lower() == "default":
                    client_state["avatar"] = "default"
                    perms = load_perms()
                    perms["user_avatars"].pop(client_state["pubkey"], None)
                    save_perms(perms)
                    await websocket.send(json.dumps({"type": "info", "msg": "Reset avatar to default.", "equipped_avatar": "default", "avatar_md": ""}))
                    await broadcast_world(client_state["world"], {"type": "peer_avatar", "short_id": client_state["short_id"], "avatar": "default", "md": ""}, exclude_ws=websocket)
                    continue
                av_path = _safe_child_path(AVATARS_DIR, f"{name}.md")
                if av_path is None or not av_path.exists():
                    await websocket.send(json.dumps({"type": "error", "msg": f"Avatar '{name}' not found."}))
                    continue
                av_md = read_md_safe(av_path)
                client_state["avatar"] = name
                perms = load_perms()
                perms["user_avatars"][client_state["pubkey"]] = name
                save_perms(perms)
                await websocket.send(json.dumps({"type": "info", "msg": f"Equipped '{name}'.", "equipped_avatar": name, "avatar_md": av_md}))
                await broadcast_world(client_state["world"], {"type": "peer_avatar", "short_id": client_state["short_id"], "avatar": name, "md": av_md}, exclude_ws=websocket)

            elif msg_type == "list_avatars":
                await websocket.send(json.dumps({"type": "avatars_list", "avatars": sorted(f.stem for f in AVATARS_DIR.glob("*.md")), "current": client_state["avatar"]}))

            elif msg_type == "set_nick":
                desired_raw = re.sub(r"[\x00-\x1f\x7f]", "", str(data.get("nick", ""))).strip()
                cleaned_nick = sanitize_name(desired_raw)[:24]
                if not cleaned_nick:
                    await websocket.send(json.dumps({"type": "error", "msg": "Nickname must contain letters, numbers, '_' or '-'."}))
                    continue
                async with UPLOAD_LOCK:
                    old_nick = client_state["nick"]
                    new_nick = allocate_unique_nick(cleaned_nick, client_state["pubkey"], exclude_ws=websocket)
                    client_state["nick"] = new_nick
                    remember_user_nick(client_state["pubkey"], new_nick)
                    w_meta_all = load_world_meta()
                    meta_changed = False
                    for _w_name, _w_ent in w_meta_all.items():
                        if isinstance(_w_ent, dict) and _w_ent.get("owner") == client_state["pubkey"]:
                            _w_ent["owner_nick"] = new_nick
                            meta_changed = True
                    if meta_changed:
                        save_world_meta(w_meta_all)
                await websocket.send(json.dumps({
                    "type": "nick_updated",
                    "nick": new_nick,
                    "requested": cleaned_nick,
                    "short_id": client_state["short_id"],
                }))
                if old_nick != new_nick:
                    await broadcast_world(client_state["world"], {
                        "type": "peer_nick",
                        "short_id": client_state["short_id"],
                        "old_nick": old_nick,
                        "nick": new_nick,
                    })

            elif msg_type == "admin_perm":
                if client_state["role"] != "admin":
                    await websocket.send(json.dumps({"type": "error", "msg": "Admin privileges required."}))
                    continue
                action = str(data.get("action", ""))
                raw_target = str(data.get("target_key", "")).strip()
                target_key = raw_target.lower()
                perms = load_perms()
                if action == "list_perms":
                    all_custom = [f.stem for f in WORLDS_DIR.glob("*.md") if f.stem.lower() != "hub"]
                    await websocket.send(json.dumps({"type": "perms_list", "perms": perms, "current_world_count": len(all_custom)}))
                    continue
                if action in ("grant_admin", "revoke_admin", "grant_builder", "revoke_builder", "ban", "unban"):
                    if not re.fullmatch(r"[0-9a-f]{64}", target_key):
                        resolved_pk, _ = resolve_user_key(raw_target, load_friends(), client_state["pubkey"])
                        if not resolved_pk:
                            await websocket.send(json.dumps({"type": "error", "msg": f"User '{raw_target}' not found or ambiguous (pass nick, 8-char ID, or 64-char key)."}))
                            continue
                        target_key = resolved_pk
                    if action == "grant_admin" and target_key not in perms["admins"]:
                        perms["admins"].append(target_key)
                    elif action == "revoke_admin":
                        if len(perms["admins"]) <= 1 and target_key in perms["admins"]:
                            await websocket.send(json.dumps({"type": "error", "msg": "Cannot remove last admin."}))
                            continue
                        perms["admins"] = [k for k in perms["admins"] if k != target_key]
                    elif action == "grant_builder" and target_key not in perms["builders"]:
                        perms["builders"].append(target_key)
                    elif action == "revoke_builder":
                        perms["builders"] = [k for k in perms["builders"] if k != target_key]
                    elif action == "ban":
                        if target_key == client_state["pubkey"]:
                            await websocket.send(json.dumps({"type": "error", "msg": "Cannot ban yourself."}))
                            continue
                        perms["admins"] = [k for k in perms["admins"] if k != target_key]
                        perms["builders"] = [k for k in perms["builders"] if k != target_key]
                        if target_key not in perms["banned"]:
                            perms["banned"].append(target_key)
                    elif action == "unban":
                        perms["banned"] = [k for k in perms["banned"] if k != target_key]
                elif action == "set_default" and target_key in ("guest", "builder"):
                    perms["default_role"] = target_key
                elif action in ("set_max_worlds", "set_user_world_limit") and _is_strict_int(data.get("limit")):
                    val = max(0, min(100000, int(data["limit"])))
                    perms["max_total_worlds" if action == "set_max_worlds" else "max_worlds_per_user"] = val
                else:
                    await websocket.send(json.dumps({"type": "error", "msg": "Invalid permission command."}))
                    continue
                save_perms(perms)
                for peer_ws, peer_st in list(CLIENTS.items()):
                    if not target_key or peer_st["pubkey"] == target_key:
                        new_r = get_role(peer_st["pubkey"])
                        peer_st["role"] = new_r
                        if new_r == "banned":
                            try:
                                await peer_ws.send(json.dumps({"type": "error", "msg": "Your key has been banned by an admin."}))
                                await peer_ws.close(code=1008)
                            except Exception:
                                pass
                        else:
                            await peer_ws.send(json.dumps({"type": "role_updated", "role": new_r}))
                await websocket.send(json.dumps({"type": "info", "msg": f"Updated permissions ({action})."}))

            elif msg_type == "world_acl":
                action = str(data.get("action", "list"))
                target = sanitize_name(str(data.get("world") or client_state["world"]))
                md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
                if md_path is None or not md_path.exists():
                    continue
                meta = load_world_meta()
                w_entry = meta.get(target, {})
                owner_key = w_entry.get("owner", "")
                if not (client_state["role"] == "admin" or (bool(owner_key) and owner_key == client_state["pubkey"])):
                    await websocket.send(json.dumps({"type": "error", "msg": "Only the world owner or admin can manage ACLs."}))
                    continue
                w_entry.setdefault("private", False)
                w_entry.setdefault("co_builders", [])
                w_entry.setdefault("whitelist", [])
                nick_reg = load_perms().get("nick_registry", {})
                online_by_pk = {st["pubkey"]: st for st in CLIENTS.values()}
                def _acl_members(klist):
                    return [
                        {
                            "pubkey": k,
                            "short_id": k[:8],
                            "nick": (online_by_pk[k]["nick"] if k in online_by_pk else "") or nick_reg.get(k) or k[:8],
                        }
                        for k in klist
                    ]
                if action == "list":
                    await websocket.send(json.dumps({
                        "type": "world_acl_info",
                        "world": target,
                        "private": w_entry["private"],
                        "co_builders": _acl_members(w_entry["co_builders"]),
                        "whitelist": _acl_members(w_entry["whitelist"]),
                    }))
                elif action == "set_private":
                    if target.lower() == "hub":
                        await websocket.send(json.dumps({"type": "error", "msg": "Hub cannot be made private."}))
                        continue
                    is_priv = bool(data.get("private", True))
                    w_entry["private"] = is_priv
                    meta[target] = w_entry
                    save_world_meta(meta)
                    if is_priv:
                        hub_meta = meta.get("hub", {})
                        for peer_ws, peer_st in list(CLIENTS.items()):
                            if peer_st["world"] == target and not can_visit_world(target, w_entry, peer_st["pubkey"], peer_st["role"]):
                                peer_st["world"] = "hub"
                                await broadcast_world(target, {"type": "peer_leave", "short_id": peer_st["short_id"]}, exclude_ws=peer_ws)
                                try:
                                    await peer_ws.send(json.dumps({"type": "info", "msg": f"World '{target}' is now PRIVATE. Returning to hub."}))
                                    await send_world_transition(peer_ws, peer_st, "hub", hub_meta)
                                except Exception:
                                    pass
                    await websocket.send(json.dumps({"type": "info", "msg": f"World '{target}' private={is_priv}."}))
                else:
                    r_key, r_nick = resolve_user_key(str(data.get("target", "")), load_friends(), client_state["pubkey"])
                    if not r_key:
                        await websocket.send(json.dumps({"type": "error", "msg": "User not found or ambiguous."}))
                        continue
                    async with UPLOAD_LOCK:
                        meta = load_world_meta()
                        w_entry = meta.get(target, {})
                        w_entry.setdefault("co_builders", [])
                        w_entry.setdefault("whitelist", [])
                        if action == "add_builder":
                            if len(w_entry["co_builders"]) >= 50:
                                await websocket.send(json.dumps({"type": "error", "msg": "Maximum 50 co-builders per world."}))
                                continue
                            if r_key not in w_entry["co_builders"]:
                                w_entry["co_builders"].append(r_key)
                        elif action == "remove_builder":
                            w_entry["co_builders"] = [k for k in w_entry["co_builders"] if k != r_key]
                        elif action == "allow_visitor":
                            if len(w_entry["whitelist"]) >= 200:
                                await websocket.send(json.dumps({"type": "error", "msg": "Maximum 200 whitelisted visitors per world."}))
                                continue
                            if r_key not in w_entry["whitelist"]:
                                w_entry["whitelist"].append(r_key)
                        elif action == "revoke_visitor":
                            w_entry["whitelist"] = [k for k in w_entry["whitelist"] if k != r_key]
                        else:
                            await websocket.send(json.dumps({"type": "error", "msg": "Unknown world ACL action."}))
                            continue
                        meta[target] = w_entry
                        save_world_meta(meta)
                    await websocket.send(json.dumps({"type": "info", "msg": f"Updated ACL for {r_nick} on '{target}'."}))

            elif msg_type == "world_backup":
                action = str(data.get("action", ""))
                target = sanitize_name(str(data.get("world") or client_state["world"]))
                md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
                if md_path is None or not md_path.exists():
                    continue
                meta = load_world_meta()
                w_entry = meta.get(target, {})
                if not can_edit_world(target, w_entry, client_state["pubkey"], client_state["role"]):
                    await websocket.send(json.dumps({"type": "error", "msg": "Permission denied."}))
                    continue
                if action == "create":
                    snap = create_world_snapshot(target, label=str(data.get("label", "")))
                    await websocket.send(json.dumps({"type": "info", "msg": f"Created snapshot: {snap}"}))
                elif action == "list":
                    await websocket.send(json.dumps({"type": "world_backups_list", "world": target, "backups": list_world_snapshots(target)}))
                elif action == "restore":
                    raw_file = str(data.get("file", "")).strip()
                    if not re.fullmatch(r"[A-Za-z0-9_-]+\.md", raw_file):
                        await websocket.send(json.dumps({"type": "error", "msg": "Invalid backup filename."}))
                        continue
                    snap_path = _safe_child_path(BACKUPS_DIR / target, raw_file)
                    if snap_path is None or not snap_path.exists():
                        await websocket.send(json.dumps({"type": "error", "msg": "Backup not found."}))
                        continue
                    async with UPLOAD_LOCK:
                        create_world_snapshot(target, label="pre_restore")
                        restored_md = read_md_safe(snap_path)
                        _atomic_write_text(md_path, restored_md)
                        parsed_rest = parse_mv3d(restored_md)
                        if parsed_rest.get("spawn_pos") is not None:
                            w_entry["spawn_pos"] = list(parsed_rest["spawn_pos"])
                            w_entry["spawn_yaw"] = float(parsed_rest.get("spawn_yaw", 0.0) or 0.0)
                        else:
                            w_entry.pop("spawn_pos", None)
                            w_entry.pop("spawn_yaw", None)
                        w_entry["updated_at"] = int(time.time())
                        meta[target] = w_entry
                        save_world_meta(meta)
                    rest_pkt = {
                        "type": "world_data",
                        "world": target,
                        "md_hash": compute_md_sha256(restored_md),
                        "description": w_entry.get("description", ""),
                        "radio_url": w_entry.get("radio_url", ""),
                        "radio_name": w_entry.get("radio_name", ""),
                        "md": restored_md,
                        "in_place": True,
                    }
                    if w_entry.get("spawn_pos"):
                        rest_pkt["spawn_pos"] = w_entry["spawn_pos"]
                        rest_pkt["spawn_yaw"] = float(w_entry.get("spawn_yaw", 0.0) or 0.0)
                    await broadcast_world(target, rest_pkt)
                    await websocket.send(json.dumps({"type": "info", "msg": f"Restored '{target}' from {raw_file}."}))
                elif action == "export":
                    await websocket.send(json.dumps({"type": "world_export_data", "world": target, "md": read_md_safe(md_path), "meta": w_entry, "save_path": str(data.get("save_path", ""))}))

            elif msg_type == "federation":
                action = str(data.get("action", "list"))
                fed = load_federation()
                if action == "list":
                    peers_map = fed.get("peers", {})
                    urls = list(peers_map.keys())
                    statuses = await asyncio.gather(*(probe_federated_node(u) for u in urls)) if urls else []
                    out_peers = [
                        {"url": u, "label": peers_map[u].get("label", u), "online": st.get("online", False), "online_count": st.get("online_count", 0), "worlds": st.get("worlds", [])}
                        for u, st in zip(urls, statuses)
                    ]
                    await websocket.send(json.dumps({"type": "federation_list", "node_name": fed.get("node_name", "Makerverse Node"), "node_desc": fed.get("node_desc", ""), "peers": out_peers}))
                elif client_state["role"] == "admin" and action == "link":
                    ok_u, norm_url, err_u = validate_ws_peer_url(str(data.get("url", "")))
                    if not ok_u:
                        await websocket.send(json.dumps({"type": "error", "msg": err_u}))
                        continue
                    label = sanitize_name(str(data.get("label", ""))) or sanitize_name(urllib.parse.urlsplit(norm_url).hostname or "node")
                    fed["peers"][norm_url] = {"label": label, "default_world": "hub", "added_at": int(time.time())}
                    save_federation(fed)
                    await websocket.send(json.dumps({"type": "info", "msg": f"Linked '{label}' ({norm_url})."}))
                elif client_state["role"] == "admin" and action == "unlink":
                    raw_t = str(data.get("url", "")).strip()
                    _, norm_u, _ = validate_ws_peer_url(raw_t)
                    removed = None
                    for k, v in list(fed.get("peers", {}).items()):
                        if k in (raw_t, norm_u) or v.get("label", "").lower() == raw_t.lower():
                            removed = k
                            fed["peers"].pop(k, None)
                            break
                    if removed:
                        save_federation(fed)
                        await websocket.send(json.dumps({"type": "info", "msg": f"Unlinked '{removed}'."}))

            elif msg_type == "set_world_meta":
                target = sanitize_name(str(data.get("world") or client_state["world"]))
                md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
                if md_path is None or not md_path.exists():
                    await websocket.send(json.dumps({"type": "error", "msg": f"World '{target}' does not exist."}))
                    continue
                fm_changed = False
                async with UPLOAD_LOCK:
                    meta = load_world_meta()
                    w_entry = meta.get(target, {})
                    if not (client_state["role"] == "admin" or w_entry.get("owner") == client_state["pubkey"]):
                        await websocket.send(json.dumps({"type": "error", "msg": "Permission denied: only the world creator or an admin can edit metadata."}))
                        continue
                    fm_updates = {}
                    if "description" in data and data["description"] is not None:
                        w_entry["description"] = re.sub(r'[\x00-\x1f\x7f"]', " ", str(data["description"])).strip()[:240]
                        fm_updates["description"] = f'"{w_entry["description"]}"'
                    if "title" in data and str(data["title"]).strip():
                        w_entry["title"] = re.sub(r'[\x00-\x1f\x7f"]', " ", str(data["title"])).strip()[:64]
                        fm_updates["title"] = f'"{w_entry["title"]}"'
                    if "tags" in data and data["tags"] is not None:
                        raw_tags_in = data["tags"]
                        if isinstance(raw_tags_in, str):
                            raw_items = [t.lstrip("#").strip() for t in re.split(r"[,\s]+", raw_tags_in) if t.strip()]
                        elif isinstance(raw_tags_in, list):
                            raw_items = [
                                str(t).lstrip("#").strip()
                                for t in raw_tags_in[:32]
                                if isinstance(t, (str, int, float)) and not isinstance(t, bool) and str(t).strip()
                            ]
                        else:
                            raw_items = []
                        clean_tags = []
                        for t_item in raw_items:
                            st = sanitize_name(t_item).lower()[:24]
                            if st and st != "unnamed" and st not in clean_tags:
                                clean_tags.append(st)
                            if len(clean_tags) >= 8:
                                break
                        w_entry["tags"] = clean_tags
                        fm_updates["tags"] = f"[{', '.join(clean_tags)}]" if clean_tags else None
                    if "sky" in data and str(data["sky"]).strip():
                        sky_raw = re.sub(r'[\x00-\x1f\x7f"]', "", str(data["sky"])).strip()[:120]
                        fm_updates["sky"] = sky_raw
                    if "fog" in data and str(data["fog"]).strip():
                        fog_raw = re.sub(r'[\x00-\x1f\x7f"]', "", str(data["fog"])).strip()[:120]
                        fm_updates["fog"] = fog_raw
                    if "radio_url" in data and data["radio_url"] is not None:
                        r_url = str(data["radio_url"]).strip()
                        if r_url:
                            ok_r, err_r = await is_safe_public_stream_url_async(r_url)
                            if not ok_r:
                                await websocket.send(json.dumps({"type": "error", "msg": err_r}))
                                continue
                        w_entry["radio_url"] = r_url
                        safe_rname = re.sub(r'[\x00-\x1f\x7f"]', " ", str(data.get("radio_name", r_url))).strip()[:64]
                        w_entry["radio_name"] = safe_rname if r_url else ""
                        fm_updates["radio"] = f'{{ url: "{r_url}", name: "{w_entry["radio_name"]}" }}' if r_url else None
                    w_entry["updated_at"] = int(time.time())
                    meta[target] = w_entry
                    save_world_meta(meta)
                    if fm_updates:
                        new_md = update_mv3d_frontmatter(read_md_safe(md_path), fm_updates)
                        _atomic_write_text(md_path, new_md)
                        fm_changed = True
                desc_pkt = {
                    "type": "world_desc_updated",
                    "world": target,
                    "title": w_entry.get("title", target),
                    "description": w_entry.get("description", ""),
                    "tags": w_entry.get("tags", []),
                    "radio_url": w_entry.get("radio_url", ""),
                    "radio_name": w_entry.get("radio_name", ""),
                }
                await broadcast_world(target, desc_pkt)
                if client_state["world"] != target:
                    await websocket.send(json.dumps(desc_pkt))
                if fm_changed and ("sky" in data or "fog" in data):
                    cur_md_now = read_md_safe(md_path)
                    await broadcast_world(target, {
                        "type": "world_data",
                        "world": target,
                        "md_hash": compute_md_sha256(cur_md_now),
                        "description": w_entry.get("description", ""),
                        "radio_url": w_entry.get("radio_url", ""),
                        "radio_name": w_entry.get("radio_name", ""),
                        "md": cur_md_now,
                        "in_place": True,
                    })

            elif msg_type == "rate_world":
                action = str(data.get("action", "list")).lower()
                if action == "rate":
                    target = sanitize_name(str(data.get("world") or client_state["world"]))
                    md_path = _safe_child_path(WORLDS_DIR, f"{target}.md")
                    if md_path is None or not md_path.exists():
                        await websocket.send(json.dumps({"type": "error", "msg": f"World '{target}' does not exist."}))
                        continue
                    raw_stars = data.get("stars")
                    if not _is_strict_int(raw_stars) or not (1 <= raw_stars <= 5):
                        await websocket.send(json.dumps({"type": "error", "msg": "Stars must be an integer from 1 to 5."}))
                        continue
                    async with UPLOAD_LOCK:
                        meta = load_world_meta()
                        w_entry = meta.get(target, {})
                        if not can_visit_world(target, w_entry, client_state["pubkey"], client_state["role"]):
                            await websocket.send(json.dumps({"type": "error", "msg": f"Access denied: '{target}' is private."}))
                            continue
                        if w_entry.get("owner") == client_state["pubkey"]:
                            await websocket.send(json.dumps({"type": "error", "msg": "You cannot rate your own world."}))
                            continue
                        w_entry.setdefault("ratings", {})[client_state["pubkey"]] = {
                            "stars": raw_stars,
                            "review": re.sub(r"[\x00-\x1f\x7f]", " ", str(data.get("review", ""))).strip()[:140],
                            "nick": client_state["nick"],
                            "updated_at": int(time.time()),
                        }
                        meta[target] = w_entry
                        save_world_meta(meta)
                        avg_r, cnt_r, _ = summarize_world_ratings(w_entry)
                    await websocket.send(json.dumps({"type": "info", "msg": f"Rated '{target}' {raw_stars}/5 (avg {avg_r}/5 across {cnt_r} votes)."}))
                elif action == "top":
                    meta = load_world_meta()
                    ranked = []
                    for f in WORLDS_DIR.glob("*.md"):
                        if not can_visit_world(f.stem, meta.get(f.stem, {}), client_state["pubkey"], client_state["role"]):
                            continue
                        avg_r, cnt_r, _ = summarize_world_ratings(meta.get(f.stem, {}))
                        if cnt_r > 0:
                            ranked.append({"world": f.stem, "avg": avg_r, "count": cnt_r, "description": meta.get(f.stem, {}).get("description", "")})
                    ranked.sort(key=lambda x: (x["avg"], x["count"]), reverse=True)
                    await websocket.send(json.dumps({"type": "world_ratings_top", "worlds": ranked[:15]}))

            elif msg_type == "friend_action":
                action = str(data.get("action", ""))
                my_key = client_state["pubkey"]
                fdata = load_friends()
                fdata.setdefault("friends", {}).setdefault(my_key, [])
                fdata.setdefault("pending", {}).setdefault(my_key, [])
                nick_reg = load_perms().get("nick_registry", {})
                online_by_key = {st["pubkey"]: (ws_p, st) for ws_p, st in CLIENTS.items()}
                if action == "list":
                    await websocket.send(json.dumps({
                        "type": "friends_list",
                        "friends": [
                            {
                                "short_id": fk[:8],
                                "nick": online_by_key[fk][1]["nick"] if fk in online_by_key else nick_reg.get(fk, fk[:8]),
                                "online": fk in online_by_key,
                                "world": online_by_key[fk][1]["world"] if fk in online_by_key else "",
                            }
                            for fk in fdata["friends"][my_key]
                        ],
                        "pending": [
                            {"short_id": pk[:8], "nick": online_by_key[pk][1]["nick"] if pk in online_by_key else nick_reg.get(pk, pk[:8])}
                            for pk in fdata["pending"][my_key]
                        ],
                    }))
                    continue
                t_key, t_nick = resolve_user_key(str(data.get("target", "")), fdata, my_key)
                if not t_key or t_key == my_key:
                    await websocket.send(json.dumps({"type": "error", "msg": "User not found or ambiguous."}))
                    continue
                fdata["friends"].setdefault(t_key, [])
                fdata["pending"].setdefault(t_key, [])
                if action == "add" and t_key not in fdata["friends"][my_key]:
                    if len(fdata["friends"][my_key]) >= MAX_FRIENDS_PER_USER:
                        await websocket.send(json.dumps({"type": "error", "msg": "Friends list limit reached."}))
                        continue
                    if t_key in fdata["pending"][my_key]:
                        _link_mutual_friends(fdata, my_key, t_key)
                    elif my_key not in fdata["pending"][t_key]:
                        if len(fdata["pending"][t_key]) >= MAX_PENDING_PER_USER:
                            await websocket.send(json.dumps({"type": "error", "msg": "Recipient pending request inbox is full."}))
                            continue
                        fdata["pending"][t_key].append(my_key)
                    save_friends(fdata)
                    await websocket.send(json.dumps({"type": "info", "msg": f"Updated friend status with {t_nick}."}))
                elif action == "accept" and t_key in fdata["pending"][my_key]:
                    _link_mutual_friends(fdata, my_key, t_key)
                    save_friends(fdata)
                    await websocket.send(json.dumps({"type": "info", "msg": f"You and {t_nick} are now friends!"}))
                elif action == "remove":
                    fdata["friends"][my_key] = [k for k in fdata["friends"][my_key] if k != t_key]
                    fdata["friends"][t_key] = [k for k in fdata["friends"][t_key] if k != my_key]
                    save_friends(fdata)
                    await websocket.send(json.dumps({"type": "info", "msg": f"Removed {t_nick} from friends."}))
                elif action == "whisper" and t_key in fdata["friends"][my_key] and t_key in online_by_key:
                    pm_text = re.sub(r"[\x00-\x1f\x7f]", "", str(data.get("text", "")))[:300].strip()
                    if pm_text:
                        pkt = {"type": "whisper", "from_id": client_state["short_id"], "from_nick": client_state["nick"], "to_nick": t_nick, "text": pm_text}
                        await online_by_key[t_key][0].send(json.dumps({**pkt, "incoming": True}))
                        await websocket.send(json.dumps({**pkt, "incoming": False}))
                elif action == "goto" and t_key in fdata["friends"][my_key] and t_key in online_by_key:
                    _, t_st = online_by_key[t_key]
                    dest_w = t_st["world"]
                    dest_meta = load_world_meta().get(dest_w, {})
                    if not can_visit_world(dest_w, dest_meta, my_key, client_state["role"]):
                        await websocket.send(json.dumps({"type": "error", "msg": f"{t_nick} is in a private world."}))
                        continue
                    if client_state["world"] != dest_w:
                        old_w = client_state["world"]
                        client_state["world"] = dest_w
                        await broadcast_world(old_w, {"type": "peer_leave", "short_id": client_state["short_id"]}, exclude_ws=websocket)
                        await send_world_transition(websocket, client_state, dest_w, dest_meta)
                    fx, fy, fz = t_st["pos"]
                    await websocket.send(json.dumps({"type": "teleport", "pos": [round(fx, 2), round(fy + 2.0, 2), round(fz + 3.0, 2)], "msg": f"Teleported to {t_nick}."}))

            elif msg_type == "prefab_action":
                action = str(data.get("action", "list")).strip().lower()
                if action == "list":
                    names = sorted(p.stem for p in PREFABS_DIR.glob("*.md"))
                    await websocket.send(json.dumps({"type": "prefabs_list", "prefabs": names}))
                    continue

                if client_state["role"] not in ("admin", "builder"):
                    await websocket.send(json.dumps({"type": "error", "msg": "Builder role required for blueprints/prefabs."}))
                    continue

                if action == "save":
                    p_name = sanitize_name(str(data.get("name", "")))
                    if not p_name:
                        await websocket.send(json.dumps({"type": "error", "msg": "Specify a valid blueprint name."}))
                        continue
                    cnt_blocks = _finite_int(data.get("count", 1), 1, 1, 16)
                    target_w = sanitize_name(str(data.get("world") or client_state["world"]))
                    md_path = _safe_child_path(WORLDS_DIR, f"{target_w}.md")
                    if md_path is None or not md_path.exists():
                        await websocket.send(json.dumps({"type": "error", "msg": f"World '{target_w}' does not exist."}))
                        continue
                    async with UPLOAD_LOCK:
                        existing_prefabs = list(PREFABS_DIR.glob("*.md"))
                        p_path = _safe_child_path(PREFABS_DIR, f"{p_name}.md")
                        if p_path is None:
                            continue
                        if not p_path.exists() and len(existing_prefabs) >= MAX_PREFABS_TOTAL:
                            await websocket.send(json.dumps({"type": "error", "msg": f"Server blueprint library cap ({MAX_PREFABS_TOTAL}) reached."}))
                            continue
                        bp_doc, taken, err_bp = extract_placed_blueprint_md(read_md_safe(md_path), p_name, count=cnt_blocks)
                        if not bp_doc:
                            await websocket.send(json.dumps({"type": "error", "msg": err_bp}))
                            continue
                        if len(bp_doc.encode("utf-8", errors="replace")) > MAX_PREFAB_SIZE:
                            await websocket.send(json.dumps({"type": "error", "msg": "Blueprint exceeds max size."}))
                            continue
                        _atomic_write_text(p_path, bp_doc)
                        names = sorted(p.stem for p in PREFABS_DIR.glob("*.md"))
                    await websocket.send(json.dumps({"type": "prefab_saved", "name": p_name, "count": taken, "prefabs": names, "md": bp_doc}))

                elif action == "stamp":
                    p_name = sanitize_name(str(data.get("name", "")))
                    p_path = _safe_child_path(PREFABS_DIR, f"{p_name}.md")
                    if not p_name or p_path is None or not p_path.exists():
                        await websocket.send(json.dumps({"type": "error", "msg": f"Blueprint '{p_name}' not found. Run /prefabs to list."}))
                        continue
                    target_w = sanitize_name(str(data.get("world") or client_state["world"]))
                    md_path = _safe_child_path(WORLDS_DIR, f"{target_w}.md")
                    if md_path is None or not md_path.exists():
                        await websocket.send(json.dumps({"type": "error", "msg": f"World '{target_w}' does not exist."}))
                        continue
                    async with UPLOAD_LOCK:
                        meta = load_world_meta()
                        w_entry = meta.get(target_w, {})
                        if not can_edit_world(target_w, w_entry, client_state["pubkey"], client_state["role"]):
                            await websocket.send(json.dumps({"type": "error", "msg": f"Permission denied on '{target_w}'."}))
                            continue
                        raw_s = (data.get("x", 0.0), data.get("y", 0.0), data.get("z", -4.0), data.get("scale", 1.0), data.get("yaw", 0.0))
                        if not all(_is_finite_num(v) for v in raw_s):
                            await websocket.send(json.dumps({"type": "error", "msg": "Invalid stamp coordinates or scale."}))
                            continue
                        prefab_md = read_md_safe(p_path)
                        cur_md = read_md_safe(md_path)
                        create_world_snapshot(target_w, label="auto")

                        def_m = re.search(r"^#\s+@def\s+([A-Za-z0-9_-]+)\s*$", prefab_md, re.M)
                        def_key = def_m.group(1) if def_m else p_name
                        def_block = ""
                        if def_m and not re.search(rf"^#\s+@def\s+{re.escape(def_key)}\b", cur_md, re.M):
                            def_lines = []
                            for ln in prefab_md[def_m.start() :].splitlines():
                                if ln.strip() == "---":
                                    break
                                def_lines.append(ln)
                            def_block = "\n".join(def_lines).strip() + "\n\n"

                        snippet, tid = build_stamped_prefab_snippet(
                            p_name,
                            prefab_md,
                            x=max(-500.0, min(500.0, float(raw_s[0]))),
                            y=max(-100.0, min(500.0, float(raw_s[1]))),
                            z=max(-500.0, min(500.0, float(raw_s[2]))),
                            scale=max(0.2, min(10.0, float(raw_s[3]))),
                            yaw_deg=float(raw_s[4]),
                            creator_nick=client_state["nick"],
                        )
                        updated_md = cur_md.rstrip() + "\n\n" + def_block + snippet + "\n"
                        if len(updated_md.encode("utf-8", errors="replace")) > MAX_FILE_SIZE:
                            await websocket.send(json.dumps({"type": "error", "msg": "World size limit reached."}))
                            continue
                        _atomic_write_text(md_path, updated_md)
                        await broadcast_world(target_w, {
                            "type": "world_data",
                            "world": target_w,
                            "md_hash": compute_md_sha256(updated_md),
                            "description": w_entry.get("description", ""),
                            "radio_url": w_entry.get("radio_url", ""),
                            "radio_name": w_entry.get("radio_name", ""),
                            "md": updated_md,
                            "in_place": True,
                        })
                    await websocket.send(json.dumps({"type": "info", "msg": f"Stamped blueprint '{p_name}' [{tid}] into '{target_w}'."}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        IP_CONNECTIONS[remote_ip] = max(0, IP_CONNECTIONS.get(remote_ip, 1) - 1)
        if IP_CONNECTIONS.get(remote_ip, 0) == 0:
            IP_CONNECTIONS.pop(remote_ip, None)
        if websocket in CLIENTS:
            st = CLIENTS.pop(websocket)
            await broadcast_world(st["world"], {"type": "peer_leave", "short_id": st["short_id"]})


async def main():
    for d in (DATA_DIR, WORLDS_DIR, AVATARS_DIR, BACKUPS_DIR, PREFABS_DIR, SERVER_PLUGINS_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    load_server_plugins()
    ensure_hub_world()
    load_perms()
    print(f"[*] Makerverse (#MV3D) v2 Server running on ws://{HOST}:{PORT}")
    async with websockets.serve(handler, HOST, PORT, max_size=MAX_FILE_SIZE + 65536, ping_interval=20, ping_timeout=20):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
