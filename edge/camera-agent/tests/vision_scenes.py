"""Synthetic scenes with known ground truth for vision tests."""

from __future__ import annotations

import numpy as np

W, H = 640, 360
BG = 80


def _noise(img: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    if sigma <= 0:
        return img
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.int16) + rng.normal(0, sigma, img.shape).astype(np.int16), 0, 255).astype(np.uint8)


def background(brightness: int = BG, noise: float = 6.0, seed: int = 0) -> np.ndarray:
    img = np.full((H, W, 3), brightness, np.uint8)
    img[250:255, :] = brightness + 30  # static "kerb" so the scene isn't perfectly flat
    return _noise(img, noise, seed)


def moving_box_scene(
    speed: float = 6.0,
    start_x: float = 20,
    y: int = 140,
    size: tuple[int, int] = (50, 80),
    noise: float = 6.0,
    vertical: bool = False,
    start_frame: int = 15,
    end_frame: int | None = None,
):
    """A bright box moving horizontally (or vertically) after ``start_frame`` (background learning)."""

    def render(i: int) -> np.ndarray:
        img = background(noise=0)
        if i >= start_frame and (end_frame is None or i <= end_frame):
            t = i - start_frame
            bw, bh = size
            if vertical:
                x, yy = int(W / 2 - bw / 2), int(start_x + t * speed)
            else:
                x, yy = int(start_x + t * speed), y
            if 0 <= x < W and 0 <= yy < H:
                img[yy : min(H, yy + bh), x : min(W, x + bw)] = 230
        return _noise(img, noise, i)

    return render


def two_boxes_scene(noise: float = 6.0, start_frame: int = 15):
    def render(i: int) -> np.ndarray:
        img = background(noise=0)
        if i >= start_frame:
            t = i - start_frame
            img[60:120, int(30 + t * 5) % 560 : int(30 + t * 5) % 560 + 40] = 230
            img[220:290, int(560 - t * 5) % 560 : int(560 - t * 5) % 560 + 40] = 220
        return _noise(img, noise, i)

    return render


def static_scene(noise: float = 6.0):
    return lambda i: background(noise=noise, seed=i)
