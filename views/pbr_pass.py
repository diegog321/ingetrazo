# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The "Realistic" display style (experimental fork, PBR phase 2).

No second renderer: the viewport keeps every pass it has (sky, culling,
section cuts, shadows, edit fade, instancing) and this module only

* adds a PBR block to ``basic.frag`` when the program is compiled
  (:func:`fragment_source`), switched off by ``u_pbr_on = 0``, so every
  other style draws exactly as before;
* turns it on for the face passes of a Realistic frame (:func:`begin_frame`,
  :func:`geometry`): roughness, metal, normal/ORM/emission maps for each
  textured run, read from the material of that picture;
* redraws the faces of colour materials that carry a surface on top of the
  batched colour pass, which has one draw call for every colour and so no
  way to tell brushed steel from grey paint (:func:`overlay`).

Normals and tangents come from screen derivatives, so no vertex buffer
changes. If the patched shader does not compile, the viewport keeps the
plain one and a Realistic frame draws as Textures (:func:`available`).
"""
from __future__ import annotations

import os
import re
from array import array
from pathlib import Path

REALISTIC = "realistic"

#: Hemisphere ambient (linear) and the key light used when sun shadows are
#: off: up and over the viewer's right shoulder of a default front view.
SKY = (0.36, 0.40, 0.48)
GROUND = (0.15, 0.14, 0.12)
KEY = (0.35, -0.50, 0.80)
SUN = 3.4
EXPOSURE = 1.0
#: Unpainted and plain colour faces: matte, like most paint and plaster.
DEFAULT = (0.8, 0.0, 0.0, 1.0)
#: cd/m² that display as 1.0 before tone mapping (the Light preset).
EMIT_REF = 3000.0

_GLSL = """
// ---- fork:pbr — the Realistic display style (views/pbr_pass.py) ----------
uniform int u_pbr_on;
uniform int u_pbr_own;       // 1: u_pbr_params apply (textured run, overlay)
uniform int u_pbr_raw;       // 1: colour not pre-shaded (overlay pass)
uniform vec3 u_pbr_eye;
uniform vec3 u_pbr_light;    // the fixed light vertex colours are shaded with
uniform vec3 u_pbr_key;
uniform vec4 u_pbr_params;   // roughness, metallic, transmission, normal scale
uniform vec4 u_pbr_default;
uniform vec3 u_pbr_emit;     // linear, exposure applied
uniform int u_pbr_maps;      // 1 rough/metal, 2 ao, 4 normal, 8 emission
uniform float u_pbr_ao;
uniform sampler2D u_pbr_orm;
uniform sampler2D u_pbr_nrm;
uniform sampler2D u_pbr_emap;
uniform vec3 u_pbr_sky;
uniform vec3 u_pbr_ground;
uniform float u_pbr_sun;
uniform float u_pbr_exposure;
float pbr_lit = 1.0;

bool pbr_active() {
    return u_pbr_on == 1 && u_stipple == 0 && u_hard_cutout == 0
        && u_shadow_overlay == 0
        && (u_use_vcolor == 1 || u_use_texture == 1 || u_pbr_own == 1);
}

vec3 pbr_shade(vec3 c) {
    vec3 p = v_world;
    vec3 n = cross(dFdx(p), dFdy(p));
    if (dot(n, n) < 1e-24) return c;
    n = normalize(n);
    vec3 v = normalize(u_pbr_eye - p);
    if (dot(n, v) < 0.0) n = -n;
    bool own = (u_use_texture == 1 || u_pbr_own == 1);
    if (u_pbr_raw == 0) {
        // Undo the maquette shade baked into the colour (viewport
        // _shade_factor), so the light below is the only light.
        float s = (u_use_texture == 1) ? u_shade
            : floor((0.62 + 0.38 * abs(dot(n, u_pbr_light))) * 32.0 + 0.5)
              / 32.0;
        c = min(c / max(s, 0.05), vec3(1.0));
    }
    vec4 prm = own ? u_pbr_params : u_pbr_default;
    float rough = prm.x;
    float metal = prm.y;
    float trans = prm.z;
    float ao = 1.0;
    vec3 emit = own ? u_pbr_emit : vec3(0.0);
    if (own && u_pbr_maps != 0) {
        if ((u_pbr_maps & 3) != 0) {
            vec3 orm = texture(u_pbr_orm, v_uv).rgb;
            if ((u_pbr_maps & 1) != 0) { rough *= orm.g; metal *= orm.b; }
            if ((u_pbr_maps & 2) != 0) ao = mix(1.0, orm.r, u_pbr_ao);
        }
        if ((u_pbr_maps & 4) != 0) {
            // Cotangent frame from derivatives (no tangent attribute).
            vec3 t = texture(u_pbr_nrm, v_uv).rgb * 2.0 - 1.0;
            t.xy *= prm.w;
            vec3 dp1 = dFdx(p);
            vec3 dp2 = dFdy(p);
            vec2 du1 = dFdx(v_uv);
            vec2 du2 = dFdy(v_uv);
            vec3 q2 = cross(dp2, n);
            vec3 q1 = cross(n, dp1);
            vec3 T = q2 * du1.x + q1 * du2.x;
            vec3 B = q2 * du1.y + q1 * du2.y;
            float m = max(dot(T, T), dot(B, B));
            if (m > 1e-30) {
                float inv = inversesqrt(m);
                vec3 bent = mat3(T * inv, B * inv, n) * t;
                if (dot(bent, bent) > 1e-12) n = normalize(bent);
            }
        }
        if ((u_pbr_maps & 8) != 0)
            emit *= pow(texture(u_pbr_emap, v_uv).rgb, vec3(2.2));
    }
    rough = clamp(rough, 0.04, 1.0);
    metal = clamp(metal, 0.0, 1.0);
    vec3 base = pow(max(c, vec3(0.0)), vec3(2.2));
    bool sun = (u_shadow_enable == 1);
    vec3 L = normalize(sun ? u_sun_dir : u_pbr_key);
    float shadow = sun ? pbr_lit : 1.0;
    vec3 h = normalize(L + v);
    float ndl = max(dot(n, L), 0.0);
    float ndv = max(dot(n, v), 1e-4);
    float ndh = max(dot(n, h), 0.0);
    float vdh = max(dot(v, h), 0.0);
    float a = rough * rough;
    float a2 = a * a;
    float dd = ndh * ndh * (a2 - 1.0) + 1.0;
    float D = a2 / (3.14159265 * dd * dd);
    float k = (rough + 1.0) * (rough + 1.0) / 8.0;
    float G = ndl / (ndl * (1.0 - k) + k) * ndv / (ndv * (1.0 - k) + k);
    vec3 f0 = mix(vec3(0.04), base, metal);
    vec3 F = f0 + (1.0 - f0) * pow(1.0 - vdh, 5.0);
    vec3 spec = D * G * F / max(4.0 * ndl * ndv, 1e-4);
    vec3 kd = (1.0 - F) * (1.0 - metal) * (1.0 - 0.6 * trans);
    vec3 direct = (kd * base / 3.14159265 + spec) * ndl * shadow * u_pbr_sun;
    // Image-based light from a two-tone studio hemisphere; Karis's
    // analytic environment BRDF stands in for the split-sum lookup.
    vec3 amb = mix(u_pbr_ground, u_pbr_sky, n.z * 0.5 + 0.5);
    vec3 R = reflect(-v, n);
    vec3 env = mix(u_pbr_ground, u_pbr_sky * 1.35,
                   smoothstep(0.35, 0.65, R.z * 0.5 + 0.5));
    env = mix(env, amb, rough);
    vec4 r = rough * vec4(-1.0, -0.0275, -0.572, 0.022)
             + vec4(1.0, 0.0425, 1.04, -0.04);
    float a004 = min(r.x * r.x, exp2(-9.28 * ndv)) * r.x + r.y;
    vec2 ab = vec2(-1.04, 1.04) * a004 + r.zw;
    vec3 ambient = (kd * base * amb + (f0 * ab.x + ab.y) * env) * ao;
    vec3 col = (direct + ambient + emit) * u_pbr_exposure;
    // ACES (Narkowicz fit), then display gamma.
    col = clamp((col * (2.51 * col + 0.03))
                / (col * (2.43 * col + 0.59) + 0.14), 0.0, 1.0);
    return pow(col, vec3(1.0 / 2.2));
}
// ---- end fork:pbr ----------------------------------------------------------
"""

_ANCHOR = "out vec4 fragColor;\n"
_SUN = re.compile(r"c\.rgb \*= mix\(u_shadow_dark, 1\.0,\s*"
                  r"clamp\(ndl \* 1\.5, 0\.0, 1\.0\) \* lit\);")
_FINAL = "    fragColor = vec4(mix(c.rgb, u_fade_color, u_fade), c.a);\n}"


def patch_fragment(src: str) -> str | None:
    """``basic.frag`` with the PBR block, or None when the upstream source
    moved and a hook no longer matches (the plain shader is then kept)."""
    if src.count(_ANCHOR) != 1 or src.count(_FINAL) != 1:
        return None
    if len(_SUN.findall(src)) != 1:
        return None
    out = src.replace(_ANCHOR, _ANCHOR + _GLSL)
    out = _SUN.sub(lambda m: "if (pbr_active()) { pbr_lit = lit; } else { "
                   + m.group(0) + " }", out)
    out = out.replace(_FINAL, "    if (pbr_active()) c.rgb = pbr_shade(c.rgb);\n"
                      + _FINAL)
    return out


def fragment_source(shader_dir) -> str | None:
    """The patched fragment shader, or None to keep the plain file
    (``INGETRAZO_NO_PBR=1`` forces that)."""
    if os.environ.get("INGETRAZO_NO_PBR"):
        return None
    try:
        src = (Path(shader_dir) / "basic.frag").read_text(encoding="utf-8")
    except OSError:
        return None
    return patch_fragment(src)


def available(vp) -> bool:
    """True when the viewport's program carries the PBR block."""
    return _locs(vp) is not None


# ---- Per frame ---------------------------------------------------------------

_NAMES = ("u_pbr_on", "u_pbr_own", "u_pbr_raw", "u_pbr_eye", "u_pbr_light",
          "u_pbr_key", "u_pbr_params", "u_pbr_default", "u_pbr_emit",
          "u_pbr_maps", "u_pbr_ao", "u_pbr_orm", "u_pbr_nrm", "u_pbr_emap",
          "u_pbr_sky", "u_pbr_ground", "u_pbr_sun", "u_pbr_exposure")
_UNIT_ORM, _UNIT_NRM, _UNIT_EMAP = 2, 3, 4


def _locs(vp):
    prog = getattr(vp, "_program", None)
    if prog is None:
        return None
    cached = getattr(vp, "_pbr_locs", None)
    if cached is not None and cached[0] is prog:
        return cached[1]
    locs = {n: prog.uniformLocation(n) for n in _NAMES}
    out = locs if locs["u_pbr_on"] >= 0 else None
    vp._pbr_locs = (prog, out)
    return out


def begin_frame(vp, mode: str) -> str:
    """Called once per paint with the style's face mode; returns the mode
    the viewport should draw with (Realistic draws the Textures passes)."""
    vp._pbr_frame = False
    locs = _locs(vp)
    if locs is not None:
        vp._program.setUniformValue(locs["u_pbr_on"], 0)
    if mode != REALISTIC:
        return mode
    if locs is None:
        return "textures"                      # M10: plain shader fallback
    from PySide6.QtGui import QVector3D, QVector4D
    prog = vp._program
    vp._pbr_frame = True
    prog.setUniformValue(locs["u_pbr_eye"], vp.camera.eye())
    prog.setUniformValue(locs["u_pbr_light"], vp._LIGHT)
    prog.setUniformValue(locs["u_pbr_key"], QVector3D(*KEY).normalized())
    prog.setUniformValue(locs["u_pbr_sky"], QVector3D(*SKY))
    prog.setUniformValue(locs["u_pbr_ground"], QVector3D(*GROUND))
    prog.setUniformValue1f(locs["u_pbr_sun"], SUN)
    prog.setUniformValue1f(locs["u_pbr_exposure"], EXPOSURE)
    prog.setUniformValue(locs["u_pbr_default"], QVector4D(*DEFAULT))
    prog.setUniformValue(locs["u_pbr_params"], QVector4D(*DEFAULT))
    prog.setUniformValue(locs["u_pbr_emit"], QVector3D(0.0, 0.0, 0.0))
    prog.setUniformValue(locs["u_pbr_own"], 0)
    prog.setUniformValue(locs["u_pbr_raw"], 0)
    prog.setUniformValue(locs["u_pbr_maps"], 0)
    prog.setUniformValue(locs["u_pbr_orm"], _UNIT_ORM)
    prog.setUniformValue(locs["u_pbr_nrm"], _UNIT_NRM)
    prog.setUniformValue(locs["u_pbr_emap"], _UNIT_EMAP)
    return "textures"


def geometry(vp, on: bool) -> None:
    """Around the model's face passes of a Realistic frame: the PBR block
    on, and every textured run's picture looked up for its material."""
    if not getattr(vp, "_pbr_frame", False):
        return
    locs = _locs(vp)
    vp._program.setUniformValue(locs["u_pbr_on"], 1 if on else 0)
    if on:
        vp._get_texture = lambda path: _run_texture(vp, path)
    else:
        vp.__dict__.pop("_get_texture", None)
        vp._program.setUniformValue(locs["u_pbr_own"], 0)
        vp._program.setUniformValue(locs["u_pbr_maps"], 0)


def _run_texture(vp, path):
    tex = type(vp)._get_texture(vp, path)
    if tex is not None:
        _bind(vp, _material_params(vp, path=path))
    return tex


# ---- Materials → uniforms -------------------------------------------------------

def _table(vp) -> dict:
    """``picture path → material`` for the registry, per scene version."""
    scene = vp.scene
    key = (id(scene), getattr(scene, "version", None))
    cached = getattr(vp, "_pbr_table", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    table = {}
    for mat in (getattr(scene, "materials", None) or {}).values():
        tex = getattr(mat, "texture", None) or {}
        if tex.get("path"):
            table.setdefault(tex["path"], mat)
    vp._pbr_table = (key, table)
    vp._pbr_params = {}
    return table


def _material_params(vp, path=None, mat=None):
    """``(params4, emit3, ao, {unit: map path}, maps bits)`` for a textured
    run's picture, or for a registry material."""
    from core import pbr
    table = _table(vp)
    cache = vp._pbr_params
    ck = ("p", path) if mat is None else ("m", mat.name)
    if ck in cache:
        return cache[ck]
    if mat is None:
        mat = table.get(path)
    if mat is None:
        surface = pbr.effective(None, name=Path(path).stem, picture=path)
    else:
        surface = pbr.effective(mat)
    cache[ck] = out = params_for(surface)
    return out


def params_for(surface):
    """The uniforms one surface asks for (pure; tested without GL)."""
    from core import pbr_maps
    units: dict = {}
    bits = 0
    nscale = 1.0
    try:
        orm, has_ao, has_rm = pbr_maps.orm_image(surface)
        if orm:
            units[_UNIT_ORM] = orm
            bits |= (1 if has_rm else 0) | (2 if has_ao else 0)
        nrm, nscale = pbr_maps.normal_image(surface)
        if nrm:
            units[_UNIT_NRM] = nrm
            bits |= 4
    except Exception:                          # a missing/broken map file
        units, bits, nscale = {}, 0, 1.0
    for unit, bit in ((_UNIT_ORM, 3), (_UNIT_NRM, 4)):
        if unit in units and not os.path.isfile(units[unit]):
            del units[unit]
            bits &= ~bit
    emit = (0.0, 0.0, 0.0)
    if surface.glows:
        col = surface.emission or (1.0, 1.0, 1.0)
        gain = float(surface.luminance or 0.0) / EMIT_REF
        emit = tuple((float(c) ** 2.2) * gain for c in col[:3])
        emap = surface.map("emission")
        if emap is not None and emap.path:
            units[_UNIT_EMAP] = emap.path
            bits |= 8
    ao_map = surface.map("ao")
    ao = float(ao_map.strength) if ao_map is not None else 1.0
    params = (float(surface.roughness), float(surface.metallic),
              float(surface.transmission), float(nscale))
    return params, emit, ao, units, bits


def _bind(vp, entry) -> None:
    from PySide6.QtGui import QVector3D, QVector4D
    params, emit, ao, units, bits = entry
    locs = _locs(vp)
    prog = vp._program
    prog.setUniformValue(locs["u_pbr_params"], QVector4D(*params))
    prog.setUniformValue(locs["u_pbr_emit"], QVector3D(*emit))
    prog.setUniformValue1f(locs["u_pbr_ao"], ao)
    for unit, map_path in units.items():
        tex = type(vp)._get_texture(vp, map_path)
        if tex is None:
            bits &= ~{_UNIT_ORM: 3, _UNIT_NRM: 4, _UNIT_EMAP: 8}[unit]
            continue
        tex.bind(unit)
    prog.setUniformValue(locs["u_pbr_maps"], bits)
    if units:
        vp._gl.glActiveTexture(0x84C0)          # GL_TEXTURE0


# ---- Overlay for colour materials --------------------------------------------

def _wants_overlay(surface) -> bool:
    return bool(surface.maps or surface.glows or surface.metallic > 0.0
                or surface.transmission > 0.0
                or abs(surface.roughness - DEFAULT[0]) > 0.15)


def overlay_split(scene) -> dict:
    """``name → UV tile`` for the colour materials the batched pass cannot
    show (their surface differs from the default eggshell)."""
    from core import pbr
    from core.pbr_gltf import DEFAULT_TILE
    out = {}
    for name, mat in (getattr(scene, "materials", None) or {}).items():
        if (getattr(mat, "texture", None) or {}).get("path"):
            continue                           # textured runs handle these
        s = pbr.effective(mat)
        if _wants_overlay(s):
            out[name] = DEFAULT_TILE if s.maps else None
    return out


def overlay_triangles(scene, split) -> dict:
    """``name → array('f')`` of pos(3) uv(2) colour(3) per vertex, for the
    opaque colour faces of each material in ``split``."""
    from formats.meshexport import _add_face, world_faces
    if not split:
        return {}
    materials: dict = {}
    prims: dict = {}
    for face in world_faces(scene):
        attrs = face.attrs or {}
        if attrs.get("mat") not in split or attrs.get("texture"):
            continue
        _add_face(face, materials, prims, split)
    out: dict = {}
    for key, tris in prims.items():
        info = materials.get(key, {})
        if key[0] != "color" or info.get("opacity") is not None:
            continue
        own = next((k[1] for k in key if isinstance(k, tuple)
                    and len(k) == 2 and k[0] == "mat"), None)
        if own is None:
            continue
        col = info.get("color", (1.0, 1.0, 1.0))
        buf = out.setdefault(own, array("f"))
        for _n, verts in tris:
            for pos, uv in verts:
                u, v = uv if uv is not None else (0.0, 0.0)
                buf.extend((pos.x(), pos.y(), pos.z(), u, v,
                            col[0], col[1], col[2]))
    return out


def overlay(vp) -> None:
    """Redraw the faces of colour materials with a surface, with their own
    roughness/metal/maps, over the batched colour pass."""
    if not getattr(vp, "_pbr_frame", False):
        return
    scene = vp.scene
    if scene.edit_group is not None:
        return                                 # the faded context stays as is
    runs = _overlay_runs(vp)
    if not runs:
        return
    locs = _locs(vp)
    prog = vp._program
    gl = vp._gl
    prog.setUniformValue(locs["u_pbr_own"], 1)
    prog.setUniformValue(locs["u_pbr_raw"], 1)
    prog.setUniformValue(vp._loc_use_vcolor, 1)
    gl.glEnable(0x8037)                        # GL_POLYGON_OFFSET_FILL
    gl.glPolygonOffset(0.5, 0.5)
    vao = vp._pbr_vao
    vao.bind()
    for name, start, count in runs:
        mat = scene.materials.get(name)
        if mat is None:
            continue
        _bind(vp, _material_params(vp, mat=mat))
        gl.glDrawArrays(0x0004, start, count)  # GL_TRIANGLES
    vao.release()
    gl.glDisable(0x8037)
    prog.setUniformValue(vp._loc_use_vcolor, 0)
    prog.setUniformValue(locs["u_pbr_own"], 0)
    prog.setUniformValue(locs["u_pbr_raw"], 0)
    prog.setUniformValue(locs["u_pbr_maps"], 0)


def _overlay_runs(vp):
    scene = vp.scene
    key = (id(scene), getattr(scene, "version", None))
    cached = getattr(vp, "_pbr_overlay", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    tris = overlay_triangles(scene, overlay_split(scene))
    runs = []
    data = array("f")
    for name, buf in tris.items():
        start = len(data) // 8
        data.extend(buf)
        runs.append((name, start, len(buf) // 8))
    if runs:
        _upload(vp, data)
    vp._pbr_overlay = (key, runs)
    return runs


def _upload(vp, data: array) -> None:
    from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLVertexArrayObject
    vao = getattr(vp, "_pbr_vao", None)
    if vao is None:
        vao = QOpenGLVertexArrayObject(vp)
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.setUsagePattern(QOpenGLBuffer.DynamicDraw)
        vbo.create()
        vbo.bind()
        stride = 8 * 4
        prog = vp._program
        prog.enableAttributeArray(0)           # a_pos (layout 0)
        prog.setAttributeBuffer(0, 0x1406, 0, 3, stride)
        prog.enableAttributeArray(1)           # a_uv
        prog.setAttributeBuffer(1, 0x1406, 3 * 4, 2, stride)
        prog.enableAttributeArray(2)           # a_color
        prog.setAttributeBuffer(2, 0x1406, 5 * 4, 3, stride)
        vao.release()
        vp._pbr_vao, vp._pbr_vbo = vao, vbo
    vbo = vp._pbr_vbo
    vbo.bind()
    raw = data.tobytes()
    vbo.allocate(raw, len(raw))
    vbo.release()
