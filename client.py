import asyncio
import ctypes
import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import numpy as np
import pygame
from pygame.locals import *
from OpenGL.GL import *
from OpenGL.GL import shaders
import websockets
from nacl.signing import SigningKey

from mv3d import (
    PLACED_SHAPES,
    RESERVED_ROLE_LABELS,
    WORLD_TEMPLATES,
    _axis_angle_to_quat,
    _quat_rotate_vec,
    build_ghost_preview_objects,
    build_template_mv3d_world,
    resolve_world_template_match,
    compute_md_sha256,
    format_rank_prefix,
    get_rank_badge_color,
    normalize_role_name,
    normalize_expression_name,
    parse_color,
    parse_mv3d,
    validate_mv3d_document,
)

# --- Portable Configuration & Storage Resolution ---
def _resolve_client_base_dir(argv: list[str] | None = None) -> Path:
    args = sys.argv[1:] if argv is None else argv
    env_dir = os.environ.get("MAKERVERSE_DATA_DIR", "").strip()
    if env_dir:
        cand = Path(env_dir).expanduser().resolve()
    else:
        cand = None
        for idx, arg in enumerate(args):
            if arg == "--data-dir" and idx + 1 < len(args):
                cand = Path(args[idx + 1]).expanduser().resolve()
                break
            if arg.startswith("--data-dir="):
                cand = Path(arg.split("=", 1)[1]).expanduser().resolve()
                break
        if cand is None:
            app_dir = Path(__file__).resolve().parent
            portable_dir = app_dir / "makerverse_client_data"
            if "--portable" in args or portable_dir.exists():
                cand = portable_dir
            else:
                cand = Path.home() / ".makerverse"
    if cand.exists() and not cand.is_dir():
        return Path.home() / ".makerverse"
    return cand


CLIENT_DATA_DIR = _resolve_client_base_dir()
KEY_FILE = CLIENT_DATA_DIR / "identity.key.hex"
PROFILE_CACHE_FILE = CLIENT_DATA_DIR / "profile.json"
SERVERS_FILE = CLIENT_DATA_DIR / "servers.json"
BLOCKED_FILE = CLIENT_DATA_DIR / "blocked.json"
WORLD_CACHE_DIR = CLIENT_DATA_DIR / "cache" / "worlds"
AVATAR_CACHE_DIR = CLIENT_DATA_DIR / "cache" / "avatars"
PREFAB_CACHE_DIR = CLIENT_DATA_DIR / "cache" / "prefabs"
EXPORTS_DIR = CLIENT_DATA_DIR / "exports"
DEFAULT_SERVER = os.environ.get("MAKERVERSE_SERVER", "ws://localhost:8765").strip() or "ws://localhost:8765"


def configure_client_storage(base_dir: Path):
    global CLIENT_DATA_DIR, KEY_FILE, PROFILE_CACHE_FILE, SERVERS_FILE, BLOCKED_FILE
    global WORLD_CACHE_DIR, AVATAR_CACHE_DIR, PREFAB_CACHE_DIR, EXPORTS_DIR
    if base_dir.exists() and not base_dir.is_dir():
        base_dir = Path.home() / ".makerverse"
    CLIENT_DATA_DIR = base_dir
    KEY_FILE = CLIENT_DATA_DIR / "identity.key.hex"
    PROFILE_CACHE_FILE = CLIENT_DATA_DIR / "profile.json"
    SERVERS_FILE = CLIENT_DATA_DIR / "servers.json"
    BLOCKED_FILE = CLIENT_DATA_DIR / "blocked.json"
    WORLD_CACHE_DIR = CLIENT_DATA_DIR / "cache" / "worlds"
    AVATAR_CACHE_DIR = CLIENT_DATA_DIR / "cache" / "avatars"
    PREFAB_CACHE_DIR = CLIENT_DATA_DIR / "cache" / "prefabs"
    EXPORTS_DIR = CLIENT_DATA_DIR / "exports"

# Legacy home-directory paths (auto-migrated on first run if present)
_LEGACY_KEY_FILE = Path.home() / ".makerverse_key.hex"
_LEGACY_PROFILE_FILE = Path.home() / ".makerverse_profile.json"
_LEGACY_SERVERS_FILE = Path.home() / ".makerverse_servers.json"
_LEGACY_BLOCKED_FILE = Path.home() / ".makerverse_blocked.json"

WINDOW_W, WINDOW_H = 1280, 760
MAX_AVATAR_CACHE = 48
MAX_AVATAR_REQ_TRACK = 256

# --- GLSL 3.30 Shaders ---
VERT_3D = """
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNormal;

uniform mat4 uMVP;
uniform mat4 uModel;

out vec3 vNormal;
out vec3 vFragPos;

void main() {
    vec4 worldPos = uModel * vec4(aPos, 1.0);
    vFragPos = worldPos.xyz;
    vNormal = mat3(transpose(inverse(uModel))) * aNormal;
    gl_Position = uMVP * vec4(aPos, 1.0);
}
"""

FRAG_3D = """
#version 330 core
in vec3 vNormal;
in vec3 vFragPos;

uniform vec3 uColor;
uniform vec3 uEmissive;
uniform float uAlpha;
uniform vec3 uLightDir;
uniform vec3 uCamPos;
uniform vec3 uFogColor;
uniform float uFogDensity;

out vec4 FragColor;

void main() {
    vec3 norm = normalize(vNormal);
    float diff = max(dot(norm, normalize(uLightDir)), 0.0);
    vec3 ambient = 0.42 * uColor;
    vec3 lit = ambient + (0.68 * diff) * uColor + uEmissive;

    float dist = length(uCamPos - vFragPos);
    float fogFactor = clamp(exp(-pow(dist * uFogDensity, 2.0)), 0.0, 1.0);
    vec3 finalRgb = mix(uFogColor, lit, fogFactor);
    FragColor = vec4(finalRgb, uAlpha);
}
"""

VERT_2D = """
#version 330 core
layout(location = 0) in vec2 aPos;
layout(location = 1) in vec2 aUV;

uniform mat4 uOrtho;
out vec2 vUV;

void main() {
    vUV = aUV;
    gl_Position = uOrtho * vec4(aPos, 0.0, 1.0);
}
"""

FRAG_2D = """
#version 330 core
in vec2 vUV;
uniform vec4 uColor;
uniform sampler2D uTex;
uniform int uUseTex;

out vec4 FragColor;

void main() {
    if (uUseTex == 1) {
        FragColor = texture(uTex, vUV) * uColor;
    } else {
        FragColor = uColor;
    }
}
"""


# --- Pure-NumPy Matrix Math ---
def mat4_perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2.0 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def mat4_ortho(left: float, right: float, bottom: float, top: float) -> np.ndarray:
    m = np.eye(4, dtype=np.float32)
    m[0, 0] = 2.0 / (right - left)
    m[1, 1] = 2.0 / (top - bottom)
    m[2, 2] = -1.0
    m[0, 3] = -(right + left) / (right - left)
    m[1, 3] = -(top + bottom) / (top - bottom)
    return m


def mat4_translate(x: float, y: float, z: float) -> np.ndarray:
    m = np.eye(4, dtype=np.float32)
    m[0, 3], m[1, 3], m[2, 3] = x, y, z
    return m


def mat4_scale(sx: float, sy: float, sz: float) -> np.ndarray:
    m = np.eye(4, dtype=np.float32)
    m[0, 0], m[1, 1], m[2, 2] = sx, sy, sz
    return m


def mat4_rotate_axis_angle(ax: float, ay: float, az: float, angle_deg: float) -> np.ndarray:
    if abs(angle_deg) < 1e-5:
        return np.eye(4, dtype=np.float32)
    length = math.sqrt(ax * ax + ay * ay + az * az)
    if length < 1e-8:
        return np.eye(4, dtype=np.float32)
    x, y, z = ax / length, ay / length, az / length
    rad = math.radians(angle_deg)
    c, s = math.cos(rad), math.sin(rad)
    t = 1.0 - c
    m = np.eye(4, dtype=np.float32)
    m[0, 0] = t * x * x + c
    m[0, 1] = t * x * y - s * z
    m[0, 2] = t * x * z + s * y
    m[1, 0] = t * x * y + s * z
    m[1, 1] = t * y * y + c
    m[1, 2] = t * y * z - s * x
    m[2, 0] = t * x * z - s * y
    m[2, 1] = t * y * z + s * x
    m[2, 2] = t * z * z + c
    return m


def project_3d_to_2d(pt3: tuple[float, float, float], mvp: np.ndarray) -> tuple[float, float, float] | None:
    v = np.array([pt3[0], pt3[1], pt3[2], 1.0], dtype=np.float32)
    clip = mvp @ v
    if clip[3] <= 0.001:
        return None
    ndc = clip[:3] / clip[3]
    if not (-1.0 <= ndc[2] <= 1.0):
        return None
    sx = (ndc[0] * 0.5 + 0.5) * WINDOW_W
    sy = (1.0 - (ndc[1] * 0.5 + 0.5)) * WINDOW_H
    return (float(sx), float(sy), float(ndc[2]))


# --- Pre-Baked Unit VBO Meshes ---
def _build_unit_box_verts() -> np.ndarray:
    faces = [
        ((0, 0, 1), [(-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)]),
        ((0, 0, -1), [(0.5, -0.5, -0.5), (-0.5, -0.5, -0.5), (-0.5, 0.5, -0.5), (0.5, -0.5, -0.5), (-0.5, 0.5, -0.5), (0.5, 0.5, -0.5)]),
        ((0, 1, 0), [(-0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, 0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5)]),
        ((0, -1, 0), [(-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (-0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (-0.5, -0.5, 0.5)]),
        ((1, 0, 0), [(0.5, -0.5, 0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (0.5, -0.5, 0.5), (0.5, 0.5, -0.5), (0.5, 0.5, 0.5)]),
        ((-1, 0, 0), [(-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5), (-0.5, -0.5, -0.5), (-0.5, 0.5, 0.5), (-0.5, 0.5, -0.5)]),
    ]
    data = []
    for n, pts in faces:
        for p in pts:
            data.extend([*p, *n])
    return np.array(data, dtype=np.float32)


def _build_unit_cylinder_verts(segs: int = 24, cone: bool = False) -> np.ndarray:
    data = []
    for i in range(segs):
        a0 = (2.0 * math.pi * i) / segs
        a1 = (2.0 * math.pi * (i + 1)) / segs
        x0, z0 = math.cos(a0), math.sin(a0)
        x1, z1 = math.cos(a1), math.sin(a1)
        nx, nz = math.cos((a0 + a1) * 0.5), math.sin((a0 + a1) * 0.5)
        if cone:
            p0, p1, top = (x0, -0.5, z0), (x1, -0.5, z1), (0.0, 0.5, 0.0)
            for p in (p0, p1, top):
                data.extend([*p, nx, 0.45, nz])
        else:
            b0, b1 = (x0, -0.5, z0), (x1, -0.5, z1)
            t0, t1 = (x0, 0.5, z0), (x1, 0.5, z1)
            for p in (b0, b1, t1, b0, t1, t0):
                data.extend([*p, nx, 0.0, nz])
            for p in ((0.0, 0.5, 0.0), t0, t1):
                data.extend([*p, 0.0, 1.0, 0.0])
        for p in ((0.0, -0.5, 0.0), (x1, -0.5, z1), (x0, -0.5, z0)):
            data.extend([*p, 0.0, -1.0, 0.0])
    return np.array(data, dtype=np.float32)


def _build_unit_sphere_verts(stacks: int = 12, slices: int = 18) -> np.ndarray:
    data = []
    for i in range(stacks):
        lat0 = math.pi * (-0.5 + float(i) / stacks)
        lat1 = math.pi * (-0.5 + float(i + 1) / stacks)
        y0, r0 = math.sin(lat0), math.cos(lat0)
        y1, r1 = math.sin(lat1), math.cos(lat1)
        for j in range(slices):
            lng0 = 2 * math.pi * float(j) / slices
            lng1 = 2 * math.pi * float(j + 1) / slices
            p00 = (r0 * math.cos(lng0), y0, r0 * math.sin(lng0))
            p10 = (r0 * math.cos(lng1), y0, r0 * math.sin(lng1))
            p11 = (r1 * math.cos(lng1), y1, r1 * math.sin(lng1))
            p01 = (r1 * math.cos(lng0), y1, r1 * math.sin(lng0))
            for p in (p00, p10, p11, p00, p11, p01):
                data.extend([*p, *p])
    return np.array(data, dtype=np.float32)


def _build_wire_box_verts() -> np.ndarray:
    c = [
        (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (-0.5, -0.5, 0.5),
        (-0.5, 0.5, -0.5), (0.5, 0.5, -0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    data = []
    for a, b in edges:
        data.extend([*c[a], 0.0, 1.0, 0.0, *c[b], 0.0, 1.0, 0.0])
    return np.array(data, dtype=np.float32)


class MeshVAO:
    def __init__(self, interleaved_verts: np.ndarray, draw_mode=GL_TRIANGLES):
        self.vert_count = len(interleaved_verts) // 6
        self.draw_mode = draw_mode
        self.vao = glGenVertexArrays(1)
        self.vbo = glGenBuffers(1)
        glBindVertexArray(self.vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo)
        glBufferData(GL_ARRAY_BUFFER, interleaved_verts.nbytes, interleaved_verts, GL_STATIC_DRAW)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 24, ctypes.c_void_p(0))
        glEnableVertexAttribArray(1)
        glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 24, ctypes.c_void_p(12))
        glBindVertexArray(0)

    def draw(self):
        if self.vert_count > 0:
            glBindVertexArray(self.vao)
            glDrawArrays(self.draw_mode, 0, self.vert_count)
            glBindVertexArray(0)

    def delete(self):
        try:
            glDeleteBuffers(1, [self.vbo])
            glDeleteVertexArrays(1, [self.vao])
        except Exception:
            pass


class ModernRenderer:
    def __init__(self):
        self.prog3d = shaders.compileProgram(
            shaders.compileShader(VERT_3D, GL_VERTEX_SHADER),
            shaders.compileShader(FRAG_3D, GL_FRAGMENT_SHADER),
        )
        self.prog2d = shaders.compileProgram(
            shaders.compileShader(VERT_2D, GL_VERTEX_SHADER),
            shaders.compileShader(FRAG_2D, GL_FRAGMENT_SHADER),
        )
        self.meshes = {
            "box": MeshVAO(_build_unit_box_verts()),
            "cylinder": MeshVAO(_build_unit_cylinder_verts(24, cone=False)),
            "cone": MeshVAO(_build_unit_cylinder_verts(24, cone=True)),
            "sphere": MeshVAO(_build_unit_sphere_verts(12, 18)),
            "wirebox": MeshVAO(_build_wire_box_verts(), draw_mode=GL_LINES),
        }
        self.dynamic_vaos: dict[int, MeshVAO] = {}

        self.quad_vao = glGenVertexArrays(1)
        self.quad_vbo = glGenBuffers(1)
        glBindVertexArray(self.quad_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.quad_vbo)
        glBufferData(GL_ARRAY_BUFFER, 24 * 4, None, GL_DYNAMIC_DRAW)
        glEnableVertexAttribArray(0)
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 16, ctypes.c_void_p(0))
        glEnableVertexAttribArray(1)
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 16, ctypes.c_void_p(8))
        glBindVertexArray(0)

        self.text_cache: dict[tuple[str, tuple[int, int, int]], tuple[int, int, int]] = {}

    def clear_dynamic_vaos(self):
        for vao in self.dynamic_vaos.values():
            vao.delete()
        self.dynamic_vaos.clear()

    def get_custom_mesh(self, obj: dict) -> MeshVAO:
        oid = id(obj)
        if oid in self.dynamic_vaos:
            return self.dynamic_vaos[oid]
        verts = []
        if obj["kind"] == "faces":
            pts = obj.get("pts", [])
            poly = []
            for idx in obj.get("idxs", []):
                if idx == -1:
                    if len(poly) >= 3:
                        u = np.subtract(poly[1], poly[0])
                        v = np.subtract(poly[2], poly[0])
                        n = np.cross(u, v)
                        ln = np.linalg.norm(n)
                        n = n / ln if ln > 1e-6 else np.array([0.0, 1.0, 0.0])
                        for k in range(1, len(poly) - 1):
                            for p in (poly[0], poly[k], poly[k + 1]):
                                verts.extend([*p, *n])
                    poly = []
                elif 0 <= idx < len(pts):
                    poly.append(pts[idx])
        elif obj["kind"] == "grid":
            xd, zd, xs, zs, hs = obj["xd"], obj["zd"], obj["xs"], obj["zs"], obj["heights"]
            for z in range(zd - 1):
                for x in range(xd - 1):
                    i00 = z * xd + x
                    i10, i11, i01 = i00 + 1, (z + 1) * xd + (x + 1), (z + 1) * xd + x
                    if i11 >= len(hs):
                        continue
                    p00 = (x * xs, hs[i00], z * zs)
                    p10 = ((x + 1) * xs, hs[i10], z * zs)
                    p11 = ((x + 1) * xs, hs[i11], (z + 1) * zs)
                    p01 = (x * xs, hs[i01], (z + 1) * zs)
                    for p in (p00, p10, p11, p00, p11, p01):
                        verts.extend([*p, 0.0, 1.0, 0.0])
        arr = np.array(verts, dtype=np.float32) if verts else np.zeros(6, dtype=np.float32)
        vao = MeshVAO(arr)
        self.dynamic_vaos[oid] = vao
        return vao

    def draw_object(self, obj: dict, vp_mat: np.ndarray, parent_mat: np.ndarray | None = None):
        px, py, pz = obj["pos"]
        rx, ry, rz, ang = obj.get("rot", (0.0, 1.0, 0.0, 0.0))
        sx, sy, sz = obj.get("scale", (1.0, 1.0, 1.0))

        kind = obj["kind"]
        if kind == "box":
            bx, by, bz = obj.get("size", (1.0, 1.0, 1.0))
            sx, sy, sz = sx * bx, sy * by, sz * bz
            mesh = self.meshes["box"]
        elif kind in ("cylinder", "cone"):
            r, h = obj.get("radius", 1.0), obj.get("height", 2.0)
            sx, sy, sz = sx * r, sy * h, sz * r
            mesh = self.meshes[kind]
        elif kind == "sphere":
            r = obj.get("radius", 1.0)
            sx, sy, sz = sx * r, sy * r, sz * r
            mesh = self.meshes["sphere"]
        else:
            mesh = self.get_custom_mesh(obj)

        model = mat4_translate(px, py, pz) @ mat4_rotate_axis_angle(rx, ry, rz, ang) @ mat4_scale(sx, sy, sz)
        if parent_mat is not None:
            model = parent_mat @ model
        mvp = vp_mat @ model

        glUniformMatrix4fv(glGetUniformLocation(self.prog3d, "uModel"), 1, GL_TRUE, model)
        glUniformMatrix4fv(glGetUniformLocation(self.prog3d, "uMVP"), 1, GL_TRUE, mvp)
        glUniform3f(glGetUniformLocation(self.prog3d, "uColor"), *obj["diff"])
        glUniform3f(glGetUniformLocation(self.prog3d, "uEmissive"), *obj["emis"])
        glUniform1f(glGetUniformLocation(self.prog3d, "uAlpha"), obj.get("alpha", 1.0))
        mesh.draw()

    def draw_wire_aabb(self, box: tuple[float, float, float, float, float, float], vp_mat: np.ndarray):
        cx = 0.5 * (box[0] + box[1])
        cy = 0.5 * (box[2] + box[3])
        cz = 0.5 * (box[4] + box[5])
        sx = (box[1] - box[0]) + 0.08
        sy = (box[3] - box[2]) + 0.08
        sz = (box[5] - box[4]) + 0.08
        model = mat4_translate(cx, cy, cz) @ mat4_scale(sx, sy, sz)
        mvp = vp_mat @ model
        glUniformMatrix4fv(glGetUniformLocation(self.prog3d, "uModel"), 1, GL_TRUE, model)
        glUniformMatrix4fv(glGetUniformLocation(self.prog3d, "uMVP"), 1, GL_TRUE, mvp)
        glUniform3f(glGetUniformLocation(self.prog3d, "uColor"), 0.0, 1.0, 0.85)
        glUniform3f(glGetUniformLocation(self.prog3d, "uEmissive"), 0.0, 1.0, 0.85)
        glUniform1f(glGetUniformLocation(self.prog3d, "uAlpha"), 1.0)
        self.meshes["wirebox"].draw()

    def draw_rect_2d(self, x: float, y: float, w: float, h: float, rgba: tuple[float, float, float, float]):
        verts = np.array([
            x, y, 0, 0, x + w, y, 1, 0, x + w, y + h, 1, 1,
            x, y, 0, 0, x + w, y + h, 1, 1, x, y + h, 0, 1,
        ], dtype=np.float32)
        glUniform1i(glGetUniformLocation(self.prog2d, "uUseTex"), 0)
        glUniform4f(glGetUniformLocation(self.prog2d, "uColor"), *rgba)
        glBindVertexArray(self.quad_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.quad_vbo)
        glBufferSubData(GL_ARRAY_BUFFER, 0, verts.nbytes, verts)
        glDrawArrays(GL_TRIANGLES, 0, 6)
        glBindVertexArray(0)

    def draw_text_2d(self, font, x: float, y: float, text: str, color=(0, 255, 204)):
        if not text:
            return
        key = (text, color)
        if key not in self.text_cache:
            if len(self.text_cache) > 400:
                old_k = next(iter(self.text_cache))
                glDeleteTextures([self.text_cache.pop(old_k)[0]])
            surf = font.render(text, True, color)
            w, h = surf.get_size()
            if w <= 0 or h <= 0:
                return
            raw = pygame.image.tostring(surf, "RGBA", True)
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, raw)
            self.text_cache[key] = (tex, w, h)

        tex, w, h = self.text_cache[key]
        verts = np.array([
            x, y, 0.0, 1.0,
            x + w, y, 1.0, 1.0,
            x + w, y + h, 1.0, 0.0,
            x, y, 0.0, 1.0,
            x + w, y + h, 1.0, 0.0,
            x, y + h, 0.0, 0.0,
        ], dtype=np.float32)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, tex)
        glUniform1i(glGetUniformLocation(self.prog2d, "uUseTex"), 1)
        glUniform4f(glGetUniformLocation(self.prog2d, "uColor"), 1.0, 1.0, 1.0, 1.0)
        glBindVertexArray(self.quad_vao)
        glBindBuffer(GL_ARRAY_BUFFER, self.quad_vbo)
        glBufferSubData(GL_ARRAY_BUFFER, 0, verts.nbytes, verts)
        glDrawArrays(GL_TRIANGLES, 0, 6)
        glBindVertexArray(0)


# --- Spatial Hash Grid & Raycast ---
def get_object_aabb(obj: dict) -> tuple[float, float, float, float, float, float] | None:
    px, py, pz = obj["pos"]
    sx, sy, sz = obj["scale"]
    rx, ry, rz, ang_deg = obj.get("rot", (0.0, 1.0, 0.0, 0.0))
    kind = obj["kind"]

    def _rot_box(hx: float, hy: float, hz: float):
        if abs(ang_deg) < 1e-3:
            return (px - hx, px + hx, py - hy, py + hy, pz - hz, pz + hz)
        q = _axis_angle_to_quat(rx, ry, rz, math.radians(ang_deg))
        rpts = [_quat_rotate_vec(q, c) for c in (
            (-hx, -hy, -hz), (hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz),
            (-hx, -hy, hz), (hx, -hy, hz), (-hx, hy, hz), (hx, hy, hz),
        )]
        return (
            min(px + p[0] for p in rpts), max(px + p[0] for p in rpts),
            min(py + p[1] for p in rpts), max(py + p[1] for p in rpts),
            min(pz + p[2] for p in rpts), max(pz + p[2] for p in rpts),
        )

    if kind == "box":
        bx, by, bz = obj["size"]
        return _rot_box(abs(bx * sx) * 0.5, abs(by * sy) * 0.5, abs(bz * sz) * 0.5)
    if kind in ("cylinder", "cone"):
        r = abs(obj["radius"] * max(sx, sz))
        return _rot_box(r, abs(obj["height"] * sy) * 0.5, r)
    if kind == "sphere":
        r = abs(obj["radius"] * max(sx, sy, sz))
        return (px - r, px + r, py - r, py + r, pz - r, pz + r)
    if kind == "faces" and obj.get("pts"):
        return (
            min(px + p[0] * sx for p in obj["pts"]), max(px + p[0] * sx for p in obj["pts"]),
            min(py + p[1] * sy for p in obj["pts"]), max(py + p[1] * sy for p in obj["pts"]),
            min(pz + p[2] * sz for p in obj["pts"]), max(pz + p[2] * sz for p in obj["pts"]),
        )
    return None


def raycast_aabb(origin, direction, box, max_dist=60.0):
    bounds = ((box[0], box[1]), (box[2], box[3]), (box[4], box[5]))
    normals_neg = ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0))
    normals_pos = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    tmin, tmax = 0.0, max_dist
    hit_normal = (0.0, 1.0, 0.0)
    for axis in range(3):
        orig, d = origin[axis], direction[axis]
        bmin, bmax = bounds[axis]
        if abs(d) < 1e-8:
            if orig < bmin or orig > bmax:
                return None
        else:
            inv_d = 1.0 / d
            t1, t2 = (bmin - orig) * inv_d, (bmax - orig) * inv_d
            n1, n2 = normals_neg[axis], normals_pos[axis]
            if t1 > t2:
                t1, t2, n1, n2 = t2, t1, n2, n1
            if t1 > tmin:
                tmin, hit_normal = t1, n1
            if t2 < tmax:
                tmax = t2
            if tmin > tmax:
                return None
    if tmin < 0.0 or tmin > max_dist:
        return None
    return tmin, (origin[0] + direction[0] * tmin, origin[1] + direction[1] * tmin, origin[2] + direction[2] * tmin), hit_normal


class SpatialHashGrid:
    def __init__(self, cell_size: float = 8.0):
        self.cell_size = cell_size
        self.cells: dict[tuple[int, int], list[tuple[int, dict, tuple]]] = {}
        self.large_colliders: list[tuple[int, dict, tuple]] = []

    def rebuild(self, objects: list[dict]):
        self.cells.clear()
        self.large_colliders.clear()
        for idx, obj in enumerate(objects):
            box = get_object_aabb(obj)
            if not box:
                continue
            if (box[1] - box[0]) > 24.0 or (box[5] - box[4]) > 24.0:
                self.large_colliders.append((idx, obj, box))
                continue
            cx0, cx1 = int(math.floor(box[0] / self.cell_size)), int(math.floor(box[1] / self.cell_size))
            cz0, cz1 = int(math.floor(box[4] / self.cell_size)), int(math.floor(box[5] / self.cell_size))
            for cx in range(cx0, cx1 + 1):
                for cz in range(cz0, cz1 + 1):
                    self.cells.setdefault((cx, cz), []).append((idx, obj, box))

    def query_aabb(self, min_x: float, max_x: float, min_z: float, max_z: float) -> list[tuple[int, dict, tuple]]:
        seen = set()
        out = list(self.large_colliders)
        for item in out:
            seen.add(item[0])
        cx0, cx1 = int(math.floor(min_x / self.cell_size)), int(math.floor(max_x / self.cell_size))
        cz0, cz1 = int(math.floor(min_z / self.cell_size)), int(math.floor(max_z / self.cell_size))
        for cx in range(cx0, cx1 + 1):
            for cz in range(cz0, cz1 + 1):
                for item in self.cells.get((cx, cz), ()):
                    if item[0] not in seen:
                        seen.add(item[0])
                        out.append(item)
        return out


def find_looked_at_object(cam_pos, yaw_deg, pitch_deg, spatial_hash: SpatialHashGrid, max_dist=60.0):
    yr, pr = math.radians(yaw_deg), math.radians(pitch_deg)
    cp = math.cos(pr)
    look_dir = (-math.sin(yr) * cp, math.sin(pr), -math.cos(yr) * cp)
    best_hit = None
    best_dist = max_dist + 1.0
    end_x = cam_pos[0] + look_dir[0] * max_dist
    end_z = cam_pos[2] + look_dir[2] * max_dist
    candidates = spatial_hash.query_aabb(min(cam_pos[0], end_x), max(cam_pos[0], end_x), min(cam_pos[2], end_z), max(cam_pos[2], end_z))

    for idx, obj, box in candidates:
        if (box[1] - box[0]) >= 60 and (box[5] - box[4]) >= 60 and (box[3] - box[2]) <= 0.5:
            continue
        res = raycast_aabb(cam_pos, look_dir, box, max_dist=max_dist)
        if res is not None and res[0] < best_dist:
            best_dist = res[0]
            best_hit = ({**obj, "_index": idx, "_aabb": box}, res[0], res[1], res[2])
    return best_hit


def step_player_physics(cam_pos, dx, dz, vel_y, jump_pressed, dt, spatial_hash: SpatialHashGrid):
    eye_h, radius, step_height = 1.8, 0.45, 0.65
    dt = min(dt, 0.05)
    nearby = [
        box
        for _, obj, box in spatial_hash.query_aabb(cam_pos[0] - 3.0, cam_pos[0] + 3.0, cam_pos[2] - 3.0, cam_pos[2] + 3.0)
        if not obj.get("is_portal")
    ]
    orig_feet_y = cam_pos[1] - eye_h
    feet_y = orig_feet_y

    for axis, delta in ((0, dx), (2, dz)):
        if delta == 0.0:
            continue
        cand_x = cam_pos[0] + (delta if axis == 0 else 0.0)
        cand_z = cam_pos[2] + (delta if axis == 2 else 0.0)
        blocked = False
        step_target_y = feet_y
        for min_x, max_x, min_y, max_y, min_z, max_z in nearby:
            if cand_x + radius > min_x and cand_x - radius < max_x and cand_z + radius > min_z and cand_z - radius < max_z:
                if orig_feet_y + eye_h > min_y + 0.05 and orig_feet_y < max_y - 0.02:
                    if max_y - orig_feet_y <= step_height:
                        step_target_y = max(step_target_y, max_y)
                    else:
                        blocked = True
                        break
        if not blocked:
            feet_y = step_target_y
            if axis == 0:
                cam_pos[0] = cand_x
            else:
                cam_pos[2] = cand_z

    ground_y = 0.0
    for min_x, max_x, min_y, max_y, min_z, max_z in nearby:
        if cam_pos[0] + radius > min_x and cam_pos[0] - radius < max_x and cam_pos[2] + radius > min_z and cam_pos[2] - radius < max_z:
            if ground_y < max_y <= feet_y + step_height:
                ground_y = max_y

    if feet_y <= ground_y + 0.03 and vel_y <= 0.0:
        feet_y = ground_y
        vel_y = 7.5 if jump_pressed else 0.0
    else:
        vel_y -= 18.0 * dt
        feet_y += vel_y * dt
        if feet_y <= ground_y:
            feet_y = ground_y
            vel_y = 0.0

    cam_pos[1] = feet_y + eye_h
    return vel_y


# --- Custom Avatar Mesh Normalization ---
def build_normalized_avatar_mesh(md_text: str) -> dict | None:
    parsed = parse_mv3d(md_text)
    objs = [
        o for o in parsed.get("objects", [])
        if o["kind"] != "grid" and ((o.get("size") or (0, 0, 0))[0] < 20)
    ]
    if not objs:
        return None
    aabbs = [b for b in (get_object_aabb(o) for o in objs) if b is not None]
    if not aabbs:
        return None

    min_x, max_x = min(b[0] for b in aabbs), max(b[1] for b in aabbs)
    min_y, max_y = min(b[2] for b in aabbs), max(b[3] for b in aabbs)
    min_z, max_z = min(b[4] for b in aabbs), max(b[5] for b in aabbs)

    span_y = max(0.05, max_y - min_y)
    max_horiz = max(max(0.05, max_x - min_x), max(0.05, max_z - min_z))
    scale_f = 1.85 / span_y
    if max_horiz * scale_f > 1.4:
        scale_f = 1.4 / max_horiz

    return {
        "objects": objs,
        "offset": (-0.5 * (min_x + max_x), -min_y, -0.5 * (min_z + max_z)),
        "scale": scale_f,
        "height": span_y * scale_f,
    }


# --- Identity, Cache & Network Client ---
def _write_secure_file(path: Path, text: str, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _clean_ident(raw: str, max_len: int = 32, fallback: str = "") -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(raw or ""))[:max_len] or fallback


def load_or_create_key() -> SigningKey:
    if not KEY_FILE.exists() and _LEGACY_KEY_FILE.exists():
        raw_legacy = _LEGACY_KEY_FILE.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-fA-F]{64}", raw_legacy):
            _write_secure_file(KEY_FILE, raw_legacy)
    if KEY_FILE.exists():
        raw_hex = KEY_FILE.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", raw_hex):
            raise ValueError(f"Corrupted Ed25519 key file at {KEY_FILE}")
        return SigningKey(bytes.fromhex(raw_hex))
    sk = SigningKey.generate()
    _write_secure_file(KEY_FILE, sk.encode().hex())
    return sk


def is_safe_radio_url(url: str) -> tuple[bool, str]:
    if not url or len(url) > 300 or any(ch in url for ch in ('"', "'", "\n", "\r", "\x00", " ")):
        return False, "Radio stream URL is invalid."
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False, "Malformed radio URL."
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False, "Radio stream URL must start with http:// or https://."
    host = parsed.hostname.strip().lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False, "Blocked local radio host."
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False, f"Cannot resolve radio host '{host}'."
    for info in infos:
        ip_obj = ipaddress.ip_address(info[4][0])
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
            return False, "Blocked private/loopback IP for radio stream."
    return True, ""


def get_cached_world(world_name: str) -> dict | None:
    safe_w = _clean_ident(world_name, 32, "hub")
    fp = (WORLD_CACHE_DIR.resolve() / f"{safe_w}.md").resolve()
    if fp.parent != WORLD_CACHE_DIR.resolve() or not fp.is_file():
        return None
    md_text = fp.read_text(encoding="utf-8", errors="replace")
    return {"world": safe_w, "md": md_text, "sha256": compute_md_sha256(md_text)}


def store_cached_world(world_name: str, md_text: str):
    safe_w = _clean_ident(world_name, 32, "hub")
    _write_secure_file(WORLD_CACHE_DIR / f"{safe_w}.md", md_text)


def store_cached_avatar_md(av_name: str, md_text: str):
    safe_a = _clean_ident(av_name, 32)
    if safe_a and safe_a.lower() != "default" and md_text:
        _write_secure_file(AVATAR_CACHE_DIR / f"{safe_a}.md", md_text)


def load_cached_avatar_md(av_name: str) -> str:
    safe_a = _clean_ident(av_name, 32)
    if not safe_a or safe_a.lower() == "default":
        return ""
    fp = (AVATAR_CACHE_DIR.resolve() / f"{safe_a}.md").resolve()
    if fp.parent != AVATAR_CACHE_DIR.resolve() or not fp.is_file():
        return ""
    return fp.read_text(encoding="utf-8", errors="replace")




def normalize_ws_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url or len(url) > 200 or any(ch in url for ch in ('"', "'", "\n", "\r", "\x00", " ")):
        return ""
    if not url.startswith(("ws://", "wss://")):
        url = f"ws://{url}"
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return ""
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        return ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 8765)
    return f"{parsed.scheme}://{parsed.hostname}:{port}"


def load_server_directory() -> dict:
    if not SERVERS_FILE.exists() and _LEGACY_SERVERS_FILE.exists():
        try:
            _write_secure_file(SERVERS_FILE, _LEGACY_SERVERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not SERVERS_FILE.exists():
        _write_secure_file(SERVERS_FILE, json.dumps({"bookmarks": {}, "recent": []}, indent=2))
    try:
        data = json.loads(SERVERS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("bookmarks", {})
            data.setdefault("recent", [])
            return data
    except Exception:
        pass
    return {"bookmarks": {}, "recent": []}


def save_server_directory(data: dict):
    _write_secure_file(SERVERS_FILE, json.dumps(data, indent=2))


def load_blocked_users() -> set[str]:
    if not BLOCKED_FILE.exists() and _LEGACY_BLOCKED_FILE.exists():
        try:
            _write_secure_file(BLOCKED_FILE, _LEGACY_BLOCKED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not BLOCKED_FILE.exists():
        return set()
    try:
        raw = json.loads(BLOCKED_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {str(x).strip().lower() for x in raw if str(x).strip()}
    except Exception:
        pass
    return set()


def save_blocked_users(blocked: set[str]):
    _write_secure_file(BLOCKED_FILE, json.dumps(sorted(blocked), indent=2))


def store_cached_prefab_md(name: str, md_text: str):
    clean_n = _clean_ident(name, 32)
    if clean_n and md_text:
        _write_secure_file(PREFAB_CACHE_DIR / f"{clean_n}.md", md_text)


class RadioPlayer:
    def __init__(self):
        self.url = ""
        self.name = ""
        self.muted = False
        self.proc = None

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def set_stream(self, url: str, name: str = ""):
        url = (url or "").strip()
        if url and not is_safe_radio_url(url)[0]:
            self.url = ""
            self.name = ""
            self.stop()
            return
        if url == self.url and (self.proc is not None or self.muted or not url):
            return
        self.url, self.name = url, name or url
        self.stop()
        if self.url and not self.muted:
            for cmd, bin_name in (
                (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-i", self.url], "ffplay"),
                (["mpv", "--no-video", "--really-quiet", "--", self.url], "mpv"),
                (["cvlc", "--intf", "dummy", "--no-video", "--quiet", self.url], "cvlc"),
                (["vlc", "--intf", "dummy", "--no-video", "--quiet", self.url], "vlc"),
            ):
                if shutil.which(bin_name):
                    popen_kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "shell": False}
                    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                        popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
                    elif os.name == "posix":
                        popen_kw["start_new_session"] = True
                    self.proc = subprocess.Popen(cmd, **popen_kw)
                    break

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        if self.muted:
            self.stop()
        elif self.url:
            self.set_stream(self.url, self.name)
        return self.muted


_rank_prefix = format_rank_prefix


class MakerverseClient:
    def __init__(self, sk: SigningKey):
        self.sk = sk
        self.pubkey_hex = sk.verify_key.encode().hex()
        self.server_url = DEFAULT_SERVER
        self.node_name = "Makerverse"
        self.nick = f"Maker_{self.pubkey_hex[:5]}"
        self.color = "#00ffcc"
        if not PROFILE_CACHE_FILE.exists() and _LEGACY_PROFILE_FILE.exists():
            try:
                _write_secure_file(PROFILE_CACHE_FILE, _LEGACY_PROFILE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.role = "OFFLINE"
        self.short_id = ""
        self.avatar = "default"
        if PROFILE_CACHE_FILE.exists():
            try:
                p_data = json.loads(PROFILE_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(p_data, dict):
                    if p_data.get("nick"):
                        self.nick = _clean_ident(p_data["nick"], 24, self.nick)
                    if p_data.get("color"):
                        c_val = str(p_data["color"]).strip().lower()
                        if re.fullmatch(r"#[0-9a-f]{6}", c_val):
                            self.color = c_val
                    if p_data.get("avatar"):
                        self.avatar = _clean_ident(p_data["avatar"], 32, "default")
            except Exception:
                pass
        self.world = "hub"
        self.world_desc = ""
        self.worlds = ["hub"]
        self.avatars = []
        self.prefabs = []
        self.avatar_meshes: dict[str, dict] = {}
        self.avatar_requested: dict[str, float] = {}
        self.scene = parse_mv3d("")
        self.spatial_hash = SpatialHashGrid()
        self.scene_dirty = False
        self.triggered_ids: set[str] = set()
        self.trigger_toggled_state: dict[str, bool] = {}
        self.trigger_cooldowns: dict[str, float] = {}
        self.nearby_interact_prompt = ""
        self.nearby_interact_target = None
        self.portal_cooldown_until = 0.0
        self.world_search_query = ""
        self.blocked_users: set[str] = load_blocked_users()
        self.waypoints: dict[str, list[float]] = {}
        self.peers = {}
        self.media_states: dict[str, dict] = {}
        self.radio = RadioPlayer()
        self.fly_mode = False
        self.inspect_mode = False
        self.grid_snap = 0.0
        self.ghost_active = False
        self.ghost_shape = "box"
        self.ghost_color = "cyan"
        self.ghost_scale = 1.0
        self.ghost_dist = 4.5
        self.ghost_yaw_offset = 0.0
        self.watch_spec = None  # {"kind": "world"|"avatar", "name": str, "path": Path, "mtime": float, "sha256": str, "last_poll": float}
        self.looked_at_hit = None
        self.camera_mode = "first"
        self.cam_dist = 4.5
        self.hud_hidden = False
        self.local_emote = None
        self.local_emote_until = 0.0
        self.mood = "neutral"
        self.mood_pinned = False
        self.cam_pos_ref = [0.0, 4.05, 13.5]
        self.cam_yaw_ref = 0.0
        self.vel_y = 0.0
        self.chat_log = [("SYSTEM", "#00ffcc", "Makerverse #MV3D v2 Ready. Press [T] or [/] or /help.")]
        self.prev_server = None
        self.reconnect_target = None
        self.active_ws = None
        self._connect_task = None
        self.out_queue = asyncio.Queue()
        self.loop = None
        self.connected = False
        self.teleport_req = None
        self.teleport_yaw = None
        self.running = True

    def _save_profile(self):
        _write_secure_file(
            PROFILE_CACHE_FILE,
            json.dumps({"nick": self.nick, "color": self.color, "avatar": self.avatar}, indent=2),
        )

    def _front_placement_pos(self, dist: float = 4.5, snap: bool = True) -> tuple[float, float, float, float]:
        rad = math.radians(self.cam_yaw_ref)
        cx, cy, cz = self.cam_pos_ref
        rx = cx - math.sin(rad) * dist
        ry = max(0.0, cy - 1.8)
        rz = cz - math.cos(rad) * dist
        px = self._snap_coord(rx) if snap else round(rx, 2)
        py = self._snap_coord(ry) if snap else round(ry, 2)
        pz = self._snap_coord(rz) if snap else round(rz, 2)
        yaw_p = round((-self.cam_yaw_ref) % 360.0, 1)
        if snap and self.grid_snap > 0.0:
            yaw_p = round((round(yaw_p / 45.0) * 45.0) % 360.0, 1)
        return px, py, pz, yaw_p

    def _compute_ghost_placement(self) -> tuple[float, float, float, float]:
        dist = max(2.0, min(25.0, float(self.ghost_dist)))
        px, py, pz, base_yaw = self._front_placement_pos(dist=dist, snap=True)
        yaw_p = round((base_yaw + float(self.ghost_yaw_offset)) % 360.0, 1)
        if self.grid_snap > 0.0:
            yaw_p = round((round(yaw_p / 45.0) * 45.0) % 360.0, 1)
        return px, py, pz, yaw_p

    def _ghost_aabb(self, ghost_objs: list[dict]) -> tuple[float, float, float, float, float, float] | None:
        boxes = [b for b in (get_object_aabb(o) for o in ghost_objs) if b is not None]
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes), max(b[1] for b in boxes),
            min(b[2] for b in boxes), max(b[3] for b in boxes),
            min(b[4] for b in boxes), max(b[5] for b in boxes),
        )

    def commit_ghost_placement(self):
        if not self.ghost_active:
            return
        if not self.connected:
            self.log("NET", "#ffaa00", "Not connected to server yet; ghost placement skipped.")
            return
        if self.role not in ("ADMIN", "BUILDER"):
            self.log("ERR", "#ff4444", "Builder or admin role required to place objects.")
            return
        px, py, pz, yaw_p = self._compute_ghost_placement()
        self.send_json({
            "type": "world_edit",
            "action": "place",
            "world": self.world,
            "shape": self.ghost_shape,
            "x": px,
            "y": py,
            "z": pz,
            "scale": self.ghost_scale,
            "color": self.ghost_color,
            "yaw": yaw_p,
        })

    def log(self, sender: str, color: str, text: str, rank: str = ""):
        clean_t = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
        norm_r = normalize_role_name(rank)
        self.chat_log.append((sender, color, clean_t, norm_r))
        if len(self.chat_log) > 100:
            self.chat_log.pop(0)

    def send_json(self, payload: dict):
        if self.loop and not self.loop.is_closed() and self.connected:
            try:
                self.loop.call_soon_threadsafe(self.out_queue.put_nowait, json.dumps(payload))
            except RuntimeError:
                pass
        elif payload.get("type") not in ("get_avatar", "move"):
            self.log("NET", "#ffaa00", "Not connected to server yet; action skipped.")

    def _store_avatar_mesh(self, av_name: str, mesh: dict, md_text: str = ""):
        if md_text:
            store_cached_avatar_md(av_name, md_text)
        if av_name in self.avatar_meshes:
            self.avatar_meshes.pop(av_name, None)
        elif len(self.avatar_meshes) >= MAX_AVATAR_CACHE:
            self.avatar_meshes.pop(next(iter(self.avatar_meshes)), None)
        self.avatar_meshes[av_name] = mesh

    def _ensure_avatar_loaded(self, av_name: str):
        if not av_name or av_name == "default" or av_name in self.avatar_meshes:
            return
        cached_md = load_cached_avatar_md(av_name)
        if cached_md:
            mesh = build_normalized_avatar_mesh(cached_md)
            if mesh:
                self._store_avatar_mesh(av_name, mesh)
                return
        now = time.time()
        if now - self.avatar_requested.get(av_name, 0.0) < 15.0:
            return
        if len(self.avatar_requested) >= MAX_AVATAR_REQ_TRACK:
            self.avatar_requested.pop(next(iter(self.avatar_requested)), None)
        self.avatar_requested[av_name] = now
        self.send_json({"type": "get_avatar", "name": av_name})

    def _apply_world(self, world_name: str, md_text: str, desc: str = "", radio_url: str = "", radio_name: str = "", spawn_pos=None, spawn_yaw: float = 0.0, in_place: bool = False):
        self.world = world_name
        self.scene = parse_mv3d(md_text)
        self.spatial_hash.rebuild(self.scene.get("objects", []))
        self.scene_dirty = True
        self.triggered_ids.clear()
        self.trigger_toggled_state.clear()
        self.trigger_cooldowns.clear()
        self.nearby_interact_prompt = ""
        self.nearby_interact_target = None
        self.world_desc = desc or self.scene.get("world_info_desc", "")
        store_cached_world(world_name, md_text)
        self.radio.set_stream(radio_url or self.scene.get("radio_url", ""), radio_name or self.scene.get("radio_name", ""))
        if in_place:
            return
        self.portal_cooldown_until = time.time() + 2.0
        self.peers.clear()
        self.log("WORLD", "#00ff66", f"Loaded '{world_name}' ({len(self.scene['objects'])} primitives, {len(md_text)} bytes)")
        sp = spawn_pos or self.scene.get("spawn_pos")
        if sp and len(sp) >= 3:
            self.teleport_req = [float(sp[0]), float(sp[1]) + 1.8, float(sp[2])]
            self.teleport_yaw = float(spawn_yaw or self.scene.get("spawn_yaw", 0.0))
        else:
            self.teleport_req = [0.0, 4.05, 13.5] if world_name.lower() == "hub" else [0.0, 2.2, 12.0]
            self.teleport_yaw = 0.0

    def apply_trigger(self, trig: dict, now: float = 0.0):
        tid = trig["id"]
        mode = trig.get("mode", "once")
        if mode == "once":
            if tid in self.triggered_ids:
                return
            self.triggered_ids.add(tid)
        else:
            if now < self.trigger_cooldowns.get(tid, 0.0):
                return
            self.trigger_cooldowns[tid] = now + float(trig.get("cooldown", 1.5))

        is_active = not self.trigger_toggled_state.get(tid, False)
        self.trigger_toggled_state[tid] = is_active
        t_name = trig.get("target", "")
        act = trig.get("action", "toggle_glow")
        matched = [
            o for o in self.scene.get("objects", [])
            if t_name and t_name.lower() in (
                str(o.get("def_name", "")).lower(),
                str(o.get("node_name", "")).lower(),
            )
        ]
        if act == "toggle_glow":
            for o in matched:
                if "_orig_emis" not in o:
                    o["_orig_emis"] = o.get("emis", (0.0, 0.0, 0.0))
                if is_active:
                    o["emis"] = (0.0, 0.0, 0.0) if sum(o["_orig_emis"]) > 0.1 else (0.25, 0.95, 0.85)
                else:
                    o["emis"] = o["_orig_emis"]
        elif act == "move_offset":
            ox, oy, oz = trig.get("offset", (0.0, 3.0, 0.0))
            sign = 1.0 if is_active else -1.0
            for o in matched:
                px, py, pz = o["pos"]
                o["pos"] = (px + ox * sign, py + oy * sign, pz + oz * sign)
        self.spatial_hash.rebuild(self.scene.get("objects", []))
        state_lbl = "ON" if is_active else "OFF"
        self.log("TRIGGER", "#ffaa00", f"Activated '{tid}' [{state_lbl}] ({act}) on {len(matched)} object(s).")

    def trigger_interact(self, now: float):
        if self.nearby_interact_target is not None:
            self.apply_trigger(self.nearby_interact_target, now=now)

    def check_portals_and_triggers(self, cam_pos: list[float], now: float):
        self.nearby_interact_prompt = ""
        self.nearby_interact_target = None
        best_interact_d2 = 1e9
        for trig in self.scene.get("triggers", []):
            tx, ty, tz = trig["pos"]
            rad = trig.get("radius", 2.5)
            d_sq = (cam_pos[0] - tx) ** 2 + ((cam_pos[1] - 1.8) - ty) ** 2 + (cam_pos[2] - tz) ** 2
            if d_sq <= rad * rad:
                if trig.get("interact"):
                    if d_sq < best_interact_d2:
                        best_interact_d2 = d_sq
                        self.nearby_interact_target = trig
                        lbl = trig.get("label") or f"Activate {trig['id']}"
                        self.nearby_interact_prompt = f"[F] {lbl}"
                else:
                    self.apply_trigger(trig, now=now)

        if now < self.portal_cooldown_until:
            return
        for portal in self.scene.get("portals", []):
            px, py, pz = portal["pos"]
            rad = portal.get("radius", 2.0)
            d_sq = (cam_pos[0] - px) ** 2 + ((cam_pos[1] - 1.8) - py) ** 2 + (cam_pos[2] - pz) ** 2
            if d_sq <= rad * rad:
                dest = portal.get("to", "hub")
                p_srv = str(portal.get("server", "") or "").strip()
                p_spawn = portal.get("spawn")
                p_yaw = portal.get("yaw")
                self.portal_cooldown_until = now + 3.0
                if p_srv:
                    bmarks = load_server_directory().get("bookmarks", {})
                    resolved_srv = next((v for k, v in bmarks.items() if k.lower() == p_srv.lower()), p_srv)
                    norm_srv = normalize_ws_url(resolved_srv)
                    if not norm_srv:
                        self.log("ERR", "#ff4444", f"Portal has invalid target server '{p_srv}'.")
                        break
                    if norm_srv != self.server_url:
                        self.log("PORTAL", "#00ff66", f"Stepping through cross-server portal -> {norm_srv}/{dest}...")
                        self.hop_to_server(norm_srv, dest, spawn_pos=p_spawn, spawn_yaw=p_yaw)
                        break
                self.log("PORTAL", "#00ff66", f"Stepping through portal -> '{dest}'...")
                cached = get_cached_world(dest)
                pkt = {"type": "join_world", "world": dest}
                if cached:
                    pkt["cached_hash"] = cached["sha256"]
                if p_spawn is not None:
                    pkt["spawn_pos"] = p_spawn
                if p_yaw is not None:
                    pkt["spawn_yaw"] = p_yaw
                self.send_json(pkt)
                break

    def _trigger_ws_reconnect(self):
        if self.loop and not self.loop.is_closed():
            ws_ref = self.active_ws
            ct_ref = self._connect_task
            def _do_close():
                if ct_ref is not None and not ct_ref.done():
                    ct_ref.cancel()
                if ws_ref is not None:
                    asyncio.create_task(ws_ref.close())
            try:
                self.loop.call_soon_threadsafe(_do_close)
            except RuntimeError:
                pass

    def _record_recent_server(self, url: str, node_name: str):
        sdir = load_server_directory()
        recent = [r for r in sdir.get("recent", []) if r.get("url") != url]
        recent.insert(0, {"url": url, "node_name": node_name, "last_seen": int(time.time())})
        sdir["recent"] = recent[:12]
        save_server_directory(sdir)

    def _drain_out_queue(self):
        if self.loop and not self.loop.is_closed():
            def _clear():
                while not self.out_queue.empty():
                    try:
                        self.out_queue.get_nowait()
                    except Exception:
                        break
            try:
                self.loop.call_soon_threadsafe(_clear)
            except RuntimeError:
                pass

    def hop_to_server(self, target_url: str, target_world: str = "hub", spawn_pos=None, spawn_yaw=None):
        norm_url = normalize_ws_url(target_url)
        if not norm_url:
            self.log("ERR", "#ff4444", "Invalid WebSocket URL (expected ws:// or wss://host[:port]).")
            return
        self.ghost_active = False
        self.peers.clear()
        self.radio.stop()
        self._drain_out_queue()
        if self.server_url and self.server_url != norm_url:
            self.prev_server = (self.server_url, self.world or "hub")
        self.reconnect_target = (norm_url, target_world or "hub", spawn_pos, spawn_yaw)
        self.log("HOP", "#00ff66", f"Switching server to {norm_url} (world: {target_world or 'hub'})...")
        self._trigger_ws_reconnect()

    def _snap_coord(self, val: float) -> float:
        if self.grid_snap > 0.0:
            return round(round(val / self.grid_snap) * self.grid_snap, 3)
        return round(val, 2)

    def poll_file_watch(self, now: float):
        if not self.watch_spec or not self.connected:
            return
        if now - self.watch_spec.get("last_poll", 0.0) < 1.5:
            return
        self.watch_spec["last_poll"] = now
        fp: Path = self.watch_spec["path"]
        try:
            if not fp.is_file():
                self.log("WATCH", "#ff4444", f"Watched file missing ({fp.name}); stopping watch.")
                self.watch_spec = None
                return
            st = fp.stat()
            if st.st_size > 5 * 1024 * 1024:
                self.log("WATCH", "#ff4444", f"Watched file exceeds 5 MB limit; stopping watch.")
                self.watch_spec = None
                return
            if st.st_mtime <= self.watch_spec.get("mtime", 0.0):
                return
            self.watch_spec["mtime"] = st.st_mtime
            raw_md = fp.read_text(encoding="utf-8", errors="replace")
            digest = compute_md_sha256(raw_md)
            if digest == self.watch_spec.get("sha256"):
                return
            kind = self.watch_spec["kind"]
            name = self.watch_spec["name"]
            ok, err, _ = validate_mv3d_document(raw_md, kind=kind)
            if not ok:
                self.log("WATCH-ERR", "#ff4444", f"Skipped reload ({err})")
                return
            self.watch_spec["sha256"] = digest
            if kind == "avatar":
                store_cached_avatar_md(name, raw_md)
            self.send_json({"type": "upload", "kind": kind, "name": name, "content": raw_md})
            self.log("WATCH", "#00ff66", f"Hot-reloaded {kind} '{name}' from {fp.name}.")
        except Exception as exc:
            self.log("WATCH-ERR", "#ff4444", f"Watch error: {exc}")
            self.watch_spec = None

    def start_background(self):
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main_net())
        finally:
            try:
                pending = asyncio.all_tasks(self.loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                self.loop.close()
            except Exception:
                pass

    async def _main_net(self):
        while self.running:
            target_world = self.world or "hub"
            target_spawn = None
            target_yaw = None
            if self.reconnect_target:
                self.server_url, target_world, target_spawn, target_yaw = self.reconnect_target
                self.reconnect_target = None
            try:
                self._connect_task = asyncio.current_task()
                async with websockets.connect(self.server_url, open_timeout=10.0, max_size=6 * 1024 * 1024, ping_interval=20, ping_timeout=20) as ws:
                    self.active_ws = ws
                    self.connected = True
                    async def sender():
                        while True:
                            await ws.send(await self.out_queue.get())
                    send_task = asyncio.create_task(sender())
                    try:
                        async for raw in ws:
                            try:
                                data = json.loads(raw)
                            except Exception:
                                continue
                            if not isinstance(data, dict):
                                continue
                            mtype = data.get("type")
                            if mtype == "challenge":
                                sig = self.sk.sign(str(data.get("nonce", "")).encode("utf-8")).signature.hex()
                                auth_pkt = {
                                    "type": "auth",
                                    "pubkey": self.pubkey_hex,
                                    "signature": sig,
                                    "nick": self.nick,
                                    "color": self.color,
                                    "avatar": self.avatar,
                                    "world": target_world,
                                }
                                if target_spawn is not None:
                                    auth_pkt["spawn_pos"] = target_spawn
                                if target_yaw is not None:
                                    auth_pkt["spawn_yaw"] = target_yaw
                                if self.avatar and self.avatar != "default":
                                    av_md = load_cached_avatar_md(self.avatar)
                                    if av_md:
                                        auth_pkt["avatar_md"] = av_md
                                await ws.send(json.dumps(auth_pkt))
                            elif mtype == "auth_ok":
                                prev_av = self.avatar
                                self.role = data.get("role", "guest").upper()
                                self.short_id = data.get("short_id", "")
                                self.nick = data.get("nick", self.nick)
                                self.avatar = _clean_ident(data.get("avatar", self.avatar or "default"), 32, "default")
                                self.node_name = data.get("node_name", "Makerverse")
                                self.worlds = data.get("worlds", ["hub"])
                                self.avatars = data.get("avatars", [])
                                srv_av_md = str(data.get("avatar_md", "") or "")
                                if self.avatar != "default" and srv_av_md:
                                    mesh = build_normalized_avatar_mesh(srv_av_md)
                                    if mesh:
                                        self._store_avatar_mesh(self.avatar, mesh, md_text=srv_av_md)
                                elif self.avatar != "default" and prev_av != "default" and self.avatar != prev_av:
                                    old_cached_md = load_cached_avatar_md(prev_av)
                                    if old_cached_md:
                                        store_cached_avatar_md(self.avatar, old_cached_md)
                                self._save_profile()
                                self._record_recent_server(self.server_url, self.node_name)
                                if data.get("avatar_synced") and self.avatar != "default":
                                    self.log("ID", "#00ff66", f"Carried identity & avatar '{self.avatar}' to {self.node_name}.")
                                self._apply_world(data.get("world", "hub"), data.get("md", ""), data.get("description", ""), data.get("radio_url", ""), data.get("radio_name", ""), data.get("spawn_pos"), data.get("spawn_yaw", 0.0))
                            elif mtype == "world_data":
                                self._apply_world(data.get("world", "hub"), data.get("md", ""), data.get("description", ""), data.get("radio_url", ""), data.get("radio_name", ""), data.get("spawn_pos"), data.get("spawn_yaw", 0.0), bool(data.get("in_place")))
                            elif mtype == "world_cached":
                                cached = get_cached_world(data.get("world", "hub"))
                                if cached:
                                    self._apply_world(cached["world"], cached["md"], data.get("description", ""), data.get("radio_url", ""), data.get("radio_name", ""), data.get("spawn_pos"), data.get("spawn_yaw", 0.0))
                                else:
                                    await ws.send(json.dumps({"type": "join_world", "world": data.get("world", "hub")}))
                            elif mtype == "world_ratings_top":
                                ranked = data.get("worlds", [])
                                if not ranked:
                                    self.log("TOP-WORLDS", "#ffaa00", "No rated worlds yet. Use /rate <1-5> [comment] in a world.")
                                else:
                                    self.log("TOP-WORLDS", "#00ff66", f"Top Rated Worlds ({len(ranked)}):")
                                    for idx_r, item in enumerate(ranked, 1):
                                        d_txt = f" - {item['description']}" if item.get("description") else ""
                                        self.log("RANK", "#00ffcc", f"  #{idx_r} {item['world']} ({item['avg']:.2f}/5, {item['count']} vote{'s' if item['count'] != 1 else ''}){d_txt}")
                            elif mtype == "worlds_list":
                                self.worlds = data.get("worlds", ["hub"])
                                descs = data.get("descriptions", {})
                                tags_map = data.get("tags", {})
                                owners_map = data.get("owners", {})
                                ratings_map = data.get("ratings", {})
                                q = self.world_search_query.strip().lower()
                                self.world_search_query = ""
                                matched_worlds = []
                                for w_nm in self.worlds:
                                    if not q:
                                        matched_worlds.append(w_nm)
                                        continue
                                    hay = " ".join([
                                        w_nm,
                                        descs.get(w_nm, ""),
                                        owners_map.get(w_nm, ""),
                                        " ".join(tags_map.get(w_nm, [])),
                                    ]).lower()
                                    if q in hay:
                                        matched_worlds.append(w_nm)
                                hdr = f"Matching '{q}' ({len(matched_worlds)}):" if q else f"Available ({len(matched_worlds)}):"
                                self.log("WORLDS", "#00ff66", f"{hdr} " + (", ".join(matched_worlds) or "none"))
                                for w_nm in matched_worlds:
                                    r_meta = ratings_map.get(w_nm)
                                    r_tag = f" [{r_meta['avg']:.1f}/5 ({r_meta['count']})]" if r_meta else ""
                                    t_list = tags_map.get(w_nm, [])
                                    t_tag = f" #{' #'.join(t_list)}" if t_list else ""
                                    o_tag = f" (by {owners_map[w_nm]})" if owners_map.get(w_nm) else ""
                                    d_tag = f": {descs[w_nm]}" if descs.get(w_nm) else ""
                                    if r_tag or d_tag or t_tag or o_tag:
                                        self.log("WORLD", "#00ffcc", f"  - {w_nm}{o_tag}{r_tag}{t_tag}{d_tag}")
                            elif mtype == "world_info":
                                w_nm = data.get("world", self.world)
                                title_s = str(data.get("title") or w_nm).strip()
                                title_hdr = f"{title_s} ({w_nm})" if title_s.lower() != w_nm.lower() else w_nm
                                vis_lbl = "PRIVATE" if data.get("private") else "PUBLIC"
                                cnt_r = int(data.get("rating_count", 0) or 0)
                                avg_r = float(data.get("avg_rating", 0.0) or 0.0)
                                rat_str = f"  |  Rating: {avg_r:.1f}/5 ({cnt_r} vote{'s' if cnt_r != 1 else ''})" if cnt_r > 0 else "  |  Unrated"
                                self.log("WORLD", "#00ff66", f"{title_hdr}  [{vis_lbl}]{rat_str}")

                                desc_s = str(data.get("description") or "").strip() or "No description set."
                                tags_l = [str(t).strip() for t in data.get("tags", []) if str(t).strip()]
                                tags_s = f"  |  Tags: {' '.join('#' + t for t in tags_l)}" if tags_l else ""
                                self.log("ABOUT", "#00ffcc", f"{desc_s}{tags_s}")

                                owner = data.get("owner")
                                upd_ts = int(data.get("updated_at", 0) or 0)
                                upd_str = f"  |  Updated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(upd_ts))}" if upd_ts > 0 else ""
                                if data.get("is_system") and not owner:
                                    self.log("AUTHOR", "#88ccff", f"Neo-Genesis Grid (System World){upd_str}")
                                elif owner:
                                    on_tag = " [ONLINE]" if owner.get("online") else ""
                                    self.log("AUTHOR", "#88ccff", f"{owner['nick']} (#{owner['short_id']}){on_tag}{upd_str}")
                                else:
                                    self.log("AUTHOR", "#ffaa00", f"Unclaimed{upd_str}")

                                raw_sp = data.get("spawn_pos")
                                sp_pos = raw_sp if (isinstance(raw_sp, list) and len(raw_sp) >= 3) else [0.0, 2.25, 13.5]
                                sp_yaw = float(data.get("spawn_yaw", 0.0) or 0.0)
                                sp_kind = "custom" if data.get("spawn_custom") else "default"
                                n_objs = int(data.get("object_count", 0) or 0)
                                n_ports = int(data.get("portal_count", 0) or 0)
                                n_trigs = int(data.get("trigger_count", 0) or 0)
                                kb_sz = max(1, (int(data.get("bytes", 0) or 0) + 512) // 1024)
                                snaps_n = int(data.get("snapshots_count", 0) or 0)
                                self.log(
                                    "SCENE",
                                    "#b8d8ff",
                                    f"Spawn: ({float(sp_pos[0]):.1f}, {float(sp_pos[1]):.1f}, {float(sp_pos[2]):.1f}) yaw {sp_yaw:.0f} deg ({sp_kind})  |  "
                                    f"{n_objs} objs, {n_ports} portals, {n_trigs} triggers ({kb_sz} KB, {snaps_n} backups)",
                                )

                                r_url = str(data.get("radio_url") or "").strip()
                                r_name = str(data.get("radio_name") or "").strip()
                                if r_url:
                                    r_disp = f"{r_name} ({r_url})" if (r_name and r_name != r_url) else r_url
                                    self.log("RADIO", "#ff99dd", r_disp)

                                players = data.get("players", [])
                                cbs = data.get("co_builders", [])
                                p_str = ", ".join(f"{p['nick']} (#{p['short_id']})" for p in players) if players else "none"
                                cb_str = f"  |  Co-builders: {', '.join(c['nick'] for c in cbs)}" if cbs else ""
                                self.log("ONLINE", "#7dffb3", f"Players here ({len(players)}): {p_str}{cb_str}")
                            elif mtype == "world_desc_updated":
                                w_nm = data.get("world", self.world)
                                d_txt = data.get("description", "")
                                has_tags = "tags" in data and isinstance(data.get("tags"), list)
                                t_list = [str(t).strip() for t in (data.get("tags") or []) if isinstance(t, (str, int, float)) and not isinstance(t, bool) and str(t).strip()]
                                r_url = str(data.get("radio_url", "") or "").strip()
                                r_name = str(data.get("radio_name", "") or r_url).strip()
                                if w_nm == self.world:
                                    self.world_desc = d_txt
                                    if "radio_url" in data:
                                        self.radio.set_stream(r_url, r_name)
                                t_title = " ".join(str(data.get("title") or "").split()).strip()[:64]
                                title_prefix = f'"{t_title}" - ' if (t_title and t_title.lower() != str(w_nm).lower()) else ""
                                if t_list:
                                    tags_suffix = f" | Tags: {' '.join('#' + t for t in t_list)}"
                                elif has_tags:
                                    tags_suffix = " | Tags: (cleared)"
                                else:
                                    tags_suffix = ""
                                self.log("META", "#00ff66", f"[{w_nm}] {title_prefix}{d_txt or '(no description)'}{tags_suffix}")
                            elif mtype == "role_updated":
                                self.role = str(data.get("role", "guest")).upper()
                                self.log("PERMS", "#ff66cc", f"Your server role was updated to: {self.role}")
                            elif mtype == "perms_list":
                                p = data.get("perms", {})
                                cur_c = int(data.get("current_world_count", max(0, len(self.worlds) - 1)))
                                m_tot = f"{cur_c}/{p.get('max_total_worlds')}" if p.get("max_total_worlds") else f"{cur_c}/unlimited"
                                m_usr = p.get("max_worlds_per_user", 0) or "unlimited"
                                nick_reg = p.get("nick_registry", {})
                                fmt_k = lambda ks: ", ".join(f"{nick_reg.get(k, k[:8])} ({k[:8]})" for k in ks) or "none"
                                self.log("PERMS", "#00ff66", f"Default Role: {p.get('default_role', 'builder')} | Worlds: {m_tot} total, {m_usr}/user")
                                self.log("PERMS", "#00ffcc", f"Admins: {fmt_k(p.get('admins', []))} | Builders: {fmt_k(p.get('builders', []))} | Banned: {len(p.get('banned', []))}")
                            elif mtype == "world_acl_info":
                                w_nm = data.get("world", self.world)
                                cbs = data.get("co_builders", [])
                                wls = data.get("whitelist", [])
                                fmt_m = lambda ms: ", ".join(f"{m['nick']} ({m['short_id']})" if isinstance(m, dict) else str(m)[:8] for m in ms) or "none"
                                self.log("WORLD-ACL", "#00ff66", f"World '{w_nm}' visibility: {'PRIVATE' if data.get('private') else 'PUBLIC'}")
                                self.log("CO-BUILDERS", "#00ffcc", f"Co-Builders: {fmt_m(cbs)}")
                                self.log("WHITELIST", "#ff66cc", f"Whitelist: {fmt_m(wls)}")
                            elif mtype == "prefabs_list":
                                p_names = data.get("prefabs", [])
                                local_p = sorted(p.stem for p in PREFAB_CACHE_DIR.glob("*.md")) if PREFAB_CACHE_DIR.exists() else []
                                merged_p = sorted(set(p_names) | set(local_p))
                                self.prefabs = merged_p
                                self.log("PREFABS", "#00ff66", f"Blueprints ({len(merged_p)}): " + (", ".join(merged_p) or "none. Use /saveprefab <name> [count]"))
                            elif mtype == "prefab_saved":
                                p_nm = str(data.get("name", ""))
                                if data.get("md"):
                                    store_cached_prefab_md(p_nm, str(data["md"]))
                                if data.get("prefabs"):
                                    self.prefabs = data["prefabs"]
                                self.log("PREFAB", "#00ff66", f"Saved blueprint '{p_nm}' ({int(data.get('count', 1))} block(s)). Stamp with /stamp {p_nm}")
                            elif mtype == "world_backups_list":
                                snaps = data.get("backups", [])
                                if not snaps:
                                    self.log("BACKUPS", "#ffaa00", f"No backups for '{data.get('world', self.world)}'.")
                                else:
                                    self.log("BACKUPS", "#00ff66", f"Backups ({len(snaps)}): " + ", ".join(s["file"] for s in snaps[:6]))
                            elif mtype == "world_export_data":
                                w_nm = _clean_ident(data.get("world", self.world), 32, "world")
                                md_out = str(data.get("md", ""))
                                EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                                out_fp = (EXPORTS_DIR.resolve() / f"{w_nm}.md").resolve()
                                if out_fp.parent == EXPORTS_DIR.resolve():
                                    _write_secure_file(out_fp, md_out)
                                    self.log("EXPORT", "#00ff66", f"Exported '{w_nm}' -> {out_fp}")
                            elif mtype == "friends_list":
                                fl = data.get("friends", [])
                                pl = data.get("pending", [])
                                f_txt = ", ".join(f"{f['nick']} ({f['short_id']}){' [in ' + f['world'] + ']' if f.get('online') else ' [off]'}" for f in fl) or "none"
                                self.log("FRIENDS", "#00ff66", f"Friends: {f_txt}")
                                if pl:
                                    self.log("REQUESTS", "#ff66cc", "Pending: " + ", ".join(f"{p['nick']} ({p['short_id']})" for p in pl) + " (/accept <id>)")
                            elif mtype == "whisper":
                                if data.get("incoming"):
                                    f_nick = str(data.get("from_nick", "")).strip().lower()
                                    f_id = str(data.get("from_id", "")).strip().lower()
                                    if (f_nick and f_nick in self.blocked_users) or (f_id and f_id in self.blocked_users):
                                        continue
                                    who = data.get("from_nick") or "?"
                                    r_pref = str(data.get("from_rank_prefix") or _rank_prefix(data.get("from_role", ""))).strip()
                                    tag = "PM from"
                                else:
                                    who = data.get("to_nick") or "?"
                                    r_pref = str(data.get("to_rank_prefix") or _rank_prefix(data.get("to_role", ""))).strip()
                                    tag = "PM to"
                                w_role = data.get("from_role", "") if data.get("incoming") else data.get("to_role", "")
                                self.log(f"{tag} {who}", "#ff66ff", data.get("text", ""), rank=w_role)
                            elif mtype == "teleport":
                                if data.get("pos") and len(data["pos"]) >= 3:
                                    self.teleport_req = [float(data["pos"][0]), float(data["pos"][1]), float(data["pos"][2])]
                                if data.get("msg"):
                                    self.log("TELEPORT", "#00ff66", data["msg"])
                            elif mtype == "emote":
                                sid = data.get("short_id")
                                em_nick_l = str(data.get("nick", "")).strip().lower()
                                em_id_l = str(sid or "").strip().lower()
                                if (em_nick_l and em_nick_l in self.blocked_users) or (em_id_l and em_id_l in self.blocked_users):
                                    continue
                                em = str(data.get("emote", "wave")).strip()
                                if sid in self.peers and em:
                                    self.peers[sid]["emote"] = em
                                    self.peers[sid]["emote_until"] = time.time() + 2.5
                                peer_r = data.get("role") or (self.peers[sid].get("role", "") if sid in self.peers else (self.role if sid == self.short_id else ""))
                                self.log("*", "#ff66cc", f"{data.get('nick', '?')} performs /{em}", rank=peer_r)
                            elif mtype == "avatars_list":
                                self.avatars = data.get("avatars", [])
                                self.log("AVATARS", "#00ff66", f"Available avatars (active: {data.get('current', self.avatar)}): {', '.join(self.avatars) or 'none'}")
                            elif mtype == "avatar_data" and data.get("name") and data.get("md"):
                                mesh = build_normalized_avatar_mesh(data["md"])
                                if mesh:
                                    self._store_avatar_mesh(data["name"], mesh, md_text=data["md"])
                            elif mtype == "peer_avatar":
                                sid, av_name, av_md = data.get("short_id"), data.get("avatar", "default"), str(data.get("md", ""))
                                if sid in self.peers:
                                    self.peers[sid]["avatar"] = av_name
                                if av_name and av_name != "default":
                                    mesh = build_normalized_avatar_mesh(av_md) if av_md else None
                                    if mesh:
                                        self._store_avatar_mesh(av_name, mesh, md_text=av_md)
                                    else:
                                        self._ensure_avatar_loaded(av_name)
                            elif mtype == "peers":
                                for p in data.get("peers", []):
                                    self.peers[p["short_id"]] = p
                                    self._ensure_avatar_loaded(p.get("avatar", "default"))
                            elif mtype == "peer_join":
                                p = data["peer"]
                                self.peers[p["short_id"]] = p
                                p_av, p_md = p.get("avatar", "default"), str(data.get("avatar_md", ""))
                                if p_av and p_av != "default" and p_md:
                                    mesh = build_normalized_avatar_mesh(p_md)
                                    if mesh:
                                        self._store_avatar_mesh(p_av, mesh, md_text=p_md)
                                self._ensure_avatar_loaded(p_av)
                                self.log("WORLD", "#8888ff", f"{p['nick']} joined.", rank=p.get("role", ""))
                            elif mtype == "peer_role":
                                sid = data.get("short_id")
                                new_pr = str(data.get("role", "")).strip()
                                if sid in self.peers and new_pr:
                                    self.peers[sid]["role"] = new_pr
                                    self.log("PERMS", "#ff66cc", f"{self.peers[sid].get('nick', 'Peer')} rank updated:", rank=new_pr)
                            elif mtype == "nick_updated":
                                prev_n = self.nick
                                self.nick = _clean_ident(data.get("nick", self.nick), 24, self.nick)
                                self._save_profile()
                                if data.get("requested") and data["requested"] != self.nick:
                                    self.log("ID", "#ffaa00", f"'{data['requested']}' taken; set nick to '{self.nick}'.")
                                elif prev_n != self.nick:
                                    self.log("ID", "#00ff66", f"Nickname changed: {prev_n} -> {self.nick}")
                            elif mtype == "peer_nick":
                                sid = data.get("short_id")
                                new_n = str(data.get("nick", "")).strip()
                                old_n = str(data.get("old_nick", "")).strip()
                                if sid in self.peers and new_n:
                                    self.peers[sid]["nick"] = new_n
                                    if old_n and old_n != new_n:
                                        self.log("WORLD", "#8888ff", f"{old_n} is now known as {new_n}.")
                            elif mtype == "peer_leave":
                                self.peers.pop(data.get("short_id"), None)
                            elif mtype == "peer_move":
                                sid = data.get("short_id")
                                if sid in self.peers:
                                    self.peers[sid]["pos"] = data.get("pos", [0, 0, 0])
                                    self.peers[sid]["rot"] = data.get("rot", [0, 1, 0, 0])
                            elif mtype == "mood_sync":
                                sid_mo = str(data.get("short_id", "") or "")
                                m_val = normalize_expression_name(str(data.get("mood", "neutral")))
                                if sid_mo in self.peers:
                                    self.peers[sid_mo]["mood"] = m_val
                            elif mtype == "media_sync":
                                sid_m = str(data.get("screen_id", "") or "")
                                if sid_m:
                                    try:
                                        s_y = max(0, min(1200, int(data.get("scroll_y", 0) or 0)))
                                    except (TypeError, ValueError):
                                        s_y = 0
                                    self.media_states[sid_m] = {
                                        "paused": bool(data.get("paused", False)),
                                        "muted": bool(data.get("muted", False)),
                                        "scroll_y": s_y,
                                        "updated_by": str(data.get("nick", "peer")),
                                    }
                            elif mtype == "chat":
                                sid_c = str(data.get("short_id", "")).strip()
                                c_nick = str(data.get("nick", "")).strip()
                                c_nick_l = c_nick.lower()
                                c_id_l = sid_c.lower()
                                if (c_nick_l and c_nick_l in self.blocked_users) or (c_id_l and c_id_l in self.blocked_users):
                                    continue
                                c_role = data.get("role") or (
                                    self.peers[sid_c].get("role", "") if sid_c in self.peers else (self.role if sid_c == self.short_id else "")
                                )
                                if not c_role and data.get("rank_prefix"):
                                    c_role = str(data["rank_prefix"]).strip("[] ")
                                self.log(c_nick or "?", data.get("color", "#00ffcc"), data.get("text", ""), rank=c_role)
                            elif mtype == "upload_ok":
                                kind_up, name_up = data.get("kind"), data.get("name", "")
                                if data.get("worlds"):
                                    self.worlds = data["worlds"]
                                self.log("UPLOAD", "#00ff66", f"Uploaded {kind_up}: {name_up}")
                                if kind_up == "world":
                                    if self.watch_spec and self.watch_spec.get("kind") == "world" and self.watch_spec.get("name") == name_up and self.world == name_up:
                                        # Reload in-place without resetting camera position during live watch
                                        fp_w = self.watch_spec["path"]
                                        if fp_w.is_file():
                                            self._apply_world(name_up, fp_w.read_text(encoding="utf-8", errors="replace"), in_place=True)
                                    else:
                                        await ws.send(json.dumps({"type": "join_world", "world": name_up}))
                                elif kind_up == "avatar":
                                    self.avatar = name_up
                                    self._save_profile()
                                    if data.get("md"):
                                        mesh = build_normalized_avatar_mesh(data["md"])
                                        if mesh:
                                            self._store_avatar_mesh(name_up, mesh, md_text=data["md"])
                                            self.log("AVATAR", "#00ff66", f"Equipped avatar '{name_up}' ({len(mesh['objects'])} parts). Press [V] to view.")
                            elif mtype in ("error", "info"):
                                if data.get("equipped_avatar"):
                                    self.avatar = _clean_ident(data["equipped_avatar"], 32, "default")
                                    self._save_profile()
                                if data.get("avatar_md"):
                                    mesh = build_normalized_avatar_mesh(data["avatar_md"])
                                    if mesh:
                                        self._store_avatar_mesh(self.avatar, mesh, md_text=data["avatar_md"])
                                self.log("SERVER", "#ffaa00", data.get("msg", ""))
                    finally:
                        self.active_ws = None
                        self._connect_task = None
                        self.connected = False
                        send_task.cancel()
                        try:
                            await send_task
                        except asyncio.CancelledError:
                            pass
            except Exception as exc:
                self.active_ws = None
                self._connect_task = None
                self.connected = False
                if self.reconnect_target:
                    continue
                if self.running:
                    detail = str(exc).strip() or type(exc).__name__
                    self.log("NET-ERR", "#ff4444", f"Connection to {self.server_url} failed ({detail}); retrying...")
                    await asyncio.sleep(2.5)


def _target_placed_id(client: MakerverseClient, explicit_tok: str = "") -> str:
    if explicit_tok and re.fullmatch(r"[0-9a-fA-F]{4,8}", explicit_tok.strip()):
        return explicit_tok.strip().lower()
    if client.looked_at_hit is not None:
        d_name = client.looked_at_hit[0].get("def_name", "")
        if d_name.startswith("PLACED_"):
            return d_name.split("_", 1)[1].lower()
    return ""


def handle_command(client: MakerverseClient, text: str):
    parts = text.strip().split(" ", 3)
    cmd = parts[0].lower()
    if cmd == "/help":
        client.log("HELP", "#ff66cc", "/spawn | /setspawn [x y z] [yaw] | /clearspawn | /worldinfo [w] | /title [w] <txt|clear> | /desc [w] <txt|clear> | /tags [w] <t1,t2|clear> | /sky <hex> | /fog <hex> | /radio <url|off>")
        client.log("HELP", "#ff66cc", "/ghost [shape|off] [col] [sc] | /place <shape> [col] [sc] | /placeportal <world|srv/world> [col] [rad] | /moveobj | /scaleobj | /paint | /delobj | /undo | /snap [m]")
        client.log("HELP", "#ff66cc", "/perms | /admin <u> | /builder <u> | /setrole <u> <admin|builder|guest> | /ban <u> | /defaultrole <guest|builder> | /acl | /privateworld <on|off>")
        client.log("HELP", "#ff66cc", "/backup | /backups | /restore <file.md> | /export | /emote <1-4> | /friends | /addfriend <u> | /msg <u> <txt> | /goto <u> | /hop <node> | /back | /nick <n> | /key")
    elif cmd == "/rate":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /rate <1..5> [comment] or /rate <world> <1..5> [comment]")
            return
        rest_str = text.strip().split(" ", 1)[1].strip()
        sub = rest_str.split(" ", 2)
        tok1 = sub[0].strip()
        tok2 = sub[1].strip() if len(sub) >= 2 else ""
        is_int = lambda s: bool(s) and (s.isdigit() or (s.startswith("-") and s[1:].isdigit()))
        if is_int(tok1):
            target_w, stars_val = client.world, int(tok1)
            review_str = " ".join(rest_str.split(" ", 1)[1].split())[:140] if len(sub) >= 2 else ""
        elif is_int(tok2):
            target_w, stars_val = tok1, int(tok2)
            review_str = " ".join(sub[2].split())[:140] if len(sub) >= 3 else ""
        else:
            client.log("ERR", "#ff4444", "Rating must be a whole number from 1 to 5.")
            return
        if not (1 <= stars_val <= 5):
            client.log("ERR", "#ff4444", "Rating must be between 1 and 5 stars.")
            return
        client.send_json({"type": "rate_world", "action": "rate", "world": target_w, "stars": stars_val, "review": review_str})
    elif cmd == "/topworlds":
        client.send_json({"type": "rate_world", "action": "top"})
    elif cmd == "/worldinfo":
        target_w = parts[1].strip() if len(parts) > 1 and parts[1].strip() else client.world
        client.send_json({"type": "get_world_info", "world": target_w})
    elif cmd == "/title":
        if len(parts) < 2 or not parts[1].strip():
            client.log("TITLE", "#00ffcc", "Usage: /title [world] <new title | clear>")
            return
        rest_str = text.strip().split(" ", 1)[1].strip()
        sub2 = rest_str.split(" ", 1)
        if len(sub2) == 2 and sub2[0] in client.worlds and sub2[1].strip():
            target_w, title_body = sub2[0], sub2[1].strip()
        else:
            target_w, title_body = client.world, rest_str
        if title_body.lower() in ("clear", "none"):
            title_body = ""
        elif len(title_body) >= 2 and title_body[0] == title_body[-1] and title_body[0] in ('"', "'"):
            title_body = title_body[1:-1].strip()
        title_body = " ".join(title_body.split())[:64]
        client.send_json({"type": "set_world_meta", "world": target_w, "title": title_body})
    elif cmd == "/desc":
        if len(parts) < 2 or not parts[1].strip():
            client.log("DESC", "#00ffcc", f"'{client.world}': {client.world_desc or '(none set)'} (usage: /desc [world] <description|clear>)")
            return
        rest_str = text.strip().split(" ", 1)[1].strip()
        sub2 = rest_str.split(" ", 1)
        if len(sub2) == 2 and sub2[0] in client.worlds:
            target_w, desc_body = sub2[0], sub2[1].strip()
        else:
            target_w, desc_body = client.world, rest_str
        if desc_body.lower() in ("clear", "none"):
            desc_body = ""
        client.send_json({"type": "set_world_meta", "world": target_w, "description": desc_body})
    elif cmd == "/tags":
        if len(parts) < 2 or not parts[1].strip():
            client.log("TAGS", "#00ffcc", f"Usage: /tags [world] <tag1, tag2, ... | clear> (search with /worlds <tag>)")
            return
        rest_str = text.strip().split(" ", 1)[1].strip()
        sub2 = rest_str.split(" ", 1)
        if len(sub2) == 2 and sub2[0] in client.worlds:
            target_w, tags_body = sub2[0], sub2[1].strip()
        else:
            target_w, tags_body = client.world, rest_str
        if tags_body.lower() in ("clear", "none"):
            tag_list = []
        else:
            tag_list = []
            for tok in re.split(r"[,\s]+", tags_body):
                ct = _clean_ident(tok.lstrip("#"), 24, "").lower()
                if ct and ct not in tag_list:
                    tag_list.append(ct)
                if len(tag_list) >= 8:
                    break
            if not tag_list:
                client.log("ERR", "#ff4444", "Provide at least one valid tag (letters, numbers, '_' or '-') or 'clear'.")
                return
        client.send_json({"type": "set_world_meta", "world": target_w, "tags": tag_list})
    elif cmd == "/sky" and len(parts) > 1:
        c_hex = "".join(f"{max(0,min(255,int(round(v*255)))):02x}" for v in parse_color(parts[1]))
        client.send_json({"type": "set_world_meta", "world": client.world, "sky": f'"#{c_hex}"'})
    elif cmd == "/fog" and len(parts) > 1:
        c_hex = "".join(f"{max(0,min(255,int(round(v*255)))):02x}" for v in parse_color(parts[1]))
        client.send_json({"type": "set_world_meta", "world": client.world, "fog": f'"#{c_hex}"'})
    elif cmd == "/radio":
        if len(parts) < 2 or not parts[1].strip():
            client.log("RADIO", "#00ffcc", f"{client.radio.name or 'None'} ({'MUTED' if client.radio.muted else 'ACTIVE'})")
            return
        arg_s = text.strip().split(" ", 1)[1].strip()
        if arg_s.lower() in ("off", "stop", "none"):
            client.send_json({"type": "set_world_meta", "world": client.world, "radio_url": "", "radio_name": ""})
        else:
            r_parts = arg_s.split(" ", 1)
            ok_u, err_u = is_safe_radio_url(r_parts[0].strip())
            if not ok_u:
                client.log("ERR", "#ff4444", err_u)
                return
            client.send_json({"type": "set_world_meta", "world": client.world, "radio_url": r_parts[0].strip(), "radio_name": r_parts[1].strip() if len(r_parts) > 1 else r_parts[0].strip()})
    elif cmd == "/mute":
        m = client.radio.toggle_mute()
        client.log("RADIO", "#ffaa00" if m else "#00ff66", "Radio muted." if m else "Radio unmuted.")
    elif cmd == "/hop":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", f"Usage: {cmd} <bookmark_name|ws://host:port> [world]")
            return
        rest_s = text.strip().split(" ", 1)[1].strip()
        sub_s = rest_s.split(" ", 1)
        bmarks = load_server_directory().get("bookmarks", {})
        matched_url = next((v for k, v in bmarks.items() if k.lower() == sub_s[0].strip().lower()), sub_s[0].strip())
        dest_w = sub_s[1].strip() if len(sub_s) > 1 and sub_s[1].strip() else "hub"
        client.hop_to_server(matched_url, dest_w)
    elif cmd == "/back":
        if not client.prev_server:
            client.log("INFO", "#ffaa00", "No previous server to return to.")
            return
        prev_u, prev_w = client.prev_server
        client.prev_server = None
        client.hop_to_server(prev_u, prev_w)
    elif cmd == "/servers":
        sdir = load_server_directory()
        bmarks = sdir.get("bookmarks", {})
        recent = sdir.get("recent", [])
        client.log("SERVERS", "#00ff66", f"Active: {client.server_url} ({client.node_name})")
        if bmarks:
            client.log("BOOKMARKS", "#00ffcc", "Bookmarks: " + ", ".join(f"{k} -> {v}" for k, v in bmarks.items()))
        if recent:
            client.log("RECENT", "#88ccff", "Recent: " + ", ".join(f"{r.get('url')} ({r.get('node_name', 'node')})" for r in recent[:6]))
        if not bmarks and not recent:
            client.log("SERVERS", "#ffaa00", "No bookmarks yet. Use /hop <host:port> or /bookmark <name> <host:port>")
    elif cmd == "/bookmark":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /bookmark <name> [ws://host:port] or /bookmark <ws://host:port>")
            return
        rest_s = text.strip().split(" ", 1)[1].strip()
        sub_s = rest_s.split(" ", 1)
        b_name = _clean_ident(sub_s[0], 24, "") if len(sub_s) > 1 else ""
        norm_u = normalize_ws_url(sub_s[1] if len(sub_s) > 1 else sub_s[0])
        if not b_name and norm_u:
            b_name = _clean_ident(urllib.parse.urlsplit(norm_u).hostname or "node", 24, "node")
        if not b_name or not norm_u:
            client.log("ERR", "#ff4444", "Invalid bookmark name or WebSocket URL.")
            return
        sdir = load_server_directory()
        sdir.setdefault("bookmarks", {})[b_name] = norm_u
        save_server_directory(sdir)
        client.log("SERVERS", "#00ff66", f"Saved bookmark '{b_name}' -> {norm_u} (use /hop {b_name})")
    elif cmd == "/unbookmark":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /unbookmark <name|url>")
            return
        target_b = parts[1].strip().lower()
        sdir = load_server_directory()
        bmarks = sdir.get("bookmarks", {})
        removed = next((k for k, v in list(bmarks.items()) if k.lower() == target_b or v.lower() == target_b), None)
        if removed:
            bmarks.pop(removed, None)
            save_server_directory(sdir)
            client.log("SERVERS", "#00ff66", f"Removed bookmark '{removed}'.")
        else:
            client.log("ERR", "#ff4444", f"Bookmark '{parts[1].strip()}' not found.")
    elif cmd == "/worlds":
        client.world_search_query = parts[1].strip() if len(parts) > 1 and parts[1].strip() else ""
        client.send_json({"type": "list_worlds"})
    elif cmd == "/block":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /block <nick|short_id>")
            return
        tok = re.sub(r"^\[[A-Z]+\]\s*", "", parts[1].strip(), flags=re.I).lstrip("#").strip().lower()
        if not tok:
            client.log("ERR", "#ff4444", "Specify a valid nickname or short ID to block.")
            return
        if tok in (client.nick.lower(), client.short_id.lower()):
            client.log("ERR", "#ff4444", "You cannot block yourself.")
            return
        client.blocked_users.add(tok)
        save_blocked_users(client.blocked_users)
        client.log("BLOCK", "#ffaa00", f"Muted chat/whispers from '{tok}' locally.")
    elif cmd == "/unblock":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /unblock <nick|short_id>")
            return
        tok = re.sub(r"^\[[A-Z]+\]\s*", "", parts[1].strip(), flags=re.I).lstrip("#").strip().lower()
        if tok in client.blocked_users:
            client.blocked_users.remove(tok)
            save_blocked_users(client.blocked_users)
            client.log("BLOCK", "#00ff66", f"Unblocked '{tok}'.")
        else:
            client.log("ERR", "#ff4444", f"'{tok or parts[1].strip()}' is not in your block list.")
    elif cmd == "/blocked":
        client.log("BLOCK", "#00ffcc", "Blocked users: " + (", ".join(sorted(client.blocked_users)) or "none"))
    elif cmd == "/waypoint":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /waypoint <name> [x y z]")
            return
        w_toks = text.strip().split()[1:]
        wp_name = _clean_ident(w_toks[0], 24, "")
        if not wp_name:
            client.log("ERR", "#ff4444", "Invalid waypoint name.")
            return
        nums = [float(x) for x in w_toks[1:] if re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", x)]
        if len(nums) >= 3:
            wp_pos = [max(-500.0, min(500.0, nums[0])), max(-100.0, min(500.0, nums[1])), max(-500.0, min(500.0, nums[2]))]
        else:
            cx, cy, cz = client.cam_pos_ref
            wp_pos = [round(cx, 1), round(max(0.0, cy - 1.8), 1), round(cz, 1)]
        client.waypoints[wp_name] = wp_pos
        client.log("WAYPOINT", "#00ff66", f"Set waypoint '{wp_name}' at ({wp_pos[0]:.1f}, {wp_pos[1]:.1f}, {wp_pos[2]:.1f}).")
    elif cmd == "/delwaypoint":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /delwaypoint <name>")
            return
        wp_name = _clean_ident(parts[1].strip(), 24, "")
        if wp_name in client.waypoints:
            client.waypoints.pop(wp_name, None)
            client.log("WAYPOINT", "#00ff66", f"Removed waypoint '{wp_name}'.")
        else:
            client.log("ERR", "#ff4444", f"Waypoint '{parts[1].strip()}' not found.")
    elif cmd == "/waypoints":
        if not client.waypoints:
            client.log("WAYPOINTS", "#ffaa00", "No waypoints set. Use /waypoint <name> [x y z].")
        else:
            client.log("WAYPOINTS", "#00ff66", "Active: " + ", ".join(f"{k} ({v[0]:.0f},{v[1]:.0f},{v[2]:.0f})" for k, v in client.waypoints.items()))
    elif cmd == "/setspawn":
        raw_sp_args = text.strip().split()[1:]
        if not raw_sp_args:
            cx, cy, cz = client.cam_pos_ref
            sx = client._snap_coord(cx)
            sy = client._snap_coord(max(0.0, cy - 1.8))
            sz = client._snap_coord(cz)
            syaw = round((-client.cam_yaw_ref) % 360.0, 1)
        else:
            nums = []
            for tok in raw_sp_args:
                if not re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", tok):
                    client.log("ERR", "#ff4444", "Usage: /setspawn [x y z] [yaw]")
                    return
                val_f = float(tok)
                if not math.isfinite(val_f):
                    client.log("ERR", "#ff4444", "Spawn coordinates must be finite numbers.")
                    return
                nums.append(val_f)
            if len(nums) not in (3, 4):
                client.log("ERR", "#ff4444", "Usage: /setspawn [x y z] [yaw]")
                return
            sx = max(-500.0, min(500.0, round(nums[0], 3)))
            sy = max(-100.0, min(500.0, round(nums[1], 3)))
            sz = max(-500.0, min(500.0, round(nums[2], 3)))
            syaw = round((nums[3] if len(nums) == 4 else (-client.cam_yaw_ref)) % 360.0, 1)
        client.send_json({
            "type": "world_edit",
            "action": "set_spawn",
            "world": client.world,
            "x": sx,
            "y": sy,
            "z": sz,
            "yaw": syaw,
        })
    elif cmd == "/clearspawn":
        client.send_json({"type": "world_edit", "action": "clear_spawn", "world": client.world})
    elif cmd == "/spawn":
        sp = client.scene.get("spawn_pos")
        if sp and len(sp) >= 3:
            client.teleport_req = [float(sp[0]), float(sp[1]) + 1.8, float(sp[2])]
            client.teleport_yaw = float(client.scene.get("spawn_yaw", 0.0))
        else:
            client.teleport_req = [0.0, 4.05, 13.5] if client.world.lower() == "hub" else [0.0, 2.2, 12.0]
            client.teleport_yaw = 0.0
        client.log("MOVE", "#00ff66", f"Returned to '{client.world}' spawn point.")
    elif cmd == "/world" and len(parts) > 1:
        cached = get_cached_world(parts[1])
        payload = {"type": "join_world", "world": parts[1]}
        if cached:
            payload["cached_hash"] = cached["sha256"]
        client.send_json(payload)
    elif cmd == "/templates":
        t_items = [f"{k} ({v['label']})" for k, v in WORLD_TEMPLATES.items()]
        client.log("TEMPLATES", "#00ff66", "Templates: " + ", ".join(t_items))
    elif cmd == "/createworld" and len(parts) > 1:
        wname = _clean_ident(parts[1], 32, "world")
        raw_rest = text.strip().split(" ", 2)[2].strip() if len(text.strip().split(" ", 2)) >= 3 else "flat"
        rest_toks = raw_rest.split(" ", 1)
        raw_tpl = rest_toks[0].strip() if rest_toks and rest_toks[0].strip() else "flat"
        tkey, matched_tpl = resolve_world_template_match(raw_tpl)
        if not matched_tpl:
            client.log("WARN", "#ffaa00", f"Unknown template '{raw_tpl}'; using 'flat'. Run /templates to see options.")
        raw_desc = rest_toks[1].strip() if len(rest_toks) >= 2 else ""
        desc = raw_desc[1:-1] if len(raw_desc) >= 2 and raw_desc[0] == raw_desc[-1] and raw_desc[0] in ('"', "'") else raw_desc
        clean_send_desc = re.sub(r'[\\"\r\n\x00]', " ", " ".join(desc.split())).strip()[:200]
        md = build_template_mv3d_world(tkey, wname, clean_send_desc)
        client.log("BUILD", "#00ffcc", f"Creating world '{wname}' with template '{tkey}' ({WORLD_TEMPLATES[tkey]['label']})...")
        client.send_json({"type": "upload", "kind": "world", "name": wname, "content": md, "description": clean_send_desc})
    elif cmd == "/prefabs":
        client.send_json({"type": "prefab_action", "action": "list"})
    elif cmd == "/saveprefab":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /saveprefab <name> [last_n_placed_blocks]")
            return
        rest_p = text.strip().split(" ", 1)[1].strip().split()
        p_nm = _clean_ident(rest_p[0], 32, "")
        cnt_p = int(rest_p[1]) if len(rest_p) > 1 and rest_p[1].isdigit() else 1
        if not p_nm:
            client.log("ERR", "#ff4444", "Invalid blueprint name.")
            return
        client.send_json({"type": "prefab_action", "action": "save", "name": p_nm, "count": max(1, min(16, cnt_p)), "world": client.world})
    elif cmd == "/stamp":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /stamp <blueprint_name> [scale]")
            return
        rest_p = text.strip().split(" ", 1)[1].strip().split()
        p_nm = _clean_ident(rest_p[0], 32, "")
        sc_p = 1.0
        if len(rest_p) > 1:
            try:
                val_s = float(rest_p[1])
                if math.isfinite(val_s):
                    sc_p = max(0.2, min(10.0, val_s))
            except ValueError:
                pass
        px, py, pz, yaw_p = client._front_placement_pos(dist=5.0, snap=False)
        client.send_json({
            "type": "prefab_action",
            "action": "stamp",
            "name": p_nm,
            "world": client.world,
            "x": px,
            "y": py,
            "z": pz,
            "scale": sc_p,
            "yaw": yaw_p,
        })
    elif cmd == "/snap":
        if len(parts) < 2 or not parts[1].strip():
            client.grid_snap = 0.0 if client.grid_snap > 0.0 else 0.5
        else:
            raw_s = parts[1].strip().lower()
            if raw_s in ("off", "0", "none"):
                client.grid_snap = 0.0
            else:
                try:
                    sv = float(raw_s)
                    client.grid_snap = max(0.0, min(10.0, sv if math.isfinite(sv) else 0.5))
                except ValueError:
                    client.log("ERR", "#ff4444", "Usage: /snap [step_meters|off] (e.g. /snap 0.5)")
                    return
        client.log("BUILD", "#00ff66", f"Grid snap: {client.grid_snap}m" if client.grid_snap > 0.0 else "Grid snap: OFF")
    elif cmd == "/watch":
        if len(parts) < 4:
            client.log("ERR", "#ff4444", "Usage: /watch <world|avatar> <name> <path/to/file.md>")
            return
        kind = parts[1].lower()
        if kind not in ("world", "avatar"):
            client.log("ERR", "#ff4444", "Watch kind must be 'world' or 'avatar'.")
            return
        w_name = _clean_ident(parts[2], 32, "")
        fpath = parts[3].strip().strip('"').strip("'")
        p = Path(fpath).expanduser().resolve()
        if not w_name or not p.is_file():
            client.log("ERR", "#ff4444", f"Valid name and existing file required: {p}")
            return
        raw_md = p.read_text(encoding="utf-8", errors="replace")
        ok, err, _ = validate_mv3d_document(raw_md, kind=kind)
        if not ok:
            client.log("ERR", "#ff4444", err)
            return
        client.watch_spec = {
            "kind": kind,
            "name": w_name,
            "path": p,
            "mtime": p.stat().st_mtime,
            "sha256": compute_md_sha256(raw_md),
            "last_poll": time.time(),
        }
        if kind == "avatar":
            store_cached_avatar_md(w_name, raw_md)
        client.send_json({"type": "upload", "kind": kind, "name": w_name, "content": raw_md})
        client.log("WATCH", "#00ff66", f"Watching {p.name} -> {kind} '{w_name}' (use /unwatch to stop).")
    elif cmd == "/unwatch":
        if client.watch_spec:
            fn_w = client.watch_spec["path"].name
            client.watch_spec = None
            client.log("WATCH", "#ffaa00", f"Stopped watching {fn_w}.")
        else:
            client.log("WATCH", "#ffaa00", "No active file watch.")
    elif cmd in ("/ghost", "/preview"):
        raw_g = text.strip().split()[1:]
        if not raw_g:
            client.ghost_active = not client.ghost_active
            if client.ghost_active:
                client.log("GHOST", "#00ff66", f"3D Ghost Preview ON: {client.ghost_shape} ({client.ghost_color}, {client.ghost_scale:.2f}x). [LMB/Enter] Place | [Scroll] Dist | [[/]] Scale | [R] Rot | [B] Cycle | [Esc/X] Cancel")
            else:
                client.log("GHOST", "#ffaa00", "3D Ghost Preview OFF.")
            return
        tok0 = raw_g[0].strip().lower()
        if tok0 in ("off", "stop", "cancel", "none", "0"):
            client.ghost_active = False
            client.log("GHOST", "#ffaa00", "3D Ghost Preview OFF.")
            return
        if tok0 not in ("on", "start", "1"):
            if tok0 not in PLACED_SHAPES:
                client.log("ERR", "#ff4444", f"Unknown shape '{tok0}'. Valid: {', '.join(PLACED_SHAPES)}")
                return
            client.ghost_shape = tok0
        if len(raw_g) >= 2 and raw_g[1].strip():
            client.ghost_color = raw_g[1].strip()[:24]
        if len(raw_g) >= 3 and raw_g[2].strip():
            try:
                sc_v = float(raw_g[2].strip())
                if math.isfinite(sc_v):
                    client.ghost_scale = max(0.2, min(20.0, round(sc_v, 2)))
            except ValueError:
                client.log("ERR", "#ff4444", "Ghost scale must be a number (0.2 to 20.0).")
                return
        client.ghost_active = True
        client.log("GHOST", "#00ff66", f"3D Ghost Preview ON: {client.ghost_shape} ({client.ghost_color}, {client.ghost_scale:.2f}x). [LMB] Place | [Scroll] Dist | [[/]] Scale | [R] Rot | [B] Shape")
    elif cmd == "/place":
        shape = parts[1].lower() if len(parts) > 1 and parts[1].strip() else (client.ghost_shape if client.ghost_active else "box")
        if shape not in PLACED_SHAPES:
            client.log("ERR", "#ff4444", f"Invalid shape '{shape}'. Valid: {', '.join(PLACED_SHAPES)}")
            return
        color = parts[2].strip() if len(parts) > 2 and parts[2].strip() else (client.ghost_color if client.ghost_active else "cyan")
        try:
            scale = float(parts[3]) if len(parts) > 3 and parts[3].strip() else (client.ghost_scale if client.ghost_active else 1.0)
            scale = max(0.2, min(20.0, scale if math.isfinite(scale) else 1.0))
        except ValueError:
            scale = 1.0
        if client.ghost_active:
            client.ghost_shape, client.ghost_color, client.ghost_scale = shape, color, round(scale, 2)
            px, py, pz, yaw_p = client._compute_ghost_placement()
        else:
            px, py, pz, yaw_p = client._front_placement_pos(dist=4.5, snap=True)
        client.send_json({
            "type": "world_edit", "action": "place", "world": client.world,
            "shape": shape, "x": px, "y": py, "z": pz,
            "scale": scale, "color": color, "yaw": yaw_p,
        })
    elif cmd in ("/placeportal", "/portal"):
        raw_tokens = text.strip().split()[1:]
        if not raw_tokens:
            client.log("ERR", "#ff4444", "Usage: /placeportal <world | server/world> [color] [radius] [@x,y,z] [yaw=deg]")
            return
        target_spec = raw_tokens[0].strip()
        target_srv = ""
        target_w = target_spec
        if target_spec.startswith(("ws://", "wss://")):
            try:
                u_sp = urllib.parse.urlsplit(target_spec)
                p_path = u_sp.path.strip("/")
                if u_sp.hostname and p_path:
                    port_p = u_sp.port or (443 if u_sp.scheme == "wss" else 8765)
                    target_srv = f"{u_sp.scheme}://{u_sp.hostname}:{port_p}"
                    target_w = p_path.split("/", 1)[0]
                else:
                    client.log("ERR", "#ff4444", "Cross-server portal URL must include /<world> (e.g. ws://host:8765/hub).")
                    return
            except Exception:
                client.log("ERR", "#ff4444", "Malformed portal server/world target.")
                return
        elif "/" in target_spec:
            srv_part, w_part = target_spec.split("/", 1)
            target_srv = srv_part.strip()
            target_w = w_part.strip()

        clean_dest = _clean_ident(target_w, 32, "")
        if not clean_dest:
            client.log("ERR", "#ff4444", "Specify a valid destination world name.")
            return

        color = "cyan"
        p_rad = 2.0
        t_spawn = None
        t_yaw = None
        for tok in raw_tokens[1:]:
            if tok.startswith("@"):
                coords = [x.strip() for x in tok[1:].split(",")]
                if len(coords) >= 3 and all(re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", c) for c in coords[:3]):
                    t_spawn = [
                        max(-500.0, min(500.0, float(coords[0]))),
                        max(-100.0, min(500.0, float(coords[1]))),
                        max(-500.0, min(500.0, float(coords[2]))),
                    ]
                else:
                    client.log("ERR", "#ff4444", "Spawn override must be @x,y,z (e.g. @0,2,12).")
                    return
            elif tok.lower().startswith("yaw=") and re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", tok[4:]):
                t_yaw = float(tok[4:]) % 360.0
            elif re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", tok):
                val_r = float(tok)
                if math.isfinite(val_r):
                    p_rad = max(0.5, min(15.0, val_r))
            else:
                color = tok

        px, py, pz, yaw_p = client._front_placement_pos(dist=4.5, snap=True)
        client.portal_cooldown_until = time.time() + 3.0
        pkt = {
            "type": "world_edit",
            "action": "place_portal",
            "world": client.world,
            "to": clean_dest,
            "x": px,
            "y": py,
            "z": pz,
            "radius": p_rad,
            "color": color,
            "yaw": yaw_p,
        }
        if target_srv:
            pkt["server"] = target_srv
        if t_spawn is not None:
            pkt["spawn"] = t_spawn
        if t_yaw is not None:
            pkt["target_yaw"] = t_yaw
        client.send_json(pkt)
    elif cmd == "/moveobj":
        raw_args = text.strip().split()[1:]
        tid = _target_placed_id(client, raw_args[0] if raw_args else "")
        nums = [float(x) for x in raw_args if re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", x)]
        if not tid or len(nums) < 3:
            client.log("ERR", "#ff4444", "Aim at a placed object with [I] and run /moveobj <dx> <dy> <dz>")
            return
        client.send_json({"type": "world_edit", "action": "mutate_placed", "world": client.world, "op": "move", "tag_id": tid, "dx": nums[0], "dy": nums[1], "dz": nums[2]})
    elif cmd == "/scaleobj":
        raw_args = text.strip().split()[1:]
        tid = _target_placed_id(client, raw_args[0] if raw_args else "")
        nums = [float(x) for x in raw_args if re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", x)]
        if not tid or not nums:
            client.log("ERR", "#ff4444", "Aim at a placed object with [I] and run /scaleobj <multiplier> (e.g. 1.5 or 0.75)")
            return
        client.send_json({"type": "world_edit", "action": "mutate_placed", "world": client.world, "op": "scale", "tag_id": tid, "scale_mult": nums[0]})
    elif cmd == "/rotateobj":
        raw_args = text.strip().split()[1:]
        tid = _target_placed_id(client, raw_args[0] if raw_args else "")
        nums = [float(x) for x in raw_args if re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", x)]
        if not tid or not nums:
            client.log("ERR", "#ff4444", "Aim at a placed object with [I] and run /rotateobj <degrees>")
            return
        client.send_json({"type": "world_edit", "action": "mutate_placed", "world": client.world, "op": "rotate", "tag_id": tid, "yaw_delta": nums[0]})
    elif cmd == "/paint":
        raw_args = text.strip().split()[1:]
        tid = _target_placed_id(client, raw_args[0] if raw_args else "")
        col = raw_args[-1] if raw_args else ""
        if not tid or not col:
            client.log("ERR", "#ff4444", "Aim at a placed object with [I] and run /paint <color|#hex>")
            return
        client.send_json({"type": "world_edit", "action": "mutate_placed", "world": client.world, "op": "paint", "tag_id": tid, "color": col})
    elif cmd == "/cloneobj":
        raw_args = text.strip().split()[1:]
        tid = _target_placed_id(client, raw_args[0] if raw_args else "")
        nums = [float(x) for x in raw_args if re.fullmatch(r"[-+]?(?:\d*\.\d+|\d+)", x)]
        if not tid:
            client.log("ERR", "#ff4444", "Aim at a placed object with [I] and run /cloneobj [dx dy dz]")
            return
        client.send_json({
            "type": "world_edit", "action": "mutate_placed", "world": client.world, "op": "clone", "tag_id": tid,
            "dx": nums[0] if len(nums) >= 1 else 1.5, "dy": nums[1] if len(nums) >= 2 else 0.0, "dz": nums[2] if len(nums) >= 3 else 0.0,
        })
    elif cmd == "/delobj":
        tid = _target_placed_id(client, parts[1] if len(parts) > 1 else "")
        if tid:
            client.send_json({"type": "world_edit", "action": "delete_placed", "world": client.world, "tag_id": tid})
        else:
            client.log("ERR", "#ff4444", "Aim at a placed object with [I] or pass /delobj <id>.")
    elif cmd == "/undo":
        client.send_json({"type": "world_edit", "action": "undo", "world": client.world})
    elif cmd == "/backup":
        client.send_json({"type": "world_backup", "action": "create", "world": client.world, "label": parts[1].strip() if len(parts) > 1 else ""})
    elif cmd == "/backups":
        client.send_json({"type": "world_backup", "action": "list", "world": client.world})
    elif cmd == "/restore" and len(parts) > 1 and parts[1].strip():
        client.send_json({"type": "world_backup", "action": "restore", "world": client.world, "file": parts[1].strip()})
    elif cmd == "/export":
        client.send_json({"type": "world_backup", "action": "export", "world": client.world})
    elif cmd == "/emote" and len(parts) > 1 and parts[1].strip():
        em = _clean_ident(parts[1].strip(), 16, "wave").lower()
        client.local_emote = em
        client.local_emote_until = time.time() + 2.5
        client.send_json({"type": "chat", "text": f"/emote {em}"})
    elif cmd == "/mood":
        if len(parts) < 2 or not parts[1].strip():
            client.log("MOOD", "#00ffcc", f"Current avatar expression: {client.mood} (usage: /mood <neutral|happy|smirk|angry|surprised|reset>)")
            return
        raw_m = parts[1].strip().lower()
        if raw_m in ("reset", "auto", "clear"):
            client.mood_pinned = False
            client.mood = "neutral"
        else:
            client.mood = normalize_expression_name(raw_m)
            client.mood_pinned = True
        client.send_json({"type": "mood_sync", "mood": client.mood})
        client.log("MOOD", "#00ff66", f"Avatar expression set to '{client.mood}'.")
    elif cmd == "/friends":
        client.send_json({"type": "friend_action", "action": "list"})
    elif cmd in ("/addfriend", "/accept", "/goto") and len(parts) > 1 and parts[1].strip():
        act_m = {"/addfriend": "add", "/accept": "accept", "/goto": "goto"}[cmd]
        client.send_json({"type": "friend_action", "action": act_m, "target": parts[1].strip()})
    elif cmd == "/msg" and len(parts) >= 3:
        client.send_json({"type": "friend_action", "action": "whisper", "target": parts[1].strip(), "text": parts[2].strip()})
    elif cmd == "/key":
        client.log("PUBKEY", "#00ff66", f"{client.pubkey_hex} (ID: {client.short_id}, Role: {client.role})")
    elif cmd == "/perms":
        client.send_json({"type": "admin_perm", "action": "list_perms"})
    elif cmd == "/setrole":
        raw_sr = text.strip().split()[1:]
        if len(raw_sr) < 2 or raw_sr[1].strip().lower() not in ("admin", "builder", "guest"):
            client.log("ERR", "#ff4444", "Usage: /setrole <nick|id|pubkey> <admin|builder|guest>")
            return
        client.send_json({"type": "admin_perm", "action": "set_role", "target_key": raw_sr[0].strip(), "role": raw_sr[1].strip().lower()})
    elif cmd in ("/admin", "/unadmin", "/builder", "/unbuilder", "/grant", "/ungrant", "/ban", "/unban", "/defaultrole"):
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", f"Usage: {cmd} <nick|id|pubkey>" if cmd != "/defaultrole" else "Usage: /defaultrole <guest|builder>")
            return
        act_map = {
            "/admin": "grant_admin",
            "/unadmin": "revoke_admin",
            "/builder": "grant_builder",
            "/unbuilder": "revoke_builder",
            "/grant": "grant_builder",
            "/ungrant": "revoke_builder",
            "/ban": "ban",
            "/unban": "unban",
            "/defaultrole": "set_default",
        }
        val = parts[1].strip().lower() if cmd == "/defaultrole" else parts[1].strip()
        client.send_json({"type": "admin_perm", "action": act_map[cmd], "target_key": val})
    elif cmd in ("/maxworlds", "/userworldlimit"):
        if len(parts) < 2 or not parts[1].strip().isdigit():
            client.log("ERR", "#ff4444", f"Usage: {cmd} <count> (0 = unlimited)")
            return
        act = "set_max_worlds" if cmd == "/maxworlds" else "set_user_world_limit"
        client.send_json({"type": "admin_perm", "action": act, "limit": int(parts[1].strip())})
    elif cmd == "/acl":
        target_w = parts[1].strip() if len(parts) > 1 and parts[1].strip() else client.world
        client.send_json({"type": "world_acl", "action": "list", "world": target_w})
    elif cmd == "/privateworld":
        if len(parts) < 2 or parts[1].strip().lower() not in ("on", "off", "true", "false", "1", "0"):
            client.log("ERR", "#ff4444", "Usage: /privateworld <on|off>")
            return
        client.send_json({"type": "world_acl", "action": "set_private", "world": client.world, "private": parts[1].strip().lower() in ("on", "true", "1")})
    elif cmd in ("/allowbuilder", "/removebuilder", "/allowvisitor", "/revokevisitor"):
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", f"Usage: {cmd} <nick|id|pubkey>")
            return
        acl_map = {
            "/allowbuilder": "add_builder",
            "/removebuilder": "remove_builder",
            "/allowvisitor": "allow_visitor",
            "/revokevisitor": "revoke_visitor",
        }
        client.send_json({"type": "world_acl", "action": acl_map[cmd], "world": client.world, "target": parts[1].strip()})
    elif cmd == "/delworld":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ERR", "#ff4444", "Usage: /delworld <world_name>")
            return
        client.send_json({"type": "delete_world", "world": parts[1].strip()})
    elif cmd == "/nick":
        if len(parts) < 2 or not parts[1].strip():
            client.log("ID", "#00ffcc", f"Current nickname: {client.nick} (usage: /nick <new_name>)")
            return
        clean_n = _clean_ident(parts[1].strip(), 24)
        if not clean_n:
            client.log("ERR", "#ff4444", "Nickname must contain letters, numbers, '_' or '-'.")
            return
        if clean_n.lower() in RESERVED_ROLE_LABELS:
            client.log("ERR", "#ff4444", f"'{clean_n}' is a reserved rank/system name.")
            return
        client.nick = clean_n
        client._save_profile()
        if client.connected and client.short_id:
            client.send_json({"type": "set_nick", "nick": clean_n})
        else:
            client.log("ID", "#00ff66", f"Saved nickname as '{client.nick}' (will sync on connect).")
    elif cmd == "/avatars":
        client.send_json({"type": "list_avatars"})
    elif cmd == "/avatar":
        if len(parts) < 2:
            client.log("AVATAR", "#00ffcc", f"Active avatar: {client.avatar}")
        else:
            target_av = _clean_ident(parts[1], 32, "default")
            client.avatar = target_av
            client._save_profile()
            if client.connected and client.short_id:
                client.send_json({"type": "set_avatar", "name": target_av})
            else:
                client.log("AVATAR", "#00ff66", f"Saved avatar '{target_av}' (will sync on connect).")
    elif cmd == "/fly":
        client.fly_mode = not client.fly_mode
        client.log("MOVE", "#00ff66", f"Fly mode {'ON' if client.fly_mode else 'OFF'}")
    elif cmd == "/cam":
        order = ("first", "third", "front")
        client.camera_mode = order[(order.index(client.camera_mode) + 1) % 3]
    elif cmd == "/upload":
        if len(parts) < 3 or parts[1].lower() not in ("world", "avatar"):
            client.log("ERR", "#ff4444", "Usage: /upload <world|avatar> <name> <file.md> (or /upload <world|avatar> <file.md>)")
            return
        kind = parts[1].lower()
        if len(parts) >= 4:
            name = _clean_ident(parts[2], 32, "")
            fpath = parts[3].strip().strip('"').strip("'")
        else:
            fpath = parts[2].strip().strip('"').strip("'")
            name = _clean_ident(Path(fpath).stem, 32, "")
        p = Path(fpath).expanduser()
        if not name or not p.is_file():
            client.log("ERR", "#ff4444", f"Valid name and existing .md file required (not found: {p})")
            return
        md_content = p.read_text(encoding="utf-8", errors="replace")
        ok, err, _ = validate_mv3d_document(md_content, kind=kind)
        if not ok:
            client.log("ERR", "#ff4444", err)
            return
        if kind == "avatar":
            store_cached_avatar_md(name, md_content)
        client.send_json({"type": "upload", "kind": kind, "name": name, "content": md_content})
    elif cmd == "/quit":
        client.running = False
    else:
        client.send_json({"type": "chat", "text": text})


def _get_clipboard_text() -> str:
    try:
        if not pygame.scrap.get_init():
            pygame.scrap.init()
        for mime in ("text/plain;charset=utf-8", "text/plain", pygame.SCRAP_TEXT):
            try:
                raw = pygame.scrap.get(mime)
            except Exception:
                raw = None
            if raw:
                if isinstance(raw, bytes):
                    txt = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
                else:
                    txt = str(raw).split("\x00", 1)[0]
                if txt:
                    return txt
    except Exception:
        pass
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        txt = root.clipboard_get()
        root.destroy()
        return str(txt or "")
    except Exception:
        return ""


def _set_clipboard_text(text: str) -> bool:
    clean_t = str(text or "")
    if not clean_t:
        return False
    try:
        if not pygame.scrap.get_init():
            pygame.scrap.init()
        pygame.scrap.put(pygame.SCRAP_TEXT, clean_t.encode("utf-8"))
        return True
    except Exception:
        pass
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(clean_t)
        root.update()
        root.destroy()
        return True
    except Exception:
        return False


def _sanitize_paste_text(raw_text: str, max_len: int = 300) -> str:
    cleaned = re.sub(r"[\r\n\x00-\x1f\x7f]", " ", str(raw_text or ""))
    cleaned = " ".join(cleaned.split())
    return cleaned[:max_len]


def _wrap_hud_line(font, text: str, max_px: int) -> list[str]:
    if font.size(text)[0] <= max_px:
        return [text]
    lines, current = [], ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip() if current else word
        if font.size(candidate)[0] <= max_px:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = ""
            if font.size(word)[0] > max_px:
                chunk = ""
                for ch in word:
                    if font.size(chunk + ch)[0] <= max_px:
                        chunk += ch
                    else:
                        lines.append(chunk)
                        chunk = ch
                current = chunk
            else:
                current = word
    if current:
        lines.append(current)
    return lines or [""]


def render_peer_modern(renderer: ModernRenderer, peer: dict, vp_mat: np.ndarray, avatar_meshes: dict, now: float = 0.0):
    px, py, pz = peer.get("pos", [0.0, 0.0, 0.0])
    rot = peer.get("rot", [0.0, 1.0, 0.0, 0.0])
    p_yaw = math.degrees(rot[3]) if len(rot) >= 4 else 0.0

    extra_y = spin = tilt = 0.0
    emote = peer.get("emote")
    emote_until = float(peer.get("emote_until", 0.0) or 0.0)
    if emote and now < emote_until:
        t = (emote_until - now) * 6.0
        if emote == "jump":
            extra_y = abs(math.sin(t)) * 1.4
        elif emote in ("spin", "dance"):
            spin = (t * 180.0) % 360.0
        elif emote == "wave":
            tilt = math.sin(t * 2.0) * 22.0

    av_name = peer.get("avatar", "default")
    peer_mood = normalize_expression_name(str(peer.get("mood", "neutral")))
    if emote and now < emote_until and not peer.get("mood_pinned"):
        if emote in ("wave", "dance"):
            peer_mood = "happy"
        elif emote == "jump":
            peer_mood = "surprised"
    mesh = avatar_meshes.get(av_name) if av_name and av_name != "default" else None
    if mesh and mesh.get("objects"):
        sc = mesh["scale"]
        ox, oy, oz = mesh["offset"]
        root_mat = (
            mat4_translate(px, py + extra_y, pz)
            @ mat4_rotate_axis_angle(0.0, 1.0, 0.0, p_yaw + spin)
            @ mat4_rotate_axis_angle(0.0, 0.0, 1.0, tilt)
            @ mat4_scale(sc, sc, sc)
            @ mat4_translate(ox, oy, oz)
        )
        for obj in mesh["objects"]:
            obj_exprs = obj.get("expressions") or []
            if obj_exprs and peer_mood not in obj_exprs:
                continue
            renderer.draw_object(obj, vp_mat, parent_mat=root_mat)
        return

    hx = peer.get("color", "#00ffcc").lstrip("#")
    rgb = (int(hx[0:2], 16) / 255.0, int(hx[2:4], 16) / 255.0, int(hx[4:6], 16) / 255.0) if len(hx) == 6 else (0.0, 1.0, 0.8)
    body_obj = {"kind": "cone", "pos": (px, py + 0.7 + extra_y, pz), "rot": (0, 1, 0, p_yaw + spin), "scale": (1, 1, 1), "radius": 0.45, "height": 1.4, "diff": rgb, "emis": (rgb[0] * 0.4, rgb[1] * 0.4, rgb[2] * 0.4), "alpha": 0.9}
    head_obj = {"kind": "sphere", "pos": (px, py + 1.65 + extra_y, pz), "rot": (0, 1, 0, p_yaw + spin), "scale": (1, 1, 1), "radius": 0.28, "diff": (0.95, 0.95, 1.0), "emis": (0.2, 0.2, 0.25), "alpha": 1.0}
    renderer.draw_object(body_obj, vp_mat)
    renderer.draw_object(head_obj, vp_mat)


def _load_portable_font(size: int = 15):
    for family in ("Consolas", "Menlo", "DejaVu Sans Mono", "Liberation Mono", "Courier New", "monospace"):
        try:
            matched = pygame.font.match_font(family, bold=True)
            if matched:
                return pygame.font.Font(matched, size)
        except Exception:
            pass
    return pygame.font.Font(None, size + 3)


def _init_portable_gl_window(width: int, height: int):
    flags = DOUBLEBUF | OPENGL | RESIZABLE
    try:
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        return pygame.display.set_mode((width, height), flags)
    except pygame.error:
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 0)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, 0)
        return pygame.display.set_mode((width, height), flags)


def main():
    global WINDOW_W, WINDOW_H
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "Makerverse #MV3D Portable Client\n"
            "Usage: python client.py [ws://host:port] [--server URL] [--nick NAME] [--portable] [--data-dir PATH]"
        )
        return

    custom_data_dir = None
    force_portable = False
    args_iter = iter(sys.argv[1:])
    for tok in args_iter:
        if tok == "--portable":
            force_portable = True
        elif tok == "--data-dir":
            d_val = next(args_iter, "").strip()
            if d_val:
                custom_data_dir = Path(d_val).expanduser().resolve()
        elif tok.startswith("--data-dir="):
            d_val = tok.split("=", 1)[1].strip()
            if d_val:
                custom_data_dir = Path(d_val).expanduser().resolve()

    if custom_data_dir is not None:
        configure_client_storage(custom_data_dir)
    elif force_portable:
        configure_client_storage(Path(__file__).resolve().parent / "makerverse_client_data")
    else:
        configure_client_storage(_resolve_client_base_dir(sys.argv[1:]))

    client = MakerverseClient(load_or_create_key())
    args_iter = iter(sys.argv[1:])
    for tok in args_iter:
        if tok in ("--portable",):
            continue
        elif tok == "--data-dir":
            next(args_iter, None)
        elif tok.startswith("--data-dir="):
            continue
        elif tok == "--server":
            u = next(args_iter, "").strip()
            norm_start = normalize_ws_url(u)
            if norm_start:
                client.server_url = norm_start
        elif tok.startswith("--server="):
            norm_start = normalize_ws_url(tok.split("=", 1)[1].strip())
            if norm_start:
                client.server_url = norm_start
        elif tok == "--nick":
            n_arg = _clean_ident(next(args_iter, ""), 24)
            if n_arg:
                client.nick = n_arg
        elif tok.startswith("--nick="):
            n_arg = _clean_ident(tok.split("=", 1)[1], 24)
            if n_arg:
                client.nick = n_arg
        elif not tok.startswith("-"):
            norm_start = normalize_ws_url(tok.strip())
            if norm_start:
                client.server_url = norm_start
    client.start_background()

    pygame.init()
    disp_info = pygame.display.Info()
    if disp_info.current_w > 0 and disp_info.current_h > 0:
        WINDOW_W = min(WINDOW_W, max(800, disp_info.current_w - 80))
        WINDOW_H = min(WINDOW_H, max(540, disp_info.current_h - 100))
    pygame.display.set_caption("Makerverse // #MV3D v2 Portable Client")
    _init_portable_gl_window(WINDOW_W, WINDOW_H)
    font = _load_portable_font(15)

    renderer = ModernRenderer()
    glEnable(GL_DEPTH_TEST)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    cam_pos = [0.0, 4.05, 13.5]
    yaw = pitch = 0.0
    mouse_grabbed = True
    pygame.event.set_grab(True)
    pygame.mouse.set_visible(False)

    chat_mode = False
    chat_buffer = ""
    last_move_send = 0.0
    clock = pygame.time.Clock()
    ortho_mat = mat4_ortho(0, WINDOW_W, WINDOW_H, 0)
    proj_mat = mat4_perspective(65.0, WINDOW_W / float(WINDOW_H), 0.1, 250.0)

    try:
        while client.running:
            dt = min(clock.tick(60) / 1000.0, 0.05)
            now = time.time()

            if client.scene_dirty:
                renderer.clear_dynamic_vaos()
                client.scene_dirty = False

            if client.teleport_req is not None:
                cam_pos = list(client.teleport_req)
                yaw = -float(client.teleport_yaw or 0.0)
                pitch = 0.0
                client.vel_y = 0.0
                client.teleport_req = None

            client.cam_pos_ref = list(cam_pos)
            client.cam_yaw_ref = yaw

            for event in pygame.event.get():
                if event.type == QUIT:
                    client.running = False
                elif event.type == VIDEORESIZE:
                    WINDOW_W = max(640, int(event.w))
                    WINDOW_H = max(420, int(event.h))
                    glViewport(0, 0, WINDOW_W, WINDOW_H)
                    ortho_mat = mat4_ortho(0, WINDOW_W, WINDOW_H, 0)
                    proj_mat = mat4_perspective(65.0, WINDOW_W / float(WINDOW_H), 0.1, 250.0)
                elif event.type == KEYDOWN:
                    if chat_mode:
                        mods = pygame.key.get_mods()
                        has_shortcut_mod = bool(mods & (KMOD_CTRL | KMOD_META))
                        if event.key == K_ESCAPE:
                            chat_mode, chat_buffer = False, ""
                            if mouse_grabbed:
                                pygame.event.set_grab(True); pygame.mouse.set_visible(False)
                        elif event.key == K_RETURN:
                            if chat_buffer.strip():
                                handle_command(client, chat_buffer)
                            chat_buffer, chat_mode = "", False
                            if mouse_grabbed:
                                pygame.event.set_grab(True); pygame.mouse.set_visible(False)
                        elif event.key == K_BACKSPACE:
                            chat_buffer = chat_buffer[:-1]
                        elif has_shortcut_mod and event.key == K_v:
                            pasted = _sanitize_paste_text(_get_clipboard_text(), max_len=300)
                            if pasted:
                                remaining = max(0, 300 - len(chat_buffer))
                                if remaining > 0:
                                    chat_buffer += pasted[:remaining]
                        elif has_shortcut_mod and event.key == K_c:
                            latest_msg = client.chat_log[-1][2] if (client.chat_log and len(client.chat_log[-1]) >= 3) else ""
                            to_copy = chat_buffer or latest_msg
                            if _set_clipboard_text(to_copy):
                                client.log("CLIPBOARD", "#00ffcc", "Copied text to clipboard.")
                        elif has_shortcut_mod and event.key == K_x:
                            if chat_buffer and _set_clipboard_text(chat_buffer):
                                chat_buffer = ""
                                client.log("CLIPBOARD", "#00ffcc", "Cut text to clipboard.")
                        elif not has_shortcut_mod and event.unicode and event.unicode.isprintable():
                            if len(chat_buffer) < 300:
                                chat_buffer += event.unicode
                    else:
                        if event.key == K_ESCAPE:
                            if client.ghost_active:
                                client.ghost_active = False
                                client.log("GHOST", "#ffaa00", "3D Ghost Preview cancelled.")
                            else:
                                mouse_grabbed = not mouse_grabbed
                                pygame.event.set_grab(mouse_grabbed)
                                pygame.mouse.set_visible(not mouse_grabbed)
                        elif event.key == K_b:
                            if not client.ghost_active:
                                client.ghost_active = True
                                client.log("GHOST", "#00ff66", f"3D Ghost Preview ON: {client.ghost_shape} ({client.ghost_color}, {client.ghost_scale:.2f}x). [LMB] Place | [Scroll] Dist | [[/]] Scale | [R] Rot | [B] Next | [Esc] Off")
                            else:
                                nxt_idx = (PLACED_SHAPES.index(client.ghost_shape) + 1) % len(PLACED_SHAPES) if client.ghost_shape in PLACED_SHAPES else 0
                                client.ghost_shape = PLACED_SHAPES[nxt_idx]
                        elif event.key == K_x and client.ghost_active:
                            client.ghost_active = False
                            client.log("GHOST", "#ffaa00", "3D Ghost Preview OFF.")
                        elif event.key == K_LEFTBRACKET and client.ghost_active:
                            client.ghost_scale = max(0.2, round(client.ghost_scale - 0.25, 2))
                        elif event.key == K_RIGHTBRACKET and client.ghost_active:
                            client.ghost_scale = min(20.0, round(client.ghost_scale + 0.25, 2))
                        elif event.key == K_MINUS and client.ghost_active:
                            client.ghost_dist = max(2.0, round(client.ghost_dist - 0.5, 2))
                        elif event.key == K_EQUALS and client.ghost_active:
                            client.ghost_dist = min(25.0, round(client.ghost_dist + 0.5, 2))
                        elif event.key == K_i:
                            client.inspect_mode = not client.inspect_mode
                        elif event.key == K_v:
                            order = ("first", "third", "front")
                            client.camera_mode = order[(order.index(client.camera_mode) + 1) % 3]
                        elif event.key == K_h:
                            client.hud_hidden = not client.hud_hidden
                        elif event.key == K_f:
                            client.trigger_interact(now)
                        elif event.key == K_g:
                            client.grid_snap = 0.0 if client.grid_snap > 0.0 else 0.5
                            client.log("BUILD", "#00ff66", f"Grid snap: {client.grid_snap}m" if client.grid_snap > 0.0 else "Grid snap: OFF")
                        elif event.key == K_r:
                            if client.ghost_active:
                                client.ghost_yaw_offset = round((client.ghost_yaw_offset + 45.0) % 360.0, 1)
                            elif client.inspect_mode:
                                tid_r = _target_placed_id(client)
                                if tid_r:
                                    client.send_json({"type": "world_edit", "action": "mutate_placed", "world": client.world, "op": "rotate", "tag_id": tid_r, "yaw_delta": 45.0})
                        elif event.key in (K_1, K_2, K_3, K_4):
                            em_n = {K_1: "wave", K_2: "dance", K_3: "spin", K_4: "jump"}[event.key]
                            client.local_emote = em_n
                            client.local_emote_until = now + 2.5
                            client.send_json({"type": "chat", "text": f"/emote {em_n}"})
                        elif event.key in (K_t, K_RETURN, K_SLASH):
                            chat_mode = True
                            chat_buffer = "/" if event.key == K_SLASH else ""
                            pygame.event.set_grab(False); pygame.mouse.set_visible(True)
                elif event.type == MOUSEBUTTONDOWN and not chat_mode and mouse_grabbed:
                    if event.button == 1 and client.ghost_active:
                        client.commit_ghost_placement()
                elif event.type == MOUSEWHEEL and not chat_mode:
                    if client.ghost_active:
                        client.ghost_dist = max(2.0, min(25.0, round(client.ghost_dist + event.y * 0.5, 2)))
                    elif client.camera_mode != "first":
                        client.cam_dist = max(2.0, min(14.0, round(client.cam_dist - event.y * 0.5, 2)))
                elif event.type == MOUSEMOTION and mouse_grabbed and not chat_mode:
                    dx, dy = event.rel
                    yaw -= dx * 0.18
                    pitch = max(-85.0, min(85.0, pitch - dy * 0.18))
            if not chat_mode:
                keys = pygame.key.get_pressed()
                speed = (12.0 if client.fly_mode else 7.5) * dt
                rad = math.radians(yaw)
                fx, fz = -math.sin(rad), -math.cos(rad)
                rx, rz = math.cos(rad), -math.sin(rad)
                move_dx = move_dz = 0.0
                if keys[K_w] or keys[K_UP]:
                    move_dx += fx * speed; move_dz += fz * speed
                if keys[K_s] or keys[K_DOWN]:
                    move_dx -= fx * speed; move_dz -= fz * speed
                if keys[K_a] or keys[K_LEFT]:
                    move_dx -= rx * speed; move_dz -= rz * speed
                if keys[K_d] or keys[K_RIGHT]:
                    move_dx += rx * speed; move_dz += rz * speed

                if client.fly_mode:
                    cam_pos[0] += move_dx; cam_pos[2] += move_dz
                    if keys[K_SPACE]: cam_pos[1] += speed
                    if keys[K_LSHIFT]: cam_pos[1] -= speed
                else:
                    client.vel_y = step_player_physics(cam_pos, move_dx, move_dz, client.vel_y, bool(keys[K_SPACE]), dt, client.spatial_hash)

                client.check_portals_and_triggers(cam_pos, now)
            client.poll_file_watch(now)

            if client.connected and client.short_id and now - last_move_send > 0.1:
                last_move_send = now
                client.send_json({"type": "move", "pos": [round(cam_pos[0], 3), round(cam_pos[1] - 1.8, 3), round(cam_pos[2], 3)], "rot": [0.0, 1.0, 0.0, round(math.radians(yaw), 3)]})

            view_yaw = yaw + (180.0 if client.camera_mode == "front" else 0.0)
            view_pitch = -pitch if client.camera_mode == "front" else pitch
            if client.camera_mode == "first":
                view_pos = list(cam_pos)
            else:
                yr, pr = math.radians(view_yaw), math.radians(view_pitch)
                view_pos = [
                    cam_pos[0] + math.sin(yr) * math.cos(pr) * client.cam_dist,
                    max(0.4, cam_pos[1] - math.sin(pr) * client.cam_dist),
                    cam_pos[2] + math.cos(yr) * math.cos(pr) * client.cam_dist,
                ]

            client.looked_at_hit = find_looked_at_object(view_pos, view_yaw, view_pitch, client.spatial_hash, max_dist=14.0 if not client.inspect_mode else 60.0)

            view_mat = (
                mat4_rotate_axis_angle(1, 0, 0, -view_pitch)
                @ mat4_rotate_axis_angle(0, 1, 0, -view_yaw)
                @ mat4_translate(-view_pos[0], -view_pos[1], -view_pos[2])
            )
            vp_mat = proj_mat @ view_mat

            bg = client.scene.get("bg", (0.04, 0.01, 0.12))
            fog = client.scene.get("fog", bg)
            glClearColor(bg[0], bg[1], bg[2], 1.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

            glUseProgram(renderer.prog3d)
            glUniform3f(glGetUniformLocation(renderer.prog3d, "uLightDir"), 0.4, 1.0, 0.5)
            glUniform3f(glGetUniformLocation(renderer.prog3d, "uCamPos"), *view_pos)
            glUniform3f(glGetUniformLocation(renderer.prog3d, "uFogColor"), *fog)
            glUniform1f(glGetUniformLocation(renderer.prog3d, "uFogDensity"), 1.5 / max(30.0, client.scene.get("fog_range", 160.0)))

            if client.scene.get("has_floor"):
                grid_obj = {
                    "kind": "box",
                    "pos": (0.0, 0.005, 0.0),
                    "rot": (0.0, 1.0, 0.0, 0.0),
                    "scale": (1.0, 1.0, 1.0),
                    "size": (120.0, 0.01, 120.0),
                    "diff": (0.08, 0.16, 0.22),
                    "emis": (0.01, 0.04, 0.06),
                    "alpha": 0.25,
                }
                renderer.draw_object(grid_obj, vp_mat)

            for obj in client.scene.get("objects", []):
                renderer.draw_object(obj, vp_mat)

            if client.looked_at_hit is not None and client.inspect_mode:
                renderer.draw_wire_aabb(client.looked_at_hit[0]["_aabb"], vp_mat)

            render_list = list(client.peers.values())
            if client.camera_mode != "first":
                if client.avatar and client.avatar != "default":
                    client._ensure_avatar_loaded(client.avatar)
                render_list.append({
                    "nick": client.nick,
                    "color": client.color,
                    "avatar": client.avatar,
                    "mood": client.mood,
                    "mood_pinned": client.mood_pinned,
                    "pos": [cam_pos[0], cam_pos[1] - 1.8, cam_pos[2]],
                    "rot": [0, 1, 0, math.radians(yaw)],
                    "emote": client.local_emote,
                    "emote_until": client.local_emote_until,
                })

            for peer in render_list:
                render_peer_modern(renderer, peer, vp_mat, client.avatar_meshes, now)

            if client.ghost_active:
                gx, gy, gz, gyaw = client._compute_ghost_placement()
                pulse_a = 0.34 + 0.12 * math.sin(now * 5.0)
                ghost_objs = build_ghost_preview_objects(
                    client.ghost_shape,
                    gx,
                    gy,
                    gz,
                    client.ghost_scale,
                    parse_color(client.ghost_color),
                    yaw_deg=gyaw,
                    alpha=pulse_a,
                )
                glDepthMask(GL_FALSE)
                for g_obj in ghost_objs:
                    renderer.draw_object(g_obj, vp_mat)
                glDepthMask(GL_TRUE)
                g_box = client._ghost_aabb(ghost_objs)
                if g_box is not None:
                    renderer.draw_wire_aabb(g_box, vp_mat)

            if not client.hud_hidden:
                glDisable(GL_DEPTH_TEST)
                glUseProgram(renderer.prog2d)
                glUniformMatrix4fv(glGetUniformLocation(renderer.prog2d, "uOrtho"), 1, GL_TRUE, ortho_mat)

                renderer.draw_rect_2d(0, 0, WINDOW_W, 38, (0.04, 0.03, 0.10, 0.85))
                flags = f"{' [GHOST]' if client.ghost_active else ''}{' [FLY]' if client.fly_mode else ''}{' [INSPECT]' if client.inspect_mode else ''}{f' [SNAP:{client.grid_snap}m]' if client.grid_snap > 0.0 else ''}{' [WATCH]' if client.watch_spec else ''}{' [MUTE]' if client.radio.muted else ''}"
                renderer.draw_text_2d(font, 14, 10, f"MAKERVERSE // mv3d://{client.node_name}/{client.world}.md  |  {client.nick} [{client.role}]{flags}  |  {int(clock.get_fps())} FPS", (0, 255, 204))

                # 3D Sign Billboards
                for obj in client.scene.get("objects", []):
                    stxt = obj.get("sign_text")
                    if stxt:
                        sx, sy, sz = obj["pos"]
                        pt2 = project_3d_to_2d((sx, sy + 0.55, sz), vp_mat)
                        if pt2:
                            tw = font.size(stxt)[0] + 12
                            renderer.draw_rect_2d(pt2[0] - tw * 0.5, pt2[1] - 12, tw, 22, (0.02, 0.04, 0.10, 0.85))
                            renderer.draw_text_2d(font, pt2[0] - tw * 0.5 + 6, pt2[1] - 10, stxt, (0, 255, 215))

                # Interactive Trigger Prompt
                if client.nearby_interact_prompt:
                    pw = font.size(client.nearby_interact_prompt)[0] + 18
                    renderer.draw_rect_2d(WINDOW_W * 0.5 - pw * 0.5, WINDOW_H * 0.5 + 24, pw, 26, (0.06, 0.04, 0.14, 0.90))
                    renderer.draw_text_2d(font, WINDOW_W * 0.5 - pw * 0.5 + 9, WINDOW_H * 0.5 + 29, client.nearby_interact_prompt, (255, 210, 90))

                # Waypoint HUD Markers
                for wp_name, wp_pos in client.waypoints.items():
                    dist_wp = math.sqrt((wp_pos[0] - cam_pos[0]) ** 2 + (wp_pos[2] - cam_pos[2]) ** 2)
                    wp_lbl = f"* {wp_name} ({dist_wp:.0f}m)"
                    pt2 = project_3d_to_2d((wp_pos[0], wp_pos[1] + 1.8, wp_pos[2]), vp_mat)
                    if pt2:
                        sx_w = max(12.0, min(WINDOW_W - 140.0, pt2[0] - 40.0))
                        sy_w = max(44.0, min(WINDOW_H - 230.0, pt2[1]))
                        renderer.draw_text_2d(font, sx_w, sy_w, wp_lbl, (255, 220, 80))

                # Portal Labels
                for portal in client.scene.get("portals", []):
                    px, py, pz = portal["pos"]
                    pt2 = project_3d_to_2d((px, py + 1.5, pz), vp_mat)
                    if pt2:
                        srv_prefix = f"{portal['server']}/" if portal.get("server") else ""
                        coord_tag = f" @{portal['spawn'][0]:.0f},{portal['spawn'][1]:.0f},{portal['spawn'][2]:.0f}" if portal.get("spawn") else ""
                        lbl = f"-> {srv_prefix}{portal['to']}{coord_tag}"
                        tw = font.size(lbl)[0] + 10
                        renderer.draw_rect_2d(pt2[0] - tw * 0.5, pt2[1] - 10, tw, 20, (0.08, 0.02, 0.14, 0.85))
                        renderer.draw_text_2d(font, pt2[0] - tw * 0.5 + 5, pt2[1] - 8, lbl, (255, 110, 235))

                # Peer Nametags
                for peer in client.peers.values():
                    px, py, pz = peer.get("pos", [0, 0, 0])
                    av_m = client.avatar_meshes.get(peer.get("avatar", ""))
                    head_y = py + (av_m["height"] + 0.35 if av_m else 2.2)
                    screen_pt = project_3d_to_2d((px, head_y, pz), vp_mat)
                    if screen_pt:
                        p_role = normalize_role_name(peer.get("role", ""))
                        p_badge = f"[{p_role}] " if p_role else ""
                        av_tag = f" ({peer['avatar']})" if peer.get("avatar") and peer.get("avatar") != "default" else ""
                        name_body = f"{peer.get('nick', 'Peer')}{av_tag}"
                        badge_w = font.size(p_badge)[0] if p_badge else 0
                        total_w = badge_w + font.size(name_body)[0] + 10
                        tx_n = screen_pt[0] - total_w * 0.5
                        ty_n = screen_pt[1] - 10
                        renderer.draw_rect_2d(tx_n, ty_n - 2, total_w, 20, (0.03, 0.02, 0.09, 0.78))
                        if p_badge:
                            bhx = get_rank_badge_color(p_role).lstrip("#")
                            brgb = (int(bhx[0:2], 16), int(bhx[2:4], 16), int(bhx[4:6], 16))
                            renderer.draw_text_2d(font, tx_n + 5, ty_n, p_badge, brgb)
                        renderer.draw_text_2d(font, tx_n + 5 + badge_w, ty_n, name_body, (255, 165, 235))

                box_w = min(640, max(320, WINDOW_W - 24))
                box_x, box_y, box_h = 12, WINDOW_H - 212, 200
                inner_max_w = box_w - 22
                renderer.draw_rect_2d(box_x, box_y, box_w, box_h, (0.03, 0.02, 0.08, 0.84))

                wrapped_entries = []
                for entry in client.chat_log[-10:]:
                    sender, hex_c, msg = entry[0], entry[1], entry[2]
                    rank_tag = entry[3] if len(entry) >= 4 else ""
                    hc = hex_c.lstrip("#")
                    rgb = (int(hc[0:2], 16), int(hc[2:4], 16), int(hc[4:6], 16)) if len(hc) == 6 else (0, 255, 204)
                    badge_txt = f"[{rank_tag}] " if rank_tag else ""
                    bhx = get_rank_badge_color(rank_tag).lstrip("#") if rank_tag else ""
                    badge_rgb = (int(bhx[0:2], 16), int(bhx[2:4], 16), int(bhx[4:6], 16)) if len(bhx) == 6 else rgb
                    full_line = f"{badge_txt}{sender}: {msg}" if sender != "*" else f"* {badge_txt}{msg}"
                    for l_idx, line_str in enumerate(_wrap_hud_line(font, full_line, inner_max_w)):
                        if l_idx == 0 and badge_txt:
                            if sender == "*" and line_str.startswith(f"* {badge_txt}"):
                                wrapped_entries.append(("* ", rgb, badge_txt, badge_rgb, line_str[2 + len(badge_txt):], rgb))
                            elif line_str.startswith(badge_txt):
                                wrapped_entries.append(("", rgb, badge_txt, badge_rgb, line_str[len(badge_txt):], rgb))
                            else:
                                wrapped_entries.append(("", rgb, "", badge_rgb, line_str, rgb))
                        else:
                            wrapped_entries.append(("", rgb, "", badge_rgb, line_str, rgb))

                for i, (pre_s, pre_rgb, b_s, b_rgb, body_s, body_rgb) in enumerate(wrapped_entries[-7:]):
                    cur_x = box_x + 10
                    cur_y = box_y + 10 + i * 21
                    if pre_s:
                        renderer.draw_text_2d(font, cur_x, cur_y, pre_s, pre_rgb)
                        cur_x += font.size(pre_s)[0]
                    if b_s:
                        renderer.draw_text_2d(font, cur_x, cur_y, b_s, b_rgb)
                        cur_x += font.size(b_s)[0]
                    renderer.draw_text_2d(font, cur_x, cur_y, body_s, body_rgb)

                in_x, in_y, in_w, in_h = box_x + 6, box_y + box_h - 32, box_w - 12, 26
                renderer.draw_rect_2d(in_x, in_y, in_w, in_h, (0.01, 0.01, 0.04, 0.96))
                if chat_mode:
                    disp_buf = chat_buffer
                    while font.size(f"> {disp_buf}_")[0] > in_w - 12 and len(disp_buf) > 1:
                        disp_buf = disp_buf[1:]
                    prompt = f"> {disp_buf}_"
                else:
                    prompt = "[LMB] Place | [B] Shape | [Scroll] Dist | [[/]] Scale | [R] Rot | [Esc] Off" if client.ghost_active else "[T] Chat | [B] Ghost | [I] Inspect | [V] Cam | /help"
                    while font.size(prompt)[0] > in_w - 12 and len(prompt) > 8:
                        prompt = prompt[:-4] + "..."
                renderer.draw_text_2d(font, in_x + 6, in_y + 5, prompt, (255, 255, 255) if chat_mode else (140, 185, 175))

                cx, cy = WINDOW_W * 0.5, WINDOW_H * 0.5
                ret_col = (0.0, 1.0, 0.85, 0.9) if client.inspect_mode else (0.7, 0.95, 1.0, 0.45)
                renderer.draw_rect_2d(cx - 10, cy - 1, 7, 2, ret_col)
                renderer.draw_rect_2d(cx + 3, cy - 1, 7, 2, ret_col)
                renderer.draw_rect_2d(cx - 1, cy - 10, 2, 7, ret_col)
                renderer.draw_rect_2d(cx - 1, cy + 3, 2, 7, ret_col)

                if client.ghost_active:
                    gx_h, gy_h, gz_h, gyaw_h = client._compute_ghost_placement()
                    g_iy = 120 if client.inspect_mode else 50
                    g_ix, g_iw, g_ih = WINDOW_W - 432, 420, 62
                    renderer.draw_rect_2d(g_ix, g_iy, g_iw, g_ih, (0.02, 0.07, 0.11, 0.90))
                    renderer.draw_text_2d(font, g_ix + 10, g_iy + 8, f"GHOST [B]: {client.ghost_shape.upper()} | {client.ghost_color} | {client.ghost_scale:.2f}x | {client.ghost_dist:.1f}m", (0, 255, 215))
                    renderer.draw_text_2d(font, g_ix + 10, g_iy + 32, f"XYZ ({gx_h:.1f}, {gy_h:.1f}, {gz_h:.1f}) yaw {gyaw_h:.0f} deg - [LMB] Place | [Esc] Cancel", (175, 245, 220))

                if client.inspect_mode:
                    ix, iy, iw, ih = WINDOW_W - 432, 50, 420, 62
                    renderer.draw_rect_2d(ix, iy, iw, ih, (0.03, 0.02, 0.10, 0.88))
                    if client.looked_at_hit is not None:
                        hit_obj, hit_dist, _, _ = client.looked_at_hit
                        hx_p, hy_p, hz_p = hit_obj["pos"]
                        d_name = hit_obj.get("def_name", "")
                        tag_lbl = f" [{d_name.split('_', 1)[1]}]" if d_name.startswith("PLACED_") else ""
                        renderer.draw_text_2d(font, ix + 10, iy + 8, f"TARGET {hit_obj['kind'].upper()}{tag_lbl} ({hit_dist:.1f}m)", (0, 255, 160))
                        renderer.draw_text_2d(font, ix + 10, iy + 32, f"XYZ ({hx_p:.1f}, {hy_p:.1f}, {hz_p:.1f}) - [R] Rot45 | /scaleobj /paint /delobj", (255, 150, 220))
                    else:
                        renderer.draw_text_2d(font, ix + 10, iy + 20, "INSPECT [I]: Aim reticle at an object", (160, 200, 190))
                glEnable(GL_DEPTH_TEST)

            pygame.display.flip()
    finally:
        client.running = False
        client.radio.stop()
        renderer.clear_dynamic_vaos()
        pygame.quit()


if __name__ == "__main__":
    main()
