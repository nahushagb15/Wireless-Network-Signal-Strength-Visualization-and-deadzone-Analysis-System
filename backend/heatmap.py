"""Pillow rendering: composite the heatmap + deadzone markers onto the blueprint PNG."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .analysis import colorize

STATUS_OUTLINE = {"strong": (20, 90, 40), "moderate": (120, 90, 0),
                  "suspect": (150, 90, 0), "deadzone": (140, 0, 0)}


def prepare_blueprint(file_storage, max_px):
    """Validate an uploaded image, convert to RGB and downscale (aspect ratio preserved).

    Returns a Pillow image. Raises ValueError for anything that is not a usable image.
    """
    try:
        img = Image.open(file_storage)
        img.load()
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types
        raise ValueError("File is not a readable image (use PNG, JPG, GIF or BMP).") from exc
    if img.width < 50 or img.height < 50:
        raise ValueError("Image is too small to be a floor plan (minimum 50x50 px).")
    img = img.convert("RGB")
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
    return img


def render_png(blueprint_path, analysis, alpha=0.55, show_points=True):
    base = Image.open(blueprint_path).convert("RGBA")
    W, H = base.size
    z = np.array(analysis["grid"]["z"], dtype=float)         # None -> nan
    spec = analysis["spec"]

    if z.size and not np.all(np.isnan(z)):
        rgb = colorize(np.nan_to_num(z, nan=spec["bad"]), spec)
        a = np.where(np.isnan(z), 0.0, alpha * 255)
        rgba = np.dstack([rgb, a]).astype(np.uint8)
        # resize in premultiplied space so the faded edges don't get dark halos
        layer = Image.fromarray(rgba, "RGBA").convert("RGBa").resize((W, H), Image.BICUBIC).convert("RGBA")
        base = Image.alpha_composite(base, layer)

    draw = ImageDraw.Draw(base)
    font = ImageFont.load_default()

    for r in analysis["regions"]:
        cx, cy, rad = r["cx"], r["cy"], r["radius"]
        draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=(176, 0, 32, 255), width=3)
        draw.text((cx - rad, cy - rad - 12), f"Deadzone {r['id']}", fill=(176, 0, 32, 255), font=font)

    if show_points:
        for p in analysis["points"]:
            x, y = p["x"], p["y"]
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 255, 255, 255),
                         outline=STATUS_OUTLINE.get(p["status"], (0, 0, 0)) + (255,), width=2)

    _legend(draw, spec, W, H, font)
    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _legend(draw, spec, W, H, font):
    unit = spec["unit"]
    items = [((46, 160, 67), "Strong"), ((249, 192, 38), "Moderate"), ((211, 47, 47), "Deadzone")]
    x0, y0 = 12, H - 12 - 18 * len(items) - 20
    draw.rectangle([x0 - 6, y0 - 6, x0 + 150, H - 6], fill=(255, 255, 255, 220))
    draw.text((x0, y0), f"{spec['label']} ({unit})", fill=(20, 32, 43, 255), font=font)
    for i, (c, label) in enumerate(items):
        yy = y0 + 18 + i * 18
        draw.rectangle([x0, yy, x0 + 12, yy + 12], fill=c + (255,))
        draw.text((x0 + 20, yy), label, fill=(20, 32, 43, 255), font=font)
