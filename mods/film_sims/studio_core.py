"""Core math, presets, image preview simulator, SMOD v2 builder, and USB deployer
for the ILCE-6100 Film Sim Studio.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AVCAM_PATH = ROOT / "artifacts/a6100-inputs/av-cam.bin"
LIVE_JSON = ROOT / "artifacts/a6100-pp/active-tables-live.json"
AVCAM_SHA256 = "58b2118db858f3eb1594ae6cef3f628b7fa3cc6db9173b094565535d48e6ff9e"

EC_REC28_V1_PA = 0x03E553B0
FC_TBL0_PA = 0x03E8F1D4
COLOR_A0_PA = 0x0444A254
COLOR_A1_PA = 0x0444A266
COLOR_B2_PA = 0x0444B178
CC_TUNING_PA = 0x0444A1AE
REFADR0_PA = 0x044ACD37

STOCK_A0 = (1241, -205, -12, 62, 1116, -154, 30, -251, 1245)
STOCK_A1 = (1161, -205, 68, 43, 1134, -153, 16, -236, 1243)
STOCK_B2 = (1024, -88, 88, 51, 1024, -51, 48, -289, 1264)


@dataclass
class FilmSimParams:
    name: str = "Classic Chrome"
    # Tone curve controls (post-gamma 0..1 domain)
    shadow_toe: float = -0.14        # -0.40..+0.40 (negative = deeper shadows)
    mid_contrast: float = 1.16       # 0.60..1.50 (1.0 = stock slope)
    crossover: float = 0.26          # 0.12..0.65 (anchor where shadow meets midtone)
    highlight_rolloff: float = 0.35  # 0.00..1.00 (0 = stock, 1 = heavy shoulder)
    black_fade: int = 0              # 0..40 (10-bit black lift)
    white_ceiling: int = 1018        # 920..1023 (10-bit peak white)
    # Color matrix controls
    saturation: float = 0.84         # 0.00..1.50 (0 = monochrome)
    red_amber: float = 0.12          # -0.25..+0.25 (+ = warmer amber reds)
    green_olive: float = 0.08        # -0.25..+0.25 (+ = muted olive greens)
    blue_cyan: float = 0.12          # -0.25..+0.25 (+ = slate cyan blues)
    warmth: float = 0.02             # -0.15..+0.15 (+ = warm, - = cool)
    # 5x9x9 YCbCr 3D LUT Split-Toning & Hue controls
    shadow_cb: float = 0.0           # -16..+16 (+ = cool blue/teal shadows)
    shadow_cr: float = 0.0           # -16..+16 (- = cyan/green shadows, + = magenta)
    highlight_cb: float = 0.0        # -16..+16 (- = warm golden highlights)
    highlight_cr: float = 0.0        # -16..+16 (+ = warm red/copper highlights)
    hue_shift: float = 0.0           # -20..+20 degrees chroma rotation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FilmSimParams":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


PRESETS: dict[str, FilmSimParams] = {
    "Classic Chrome": FilmSimParams(
        name="Classic Chrome",
        shadow_toe=-0.14,
        mid_contrast=1.16,
        crossover=0.26,
        highlight_rolloff=0.35,
        black_fade=0,
        white_ceiling=1016,
        saturation=0.84,
        red_amber=0.12,
        green_olive=0.08,
        blue_cyan=0.12,
        warmth=0.02,
    ),
    "Classic Neg": FilmSimParams(
        name="Classic Neg",
        shadow_toe=-0.22,
        mid_contrast=1.28,
        crossover=0.28,
        highlight_rolloff=0.45,
        black_fade=4,
        white_ceiling=1012,
        saturation=0.92,
        red_amber=0.16,
        green_olive=0.14,
        blue_cyan=0.16,
        warmth=-0.03,
        shadow_cb=4.0,
        shadow_cr=-5.0,
        highlight_cb=-4.0,
        highlight_cr=5.0,
    ),
    "Velvia": FilmSimParams(
        name="Velvia",
        shadow_toe=-0.18,
        mid_contrast=1.24,
        crossover=0.24,
        highlight_rolloff=0.20,
        black_fade=0,
        white_ceiling=1023,
        saturation=1.28,
        red_amber=-0.04,
        green_olive=-0.06,
        blue_cyan=-0.04,
        warmth=0.02,
    ),
    "Nostalgic Neg": FilmSimParams(
        name="Nostalgic Neg",
        shadow_toe=-0.05,
        mid_contrast=1.08,
        crossover=0.24,
        highlight_rolloff=0.42,
        black_fade=10,
        white_ceiling=1008,
        saturation=0.94,
        red_amber=0.18,
        green_olive=0.10,
        blue_cyan=0.15,
        warmth=0.08,
        highlight_cb=-6.0,
        highlight_cr=4.0,
    ),
    "ETERNA": FilmSimParams(
        name="ETERNA",
        shadow_toe=0.12,
        mid_contrast=0.82,
        crossover=0.28,
        highlight_rolloff=0.65,
        black_fade=14,
        white_ceiling=995,
        saturation=0.72,
        red_amber=0.06,
        green_olive=0.08,
        blue_cyan=0.10,
        warmth=-0.02,
    ),
    "ACROS B&W": FilmSimParams(
        name="ACROS B&W",
        shadow_toe=-0.20,
        mid_contrast=1.26,
        crossover=0.26,
        highlight_rolloff=0.35,
        black_fade=0,
        white_ceiling=1020,
        saturation=0.00,
        red_amber=0.00,
        green_olive=0.00,
        blue_cyan=0.00,
        warmth=0.00,
    ),
    "Stock (Reset)": FilmSimParams(
        name="Stock Standard",
        shadow_toe=0.0,
        mid_contrast=1.0,
        crossover=0.26,
        highlight_rolloff=0.0,
        black_fade=0,
        white_ceiling=1023,
        saturation=1.0,
        red_amber=0.0,
        green_olive=0.0,
        blue_cyan=0.0,
        warmth=0.0,
    ),
}


def has_3dlut_params(p: FilmSimParams) -> bool:
    return (
        abs(p.shadow_cb) > 1e-6
        or abs(p.shadow_cr) > 1e-6
        or abs(p.highlight_cb) > 1e-6
        or abs(p.highlight_cr) > 1e-6
        or abs(p.hue_shift) > 1e-6
    )


def is_stock_params(p: FilmSimParams) -> bool:
    return (
        abs(p.shadow_toe) < 1e-6
        and abs(p.mid_contrast - 1.0) < 1e-6
        and abs(p.highlight_rolloff) < 1e-6
        and p.black_fade == 0
        and p.white_ceiling == 1023
        and abs(p.saturation - 1.0) < 1e-6
        and abs(p.red_amber) < 1e-6
        and abs(p.green_olive) < 1e-6
        and abs(p.blue_cyan) < 1e-6
        and abs(p.warmth) < 1e-6
        and not has_3dlut_params(p)
    )


def _mode0_10bit(v: int) -> int:
    s = v >> 6
    if s <= 1022 and (v & 0x20):
        s += 1
    return s


def load_stock_tables() -> dict[str, list[int]]:
    av61 = AVCAM_PATH.read_bytes()
    actual_sha = hashlib.sha256(av61).hexdigest()
    assert actual_sha == AVCAM_SHA256, f"av-cam.bin hash mismatch: {actual_sha}"
    ec_off = EC_REC28_V1_PA - 0x035C6000
    fc_off = FC_TBL0_PA - 0x035C6000
    ec_stock = list(struct.unpack_from("<96I", av61, ec_off))
    fc_stock = list(struct.unpack_from("<96H", av61, fc_off))

    a0_stock = list(STOCK_A0)
    a1_stock = list(STOCK_A1)
    b2_stock = list(STOCK_B2)
    if LIVE_JSON.is_file():
        live = json.loads(LIVE_JSON.read_text(encoding="utf-8"))
        a0_stock = live["matrix_table"]["A0_idx0"]["coeffs"]
        a1_stock = live["matrix_table"]["A1_idx1"]["coeffs"]
        b2_stock = live["matrix_table"]["B2_idx17"]["coeffs"]
    return {
        "ec_stock": ec_stock,
        "fc_stock": fc_stock,
        "a0_stock": a0_stock,
        "a1_stock": a1_stock,
        "b2_stock": b2_stock,
    }


def eval_tone_transfer(x_norm: float, p: FilmSimParams) -> float:
    """Evaluate parametric filmic transfer on normalized [0, 1] post-gamma input."""
    if is_stock_params(p):
        return max(0.0, min(1.0, x_norm))
    x = max(0.0, min(1.0, x_norm))
    xc = max(0.10, min(0.75, p.crossover))

    # Piece-wise smooth S-curve anchored at xc:
    # 1. Below xc: shadow toe bend
    # 2. Above xc: midtone contrast slope + soft highlight shoulder above 0.72
    if x <= xc:
        u = x / xc
        # Smooth shadow power/polynomial controlled by shadow_toe and mid_contrast
        gamma_toe = max(0.45, 1.0 - p.shadow_toe * 1.45)
        y = xc * math.pow(u, gamma_toe)
        # Blend slope near xc to match mid_contrast smoothly
        slope_blend = (p.mid_contrast - 1.0) * 0.28 * xc * (u * u * (1.0 - u))
        y = y - slope_blend
    else:
        u = (x - xc) / (1.0 - xc)
        # Midtone contrast expansion using smooth tanh-like/sine blend
        gain = p.mid_contrast
        y_lin = xc + (x - xc) * gain
        # Smooth highlight rolloff compressing values approaching 1.0
        if p.highlight_rolloff > 0.0:
            k = 1.0 + 3.8 * p.highlight_rolloff
            shoulder = (1.0 - math.exp(-k * u)) / (1.0 - math.exp(-k))
            y_sh = xc + (1.0 - xc) * shoulder
            # Weight highlight shoulder smoothly toward upper end
            w = math.pow(u, 1.6)
            y = (1.0 - w) * y_lin + w * y_sh
        else:
            y = y_lin
        # Gently blend back into 1.0 at top
        if y > 1.0:
            y = 1.0 - 0.15 * math.exp(-(y - 1.0) * 4.0) + 0.15
            y = min(1.0, y)
    return max(0.0, min(1.0, y))


def compute_curves(stock: dict[str, list[int]], p: FilmSimParams) -> tuple[list[int], list[int]]:
    """Compute monotone 96-knot EC (uint32 << 6) and FC (uint16 0..1023) curves."""
    ec_stock = stock["ec_stock"]
    fc_stock = stock["fc_stock"]
    if is_stock_params(p):
        return list(ec_stock), list(fc_stock)

    # FC curve (96 x uint16)
    fc_lo, fc_hi = fc_stock[0], fc_stock[-1]
    fc_span = float(max(1, fc_hi - fc_lo))
    out_lo = min(fc_hi - 50, fc_lo + p.black_fade)
    out_hi = max(out_lo + 50, min(1023, p.white_ceiling))
    fc_out: list[int] = []
    prev = 0
    for h in fc_stock:
        t = (h - fc_lo) / fc_span
        y_t = eval_tone_transfer(t, p)
        val = int(round(out_lo + y_t * (out_hi - out_lo)))
        val = max(prev, min(1023, max(0, val)))
        prev = val
        fc_out.append(val)

    # EC curve (96 x uint32 << 6)
    ec_10 = [_mode0_10bit(w & 0xFFFF) for w in ec_stock]
    ec_lo, ec_hi = ec_10[0], ec_10[-1]
    ec_span = float(max(1, ec_hi - ec_lo))
    ec_out_lo = min(ec_hi - 50, ec_lo + p.black_fade)
    ec_out_hi = max(ec_out_lo + 50, min(1023, int(round(ec_hi * (out_hi / 1023.0)))))
    ec_out: list[int] = []
    prev_ec = 0
    for c10 in ec_10:
        t = (c10 - ec_lo) / ec_span
        y_t = eval_tone_transfer(t, p)
        val = int(round(ec_out_lo + y_t * (ec_out_hi - ec_out_lo)))
        val = max(prev_ec, min(1023, max(0, val)))
        prev_ec = val
        ec_out.append(val << 6)

    return ec_out, fc_out


def build_look_matrix_3x3(p: FilmSimParams):
    """Build 3x3 look transform in post-gamma RGB space with exact row sums = 1.0."""
    import numpy as np
    if is_stock_params(p):
        return np.eye(3, dtype=np.float64)

    # 1. Saturation matrix (BT.709 luma weights)
    s = p.saturation
    wr, wg, wb = 0.2126, 0.7152, 0.0722
    m_sat = np.array([
        [wr + (1.0 - wr) * s, wg * (1.0 - s), wb * (1.0 - s)],
        [wr * (1.0 - s), wg + (1.0 - wg) * s, wb * (1.0 - s)],
        [wr * (1.0 - s), wg * (1.0 - s), wb + (1.0 - wb) * s],
    ], dtype=np.float64)

    # 2. Hue / primary character matrix (each row sums to 1.0)
    ra = p.red_amber
    go = p.green_olive
    bc = p.blue_cyan
    w = p.warmth
    m_hue = np.array([
        [1.0 - ra + w * 0.5, ra, -w * 0.5],
        [go * 0.65, 1.0 - go, go * 0.35],
        [-bc * 0.15 - w * 0.5, bc * 1.15, 1.0 - bc + w * 0.5],
    ], dtype=np.float64)
    # Normalize each row sum to exactly 1.0
    m_hue /= m_hue.sum(axis=1, keepdims=True)

    return m_hue @ m_sat


def compute_q10_matrix(stock9: list[int], p: FilmSimParams) -> list[int]:
    """Compute row-sum-invariant Q10 3x3 int16 matrix for A0 or A1."""
    if is_stock_params(p):
        return list(stock9)
    import numpy as np
    m_look = build_look_matrix_3x3(p)
    s = np.array(stock9, dtype=np.float64).reshape(3, 3)
    m_new = m_look @ s
    out9: list[int] = []
    for r in range(3):
        target_sum = int(sum(stock9[r * 3 : r * 3 + 3]))
        row = [int(round(v)) for v in m_new[r]]
        row[r] -= sum(row) - target_sum
        out9.extend(row)
    return out9


def build_3dlut_1215(p: FilmSimParams) -> bytes:
    """Build 5x9x9 YCbCr 3D LUT (1215 bytes: 405 dY + 405 dCb + 405 dCr, int8 clamped [-64, 63])."""
    y_axis = (0, 64, 128, 192, 256)
    c_axis = (-128, -96, -64, -32, 0, 32, 64, 96, 128)
    theta = math.radians(p.hue_shift)
    ct, st = math.cos(theta), math.sin(theta)
    dy_plane, dcb_plane, dcr_plane = [], [], []
    for y in y_axis:
        w_shad = max(0.0, 1.0 - y / 128.0)
        w_high = max(0.0, (y - 128.0) / 128.0)
        for cb in c_axis:
            for cr in c_axis:
                dcb = p.shadow_cb * w_shad + p.highlight_cb * w_high
                dcr = p.shadow_cr * w_shad + p.highlight_cr * w_high
                if abs(theta) > 1e-6:
                    dcb += (cb * ct - cr * st) - cb
                    dcr += (cb * st + cr * ct) - cr
                dy_plane.append(0)
                dcb_plane.append(max(-64, min(63, int(round(dcb)))) & 0xFF)
                dcr_plane.append(max(-64, min(63, int(round(dcr)))) & 0xFF)
    return bytes(dy_plane + dcb_plane + dcr_plane)


def simulate_image(rgb_u8, stock: dict[str, list[int]], p: FilmSimParams):
    """Apply the exact relative ISP delta (matrix + FC curve + optional 5x9x9 YCbCr 3D LUT)."""
    if is_stock_params(p):
        return rgb_u8
    import numpy as np

    a1_stock = np.array(stock["a1_stock"], dtype=np.float64).reshape(3, 3)
    a1_sim = np.array(compute_q10_matrix(stock["a1_stock"], p), dtype=np.float64).reshape(3, 3)
    # Relative matrix delta M_sim * M_stock^-1
    delta_m = a1_sim @ np.linalg.inv(a1_stock)

    # Apply matrix in float32
    flat = rgb_u8.reshape(-1, 3).astype(np.float32)
    mapped = np.clip(flat @ delta_m.T.astype(np.float32), 0.0, 255.0)

    # Apply exact 1D FC curve delta via 256-entry LUT
    fc_stock = np.array(stock["fc_stock"], dtype=np.float64)
    _, fc_sim = compute_curves(stock, p)
    fc_sim_arr = np.array(fc_sim, dtype=np.float64)
    knots = np.arange(96, dtype=np.float64)

    x_10 = np.arange(256, dtype=np.float64) * (1023.0 / 255.0)
    k_pos = np.interp(x_10, fc_stock, knots)
    y_10 = np.interp(k_pos, knots, fc_sim_arr)
    lut_u8 = np.clip(np.rint(y_10 * (255.0 / 1023.0)), 0, 255).astype(np.uint8)

    idx = np.clip(np.rint(mapped), 0, 255).astype(np.uint8)
    out = lut_u8[idx].reshape(rgb_u8.shape)

    # Apply 5x9x9 YCbCr 3D LUT split-toning & hue twist if enabled
    if has_3dlut_params(p):
        f = out.astype(np.float32)
        r, g, b = f[:, :, 0], f[:, :, 1], f[:, :, 2]
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        cb = -0.11457 * r - 0.38543 * g + 0.5 * b
        cr = 0.5 * r - 0.45415 * g - 0.04585 * b
        w_shad = np.clip(1.0 - y / 128.0, 0.0, 1.0)
        w_high = np.clip((y - 128.0) / 128.0, 0.0, 1.0)
        dcb = p.shadow_cb * w_shad + p.highlight_cb * w_high
        dcr = p.shadow_cr * w_shad + p.highlight_cr * w_high
        if abs(p.hue_shift) > 1e-6:
            th = math.radians(p.hue_shift)
            ct, st = math.cos(th), math.sin(th)
            cb_new = cb * ct - cr * st + dcb
            cr_new = cb * st + cr * ct + dcr
        else:
            cb_new = cb + dcb
            cr_new = cr + dcr
        cb_new = np.clip(cb_new, -128.0, 128.0)
        cr_new = np.clip(cr_new, -128.0, 128.0)
        r_o = y + 1.5748 * cr_new
        g_o = y - 0.18732 * cb_new - 0.46812 * cr_new
        b_o = y + 1.8556 * cb_new
        out = np.clip(np.rint(np.stack((r_o, g_o, b_o), axis=2)), 0, 255).astype(np.uint8)
    return out


def image_metrics(rgb_u8) -> dict[str, float]:
    """Compute luma, relative chroma, and local detail gradient on a strided sample."""
    import numpy as np
    s = rgb_u8[::2, ::2].astype(np.float32)
    r, g, b = s[:, :, 0], s[:, :, 1], s[:, :, 2]
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    chroma = np.max(s, axis=2) - np.min(s, axis=2)
    dx = np.abs(luma[:, 1:] - luma[:, :-1]).mean()
    dy = np.abs(luma[1:, :] - luma[:-1, :]).mean()
    mean_l = float(luma.mean())
    mean_c = float(chroma.mean())
    return {
        "luma": mean_l,
        "chroma": mean_c,
        "sat_pct": (mean_c / max(1e-3, mean_l)) * 100.0,
        "detail_grad": float(dx + dy),
    }


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").lower()
    return s or "custom_sim"


def _camel_filename(name: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", name)
    return ("".join(p[:1].upper() + p[1:] for p in parts) or "CustomSim") + ".mod"


def _fmt(vals: list[int], as_hex: bool = False) -> str:
    return ",".join(hex(v) if as_hex else str(v) for v in vals)


def emit_header(out_header: Path, stock: dict[str, list[int]], p: FilmSimParams) -> None:
    ec_sim, fc_sim = compute_curves(stock, p)
    a0_sim = compute_q10_matrix(stock["a0_stock"], p)
    a1_sim = compute_q10_matrix(stock["a1_stock"], p)
    b2_sim = compute_q10_matrix(stock["b2_stock"], p)
    slug = _slug(p.name)
    lut_block = ""
    if has_3dlut_params(p):
        lut_bytes = build_3dlut_1215(p)
        hex_b = ",".join(f"0x{b:02x}" for b in lut_bytes)
        lut_block = (
            "#define REFADR_ENABLED 1\n"
            f"#define CC_TUNING_PA 0x{CC_TUNING_PA:08x}u\n"
            f"#define REFADR0_PA 0x{REFADR0_PA:08x}u\n"
            f"static const uint8_t REFADR_SIM[1215] = {{{hex_b}}};\n"
        )
    hdr = (
        f"/* Auto-generated by mods/film_sims/studio_core.py for {p.name} */\n"
        "#include <stdint.h>\n\n"
        f"#define EC_GAMMA_PA 0x{EC_REC28_V1_PA:08x}u\n"
        f"#define FC_GAMMA_PA 0x{FC_TBL0_PA:08x}u\n"
        f"#define COLOR_A0_PA 0x{COLOR_A0_PA:08x}u\n"
        f"#define COLOR_A1_PA 0x{COLOR_A1_PA:08x}u\n"
        f"#define COLOR_B2_PA 0x{COLOR_B2_PA:08x}u\n"
        f'#define MOD_TAG "{slug}"\n'
        f'#define MOD_LOG_PATH "/tmp_bt/mods/{slug}.log"\n\n'
        f"static const uint32_t EC_GAMMA_STOCK[96] = {{{_fmt(stock['ec_stock'], True)}}};\n"
        f"static const uint32_t EC_GAMMA_SIM[96] = {{{_fmt(ec_sim, True)}}};\n"
        f"static const uint16_t FC_GAMMA_STOCK[96] = {{{_fmt(stock['fc_stock'], False)}}};\n"
        f"static const uint16_t FC_GAMMA_SIM[96] = {{{_fmt(fc_sim, False)}}};\n"
        f"static const int16_t COLOR_A0_STOCK[9] = {{{_fmt(stock['a0_stock'], False)}}};\n"
        f"static const int16_t COLOR_A0_SIM[9] = {{{_fmt(a0_sim, False)}}};\n"
        f"static const int16_t COLOR_A1_STOCK[9] = {{{_fmt(stock['a1_stock'], False)}}};\n"
        f"static const int16_t COLOR_A1_SIM[9] = {{{_fmt(a1_sim, False)}}};\n"
        f"static const int16_t COLOR_B2_STOCK[9] = {{{_fmt(stock['b2_stock'], False)}}};\n"
        f"static const int16_t COLOR_B2_SIM[9] = {{{_fmt(b2_sim, False)}}};\n"
        + lut_block
    )
    out_header.write_text(hdr, encoding="utf-8")


def _to_wsl_path(win_or_posix: Path) -> str:
    s = str(win_or_posix.resolve())
    if s.startswith("\\\\wsl.localhost\\Ubuntu"):
        return s.replace("\\\\wsl.localhost\\Ubuntu", "").replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        rest = s[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{s[0].lower()}/{rest}"
    return s.replace("\\", "/")


def build_mod(p: FilmSimParams, out_dir: Path | None = None) -> dict[str, Any]:
    stock = load_stock_tables()
    slug = _slug(p.name)
    mod_filename = _camel_filename(p.name)
    if out_dir is None:
        out_dir = ROOT / "artifacts/a6100-film-sims" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    emit_header(out_dir / "film_sim_data.h", stock, p)
    (out_dir / "recipe.json").write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")

    wsl_root = _to_wsl_path(ROOT)
    wsl_out = _to_wsl_path(out_dir)
    cmd_str = (
        f"set -euo pipefail; cd '{wsl_root}'; "
        f"export PATH='{wsl_root}/artifacts/toolchain/arm-linux-gnueabi/bin':\"$PATH\"; "
        f"arm-linux-gnueabi-gcc -mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin "
        f"-nostdlib -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror "
        f"-fpie -fvisibility=hidden -I'{wsl_out}' -c mods/film_sims/template/film_sim.c -o '{wsl_out}/film_sim.o'; "
        f"if arm-linux-gnueabi-readelf -rW '{wsl_out}/film_sim.o' | grep -E 'R_ARM_(ABS32|GOT|TARGET1|PLT32)'; then "
        "echo 'absolute relocations' >&2; exit 1; fi; "
        f"arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld '{wsl_out}/film_sim.o' -o '{wsl_out}/film_sim.elf'; "
        f"python3 -m tools.research.pack_mod_v2 --elf '{wsl_out}/film_sim.elf' --name '{p.name[:31]}' "
        f"--on sim_life --off sim_life --action --out '{wsl_out}/{mod_filename}'"
    )
    try:
        if os.name == "nt":
            proc = subprocess.run(
                [r"C:\Program Files\WSL\wsl.exe", "-d", "Ubuntu", "--", "bash", "-c", cmd_str],
                capture_output=True, text=True, check=True,
            )
        else:
            proc = subprocess.run(["bash", "-c", cmd_str], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stderr or exc.stdout or str(exc)).strip()) from exc
    info = json.loads(proc.stdout)
    info["local_path"] = str(out_dir / mod_filename)
    info["mod_filename"] = mod_filename
    info["slug"] = slug
    return info


VENV_PYTHON = Path(r"C:\Users\alex\Desktop\a6100-recovery\a6100-fw-recovery\.venv\Scripts\python.exe")


def _has_win32file() -> bool:
    try:
        import win32file  # noqa: F401
        return True
    except ImportError:
        return False


def connect_camera():
    dbg_dir = str(ROOT / "artifacts/a6100-pp")
    if dbg_dir not in sys.path:
        sys.path.insert(0, dbg_dir)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from a6100_usb_dbg import DirectScsiExchange
    from tools.camera_debugger import Debugger, OP_INFO

    for drive in (r"\\.\F:", r"\\.\E:", r"\\.\G:", r"\\.\PHYSICALDRIVE3", r"\\.\PHYSICALDRIVE4"):
        try:
            dbg = Debugger(DirectScsiExchange(drive, timeout=3.0))
            dbg.request(OP_INFO)
            return dbg, drive
        except Exception:
            continue
    raise RuntimeError("ILCE-6100 not found on USB Mass Storage (ensure camera is plugged in & USB Debugger is running)")


def push_mod_to_camera(mod_path: Path, mod_filename: str | None = None) -> dict[str, Any]:
    fname = mod_filename or mod_path.name
    if not _has_win32file() and VENV_PYTHON.is_file():
        code = (
            "import json, sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "from mods.film_sims.studio_core import push_mod_to_camera; "
            f"print(json.dumps(push_mod_to_camera(Path({str(mod_path)!r}), {fname!r})))"
        )
        try:
            proc = subprocess.run([str(VENV_PYTHON), "-c", code], capture_output=True, text=True, check=True)
            return json.loads(proc.stdout)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError((exc.stderr or exc.stdout or str(exc)).strip().splitlines()[-1]) from exc

    from tools.camera_debugger import OP_EXEC
    dbg, drive = connect_camera()
    remote = f"/tmp_bt/mods/{fname}"
    data = mod_path.read_bytes()
    res = dbg.push(remote, data)
    # Request live reload in case the same mod name is already active
    dbg.request(OP_EXEC, f"echo {fname} > /tmp_bt/mlv2/reload".encode())
    ls_out = dbg.request(OP_EXEC, b"ls -la /tmp_bt/mods")["payload"].decode(errors="replace")
    return {"drive": drive, "remote": remote, **res, "ls": ls_out.strip()}


def read_camera_logs(slug: str) -> str:
    if not _has_win32file() and VENV_PYTHON.is_file():
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "from mods.film_sims.studio_core import read_camera_logs; "
            f"print(read_camera_logs({slug!r}))"
        )
        try:
            proc = subprocess.run([str(VENV_PYTHON), "-c", code], capture_output=True, text=True, check=True)
            return proc.stdout
        except subprocess.CalledProcessError as exc:
            raise RuntimeError((exc.stderr or exc.stdout or str(exc)).strip().splitlines()[-1]) from exc

    from tools.camera_debugger import OP_EXEC
    dbg, drive = connect_camera()
    cmd = f"echo '=== {drive} /tmp_bt/mods/{slug}.log ==='; cat /tmp_bt/mods/{slug}.log 2>/dev/null || echo '(empty)'; echo '=== mod_loader.log ==='; tail -n 10 /tmp_bt/mods/mod_loader.log 2>/dev/null || true"
    return dbg.request(OP_EXEC, cmd.encode())["payload"].decode(errors="replace")


def emit_live_picker_header(out_header: Path, stock: dict[str, list[int]] | None = None,
                            presets: list[FilmSimParams] | None = None) -> Path:
    """Emit film_sim_live_data.h bundling Stock (0) + all presets for FilmSimLive.mod."""
    if stock is None:
        stock = load_stock_tables()
    if presets is None:
        presets = [p for p in PRESETS.values() if not is_stock_params(p)]

    names = ["Stock (Off)"] + [p.name[:15] for p in presets]
    ecs, fcs, a0s, a1s, b2s, luts, lut_idxs = [], [], [], [], [], [], [-1]
    for p in presets:
        ec_sim, fc_sim = compute_curves(stock, p)
        ecs.append("{" + _fmt(ec_sim, True) + "}")
        fcs.append("{" + _fmt(fc_sim, False) + "}")
        a0s.append("{" + _fmt(compute_q10_matrix(stock["a0_stock"], p), False) + "}")
        a1s.append("{" + _fmt(compute_q10_matrix(stock["a1_stock"], p), False) + "}")
        b2s.append("{" + _fmt(compute_q10_matrix(stock["b2_stock"], p), False) + "}")
        if has_3dlut_params(p):
            lut_idxs.append(len(luts))
            luts.append("{" + ",".join(f"0x{b:02x}" for b in build_3dlut_1215(p)) + "}")
        else:
            lut_idxs.append(-1)

    name_rows = ",".join(f'"{n}"' for n in names)
    lut_block = (
        f"const uint8_t LIVE_REFADR[{max(1, len(luts))}][1215] = {{"
        + (",".join(luts) if luts else "{0}")
        + "};\n"
    )
    hdr = (
        "/* Auto-generated by mods/film_sims/studio_core.py for FilmSimLive.mod */\n"
        "#include <stdint.h>\n\n"
        f"#define EC_GAMMA_PA 0x{EC_REC28_V1_PA:08x}u\n"
        f"#define FC_GAMMA_PA 0x{FC_TBL0_PA:08x}u\n"
        f"#define COLOR_A0_PA 0x{COLOR_A0_PA:08x}u\n"
        f"#define COLOR_A1_PA 0x{COLOR_A1_PA:08x}u\n"
        f"#define COLOR_B2_PA 0x{COLOR_B2_PA:08x}u\n"
        f"#define CC_TUNING_PA 0x{CC_TUNING_PA:08x}u\n"
        f"#define REFADR0_PA 0x{REFADR0_PA:08x}u\n"
        f"#define LIVE_N_PRESETS {len(names)}u\n\n"
        f"const char LIVE_NAMES[{len(names)}][16] = {{{name_rows}}};\n"
        f"const int8_t LIVE_LUT_IDX[{len(names)}] = {{{_fmt(lut_idxs, False)}}};\n\n"
        f"const uint32_t EC_GAMMA_STOCK[96] = {{{_fmt(stock['ec_stock'], True)}}};\n"
        f"const uint16_t FC_GAMMA_STOCK[96] = {{{_fmt(stock['fc_stock'], False)}}};\n"
        f"const int16_t COLOR_A0_STOCK[9] = {{{_fmt(stock['a0_stock'], False)}}};\n"
        f"const int16_t COLOR_A1_STOCK[9] = {{{_fmt(stock['a1_stock'], False)}}};\n"
        f"const int16_t COLOR_B2_STOCK[9] = {{{_fmt(stock['b2_stock'], False)}}};\n\n"
        f"const uint32_t LIVE_EC[{len(presets)}][96] = {{{','.join(ecs)}}};\n"
        f"const uint16_t LIVE_FC[{len(presets)}][96] = {{{','.join(fcs)}}};\n"
        f"const int16_t LIVE_A0[{len(presets)}][9] = {{{','.join(a0s)}}};\n"
        f"const int16_t LIVE_A1[{len(presets)}][9] = {{{','.join(a1s)}}};\n"
        f"const int16_t LIVE_B2[{len(presets)}][9] = {{{','.join(b2s)}}};\n"
        + lut_block
    )
    out_header.parent.mkdir(parents=True, exist_ok=True)
    out_header.write_text(hdr, encoding="utf-8")
    return out_header
