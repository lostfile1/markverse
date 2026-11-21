import hashlib
import math
import re
import secrets

MAX_SCENE_OBJECTS = 4096
MAX_NESTING_DEPTH = 12
MAX_GRID_DIM = 256
MAX_MESH_VERTS = 16384
MAX_MESH_FACES = 16384
MAX_SCALE_VAL = 100.0
MAX_COORD_VAL = 5000.0

COLOR_NAMES = {
    "cyan": (0.0, 1.0, 0.85),
    "magenta": (1.0, 0.15, 0.75),
    "pink": (1.0, 0.35, 0.80),
    "blue": (0.20, 0.60, 1.0),
    "purple": (0.70, 0.25, 1.0),
    "violet": (0.60, 0.20, 0.95),
    "green": (0.15, 0.95, 0.40),
    "emerald": (0.10, 0.85, 0.45),
    "amber": (1.0, 0.65, 0.0),
    "gold": (1.0, 0.78, 0.15),
    "orange": (1.0, 0.45, 0.10),
    "red": (1.0, 0.20, 0.25),
    "white": (0.95, 0.97, 1.0),
    "silver": (0.72, 0.78, 0.86),
    "dark": (0.12, 0.14, 0.22),
}

WORLD_TEMPLATES = {
    "flat": {"label": "Blank Neon Grid", "desc": "Clean 120x120 dark cyber-grid floor ready for /place"},
    "cybercity": {"label": "Cyberpunk Megacity", "desc": "Neon-trimmed skyscrapers and central plaza"},
    "forest": {"label": "Emerald Pine Forest", "desc": "Grassy clearing surrounded by tall pine trees"},
    "desert": {"label": "Solar Dunes & Pyramids", "desc": "Golden sandstone pyramids under an amber sky"},
    "crystal": {"label": "Arctic Crystal Cavern", "desc": "Glacial ice floor with cyan & violet crystal spires"},
    "fortress": {"label": "Obsidian Citadel", "desc": "Walled stone keep with four corner watchtowers"},
    "island": {"label": "Tropical Atoll", "desc": "Sandy palm islands rising out of a turquoise sea"},
    "space": {"label": "Orbital Station Deck", "desc": "Deep-space platform ringed by glowing beacons"},
}


def _finite_float(val, default: float = 0.0, lo: float = -MAX_COORD_VAL, hi: float = MAX_COORD_VAL) -> float:
    if isinstance(val, bool):
        return default
    try:
        f = float(val)
        if not math.isfinite(f):
            return default
        return max(lo, min(hi, f))
    except (TypeError, ValueError):
        return default


def _finite_int(val, default: int = 0, lo: int = 0, hi: int = 100000) -> int:
    if isinstance(val, bool):
        return default
    try:
        i = int(val)
        return max(lo, min(hi, i))
    except (TypeError, ValueError):
        return default


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r = max(0, min(255, int(round(_finite_float(rgb[0], 0.0, 0.0, 1.0) * 255))))
    g = max(0, min(255, int(round(_finite_float(rgb[1], 0.0, 0.0, 1.0) * 255))))
    b = max(0, min(255, int(round(_finite_float(rgb[2], 0.0, 0.0, 1.0) * 255))))
    return f"#{r:02x}{g:02x}{b:02x}"


def parse_color(val, default=(0.45, 0.65, 0.85)) -> tuple[float, float, float]:
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        return (
            _finite_float(val[0], default[0], 0.0, 1.0),
            _finite_float(val[1], default[1], 0.0, 1.0),
            _finite_float(val[2], default[2], 0.0, 1.0),
        )
    s = str(val or "").strip().strip('"').strip("'").lower()
    if s in COLOR_NAMES:
        return COLOR_NAMES[s]
    hx = s.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{6}", hx):
        return (int(hx[0:2], 16) / 255.0, int(hx[2:4], 16) / 255.0, int(hx[4:6], 16) / 255.0)
    if re.fullmatch(r"[0-9a-f]{3}", hx):
        return (int(hx[0] * 2, 16) / 255.0, int(hx[1] * 2, 16) / 255.0, int(hx[2] * 2, 16) / 255.0)
    nums = [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", s)]
    if len(nums) >= 3:
        return (
            _finite_float(nums[0], default[0], 0.0, 1.0),
            _finite_float(nums[1], default[1], 0.0, 1.0),
            _finite_float(nums[2], default[2], 0.0, 1.0),
        )
    return default


def _split_top_level(inner: str, sep: str = ",") -> list[str]:
    items = []
    cur = []
    depth = 0
    in_quote = False
    quote_ch = ""
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and in_quote and i + 1 < len(inner):
            cur.append(ch)
            cur.append(inner[i + 1])
            i += 2
            continue
        if ch in ('"', "'"):
            if not in_quote:
                in_quote = True
                quote_ch = ch
            elif ch == quote_ch:
                in_quote = False
            cur.append(ch)
        elif not in_quote:
            if ch in "[{":
                depth += 1
                cur.append(ch)
            elif ch in "]}":
                depth = max(0, depth - 1)
                cur.append(ch)
            elif ch == sep and depth == 0:
                items.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        else:
            cur.append(ch)
        i += 1
    if cur:
        items.append("".join(cur))
    return items


def _parse_inline_val(raw: str, depth: int = 0):
    if depth > MAX_NESTING_DEPTH:
        return ""
    s = raw.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_inline_val(p, depth + 1) for p in _split_top_level(inner, ",") if p.strip()]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        out = {}
        if not inner:
            return out
        for item in _split_top_level(inner, ","):
            if ":" in item:
                k, v = item.split(":", 1)
                out[k.strip().lower()] = _parse_inline_val(v, depth + 1)
        return out
    try:
        if "." in s or "e" in s.lower():
            val = float(s)
            return val if math.isfinite(val) else 0.0
        return int(s)
    except ValueError:
        return s


def _axis_angle_to_quat(ax: float, ay: float, az: float, angle_rad: float):
    norm = math.sqrt(ax * ax + ay * ay + az * az)
    if norm < 1e-8 or abs(angle_rad) < 1e-8:
        return (0.0, 0.0, 0.0, 1.0)
    nx, ny, nz = ax / norm, ay / norm, az / norm
    s = math.sin(angle_rad * 0.5)
    return (nx * s, ny * s, nz * s, math.cos(angle_rad * 0.5))


def _euler_deg_to_quat(rx_deg: float, ry_deg: float, rz_deg: float):
    qx = _axis_angle_to_quat(1.0, 0.0, 0.0, math.radians(rx_deg))
    qy = _axis_angle_to_quat(0.0, 1.0, 0.0, math.radians(ry_deg))
    qz = _axis_angle_to_quat(0.0, 0.0, 1.0, math.radians(rz_deg))
    return _quat_mul(_quat_mul(qy, qx), qz)


def _quat_mul(q1, q2):
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return (
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    )


def _quat_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def _quat_rotate_vec(q, v):
    qx, qy, qz, qw = q
    if abs(qx) < 1e-8 and abs(qy) < 1e-8 and abs(qz) < 1e-8:
        return v
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def _quat_to_axis_angle_deg(q):
    x, y, z, w = q
    w = max(-1.0, min(1.0, w))
    angle_deg = math.degrees(2.0 * math.acos(w))
    s = math.sqrt(max(0.0, 1.0 - w * w))
    if s < 1e-6 or angle_deg < 1e-4:
        return (0.0, 1.0, 0.0, 0.0)
    return (x / s, y / s, z / s, angle_deg)


def _rot_list_to_quat(rot_val):
    if not isinstance(rot_val, (list, tuple)):
        return (0.0, 0.0, 0.0, 1.0)
    if len(rot_val) == 4:
        return _axis_angle_to_quat(
            _finite_float(rot_val[0], 0.0, -1.0, 1.0),
            _finite_float(rot_val[1], 1.0, -1.0, 1.0),
            _finite_float(rot_val[2], 0.0, -1.0, 1.0),
            math.radians(_finite_float(rot_val[3], 0.0, -3600.0, 3600.0)),
        )
    if len(rot_val) == 3:
        return _euler_deg_to_quat(
            _finite_float(rot_val[0], 0.0, -3600.0, 3600.0),
            _finite_float(rot_val[1], 0.0, -3600.0, 3600.0),
            _finite_float(rot_val[2], 0.0, -3600.0, 3600.0),
        )
    return (0.0, 0.0, 0.0, 1.0)


def parse_mv3d(md_text: str) -> dict:
    """Single-pass hardened parser for Makerverse 3D Markdown (#MV3D) worlds and avatars.
    Supports portals, interactive triggers, 3D sign billboards, sky/fog, radio, and @def/@use templates.
    """
    scene = {
        "title": "",
        "bg": (0.04, 0.01, 0.12),
        "sky_gradient": [(0.02, 0.05, 0.11), (0.05, 0.14, 0.22)],
        "fog": (0.05, 0.02, 0.15),
        "fog_range": 160.0,
        "objects": [],
        "portals": [],
        "triggers": [],
        "has_floor": False,
        "raw_len": len(md_text or ""),
        "raw_preview": "",
        "world_info_desc": "",
        "tags": [],
        "radio_url": "",
        "radio_name": "",
        "spawn_pos": None,
        "spawn_yaw": 0.0,
    }
    if not md_text or not md_text.strip():
        return scene

    clean = md_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = clean.splitlines()
    scene["raw_preview"] = " | ".join(ln.strip() for ln in lines if ln.strip())[:160]

    body_start = 0
    if lines and lines[0].strip() == "---":
        fm_lines = []
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                body_start = idx + 1
                break
            fm_lines.append(lines[idx])
        for fl in fm_lines:
            s = fl.strip()
            if not s or s.startswith("#") or ":" not in s:
                continue
            k, v = s.split(":", 1)
            key = k.strip().lower()
            val = _parse_inline_val(v)
            if key == "title":
                scene["title"] = str(val)[:64]
            elif key == "description":
                scene["world_info_desc"] = str(val)[:240]
            elif key == "tags" and isinstance(val, list):
                scene["tags"] = [str(t)[:24] for t in val[:8]]
            elif key == "sky":
                if isinstance(val, list) and val:
                    cols = [parse_color(c) for c in val[:4]]
                    scene["sky_gradient"] = cols
                    scene["bg"] = cols[0]
                else:
                    c = parse_color(val)
                    scene["bg"] = c
                    scene["sky_gradient"] = [c, c]
            elif key == "fog":
                if isinstance(val, dict):
                    if "color" in val:
                        scene["fog"] = parse_color(val["color"])
                    if "range" in val:
                        scene["fog_range"] = _finite_float(val["range"], 160.0, 10.0, 2000.0)
                else:
                    scene["fog"] = parse_color(val)
            elif key == "spawn" and isinstance(val, dict):
                p = val.get("pos")
                if isinstance(p, list) and len(p) >= 3:
                    scene["spawn_pos"] = [
                        _finite_float(p[0], 0.0, -500.0, 500.0),
                        _finite_float(p[1], 0.0, -100.0, 500.0),
                        _finite_float(p[2], 0.0, -500.0, 500.0),
                    ]
                if "yaw" in val:
                    scene["spawn_yaw"] = _finite_float(val["yaw"], 0.0, -3600.0, 3600.0) % 360.0
            elif key == "radio" and isinstance(val, dict):
                u = str(val.get("url", "")).strip()[:300]
                if u:
                    scene["radio_url"] = u
                    scene["radio_name"] = str(val.get("name", u)).strip()[:64]

    stack = [{
        "depth": 0,
        "name": "root",
        "pos": (0.0, 0.0, 0.0),
        "quat": (0.0, 0.0, 0.0, 1.0),
        "scale": (1.0, 1.0, 1.0),
        "local_pos": (0.0, 0.0, 0.0),
        "local_quat": (0.0, 0.0, 0.0, 1.0),
        "local_scale": (1.0, 1.0, 1.0),
        "is_def": False,
        "def_name": "",
        "records": [],
    }]
    defs_table: dict[str, list[dict]] = {}

    def _recompute_world_transform(node: dict, parent: dict):
        sp = (
            node["local_pos"][0] * parent["scale"][0],
            node["local_pos"][1] * parent["scale"][1],
            node["local_pos"][2] * parent["scale"][2],
        )
        rp = _quat_rotate_vec(parent["quat"], sp)
        node["pos"] = (parent["pos"][0] + rp[0], parent["pos"][1] + rp[1], parent["pos"][2] + rp[2])
        node["quat"] = _quat_mul(parent["quat"], node["local_quat"])
        node["scale"] = (
            parent["scale"][0] * node["local_scale"][0],
            parent["scale"][1] * node["local_scale"][1],
            parent["scale"][2] * node["local_scale"][2],
        )

    def _relative_to_ancestor(cur_node: dict, anc_node: dict):
        if cur_node is anc_node:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)
        dx = cur_node["pos"][0] - anc_node["pos"][0]
        dy = cur_node["pos"][1] - anc_node["pos"][1]
        dz = cur_node["pos"][2] - anc_node["pos"][2]
        unrot = _quat_rotate_vec(_quat_conj(anc_node["quat"]), (dx, dy, dz))
        asx = anc_node["scale"][0] if abs(anc_node["scale"][0]) > 1e-6 else 1.0
        asy = anc_node["scale"][1] if abs(anc_node["scale"][1]) > 1e-6 else 1.0
        asz = anc_node["scale"][2] if abs(anc_node["scale"][2]) > 1e-6 else 1.0
        rel_pos = (unrot[0] / asx, unrot[1] / asy, unrot[2] / asz)
        rel_quat = _quat_mul(_quat_conj(anc_node["quat"]), cur_node["quat"])
        rel_scale = (cur_node["scale"][0] / asx, cur_node["scale"][1] / asy, cur_node["scale"][2] / asz)
        return rel_pos, rel_quat, rel_scale

    def _flush_node(node: dict):
        if node.get("is_def") and node.get("def_name"):
            defs_table[node["def_name"]] = list(node["records"])

    for line in lines[body_start:]:
        if len(scene["objects"]) >= MAX_SCENE_OBJECTS:
            break
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue

        hm = re.match(r"^(#{1,6})\s+(.+)$", s)
        if hm:
            depth = len(hm.group(1))
            header_txt = hm.group(2).strip()[:64]
            while len(stack) > 1 and stack[-1]["depth"] >= depth:
                _flush_node(stack.pop())
            parent = stack[-1]
            is_def = False
            def_label = ""
            m_def = re.match(r"^@def\s+([A-Za-z0-9_-]+)", header_txt)
            if m_def:
                is_def = True
                def_label = m_def.group(1)
            elif ":" in header_txt:
                _, rhs = header_txt.split(":", 1)
                def_label = rhs.strip()
            else:
                def_label = header_txt

            node = {
                "depth": depth,
                "name": header_txt,
                "local_pos": (0.0, 0.0, 0.0),
                "local_quat": (0.0, 0.0, 0.0, 1.0),
                "local_scale": (1.0, 1.0, 1.0),
                "pos": parent["pos"],
                "quat": parent["quat"],
                "scale": parent["scale"],
                "is_def": is_def,
                "def_name": def_label,
                "records": [],
            }
            stack.append(node)
            continue

        bm = re.match(r"^[-*+]\s+\*\*([^*]+)\*\*\s*:\s*(.+)$", s)
        if not bm:
            continue
        prop = bm.group(1).strip().lower()
        val = _parse_inline_val(bm.group(2))
        cur = stack[-1]
        parent = stack[-2] if len(stack) >= 2 else stack[0]

        if prop in ("pos", "position") and isinstance(val, list) and len(val) >= 3:
            cur["local_pos"] = (
                _finite_float(val[0]),
                _finite_float(val[1]),
                _finite_float(val[2]),
            )
            _recompute_world_transform(cur, parent)
        elif prop in ("rot", "rotation"):
            cur["local_quat"] = _rot_list_to_quat(val)
            _recompute_world_transform(cur, parent)
        elif prop == "scale":
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                sv = _finite_float(val, 1.0, 0.05, MAX_SCALE_VAL)
                cur["local_scale"] = (sv, sv, sv)
            elif isinstance(val, list) and len(val) >= 3:
                cur["local_scale"] = (
                    _finite_float(val[0], 1.0, 0.05, MAX_SCALE_VAL),
                    _finite_float(val[1], 1.0, 0.05, MAX_SCALE_VAL),
                    _finite_float(val[2], 1.0, 0.05, MAX_SCALE_VAL),
                )
            _recompute_world_transform(cur, parent)
        elif prop == "portal" and isinstance(val, dict):
            dest_w = re.sub(r"[^a-zA-Z0-9_-]", "", str(val.get("to", "hub")))[:32] or "hub"
            rad = _finite_float(val.get("radius", 2.0), 2.0, 0.5, 15.0)
            p_pos = val.get("pos")
            portal_xyz = (
                (
                    _finite_float(p_pos[0], cur["pos"][0]),
                    _finite_float(p_pos[1], cur["pos"][1]),
                    _finite_float(p_pos[2], cur["pos"][2]),
                )
                if isinstance(p_pos, list) and len(p_pos) >= 3
                else cur["pos"]
            )
            raw_sp = val.get("spawn")
            target_spawn = (
                [
                    _finite_float(raw_sp[0], 0.0, -500.0, 500.0),
                    _finite_float(raw_sp[1], 0.0, -100.0, 500.0),
                    _finite_float(raw_sp[2], 0.0, -500.0, 500.0),
                ]
                if isinstance(raw_sp, list) and len(raw_sp) >= 3
                else None
            )
            target_yaw = (
                _finite_float(val["yaw"], 0.0, -3600.0, 3600.0) % 360.0
                if "yaw" in val and val["yaw"] is not None
                else None
            )
            raw_srv = str(val.get("server", "")).strip()
            target_server = (
                raw_srv[:200]
                if raw_srv and not any(ch in raw_srv for ch in ('"', "'", "\n", "\r", "\x00", " "))
                else ""
            )
            p_col = parse_color(val.get("color", "#00ffd9"))
            scene["portals"].append({
                "id": cur["def_name"] or f"portal_{len(scene['portals'])}",
                "to": dest_w,
                "pos": portal_xyz,
                "radius": rad,
                "color": p_col,
                "spawn": target_spawn,
                "yaw": target_yaw,
                "server": target_server,
            })
            # Visual beacon ring/box for portal (non-solid for physics, raycastable in inspect mode)
            scene["objects"].append({
                "kind": "cylinder",
                "pos": portal_xyz,
                "rot": (0.0, 1.0, 0.0, 0.0),
                "scale": (1.0, 1.0, 1.0),
                "radius": min(1.5, rad * 0.45),
                "height": 2.4,
                "diff": p_col,
                "emis": (p_col[0] * 0.65, p_col[1] * 0.65, p_col[2] * 0.65),
                "alpha": 0.45,
                "is_portal": True,
                "def_name": cur["def_name"] or f"PORTAL_{dest_w}",
            })
        elif prop == "trigger" and isinstance(val, dict):
            act = str(val.get("action", "toggle_glow")).strip().lower()
            if act not in ("toggle_glow", "move_offset"):
                act = "toggle_glow"
            t_node = str(val.get("target", "")).strip()[:64]
            rad = _finite_float(val.get("radius", 2.5), 2.5, 0.5, 20.0)
            mode = str(val.get("mode", "once")).strip().lower()
            if mode not in ("once", "toggle"):
                mode = "once"
            cooldown = _finite_float(val.get("cooldown", 1.5), 1.5, 0.2, 60.0)
            interact_req = bool(val.get("interact", False))
            lbl = re.sub(r"[\x00-\x1f\x7f]", " ", str(val.get("label", ""))).strip()[:48]
            off_raw = val.get("offset", [0.0, 3.0, 0.0])
            off_xyz = (
                (
                    _finite_float(off_raw[0], 0.0, -50.0, 50.0),
                    _finite_float(off_raw[1], 3.0, -50.0, 50.0),
                    _finite_float(off_raw[2], 0.0, -50.0, 50.0),
                )
                if isinstance(off_raw, list) and len(off_raw) >= 3
                else (0.0, 3.0, 0.0)
            )
            scene["triggers"].append({
                "id": cur["def_name"] or f"trig_{len(scene['triggers'])}",
                "pos": cur["pos"],
                "radius": rad,
                "action": act,
                "target": t_node,
                "offset": off_xyz,
                "mode": mode,
                "cooldown": cooldown,
                "interact": interact_req,
                "label": lbl,
            })
        elif prop == "@use":
            tmpl_name = str(val).strip()
            def_anc = next((n for n in reversed(stack) if n["is_def"]), None)
            for tmpl in defs_table.get(tmpl_name, []):
                if len(scene["objects"]) >= MAX_SCENE_OBJECTS:
                    break
                scaled_loc = (
                    tmpl["rel_pos"][0] * cur["scale"][0],
                    tmpl["rel_pos"][1] * cur["scale"][1],
                    tmpl["rel_pos"][2] * cur["scale"][2],
                )
                rot_off = _quat_rotate_vec(cur["quat"], scaled_loc)
                comp_q = _quat_mul(cur["quat"], tmpl["rel_quat"])
                inst = {
                    **tmpl,
                    "def_name": cur["def_name"] or tmpl_name,
                    "pos": (cur["pos"][0] + rot_off[0], cur["pos"][1] + rot_off[1], cur["pos"][2] + rot_off[2]),
                    "rot": _quat_to_axis_angle_deg(comp_q),
                    "scale": (
                        cur["scale"][0] * tmpl["rel_scale"][0],
                        cur["scale"][1] * tmpl["rel_scale"][1],
                        cur["scale"][2] * tmpl["rel_scale"][2],
                    ),
                }
                if def_anc is not None:
                    r_pos, r_quat, r_scale = _relative_to_ancestor(cur, def_anc)
                    sub_loc = (tmpl["rel_pos"][0] * r_scale[0], tmpl["rel_pos"][1] * r_scale[1], tmpl["rel_pos"][2] * r_scale[2])
                    sub_rot = _quat_rotate_vec(r_quat, sub_loc)
                    def_anc["records"].append({
                        **tmpl,
                        "rel_pos": (r_pos[0] + sub_rot[0], r_pos[1] + sub_rot[1], r_pos[2] + sub_rot[2]),
                        "rel_quat": _quat_mul(r_quat, tmpl["rel_quat"]),
                        "rel_scale": (r_scale[0] * tmpl["rel_scale"][0], r_scale[1] * tmpl["rel_scale"][1], r_scale[2] * tmpl["rel_scale"][2]),
                    })
                else:
                    scene["objects"].append(inst)
        elif prop in ("box", "sphere", "cylinder", "cone", "grid", "mesh", "sign") and isinstance(val, dict):
            diff = parse_color(val.get("color", "#73a6d9"))
            emis = parse_color(val.get("glow", "#000000"), default=(0.0, 0.0, 0.0))
            alpha = _finite_float(val.get("alpha", 1.0), 1.0, 0.15, 1.0)
            obj = {
                "kind": prop,
                "node_name": cur["name"],
                "pos": cur["pos"],
                "rot": _quat_to_axis_angle_deg(cur["quat"]),
                "scale": cur["scale"],
                "diff": diff,
                "emis": emis,
                "alpha": alpha,
                "def_name": cur["def_name"],
            }
            if prop == "sign":
                label_txt = re.sub(r"[\x00-\x1f\x7f]", " ", str(val.get("text", cur["def_name"] or "SIGN"))).strip()[:48]
                obj["kind"] = "box"
                obj["size"] = (3.0, 0.75, 0.14)
                obj["sign_text"] = label_txt
            elif prop == "box":
                sz = val.get("size", [2.0, 2.0, 2.0])
                sz_t = (
                    (_finite_float(sz[0], 2.0, 0.01, 500.0), _finite_float(sz[1], 2.0, 0.01, 500.0), _finite_float(sz[2], 2.0, 0.01, 500.0))
                    if isinstance(sz, list) and len(sz) >= 3
                    else (2.0, 2.0, 2.0)
                )
                obj["size"] = sz_t
                if sz_t[0] >= 30 and sz_t[2] >= 30 and sz_t[1] <= 1.0:
                    scene["has_floor"] = True
            elif prop == "sphere":
                obj["radius"] = _finite_float(val.get("radius", 1.0), 1.0, 0.01, 250.0)
            elif prop in ("cylinder", "cone"):
                obj["radius"] = _finite_float(val.get("radius", 1.0), 1.0, 0.01, 250.0)
                obj["height"] = _finite_float(val.get("height", 2.0), 2.0, 0.01, 500.0)
            elif prop == "grid":
                xd = _finite_int(val.get("xd", 2), 2, 2, MAX_GRID_DIM)
                zd = _finite_int(val.get("zd", 2), 2, 2, MAX_GRID_DIM)
                xs = _finite_float(val.get("xs", 1.0), 1.0, 0.05, 100.0)
                zs = _finite_float(val.get("zs", 1.0), 1.0, 0.05, 100.0)
                raw_h = val.get("heights", [])
                hs = [_finite_float(h, 0.0, -500.0, 500.0) for h in (raw_h if isinstance(raw_h, list) else [])[: xd * zd]]
                if len(hs) < xd * zd:
                    hs.extend([0.0] * (xd * zd - len(hs)))
                obj.update({"xd": xd, "zd": zd, "xs": xs, "zs": zs, "heights": hs})
                scene["has_floor"] = True
            elif prop == "mesh":
                raw_pts = val.get("points", [])
                pts = [
                    (_finite_float(p[0], 0.0, -500.0, 500.0), _finite_float(p[1], 0.0, -500.0, 500.0), _finite_float(p[2], 0.0, -500.0, 500.0))
                    for p in (raw_pts if isinstance(raw_pts, list) else [])[:MAX_MESH_VERTS]
                    if isinstance(p, list) and len(p) >= 3
                ]
                faces = val.get("faces", [])
                idxs = []
                for f in (faces if isinstance(faces, list) else [])[:MAX_MESH_FACES]:
                    if isinstance(f, list):
                        for i_v in f[:32]:
                            iv = _finite_int(i_v, -1, 0, max(0, len(pts) - 1))
                            if 0 <= iv < len(pts):
                                idxs.append(iv)
                        idxs.append(-1)
                obj.update({"kind": "faces", "pts": pts, "idxs": idxs})

            def_anc = next((n for n in reversed(stack) if n["is_def"]), None)
            if def_anc is not None:
                if cur is def_anc:
                    r_pos, r_quat, r_scale = cur["local_pos"], cur["local_quat"], cur["local_scale"]
                else:
                    r_pos, r_quat, r_scale = _relative_to_ancestor(cur, def_anc)
                    r_pos = (
                        def_anc["local_pos"][0] + r_pos[0],
                        def_anc["local_pos"][1] + r_pos[1],
                        def_anc["local_pos"][2] + r_pos[2],
                    )
                def_anc["records"].append({
                    **obj,
                    "rel_pos": r_pos,
                    "rel_quat": r_quat,
                    "rel_scale": r_scale,
                })
            else:
                scene["objects"].append(obj)

    while len(stack) > 1:
        _flush_node(stack.pop())

    return scene


def compute_md_sha256(md_text: str) -> str:
    return hashlib.sha256(md_text.encode("utf-8", errors="replace")).hexdigest()


def validate_mv3d_document(content: str, kind: str = "world") -> tuple[bool, str, list[str]]:
    warnings: list[str] = []
    if not isinstance(content, str) or not content.strip():
        return False, "Empty #MV3D Markdown document.", warnings
    if "\x00" in content:
        return False, "Binary NUL bytes detected; upload a UTF-8 .md file.", warnings

    clean = content.lstrip("\ufeff")
    if re.search(r"<\s*(?:script|iframe|object|embed|svg|math)\b|javascript\s*:|vbscript\s*:|data\s*:text/html", clean, re.I):
        return False, "Executable HTML/script/URI payloads are forbidden in #MV3D files.", warnings

    parsed = parse_mv3d(clean)
    objs = parsed.get("objects", [])
    if not objs:
        return False, "No 3D geometry primitives found in Markdown.", warnings

    if kind == "avatar" and len(objs) > 128:
        return False, f"Avatar has {len(objs)} primitives (max 128 allowed).", warnings

    kb = max(1, len(clean.encode("utf-8", errors="replace")) // 1024)
    warnings.insert(0, f"{len(objs)} primitive(s), {kb} KB - valid #MV3D scene.")
    return True, "", warnings


def update_mv3d_frontmatter(md_text: str, updates: dict) -> str:
    clean = md_text.lstrip("\ufeff").replace("\r\n", "\n")
    lines = clean.splitlines()
    fm: dict[str, str] = {}
    body_lines = lines

    if lines and lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                for fl in lines[1:idx]:
                    if ":" in fl and not fl.strip().startswith("#"):
                        k, v = fl.split(":", 1)
                        fm[k.strip().lower()] = v.strip()
                body_lines = lines[idx + 1 :]
                break

    for k, v in updates.items():
        if v is None:
            fm.pop(k, None)
        else:
            fm[k] = v

    out = ["---"]
    for k, v in fm.items():
        out.append(f"{k}: {v}")
    out.append("---")
    out.extend(body_lines)
    return "\n".join(out).rstrip() + "\n"


def build_placed_mv3d_snippet(
    shape: str,
    x: float,
    y: float,
    z: float,
    scale: float,
    rgb: tuple[float, float, float],
    creator_nick: str,
    tag_id: str | None = None,
    yaw_deg: float = 0.0,
) -> str:
    tid = (tag_id or secrets.token_hex(3)).lower()
    safe_nick = re.sub(r"[^a-zA-Z0-9_-]", "", creator_nick)[:24] or "builder"
    s = max(0.2, min(20.0, _finite_float(scale, 1.0, 0.2, 20.0)))
    x = _finite_float(x, 0.0, -500.0, 500.0)
    y = _finite_float(y, 0.0, -100.0, 500.0)
    z = _finite_float(z, 0.0, -500.0, 500.0)
    c_hex = rgb_to_hex(rgb)
    g_hex = rgb_to_hex((rgb[0] * 0.45, rgb[1] * 0.45, rgb[2] * 0.45))
    yaw = round(_finite_float(yaw_deg, 0.0) % 360.0, 1)

    if shape == "arch":
        return (
            f"<!-- PLACED:{tid} by {safe_nick} -->\n"
            f"# Node: PLACED_{tid}\n"
            f"* **pos**: [{x:.3f}, {y:.3f}, {z:.3f}]\n"
            f"* **rot**: [0, {yaw}, 0]\n"
            f"* **scale**: [1.0, 1.0, 1.0]\n"
            f"  ## Part: LeftPost\n"
            f"  * **pos**: [{-1.6 * s:.3f}, {1.8 * s:.3f}, 0]\n"
            f'  * **cylinder**: {{ radius: {0.25 * s:.3f}, height: {3.6 * s:.3f}, color: "{c_hex}", glow: "{g_hex}" }}\n'
            f"  ## Part: RightPost\n"
            f"  * **pos**: [{1.6 * s:.3f}, {1.8 * s:.3f}, 0]\n"
            f'  * **cylinder**: {{ radius: {0.25 * s:.3f}, height: {3.6 * s:.3f}, color: "{c_hex}", glow: "{g_hex}" }}\n'
            f"  ## Part: Lintel\n"
            f"  * **pos**: [0, {3.75 * s:.3f}, 0]\n"
            f'  * **box**: {{ size: [{3.8 * s:.3f}, {0.35 * s:.3f}, {0.8 * s:.3f}], color: "{c_hex}", glow: "{c_hex}" }}'
        )

    if shape == "pillar":
        cy, prim = round(y + 2.0 * s, 3), f'cylinder: {{ radius: {0.45 * s:.3f}, height: {4.0 * s:.3f}, color: "{c_hex}", glow: "{g_hex}" }}'
    elif shape == "sphere":
        cy, prim = round(y + 1.0 * s, 3), f'sphere: {{ radius: {1.0 * s:.3f}, color: "{c_hex}", glow: "{g_hex}" }}'
    elif shape == "cone":
        cy, prim = round(y + 1.25 * s, 3), f'cone: {{ radius: {1.1 * s:.3f}, height: {2.5 * s:.3f}, color: "{c_hex}", glow: "{g_hex}" }}'
    elif shape == "platform":
        cy, prim = round(y + 0.15 * s, 3), f'box: {{ size: [{4.0 * s:.3f}, {0.3 * s:.3f}, {4.0 * s:.3f}], color: "{c_hex}", glow: "{g_hex}" }}'
    elif shape == "wall":
        cy, prim = round(y + 1.5 * s, 3), f'box: {{ size: [{4.5 * s:.3f}, {3.0 * s:.3f}, {0.4 * s:.3f}], color: "{c_hex}", glow: "{g_hex}" }}'
    else:
        cy, prim = round(y + 1.0 * s, 3), f'box: {{ size: [{2.0 * s:.3f}, {2.0 * s:.3f}, {2.0 * s:.3f}], color: "{c_hex}", glow: "{g_hex}" }}'

    return (
        f"<!-- PLACED:{tid} by {safe_nick} -->\n"
        f"# Node: PLACED_{tid}\n"
        f"* **pos**: [{x:.3f}, {cy:.3f}, {z:.3f}]\n"
        f"* **rot**: [0, {yaw}, 0]\n"
        f"* **scale**: [1.0, 1.0, 1.0]\n"
        f"* **{prim.split(':', 1)[0]}**: {prim.split(':', 1)[1].strip()}"
    )


def build_placed_portal_snippet(
    dest_world: str,
    x: float,
    y: float,
    z: float,
    rgb: tuple[float, float, float],
    creator_nick: str,
    radius: float = 2.0,
    tag_id: str | None = None,
    yaw_deg: float = 0.0,
    target_server: str = "",
    target_spawn: list[float] | None = None,
    target_yaw: float | None = None,
) -> str:
    tid = (tag_id or secrets.token_hex(3)).lower()
    safe_nick = re.sub(r"[^a-zA-Z0-9_-]", "", creator_nick)[:24] or "builder"
    safe_dest = re.sub(r"[^a-zA-Z0-9_-]", "", str(dest_world or "hub"))[:32] or "hub"
    rad = round(_finite_float(radius, 2.0, 0.5, 15.0), 2)
    x = _finite_float(x, 0.0, -500.0, 500.0)
    cy = round(_finite_float(y, 0.0, -100.0, 500.0) + 1.2, 3)
    z = _finite_float(z, 0.0, -500.0, 500.0)
    c_hex = rgb_to_hex(rgb)
    yaw = round(_finite_float(yaw_deg, 0.0) % 360.0, 1)

    fields = [f'to: "{safe_dest}"', f"radius: {rad}", f'color: "{c_hex}"']
    clean_srv = re.sub(r'[\x00-\x1f\x7f"\'\\\s]', "", str(target_server or ""))[:200]
    if clean_srv:
        fields.append(f'server: "{clean_srv}"')
    if isinstance(target_spawn, (list, tuple)) and len(target_spawn) >= 3:
        sx = round(_finite_float(target_spawn[0], 0.0, -500.0, 500.0), 2)
        sy = round(_finite_float(target_spawn[1], 0.0, -100.0, 500.0), 2)
        sz = round(_finite_float(target_spawn[2], 0.0, -500.0, 500.0), 2)
        fields.append(f"spawn: [{sx}, {sy}, {sz}]")
    if target_yaw is not None:
        tyaw = round(_finite_float(target_yaw, 0.0) % 360.0, 1)
        fields.append(f"yaw: {tyaw}")

    return (
        f"<!-- PLACED:{tid} by {safe_nick} portal -->\n"
        f"# Node: PLACED_{tid}\n"
        f"* **pos**: [{x:.3f}, {cy:.3f}, {z:.3f}]\n"
        f"* **rot**: [0, {yaw}, 0]\n"
        f"* **scale**: [1.0, 1.0, 1.0]\n"
        f"* **portal**: {{ {', '.join(fields)} }}"
    )


def _find_placed_md_span(md_text: str, target_tag_id: str) -> tuple[int, int, str] | None:
    clean_id = re.sub(r"[^a-fA-F0-9]", "", target_tag_id).lower()
    if not clean_id:
        return None
    m = re.search(rf"<!--\s*PLACED:({re.escape(clean_id)})\b[^>]*-->", md_text, re.I)
    if not m:
        return None
    start_idx = m.start()
    rest = md_text[m.end() :]
    own_hdr = re.match(r"\s*#\s+Node:\s*PLACED_[a-fA-F0-9]+\b[^\n]*", rest)
    search_from = own_hdr.end() if own_hdr else 0
    nxt = re.search(r"\n(?=#\s+|<!--\s*PLACED:)", rest[search_from:])
    end_idx = m.end() + search_from + nxt.start() if nxt else len(md_text)
    return start_idx, end_idx, m.group(1).lower()


def remove_placed_md_snippet(md_text: str, target_tag_id: str) -> tuple[str, bool]:
    span = _find_placed_md_span(md_text, target_tag_id)
    if span is None:
        return md_text, False
    start_idx, end_idx, _ = span
    updated = re.sub(r"\n{3,}", "\n\n", md_text[:start_idx] + md_text[end_idx:]).strip() + "\n"
    return updated, True


def mutate_placed_md_snippet(
    md_text: str,
    target_tag_id: str,
    op: str,
    dx: float = 0.0,
    dy: float = 0.0,
    dz: float = 0.0,
    yaw_delta_deg: float = 0.0,
    rgb: tuple[float, float, float] | None = None,
    scale_mult: float = 1.0,
) -> tuple[str, bool, str]:
    span = _find_placed_md_span(md_text, target_tag_id)
    if span is None:
        return md_text, False, f"Placed object '{target_tag_id}' not found."
    start_idx, end_idx, canon_id = span
    block = md_text[start_idx:end_idx]

    if op == "move":
        m_pos = re.search(r"(\*\s+\*\*pos\*\*:\s*\[)([^\]]+)(\])", block)
        if not m_pos:
            return md_text, False, "Could not parse object pos."
        nums = [_finite_float(x) for x in m_pos.group(2).split(",")]
        nx = max(-500.0, min(500.0, round(nums[0] + _finite_float(dx), 3)))
        ny = max(-100.0, min(500.0, round(nums[1] + _finite_float(dy), 3)))
        nz = max(-500.0, min(500.0, round(nums[2] + _finite_float(dz), 3)))
        new_block = block[: m_pos.start()] + f"{m_pos.group(1)}{nx:.3f}, {ny:.3f}, {nz:.3f}{m_pos.group(3)}" + block[m_pos.end() :]
        return md_text[:start_idx] + new_block + md_text[end_idx:], True, f"Moved [{canon_id}] to ({nx:.2f}, {ny:.2f}, {nz:.2f})."

    if op == "rotate":
        m_rot = re.search(r"(\*\s+\*\*rot\*\*:\s*\[)([^\]]+)(\])", block)
        if not m_rot:
            return md_text, False, "Could not parse object rot."
        nums = [_finite_float(x) for x in m_rot.group(2).split(",")]
        new_yaw = round((nums[1] + _finite_float(yaw_delta_deg)) % 360.0, 1)
        new_block = block[: m_rot.start()] + f"{m_rot.group(1)}{nums[0]:.1f}, {new_yaw:.1f}, {nums[2]:.1f}{m_rot.group(3)}" + block[m_rot.end() :]
        return md_text[:start_idx] + new_block + md_text[end_idx:], True, f"Rotated [{canon_id}] to yaw {new_yaw:.1f} deg."

    if op == "paint" and rgb is not None:
        c_hex = rgb_to_hex(rgb)
        g_hex = rgb_to_hex((rgb[0] * 0.45, rgb[1] * 0.45, rgb[2] * 0.45))
        new_block = re.sub(r'color:\s*"#[0-9a-fA-F]{6}"', f'color: "{c_hex}"', block)
        new_block = re.sub(r'glow:\s*"#[0-9a-fA-F]{6}"', f'glow: "{g_hex}"', new_block)
        return md_text[:start_idx] + new_block + md_text[end_idx:], True, f"Repainted [{canon_id}] to {c_hex}."

    if op == "scale":
        mult = max(0.2, min(5.0, _finite_float(scale_mult, 1.0, 0.2, 5.0)))
        if "**portal**" in block:
            m_pr = re.search(r"(\*\s+\*\*portal\*\*:[^\n]*\bradius:\s*)([-+]?(?:\d*\.\d+|\d+))", block)
            if m_pr:
                new_rad = max(0.5, min(15.0, round(_finite_float(m_pr.group(2), 2.0, 0.5, 15.0) * mult, 2)))
                new_block = block[: m_pr.start()] + f"{m_pr.group(1)}{new_rad}" + block[m_pr.end() :]
                return md_text[:start_idx] + new_block + md_text[end_idx:], True, f"Scaled portal [{canon_id}] radius to {new_rad}m."
        m_sc = re.search(r"(\*\s+\*\*scale\*\*:\s*\[)([^\]]+)(\])", block)
        if not m_sc:
            return md_text, False, "Could not parse object scale."
        nums = [_finite_float(x, 1.0, 0.05, MAX_SCALE_VAL) for x in m_sc.group(2).split(",")]
        sx = max(0.2, min(20.0, round(nums[0] * mult, 3)))
        sy = max(0.2, min(20.0, round(nums[1] * mult, 3)))
        sz = max(0.2, min(20.0, round(nums[2] * mult, 3)))
        new_block = block[: m_sc.start()] + f"{m_sc.group(1)}{sx:.3f}, {sy:.3f}, {sz:.3f}{m_sc.group(3)}" + block[m_sc.end() :]
        return md_text[:start_idx] + new_block + md_text[end_idx:], True, f"Scaled [{canon_id}] to {sx:.2f}x."

    if op == "clone":
        new_tid = secrets.token_hex(3)
        cloned = re.sub(rf"PLACED:{re.escape(canon_id)}", f"PLACED:{new_tid}", block, flags=re.I)
        cloned = re.sub(rf"PLACED_{re.escape(canon_id)}", f"PLACED_{new_tid}", cloned, flags=re.I)
        m_pos = re.search(r"(\*\s+\*\*pos\*\*:\s*\[)([^\]]+)(\])", cloned)
        if m_pos:
            nums = [_finite_float(x) for x in m_pos.group(2).split(",")]
            off_x = _finite_float(dx) if (dx or dy or dz) else 1.5
            cx = max(-500.0, min(500.0, round(nums[0] + off_x, 3)))
            cy = max(-100.0, min(500.0, round(nums[1] + _finite_float(dy), 3)))
            cz = max(-500.0, min(500.0, round(nums[2] + _finite_float(dz), 3)))
            cloned = cloned[: m_pos.start()] + f"{m_pos.group(1)}{cx:.3f}, {cy:.3f}, {cz:.3f}{m_pos.group(3)}" + cloned[m_pos.end() :]
        return md_text.rstrip() + f"\n\n{cloned}\n", True, f"Cloned [{canon_id}] -> [{new_tid}]."

    return md_text, False, f"Unknown op '{op}'."


def build_default_hub_md() -> str:
    lines = [
        "---",
        'title: "NEO-GENESIS GRID // HUB"',
        'description: "Makerverse 3D Markdown Nexus - /worlds, /world <name>, /peers, /hop <node>"',
        "tags: [hub, nexus, makerverse]",
        'sky: ["#050f1c", "#0a2938", "#0f3d47"]',
        'fog: { color: "#08242e", range: 155 }',
        "spawn: { pos: [0, 2.25, 13.5], yaw: 0 }",
        "---",
        "",
        "# Node: GridFloor",
        "* **pos**: [0, -0.05, 0]",
        '* **box**: { size: [160, 0.1, 160], color: "#0a121c", glow: "#030f17" }',
        "",
        "# Node: CommandDais",
        "* **pos**: [0, 0, -10]",
        "  ## Part: LowerTier",
        "  * **pos**: [0, 0.35, 0]",
        '  * **box**: { size: [22.0, 0.7, 22.0], color: "#141f2e", glow: "#05242e" }',
        "  ## Part: UpperTier",
        "  * **pos**: [0, 1.05, 0]",
        '  * **box**: { size: [17.5, 0.7, 17.5], color: "#1a2638", glow: "#082e3d" }',
        "  ## Part: ObeliskBase",
        "  * **pos**: [0, 2.0, 0]",
        '  * **box**: { size: [3.2, 1.2, 3.2], color: "#142433", glow: "#00738c" }',
        "  ## Part: ObeliskSpire",
        "  * **pos**: [0, 6.8, 0]",
        '  * **box**: { size: [1.65, 8.6, 1.65], color: "#00f2eb", glow: "#00f2eb" }',
        "  ## Part: ObeliskCrown",
        "  * **pos**: [0, 12.4, 0]",
        '  * **cone**: { radius: 1.18, height: 2.6, color: "#1affff", glow: "#1affff" }',
        "",
        "# Node: EntranceStairs",
    ]
    for idx, (sy, sz) in enumerate(((0.18, 2.6), (0.52, 1.6), (0.88, 0.6), (1.22, -0.4))):
        lines.extend([
            f"  ## Part: Step_{idx}",
            f"  * **pos**: [0, {sy}, {sz}]",
            '  * **box**: { size: [4.6, 0.36, 1.2], color: "#1c293b", glow: "#083b4a" }',
        ])

    lines.extend([
        "",
        "# @def SkylineTower",
        '* **box**: { size: [6.5, 24.0, 6.5], color: "#0d1421", glow: "#050f1a" }',
        "  ## Part: NeonCrown",
        "  * **pos**: [0, 10.5, 0]",
        '  * **box**: { size: [6.9, 0.55, 6.9], color: "#00c8d9", glow: "#00c8d9" }',
        "",
        "# Node: SkylineRing",
    ])
    for i, (tx, tz) in enumerate(((-28, -34), (-16.5, -38), (16.5, -38), (29, -32), (-34, -12), (34, -10))):
        lines.extend([
            f"  ## Part: Tower_{i}",
            f"  * **pos**: [{tx}, 12.0, {tz}]",
            "  * **@use**: SkylineTower",
        ])

    lines.append("\n# Node: HaloOrbs")
    for orb_i in range(16):
        ang = (2.0 * math.pi * orb_i) / 16.0
        ox = round(math.cos(ang) * 3.2, 3)
        oz = round(-10.0 + math.sin(ang) * 3.2, 3)
        lines.extend([
            f"  ## Part: Orb_{orb_i}",
            f"  * **pos**: [{ox}, 14.8, {oz}]",
            '  * **sphere**: { radius: 0.22, color: "#ff26d1", glow: "#ff26d1" }',
        ])

    lines.extend([
        "",
        "# Node: NexusSign",
        "* **pos**: [0, 3.6, -5.5]",
        '* **sign**: { text: "MAKERVERSE NEXUS", color: "#00ffd9", glow: "#006655" }',
    ])
    return "\n".join(lines) + "\n"


def build_template_mv3d_world(template_key: str, world_name: str, custom_desc: str = "") -> str:
    tkey = template_key.lower() if template_key.lower() in WORLD_TEMPLATES else "flat"
    tmeta = WORLD_TEMPLATES[tkey]
    title_str = re.sub(r'["\r\n\x00]', " ", (custom_desc or tmeta["label"] or world_name)).strip()[:64]
    desc_str = re.sub(r'["\r\n\x00]', " ", (custom_desc or tmeta["desc"])).strip()[:200]
    seed = sum((idx + 1) * ord(ch) for idx, ch in enumerate(f"{world_name}:{tkey}")) or 42

    themes = {
        "flat": ('["#050a17", "#0d1f33"]', "#0a0f1c", "#00f2d9", "#ff26bf"),
        "cybercity": ('["#08031f", "#2e0a4d"]', "#0f1224", "#00ffd9", "#ff24bf"),
        "forest": ('["#245c8f", "#599ec7"]', "#1c5724", "#2eb840", "#855729"),
        "desert": ('["#6b2e12", "#bf6124"]', "#9e662e", "#eba33d", "#fa611a"),
        "crystal": ('["#0f2e61", "#3d7ab3"]', "#add1eb", "#59ebff", "#b380ff"),
        "fortress": ('["#1f1a33", "#473357"]', "#3d3d47", "#d9ad38", "#bf3340"),
        "island": ('["#0d4785", "#4da6d9"]', "#0a5294", "#ebc76b", "#26d9bf"),
        "space": ('["#03030d", "#170533"]', "#2e3347", "#1af2cc", "#ff850d"),
    }
    sky, ground_c, acc1, acc2 = themes[tkey]
    lines = [
        "---",
        f'title: "{title_str}"',
        f'description: "{desc_str}"',
        f"tags: [{tkey}, makerverse]",
        f"sky: {sky}",
        'fog: { color: "#0a101d", range: 160 }',
        "spawn: { pos: [0, 2.0, 12.0], yaw: 0 }",
        "---",
        "",
        "# Node: Ground",
        "* **pos**: [0, -0.05, 0]",
        f'* **box**: {{ size: [120, 0.1, 120], color: "{ground_c}", glow: "#05050a" }}',
        "",
        "# Node: Scenery",
    ]
    if tkey == "flat":
        lines.extend([
            "  ## Part: SpawnPad",
            "  * **pos**: [0, 0.08, 0]",
            '  * **box**: { size: [10, 0.16, 10], color: "#142438", glow: "#053342" }',
        ])
    else:
        for idx in range(14):
            ang = (2.0 * math.pi * idx) / 14.0
            rad = 13.5 + ((idx * 7 + seed) % 11)
            x = round(math.cos(ang) * rad, 2)
            z = round(math.sin(ang) * rad - 4.0, 2)
            h = round(3.2 + ((idx * 5 + seed) % 9), 2)
            col = acc1 if idx % 2 == 0 else acc2
            lines.append(f"  ## Part: Prop_{idx}")
            if tkey in ("forest", "desert", "crystal"):
                lines.append(f"  * **pos**: [{x}, {h * 0.5:.2f}, {z}]")
                lines.append(f'  * **cone**: {{ radius: {round(h * 0.45, 2)}, height: {h}, color: "{col}", glow: "{acc1}" }}')
            elif tkey == "space":
                lines.append(f"  * **pos**: [{x}, {round(2.5 + (idx % 4) * 1.6, 2)}, {z}]")
                lines.append(f'  * **sphere**: {{ radius: {round(1.1 + (idx % 3) * 0.5, 2)}, color: "{col}", glow: "{col}" }}')
            else:
                w = round(2.4 + ((idx + seed) % 3) * 0.9, 2)
                lines.append(f"  * **pos**: [{x}, {h * 0.5:.2f}, {z}]")
                lines.append(f'  * **box**: {{ size: [{w}, {h}, {w}], color: "{col}", glow: "#080c14" }}')
    return "\n".join(lines) + "\n"

def extract_placed_blueprint_md(md_text: str, prefab_name: str, count: int = 1) -> tuple[str, int, str]:
    """Extracts the last `count` placed object blocks from `md_text` and wraps them as a reusable #MV3D prefab."""
    clean_prefab = re.sub(r"[^a-zA-Z0-9_-]", "", str(prefab_name or ""))[:32] or "prefab"
    n_take = max(1, min(16, int(count)))
    matches = list(re.finditer(r"<!--\s*PLACED:([a-fA-F0-9]+)\b[^>]*-->", md_text, re.I))
    if not matches:
        return "", 0, "No placed objects found in this world to save into a blueprint."

    selected = matches[-n_take:]
    blocks = []
    for idx, m in enumerate(selected):
        start_idx = m.start()
        rest = md_text[m.end() :]
        own_hdr = re.match(r"\s*#\s+Node:\s*PLACED_[a-fA-F0-9]+\b[^\n]*", rest)
        search_from = own_hdr.end() if own_hdr else 0
        nxt = re.search(r"\n(?=#\s+|<!--\s*PLACED:)", rest[search_from:])
        end_idx = m.end() + search_from + nxt.start() if nxt else len(md_text)
        raw_blk = md_text[start_idx:end_idx].strip()
        lines = [ln for ln in raw_blk.splitlines() if not re.match(r"^<!--\s*PLACED:", ln.strip(), re.I)]
        if not lines:
            continue
        if re.match(r"^#\s+", lines[0].strip()):
            lines[0] = f"  ## Part_{idx}: " + re.sub(r"^#\s+", "", lines[0].strip())
        norm_lines = []
        for ln in lines:
            if ln.startswith("  ## "):
                norm_lines.append("  " + ln)
            elif ln.startswith("* "):
                norm_lines.append("  " + ln)
            else:
                norm_lines.append("    " + ln.lstrip())
        blocks.append("\n".join(norm_lines))

    if not blocks:
        return "", 0, "Could not extract valid geometry from placed objects."

    prefab_doc = (
        "---\n"
        f'title: "Blueprint: {clean_prefab}"\n'
        f'description: "Saved #MV3D prefab ({len(blocks)} block(s))"\n'
        "tags: [blueprint, prefab, makerverse]\n"
        "---\n\n"
        f"# @def {clean_prefab}\n"
        + "\n\n".join(blocks)
        + "\n"
    )
    return prefab_doc, len(blocks), ""


def build_stamped_prefab_snippet(
    prefab_name: str,
    prefab_md: str,
    x: float,
    y: float,
    z: float,
    scale: float,
    yaw_deg: float,
    creator_nick: str,
    tag_id: str | None = None,
) -> tuple[str, str]:
    """Creates a stamped #MV3D world node from a saved prefab blueprint with unique PLACED tag ID."""
    clean_prefab = re.sub(r"[^a-zA-Z0-9_-]", "", str(prefab_name or ""))[:32] or "prefab"
    tid = (tag_id or secrets.token_hex(3)).lower()
    safe_nick = re.sub(r"[^a-zA-Z0-9_-]", "", creator_nick)[:24] or "builder"
    s = max(0.2, min(10.0, _finite_float(scale, 1.0, 0.2, 10.0)))
    x = _finite_float(x, 0.0, -500.0, 500.0)
    y = _finite_float(y, 0.0, -100.0, 500.0)
    z = _finite_float(z, 0.0, -500.0, 500.0)
    yaw = round(_finite_float(yaw_deg, 0.0) % 360.0, 1)

    def_match = re.search(r"^#\s+@def\s+([A-Za-z0-9_-]+)\s*$", prefab_md, re.M)
    def_key = def_match.group(1) if def_match else clean_prefab

    snippet = (
        f"<!-- PLACED:{tid} by {safe_nick} prefab:{clean_prefab} -->\n"
        f"# Node: PLACED_{tid}\n"
        f"* **pos**: [{x:.3f}, {y:.3f}, {z:.3f}]\n"
        f"* **rot**: [0, {yaw}, 0]\n"
        f"* **scale**: [{s:.3f}, {s:.3f}, {s:.3f}]\n"
        f"* **@use**: {def_key}"
    )
    return snippet, tid
