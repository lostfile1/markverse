import hashlib
import ipaddress
import math
import re
import secrets
import socket
import urllib.parse

MAX_SCENE_OBJECTS = 4096
MAX_NESTING_DEPTH = 12
MAX_GRID_DIM = 256
MAX_MESH_VERTS = 16384
MAX_MESH_FACES = 16384
MAX_SCALE_VAL = 100.0
MAX_COORD_VAL = 5000.0

COLOR_NAMES = {
    "cyan": (0.0, 1.0, 0.85),
    "aqua": (0.0, 0.92, 0.95),
    "teal": (0.0, 0.72, 0.70),
    "mint": (0.20, 0.96, 0.72),
    "ice": (0.65, 0.90, 1.0),
    "magenta": (1.0, 0.15, 0.75),
    "pink": (1.0, 0.35, 0.80),
    "hotpink": (1.0, 0.12, 0.62),
    "neonpink": (1.0, 0.16, 0.78),
    "rose": (1.0, 0.28, 0.52),
    "crimson": (0.86, 0.08, 0.24),
    "coral": (1.0, 0.42, 0.32),
    "salmon": (0.98, 0.50, 0.44),
    "blue": (0.20, 0.60, 1.0),
    "neonblue": (0.12, 0.55, 1.0),
    "navy": (0.08, 0.16, 0.42),
    "indigo": (0.35, 0.22, 0.85),
    "purple": (0.70, 0.25, 1.0),
    "neonpurple": (0.76, 0.18, 1.0),
    "violet": (0.60, 0.20, 0.95),
    "green": (0.15, 0.95, 0.40),
    "neongreen": (0.18, 1.0, 0.35),
    "lime": (0.55, 1.0, 0.12),
    "emerald": (0.10, 0.85, 0.45),
    "olive": (0.48, 0.52, 0.16),
    "yellow": (1.0, 0.92, 0.15),
    "amber": (1.0, 0.65, 0.0),
    "gold": (1.0, 0.78, 0.15),
    "orange": (1.0, 0.45, 0.10),
    "neonorange": (1.0, 0.36, 0.05),
    "red": (1.0, 0.20, 0.25),
    "maroon": (0.52, 0.08, 0.14),
    "white": (0.95, 0.97, 1.0),
    "silver": (0.72, 0.78, 0.86),
    "gray": (0.48, 0.52, 0.58),
    "grey": (0.48, 0.52, 0.58),
    "black": (0.06, 0.07, 0.10),
    "brown": (0.52, 0.30, 0.14),
    "tan": (0.78, 0.62, 0.42),
    "beige": (0.86, 0.78, 0.64),
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
    s_compact = re.sub(r"[\s_-]+", "", s)
    if s_compact in COLOR_NAMES:
        return COLOR_NAMES[s_compact]
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


def is_safe_media_url(url: str, allow_unresolvable: bool = False) -> tuple[bool, str]:
    raw = str(url or "").strip()
    if not raw or len(raw) > 500 or any(ch in raw for ch in ('"', "'", "\n", "\r", "\x00", " ")):
        return False, "Media URL is invalid or contains forbidden characters."
    try:
        parsed = urllib.parse.urlsplit(raw)
    except Exception:
        return False, "Malformed media URL."
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False, "Media URL must use http:// or https://."
    host = parsed.hostname.strip().lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False, "Localhost and .local media URLs are blocked."
    try:
        ip_literal = ipaddress.ip_address(host)
        if (
            ip_literal.is_private
            or ip_literal.is_loopback
            or ip_literal.is_link_local
            or ip_literal.is_multicast
            or ip_literal.is_reserved
            or ip_literal.is_unspecified
        ):
            return False, "Private, loopback, link-local, or unspecified IPs are blocked for embedded media."
        return True, ""
    except ValueError:
        pass
    if allow_unresolvable:
        return True, ""
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False, f"Cannot resolve media host '{host}'."
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
                return False, "Blocked private/loopback IP for embedded media."
        except ValueError:
            return False, "Invalid resolved IP address for media host."
    return True, ""


def normalize_media_embed_url(url: str) -> tuple[str, str]:
    raw = str(url or "").strip()
    ok, _ = is_safe_media_url(raw, allow_unresolvable=True)
    if not ok:
        return "", "web"
    try:
        sp = urllib.parse.urlsplit(raw)
    except Exception:
        return raw, "web"
    host = (sp.hostname or "").lower()
    path = sp.path or ""
    qs = urllib.parse.parse_qs(sp.query or "")
    vid = ""
    if host in ("youtu.be", "www.youtu.be"):
        vid = path.strip("/").split("/", 1)[0]
    elif host.endswith("youtube.com"):
        if path.startswith("/watch"):
            vid = (qs.get("v") or [""])[0]
        elif path.startswith("/embed/"):
            vid = path.split("/embed/", 1)[1].split("/", 1)[0]
        elif path.startswith("/shorts/") or path.startswith("/live/"):
            parts_p = path.strip("/").split("/")
            if len(parts_p) >= 2:
                vid = parts_p[1]
    clean_vid = re.sub(r"[^a-zA-Z0-9_-]", "", vid)[:24]
    if clean_vid:
        return f"https://www.youtube.com/embed/{clean_vid}", "youtube"
    return raw, "web"


EXPRESSION_ALIASES = {
    "neutral": "neutral",
    "default": "neutral",
    "reset": "neutral",
    "normal": "neutral",
    "happy": "happy",
    "smile": "happy",
    "joy": "happy",
    "smirk": "smirk",
    "grin": "smirk",
    "angry": "angry",
    "mad": "angry",
    "frown": "angry",
    "surprised": "surprised",
    "surprise": "surprised",
    "shock": "surprised",
    "wow": "surprised",
}


def normalize_expression_name(raw_expr: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", str(raw_expr or "")).strip().lower()
    if not cleaned:
        return "neutral"
    return EXPRESSION_ALIASES.get(cleaned, cleaned[:24])


def parse_mv3d(md_text: str) -> dict:
    scene = {
        "title": "",
        "bg": (0.04, 0.01, 0.12),
        "sky_gradient": [(0.02, 0.05, 0.11), (0.05, 0.14, 0.22)],
        "fog": (0.05, 0.02, 0.15),
        "fog_range": 160.0,
        "objects": [],
        "portals": [],
        "triggers": [],
        "media_screens": [],
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
        "expressions": [],
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

            hdr_exprs = []
            for m_ex in re.finditer(r"\[(?:expr|expression|mood):\s*([a-zA-Z0-9_-]+)\]", header_txt, re.I):
                ne = normalize_expression_name(m_ex.group(1))
                if ne and ne not in hdr_exprs:
                    hdr_exprs.append(ne)
            if not hdr_exprs and parent.get("expressions"):
                hdr_exprs = list(parent["expressions"])

            node = {
                "depth": depth,
                "name": header_txt,
                "local_pos": (0.0, 0.0, 0.0),
                "local_quat": (0.0, 0.0, 0.0, 1.0),
                "local_scale": (1.0, 1.0, 1.0),
                "pos": parent["pos"],
                "quat": parent["quat"],
                "scale": parent["scale"],
                "expressions": hdr_exprs,
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
        elif prop in ("expression", "mood"):
            ne = normalize_expression_name(str(val))
            cur["expressions"] = [ne] if ne else []
        elif prop == "expressions":
            raw_elist = val if isinstance(val, list) else [val]
            elist = []
            for item_e in raw_elist:
                ne = normalize_expression_name(str(item_e))
                if ne and ne not in elist:
                    elist.append(ne)
            cur["expressions"] = elist
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
            if def_anc is not None and tmpl_name == def_anc.get("def_name"):
                continue
            for tmpl in defs_table.get(tmpl_name, []):
                if len(scene["objects"]) >= MAX_SCENE_OBJECTS:
                    break
                if def_anc is not None and len(def_anc["records"]) >= MAX_SCENE_OBJECTS:
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
        elif prop in ("media", "screen", "embed") and isinstance(val, dict):
            raw_u = str(val.get("url", "") or val.get("src", "")).strip()
            safe_u, auto_kind = normalize_media_embed_url(raw_u)
            req_t = str(val.get("type", auto_kind)).strip().lower()
            m_type = "youtube" if (req_t == "youtube" or auto_kind == "youtube") else "web"
            mw = _finite_float(val.get("width", 4.0), 4.0, 0.5, 50.0)
            mh = _finite_float(val.get("height", 2.25), 2.25, 0.5, 50.0)
            m_title = re.sub(r"[\x00-\x1f\x7f]", " ", str(val.get("title", "") or cur["def_name"] or m_type.upper())).strip()[:64]
            m_inter = bool(val.get("interactive", True))
            m_col = parse_color(val.get("color", "#101828"))
            m_glow = parse_color(val.get("glow", "#00ffd9" if m_type == "youtube" else "#00aaff"), default=(0.0, 0.4, 0.6))
            m_face = str(val.get("face", "front")).strip().lower()
            if m_face not in ("front", "back", "left", "right", "both"):
                m_face = "front"
            media_spec = {
                "id": cur["def_name"] or f"media_{len(scene['media_screens'])}",
                "url": raw_u[:500],
                "embed_url": safe_u[:500],
                "media_type": m_type,
                "width": mw,
                "height": mh,
                "title": m_title,
                "interactive": m_inter,
                "face": m_face,
                "pos": cur["pos"],
                "rot": _quat_to_axis_angle_deg(cur["quat"]),
                "scale": cur["scale"],
            }
            scene["media_screens"].append(media_spec)
            z_nudge = _quat_rotate_vec(cur["quat"], (0.0, 0.0, 0.06))
            screen_pos = (cur["pos"][0] + z_nudge[0], cur["pos"][1] + z_nudge[1], cur["pos"][2] + z_nudge[2])
            media_spec["pos"] = screen_pos
            scene["objects"].append({
                "kind": "box",
                "node_name": cur["name"],
                "pos": screen_pos,
                "rot": _quat_to_axis_angle_deg(cur["quat"]),
                "scale": cur["scale"],
                "size": (mw, mh, 0.06),
                "diff": m_col,
                "emis": m_glow,
                "alpha": 0.94,
                "def_name": cur["def_name"],
                "media": media_spec,
            })
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
                "expressions": list(cur.get("expressions") or []),
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

    for ms in parsed.get("media_screens", []):
        u_chk = ms.get("url", "")
        if u_chk:
            ok_u, err_u = is_safe_media_url(u_chk, allow_unresolvable=True)
            if not ok_u:
                return False, f"Invalid embedded media URL ({err_u}).", warnings

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


def prune_unused_defs_md(md_text: str) -> tuple[str, int]:
    clean = md_text.lstrip("\ufeff").replace("\r\n", "\n")
    used_names = set(re.findall(r"[-*+]\s+\*\*@use\*\*\s*:\s*([A-Za-z0-9_-]+)", clean, re.I))
    lines = clean.splitlines()
    out_lines: list[str] = []
    pruned = 0
    idx = 0
    in_frontmatter = False
    while idx < len(lines):
        ln = lines[idx]
        st = ln.strip()
        if idx == 0 and st == "---":
            in_frontmatter = True
            out_lines.append(ln)
            idx += 1
            continue
        if in_frontmatter:
            if st == "---":
                in_frontmatter = False
            out_lines.append(ln)
            idx += 1
            continue
        m_def = re.match(r"^(#{1,6})\s+@def\s+([A-Za-z0-9_-]+)\s*$", st)
        if m_def:
            def_depth = len(m_def.group(1))
            def_name = m_def.group(2)
            end_i = idx + 1
            while end_i < len(lines):
                hm = re.match(r"^(#{1,6})\s+", lines[end_i].strip())
                if hm and len(hm.group(1)) <= def_depth:
                    break
                end_i += 1
            if def_name not in used_names:
                pruned += 1
                while end_i < len(lines) and not lines[end_i].strip():
                    end_i += 1
                idx = end_i
                continue
        out_lines.append(ln)
        idx += 1
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip() + "\n"
    return cleaned, pruned


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
            f" ## Part: LeftPost\n"
            f" * **pos**: [{-1.6 * s:.3f}, {1.8 * s:.3f}, 0]\n"
            f' * **cylinder**: {{ radius: {0.25 * s:.3f}, height: {3.6 * s:.3f}, color: "{c_hex}", glow: "{g_hex}" }}\n'
            f" ## Part: RightPost\n"
            f" * **pos**: [{1.6 * s:.3f}, {1.8 * s:.3f}, 0]\n"
            f' * **cylinder**: {{ radius: {0.25 * s:.3f}, height: {3.6 * s:.3f}, color: "{c_hex}", glow: "{g_hex}" }}\n'
            f" ## Part: Lintel\n"
            f" * **pos**: [0, {3.75 * s:.3f}, 0]\n"
            f' * **box**: {{ size: [{3.8 * s:.3f}, {0.35 * s:.3f}, {0.8 * s:.3f}], color: "{c_hex}", glow: "{c_hex}" }}'
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
    clean_srv = re.sub(r'[\x00-\x1f\x7f"\\\s]', "", str(target_server or ""))[:200]
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
    raw_tok = str(target_tag_id or "").strip()
    if not raw_tok:
        return None
    clean_hex = re.sub(r"[^a-fA-F0-9]", "", raw_tok).lower()
    if clean_hex and re.fullmatch(r"[0-9a-fA-F]{4,8}", raw_tok):
        m = re.search(rf"<!--\s*PLACED:({re.escape(clean_hex)})\b[^>]*-->", md_text, re.I)
        if m:
            start_idx = m.start()
            rest = md_text[m.end() :]
            own_hdr = re.match(r"\s*#\s+Node:\s*PLACED_[a-fA-F0-9]+\b[^\n]*", rest)
            search_from = own_hdr.end() if own_hdr else 0
            nxt = re.search(r"\n(?=#\s+|<!--\s*PLACED:)", rest[search_from:])
            end_idx = m.end() + search_from + nxt.start() if nxt else len(md_text)
            return start_idx, end_idx, m.group(1).lower()
    clean_name = re.sub(r"[^a-zA-Z0-9_:-]", "", raw_tok).lower()
    if not clean_name:
        return None
    lines = md_text.splitlines(keepends=True)
    body_offset = 0
    if lines and lines[0].strip() == "---":
        body_offset += len(lines[0])
        for ln in lines[1:]:
            body_offset += len(ln)
            if ln.strip() == "---":
                break
    body = md_text[body_offset:]
    hdr_matches = []
    in_def = False
    for m_ln in re.finditer(r"^[ \t]*(?:<!--\s*PLACED:([a-fA-F0-9]+)\b[^>]*-->|(#{1,6})\s+(.+))$", body, re.M):
        hashes = m_ln.group(2)
        htitle = (m_ln.group(3) or "").strip()
        if hashes:
            depth = len(hashes)
            if re.match(r"^@def\b", htitle, re.I):
                in_def = True
            elif depth <= 2:
                in_def = False
            label = htitle.split(":", 1)[1].strip() if ":" in htitle else htitle
            hdr_matches.append((body_offset + m_ln.start(), body_offset + m_ln.end(), depth, label, in_def))
        else:
            hdr_matches.append((body_offset + m_ln.start(), body_offset + m_ln.end(), 1, (m_ln.group(1) or "").lower(), False))
    for idx_h, (h_start, h_end, h_depth, h_label, h_in_def) in enumerate(hdr_matches):
        if h_in_def:
            continue
        norm_lbl = re.sub(r"[^a-zA-Z0-9_:-]", "", h_label).lower()
        if norm_lbl == clean_name or norm_lbl.endswith(":" + clean_name):
            end_idx = len(md_text)
            for nxt_start, _, nxt_depth, _, _ in hdr_matches[idx_h + 1 :]:
                if nxt_depth <= h_depth:
                    end_idx = nxt_start
                    break
            return h_start, end_idx, h_label
    return None


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
        return md_text, False, f"Target object '{target_tag_id}' not found."
    start_idx, end_idx, canon_id = span
    block = md_text[start_idx:end_idx]

    if op == "move":
        m_pos = re.search(r"(\s*[-*+]\s+\*\*pos\*\*:\s*\[)([^\]]+)(\])", block)
        if not m_pos:
            return md_text, False, "Could not parse object pos."
        nums = [_finite_float(x) for x in m_pos.group(2).split(",")]
        nx = max(-500.0, min(500.0, round(nums[0] + _finite_float(dx), 3)))
        ny = max(-100.0, min(500.0, round(nums[1] + _finite_float(dy), 3)))
        nz = max(-500.0, min(500.0, round(nums[2] + _finite_float(dz), 3)))
        new_block = block[: m_pos.start()] + f"{m_pos.group(1)}{nx:.3f}, {ny:.3f}, {nz:.3f}{m_pos.group(3)}" + block[m_pos.end() :]
        return md_text[:start_idx] + new_block + md_text[end_idx:], True, f"Moved [{canon_id}] to ({nx:.2f}, {ny:.2f}, {nz:.2f})."

    if op == "rotate":
        m_rot = re.search(r"(\s*[-*+]\s+\*\*rot\*\*:\s*\[)([^\]]+)(\])", block)
        if m_rot:
            nums = [_finite_float(x) for x in m_rot.group(2).split(",")]
            new_yaw = round((nums[1] + _finite_float(yaw_delta_deg)) % 360.0, 1)
            new_block = block[: m_rot.start()] + f"{m_rot.group(1)}{nums[0]:.1f}, {new_yaw:.1f}, {nums[2]:.1f}{m_rot.group(3)}" + block[m_rot.end() :]
        else:
            m_pos = re.search(r"^(\s*[-*+]\s+\*\*pos\*\*:[^\n]+)$", block, re.M)
            if not m_pos:
                return md_text, False, "Could not parse object transform."
            indent = re.match(r"^(\s*)", m_pos.group(1)).group(1)
            new_yaw = round(_finite_float(yaw_delta_deg) % 360.0, 1)
            ins = f"\n{indent}* **rot**: [0.0, {new_yaw:.1f}, 0.0]"
            new_block = block[: m_pos.end()] + ins + block[m_pos.end() :]
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
        m_sc = re.search(r"(\s*[-*+]\s+\*\*scale\*\*:\s*\[)([^\]]+)(\])", block)
        if m_sc:
            nums = [_finite_float(x, 1.0, 0.05, MAX_SCALE_VAL) for x in m_sc.group(2).split(",")]
            sx = max(0.2, min(20.0, round(nums[0] * mult, 3)))
            sy = max(0.2, min(20.0, round(nums[1] * mult, 3)))
            sz = max(0.2, min(20.0, round(nums[2] * mult, 3)))
            new_block = block[: m_sc.start()] + f"{m_sc.group(1)}{sx:.3f}, {sy:.3f}, {sz:.3f}{m_sc.group(3)}" + block[m_sc.end() :]
        else:
            m_pos = re.search(r"^(\s*[-*+]\s+\*\*pos\*\*:[^\n]+)$", block, re.M)
            if not m_pos:
                return md_text, False, "Could not parse object scale."
            indent = re.match(r"^(\s*)", m_pos.group(1)).group(1)
            s_val = max(0.2, min(20.0, round(mult, 3)))
            ins = f"\n{indent}* **scale**: [{s_val:.3f}, {s_val:.3f}, {s_val:.3f}]"
            new_block = block[: m_pos.end()] + ins + block[m_pos.end() :]
            sx = s_val
        return md_text[:start_idx] + new_block + md_text[end_idx:], True, f"Scaled [{canon_id}] to {sx:.2f}x."

    if op == "clone":
        new_tid = secrets.token_hex(3)
        if re.search(r"<!--\s*PLACED:", block, re.I):
            cloned = re.sub(rf"PLACED:{re.escape(canon_id)}", f"PLACED:{new_tid}", block, flags=re.I)
            cloned = re.sub(rf"PLACED_{re.escape(canon_id)}", f"PLACED_{new_tid}", cloned, flags=re.I)
        else:
            clean_base = re.sub(r"[^a-zA-Z0-9_-]", "", canon_id)[:20] or "Obj"
            cloned_name = f"{clean_base}_{new_tid}"
            prop_lines = []
            for ln in block.splitlines():
                st_ln = ln.strip()
                if re.match(r"^[-*+]\s+\*\*", st_ln) and not re.match(r"^[-*+]\s+\*\*(?:pos|position)\*\*", st_ln, re.I):
                    prop_lines.append(st_ln)
            pos_m = re.search(r"^\s*[-*+]\s+\*\*(?:pos|position)\*\*:\s*\[[^\]]+\]", block, re.M | re.I)
            pos_ln = pos_m.group(0).strip() if pos_m else "* **pos**: [0.0, 0.0, 0.0]"
            cloned = "\n".join([
                f"<!-- PLACED:{new_tid} by admin -->",
                f"# Node: PLACED_{new_tid} ({cloned_name})",
                pos_ln,
                *prop_lines,
            ])
        m_pos = re.search(r"(\s*[-*+]\s+\*\*pos\*\*:\s*\[)([^\]]+)(\])", cloned)
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
        'description: "Retrofuturistic #MV3D Nexus - /worlds, /world <name>, /peers, /hop <node>"',
        "tags: [hub, nexus, retrofuturistic, makerverse]",
        'sky: ["#060414", "#160933", "#2b0f4a"]',
        'fog: { color: "#120829", range: 165 }',
        "spawn: { pos: [0, 2.25, 14.5], yaw: 0 }",
        "---",
        "",
        "# Node: GridFloor",
        "* **pos**: [0, -0.05, 0]",
        '* **box**: { size: [160, 0.1, 160], color: "#090b16", glow: "#040612" }',
        "",
        "# Node: CyberAvenue",
        "* **pos**: [0, 0.01, 0]",
        " ## Part: MainRunway",
        " * **pos**: [0, 0.0, 9.5]",
        ' * **box**: { size: [5.8, 0.02, 13.0], color: "#0e1528", glow: "#061a2e" }',
        " ## Part: TrimLeft",
        " * **pos**: [-3.05, 0.02, 9.5]",
        ' * **box**: { size: [0.18, 0.04, 13.0], color: "#00f5d4", glow: "#00f5d4" }',
        " ## Part: TrimRight",
        " * **pos**: [3.05, 0.02, 9.5]",
        ' * **box**: { size: [0.18, 0.04, 13.0], color: "#ff2a9d", glow: "#ff2a9d" }',
        " ## Part: CrossAxisBar",
        " * **pos**: [0, 0.015, -10.0]",
        ' * **box**: { size: [64.0, 0.03, 0.28], color: "#00f5d4", glow: "#00a896" }',
        " ## Part: NorthAxisBar",
        " * **pos**: [0, 0.015, -28.0]",
        ' * **box**: { size: [0.28, 0.03, 34.0], color: "#ff2a9d", glow: "#b5179e" }',
        "",
        "# Node: CommandDais",
        "* **pos**: [0, 0, -10]",
        " ## Part: LowerTier",
        " * **pos**: [0, 0.35, 0]",
        ' * **box**: { size: [22.0, 0.7, 22.0], color: "#11162b", glow: "#091533" }',
        " ## Part: LowerNeonTrim",
        " * **pos**: [0, 0.71, 0]",
        ' * **box**: { size: [22.25, 0.08, 22.25], color: "#ff2a9d", glow: "#ff2a9d" }',
        " ## Part: UpperTier",
        " * **pos**: [0, 1.05, 0]",
        ' * **box**: { size: [17.5, 0.7, 17.5], color: "#161d38", glow: "#0c1f42" }',
        " ## Part: UpperNeonTrim",
        " * **pos**: [0, 1.41, 0]",
        ' * **box**: { size: [17.75, 0.08, 17.75], color: "#00f5d4", glow: "#00f5d4" }',
        " ## Part: ObeliskPlinth",
        " * **pos**: [0, 1.75, 0]",
        ' * **box**: { size: [4.6, 0.7, 4.6], color: "#0f172a", glow: "#062c43" }',
        " ## Part: ObeliskBase",
        " * **pos**: [0, 2.5, 0]",
        ' * **box**: { size: [3.2, 1.2, 3.2], color: "#14213d", glow: "#0077b6" }',
        " ## Part: ObeliskSpire",
        " * **pos**: [0, 7.1, 0]",
        ' * **box**: { size: [1.55, 8.0, 1.55], color: "#00f5d4", glow: "#00d4ff" }',
        " ## Part: ObeliskCoreBand",
        " * **pos**: [0, 7.1, 0]",
        ' * **box**: { size: [1.82, 0.45, 1.82], color: "#ff2a9d", glow: "#ff2a9d" }',
        " ## Part: ObeliskCrown",
        " * **pos**: [0, 12.3, 0]",
        ' * **cone**: { radius: 1.25, height: 2.4, color: "#ffbe0b", glow: "#ff7b00" }',
        "",
        "# Node: EntranceStairs",
    ]
    for idx, (sy, sz) in enumerate(((0.175, 2.8), (0.525, 1.7), (0.875, 0.6), (1.225, -0.5))):
        trim_col = "#00f5d4" if idx % 2 == 0 else "#ff2a9d"
        lines.extend([
            f" ## Part: Step_{idx}",
            f" * **pos**: [0, {sy}, {sz}]",
            ' * **box**: { size: [5.6, 0.35, 1.4], color: "#161f38", glow: "#0a1936" }',
            f" ## Part: StepGlow_{idx}",
            f" * **pos**: [0, {round(sy + 0.18, 3)}, {round(sz + 0.66, 3)}]",
            f' * **box**: {{ size: [5.64, 0.05, 0.08], color: "{trim_col}", glow: "{trim_col}" }}',
        ])
    lines.extend([
        " ## Part: StairRailLeft",
        " * **pos**: [-2.95, 0.85, 1.15]",
        " * **rot**: [18.5, 0, 0]",
        ' * **box**: { size: [0.22, 0.24, 5.2], color: "#00f5d4", glow: "#00a896" }',
        " ## Part: StairRailRight",
        " * **pos**: [2.95, 0.85, 1.15]",
        " * **rot**: [18.5, 0, 0]",
        ' * **box**: { size: [0.22, 0.24, 5.2], color: "#ff2a9d", glow: "#b5179e" }',
        "",
        "# @def CyberArchGate",
        " ## Part: LeftColumn",
        " * **pos**: [-3.4, 2.6, 0]",
        ' * **box**: { size: [0.65, 5.2, 0.65], color: "#131b33", glow: "#091836" }',
        " ## Part: LeftStrip",
        " * **pos**: [-3.4, 2.6, 0.35]",
        ' * **box**: { size: [0.14, 4.8, 0.08], color: "#00f5d4", glow: "#00f5d4" }',
        " ## Part: RightColumn",
        " * **pos**: [3.4, 2.6, 0]",
        ' * **box**: { size: [0.65, 5.2, 0.65], color: "#131b33", glow: "#091836" }',
        " ## Part: RightStrip",
        " * **pos**: [3.4, 2.6, 0.35]",
        ' * **box**: { size: [0.14, 4.8, 0.08], color: "#ff2a9d", glow: "#ff2a9d" }',
        " ## Part: CrossBeam",
        " * **pos**: [0, 5.45, 0]",
        ' * **box**: { size: [7.8, 0.55, 0.85], color: "#1a2240", glow: "#10254a" }',
        " ## Part: BeamNeon",
        " * **pos**: [0, 5.78, 0]",
        ' * **box**: { size: [7.9, 0.12, 0.9], color: "#ffbe0b", glow: "#ff9e00" }',
        "",
        "# Node: PlazaGates",
        " ## Part: SouthEntranceGate",
        " * **pos**: [0, 0, 5.8]",
        " * **@use**: CyberArchGate",
        " ## Part: NorthPortalGate",
        " * **pos**: [0, 0, -24.5]",
        " * **@use**: CyberArchGate",
        "",
        "# Node: RetroHorizonMonolith",
        "* **pos**: [0, 0, -48.0]",
    ])
    sun_bands = [
        (3.5, 18.0, 1.6, "#ffbe0b"),
        (6.0, 22.0, 1.8, "#ff9e00"),
        (8.8, 24.5, 2.0, "#ff5400"),
        (11.8, 25.5, 2.1, "#ff2a9d"),
        (14.9, 24.0, 2.0, "#b5179e"),
        (17.8, 20.5, 1.7, "#7209b7"),
        (20.3, 15.0, 1.3, "#00f5d4"),
    ]
    for b_idx, (by, bw, bh, bcol) in enumerate(sun_bands):
        lines.extend([
            f" ## Part: HorizonBar_{b_idx}",
            f" * **pos**: [0, {by}, 0]",
            f' * **box**: {{ size: [{bw}, {bh}, 0.6], color: "{bcol}", glow: "{bcol}" }}',
        ])
    lines.extend([
        "",
        "# @def SkylineTower",
        '* **box**: { size: [6.4, 24.0, 6.4], color: "#0c1022", glow: "#060a1a" }',
        " ## Part: MidBand",
        " * **pos**: [0, 2.0, 0]",
        ' * **box**: { size: [6.75, 0.45, 6.75], color: "#ff2a9d", glow: "#ff2a9d" }',
        " ## Part: NeonCrown",
        " * **pos**: [0, 10.6, 0]",
        ' * **box**: { size: [6.85, 0.55, 6.85], color: "#00f5d4", glow: "#00f5d4" }',
        " ## Part: SpireMast",
        " * **pos**: [0, 14.2, 0]",
        ' * **cylinder**: { radius: 0.16, height: 4.4, color: "#ffbe0b", glow: "#ff9e00" }',
        "",
        "# Node: SkylineRing",
    ])
    for i, (tx, tz) in enumerate(((-30, -36), (-17.5, -41), (17.5, -41), (30, -36), (-36, -12), (36, -12), (-32, 14), (32, 14))):
        lines.extend([
            f" ## Part: Tower_{i}",
            f" * **pos**: [{tx}, 12.0, {tz}]",
            " * **@use**: SkylineTower",
        ])
    lines.append("\n# Node: HaloOrbs")
    for orb_i in range(16):
        ang = (2.0 * math.pi * orb_i) / 16.0
        ox = round(math.cos(ang) * 3.5, 3)
        oz = round(-10.0 + math.sin(ang) * 3.5, 3)
        orb_col = "#ff2a9d" if orb_i % 2 == 0 else "#00f5d4"
        lines.extend([
            f" ## Part: Orb_{orb_i}",
            f" * **pos**: [{ox}, 13.9, {oz}]",
            f' * **sphere**: {{ radius: 0.36, color: "{orb_col}", glow: "{orb_col}" }}',
        ])
    for low_i in range(8):
        ang_l = (2.0 * math.pi * low_i) / 8.0 + (math.pi / 8.0)
        lx = round(math.cos(ang_l) * 2.65, 3)
        lz = round(-10.0 + math.sin(ang_l) * 2.65, 3)
        lines.extend([
            f" ## Part: InnerBeacon_{low_i}",
            f" * **pos**: [{lx}, 8.6, {lz}]",
            ' * **sphere**: { radius: 0.28, color: "#ffbe0b", glow: "#ff9e00" }',
        ])
    lines.extend([
        "",
        "# Node: DaisCornerCrystals",
    ])
    for c_idx, (cx, cz, c_col) in enumerate(((-7.5, -17.5, "#00f5d4"), (7.5, -17.5, "#ff2a9d"), (-7.5, -2.5, "#ff2a9d"), (7.5, -2.5, "#00f5d4"))):
        lines.extend([
            f" ## Part: Crystal_{c_idx}",
            f" * **pos**: [{cx}, 3.1, {cz}]",
            f' * **cone**: {{ radius: 0.68, height: 2.6, color: "{c_col}", glow: "{c_col}" }}',
        ])
    lines.extend([
        "",
        "# Node: NexusSign",
        "* **pos**: [0, 6.45, 5.8]",
        '* **sign**: { text: "MAKERVERSE // NEO-GENESIS NEXUS", color: "#00f5d4", glow: "#007766" }',
    ])
    return "\n".join(lines) + "\n"


TEMPLATE_ALIASES = {
    "flat": "flat", "grid": "flat", "blank": "flat",
    "cybercity": "cybercity", "cyber": "cybercity", "city": "cybercity",
    "forest": "forest", "pine": "forest", "woods": "forest",
    "desert": "desert", "dunes": "desert", "sand": "desert",
    "crystal": "crystal", "crystals": "crystal", "arctic": "crystal",
    "arctic_crystal": "crystal", "arctic-crystal": "crystal", "arcticcrystal": "crystal",
    "ice": "crystal", "cavern": "crystal",
    "fortress": "fortress", "citadel": "fortress", "castle": "fortress",
    "island": "island", "atoll": "island", "tropical": "island",
    "space": "space", "station": "space", "orbit": "space",
}


def resolve_world_template_match(template_key: str) -> tuple[str, bool]:
    raw = str(template_key or "").strip().lower()
    if not raw:
        return "flat", True
    if raw in WORLD_TEMPLATES:
        return raw, True
    norm = re.sub(r"[^a-z0-9_-]", "", raw)
    if norm in TEMPLATE_ALIASES:
        return TEMPLATE_ALIASES[norm], True
    compact = norm.replace("_", "").replace("-", "")
    if compact in TEMPLATE_ALIASES:
        return TEMPLATE_ALIASES[compact], True
    return "flat", False


def resolve_world_template_key(template_key: str) -> str:
    resolved, _ = resolve_world_template_match(template_key)
    return resolved


def build_template_mv3d_world(template_key: str, world_name: str, custom_desc: str = "") -> str:
    tkey = resolve_world_template_key(template_key)
    tmeta = WORLD_TEMPLATES[tkey]
    title_str = re.sub(r'[\"\r\n\x00]', " ", " ".join(str(tmeta["label"] or world_name).split())).strip()[:64]
    desc_str = re.sub(r'[\"\r\n\x00]', " ", " ".join(str(custom_desc or tmeta["desc"]).split())).strip()[:200]
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
    tags_val = f"[{tkey}, arctic, makerverse]" if tkey == "crystal" else f"[{tkey}, makerverse]"
    fog_line = 'fog: { color: "#153652", range: 160 }' if tkey == "crystal" else 'fog: { color: "#0a101d", range: 160 }'
    lines = [
        "---",
        f'title: "{title_str}"',
        f'description: "{desc_str}"',
        f"tags: {tags_val}",
        f"sky: {sky}",
        fog_line,
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
            " ## Part: SpawnPad",
            " * **pos**: [0, 0.08, 0]",
            ' * **box**: { size: [10, 0.16, 10], color: "#142438", glow: "#053342" }',
        ])
    elif tkey == "crystal":
        for idx in range(16):
            ang = (2.0 * math.pi * idx) / 16.0
            rad = 11.5 + ((idx * 7 + seed) % 12)
            x = round(math.cos(ang) * rad, 2)
            z = round(math.sin(ang) * rad - 3.0, 2)
            if abs(x) < 3.2 and 7.5 < z < 15.0:
                x = round(x + (5.5 if x >= 0 else -5.5), 2)
            h = round(3.8 + ((idx * 5 + seed) % 9), 2)
            r_base = round(max(0.65, h * 0.34), 2)
            col = acc1 if idx % 2 == 0 else acc2
            lines.append(f" ## Part: CrystalSpire_{idx}")
            lines.append(f" * **pos**: [{x}, {h * 0.5:.2f}, {z}]")
            lines.append(f' * **cone**: {{ radius: {r_base}, height: {h}, color: "{col}", glow: "{col}" }}')
        for c_i, (cx, cz, ch) in enumerate(((-5.5, 2.0, 5.2), (5.5, 2.0, 5.8), (0.0, -7.5, 7.6))):
            c_col = acc1 if c_i % 2 == 0 else acc2
            lines.append(f" ## Part: CavernCluster_{c_i}")
            lines.append(f" * **pos**: [{cx}, {ch * 0.5:.2f}, {cz}]")
            lines.append(f' * **cone**: {{ radius: {round(ch * 0.32, 2)}, height: {ch}, color: "{c_col}", glow: "{c_col}" }}')
    else:
        for idx in range(14):
            ang = (2.0 * math.pi * idx) / 14.0
            rad = 13.5 + ((idx * 7 + seed) % 11)
            x = round(math.cos(ang) * rad, 2)
            z = round(math.sin(ang) * rad - 4.0, 2)
            h = round(3.2 + ((idx * 5 + seed) % 9), 2)
            col = acc1 if idx % 2 == 0 else acc2
            lines.append(f" ## Part: Prop_{idx}")
            if tkey in ("forest", "desert"):
                lines.append(f" * **pos**: [{x}, {h * 0.5:.2f}, {z}]")
                lines.append(f' * **cone**: {{ radius: {round(h * 0.45, 2)}, height: {h}, color: "{col}", glow: "{acc1}" }}')
            elif tkey == "space":
                lines.append(f" * **pos**: [{x}, {round(2.5 + (idx % 4) * 1.6, 2)}, {z}]")
                lines.append(f' * **sphere**: {{ radius: {round(1.1 + (idx % 3) * 0.5, 2)}, color: "{col}", glow: "{col}" }}')
            else:
                w = round(2.4 + ((idx + seed) % 3) * 0.9, 2)
                lines.append(f" * **pos**: [{x}, {h * 0.5:.2f}, {z}]")
                lines.append(f' * **box**: {{ size: [{w}, {h}, {w}], color: "{col}", glow: "#080c14" }}')
    return "\n".join(lines) + "\n"



def extract_placed_blueprint_md(md_text: str, prefab_name: str, count: int = 1) -> tuple[str, int, str]:
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
            lines[0] = f" ## Part_{idx}: " + re.sub(r"^#\s+", "", lines[0].strip())
        norm_lines = []
        for ln in lines:
            if ln.startswith(" ## "):
                norm_lines.append(" " + ln)
            elif ln.startswith("* "):
                norm_lines.append(" " + ln)
            else:
                norm_lines.append(" " + ln.lstrip())
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


def build_placed_media_snippet(
    url: str,
    x: float,
    y: float,
    z: float,
    creator_nick: str,
    media_type: str = "auto",
    width: float = 4.0,
    height: float = 2.25,
    title: str = "",
    tag_id: str | None = None,
    yaw_deg: float = 0.0,
) -> str:
    tid = (tag_id or secrets.token_hex(3)).lower()
    safe_nick = re.sub(r"[^a-zA-Z0-9_-]", "", creator_nick)[:24] or "builder"
    safe_url = re.sub(r'[\x00-\x1f\x7f"\\]', "", str(url or "").strip())[:500]
    _, auto_t = normalize_media_embed_url(safe_url)
    m_type = "youtube" if (str(media_type).lower() == "youtube" or auto_t == "youtube") else "web"
    w_val = round(_finite_float(width, 4.0, 0.5, 50.0), 2)
    h_val = round(_finite_float(height, 2.25, 0.5, 50.0), 2)
    clean_title = re.sub(r'[\x00-\x1f\x7f"\\]', " ", str(title or m_type.upper())).strip()[:64]
    px = _finite_float(x, 0.0, -500.0, 500.0)
    cy = round(_finite_float(y, 0.0, -100.0, 500.0) + h_val * 0.5, 3)
    pz = _finite_float(z, 0.0, -500.0, 500.0)
    yaw = round(_finite_float(yaw_deg, 0.0) % 360.0, 1)
    glow_hex = "#ff2442" if m_type == "youtube" else "#00b8ff"
    return (
        f"<!-- PLACED:{tid} by {safe_nick} media:{m_type} -->\n"
        f"# Node: PLACED_{tid}\n"
        f"* **pos**: [{px:.3f}, {cy:.3f}, {pz:.3f}]\n"
        f"* **rot**: [0, {yaw}, 0]\n"
        f"* **scale**: [1.0, 1.0, 1.0]\n"
        f'* **media**: {{ url: "{safe_url}", type: "{m_type}", width: {w_val}, height: {h_val}, title: "{clean_title}", glow: "{glow_hex}", interactive: true }}'
    )


PLACED_SHAPES = ("box", "pillar", "sphere", "cone", "arch", "platform", "wall")


def build_ghost_preview_objects(
    shape: str,
    x: float,
    y: float,
    z: float,
    scale: float,
    rgb: tuple[float, float, float],
    yaw_deg: float = 0.0,
    alpha: float = 0.42,
) -> list[dict]:
    sh = str(shape or "box").strip().lower()
    if sh not in PLACED_SHAPES:
        sh = "box"
    s = max(0.2, min(20.0, _finite_float(scale, 1.0, 0.2, 20.0)))
    px = _finite_float(x, 0.0, -500.0, 500.0)
    py = _finite_float(y, 0.0, -100.0, 500.0)
    pz = _finite_float(z, 0.0, -500.0, 500.0)
    yaw = round(_finite_float(yaw_deg, 0.0) % 360.0, 1)
    a_val = max(0.15, min(0.85, _finite_float(alpha, 0.42, 0.15, 0.85)))
    diff = (
        max(0.0, min(1.0, float(rgb[0]))),
        max(0.0, min(1.0, float(rgb[1]))),
        max(0.0, min(1.0, float(rgb[2]))),
    )
    emis = (diff[0] * 0.55, diff[1] * 0.55, diff[2] * 0.55)

    if sh == "arch":
        yr = math.radians(yaw)
        cos_y, sin_y = math.cos(yr), math.sin(yr)
        lx, lz = -1.6 * s * cos_y, 1.6 * s * sin_y
        rx, rz = 1.6 * s * cos_y, -1.6 * s * sin_y
        return [
            {"kind": "cylinder", "pos": (px + lx, py + 1.8 * s, pz + lz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "radius": 0.25 * s, "height": 3.6 * s, "diff": diff, "emis": emis, "alpha": a_val},
            {"kind": "cylinder", "pos": (px + rx, py + 1.8 * s, pz + rz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "radius": 0.25 * s, "height": 3.6 * s, "diff": diff, "emis": emis, "alpha": a_val},
            {"kind": "box", "pos": (px, py + 3.75 * s, pz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "size": (3.8 * s, 0.35 * s, 0.8 * s), "diff": diff, "emis": (diff[0] * 0.75, diff[1] * 0.75, diff[2] * 0.75), "alpha": a_val},
        ]
    if sh == "pillar":
        return [{"kind": "cylinder", "pos": (px, py + 2.0 * s, pz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "radius": 0.45 * s, "height": 4.0 * s, "diff": diff, "emis": emis, "alpha": a_val}]
    if sh == "sphere":
        return [{"kind": "sphere", "pos": (px, py + 1.0 * s, pz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "radius": 1.0 * s, "diff": diff, "emis": emis, "alpha": a_val}]
    if sh == "cone":
        return [{"kind": "cone", "pos": (px, py + 1.25 * s, pz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "radius": 1.1 * s, "height": 2.5 * s, "diff": diff, "emis": emis, "alpha": a_val}]
    if sh == "platform":
        return [{"kind": "box", "pos": (px, py + 0.15 * s, pz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "size": (4.0 * s, 0.3 * s, 4.0 * s), "diff": diff, "emis": emis, "alpha": a_val}]
    if sh == "wall":
        return [{"kind": "box", "pos": (px, py + 1.5 * s, pz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "size": (4.5 * s, 3.0 * s, 0.4 * s), "diff": diff, "emis": emis, "alpha": a_val}]
    return [{"kind": "box", "pos": (px, py + 1.0 * s, pz), "rot": (0.0, 1.0, 0.0, yaw), "scale": (1.0, 1.0, 1.0), "size": (2.0 * s, 2.0 * s, 2.0 * s), "diff": diff, "emis": emis, "alpha": a_val}]


RESERVED_ROLE_LABELS = frozenset({
    "admin", "builder", "guest", "system", "server",
    "mod", "moderator", "owner", "staff", "bot",
})

RANK_BADGE_COLORS = {
    "ADMIN": "#ff4d94",
    "BUILDER": "#00e6b8",
    "GUEST": "#8fa6c2",
}


def normalize_role_name(role_raw: str) -> str:
    r = str(role_raw or "").strip().upper()
    return r if r in ("ADMIN", "BUILDER", "GUEST") else ""


def format_rank_prefix(role_raw: str) -> str:
    r = normalize_role_name(role_raw)
    return f"[{r}]" if r else ""


def get_rank_badge_color(role_raw: str) -> str:
    return RANK_BADGE_COLORS.get(normalize_role_name(role_raw), "#00ffcc")
