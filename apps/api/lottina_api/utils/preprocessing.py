"""Preprocessing utilities for OCR images.

All functions assume input images are numpy arrays. Public entry point
is `preprocess_pipeline`, which returns a 3‑channel RGB image suitable
for EasyOCR along with a metadata dict describing the applied steps.

Typical usage:

    from .preprocessing import preprocess_pipeline
    img_pp, meta = preprocess_pipeline(img_rgb, target_dpi=240,
                                       binarize="otsu", deskew=True)

Dependencies: opencv-python (or opencv-python-headless), numpy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Optional

import numpy as np
import cv2


# ------------------------- Helper dataclass -------------------------
@dataclass
class PipelineConfig:
    # Scaling
    target_dpi: Optional[int] = None  # if given, scale from `source_dpi` to this
    source_dpi: int = 180

    # Core steps
    grayscale: bool = True
    binarize: Optional[str] = "otsu"  # one of: None, "otsu", "adaptive"
    deskew: bool = True
    denoise: bool = True
    contrast: bool = True  # CLAHE
    remove_borders_flag: bool = False

    # Morphology (optional)
    morph_open: int = 0   # kernel size (px); 0 disables
    morph_close: int = 0  # kernel size (px); 0 disables

    # Adaptive threshold params
    adaptive_block_size: int = 25  # must be odd
    adaptive_C: int = 10


# ------------------------- Basic conversions ------------------------
def ensure_rgb(img: np.ndarray) -> np.ndarray:
    """Ensure the image has 3 channels in RGB order."""
    if img is None:
        raise ValueError("Image is None")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
    # Assume BGR or RGB – try to detect; we'll assume input from PyMuPDF is RGB
    return img


def to_gray(img_rgb: np.ndarray) -> np.ndarray:
    # PyMuPDF output is RGB; OpenCV expects BGR for many ops, but
    # cvtColor supports COLOR_RGB2GRAY explicitly.
    return cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)


# ------------------------- Core operations --------------------------
def enhance_contrast(gray: np.ndarray) -> np.ndarray:
    """Apply CLAHE to improve local contrast."""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def denoise_gray(gray: np.ndarray) -> np.ndarray:
    """Light denoising for text images."""
    # fastNlMeans is good for scanned docs without destroying edges too much
    return cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)


def binarize_otsu(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


def binarize_adaptive(gray: np.ndarray, block_size: int = 25, C: int = 10) -> np.ndarray:
    if block_size % 2 == 0:
        block_size += 1
    th = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, C
    )
    return th


def deskew(gray: np.ndarray) -> Tuple[np.ndarray, float]:
    """Deskew using minimum area rectangle angle on the text mask.

    Returns (rotated_gray, angle_degrees). Positive angle means counter‑clockwise.
    Falls back gracefully if angle cannot be estimated.
    """
    try:
        # Invert so text is white
        inv = cv2.bitwise_not(gray)
        # Binary for contour finding
        _, bw = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # Morph close to connect text lines
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
        morph = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)
        # Find coordinates of non-zero pixels
        coords = np.column_stack(np.where(morph > 0))
        if coords.size == 0:
            return gray, 0.0
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        if angle < -45:
            angle = 90 + angle
        # Rotate
        (h, w) = gray.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return rotated, angle
    except Exception:
        return gray, 0.0


def remove_borders(gray: np.ndarray) -> np.ndarray:
    """Attempt to crop strong outer borders (scans with frames)."""
    try:
        edges = cv2.Canny(gray, 50, 150)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        edges = cv2.dilate(edges, kernel, iterations=1)
        cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return gray
        # Largest contour assumed to be the page
        c = max(cnts, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(c)
        # If the contour is almost the whole image, skip
        H, W = gray.shape[:2]
        if w * h < 0.5 * W * H:
            return gray
        return gray[y : y + h, x : x + w]
    except Exception:
        return gray


def resize_to_dpi(img: np.ndarray, source_dpi: int, target_dpi: int) -> np.ndarray:
    if target_dpi <= 0 or source_dpi <= 0 or target_dpi == source_dpi:
        return img
    scale = float(target_dpi) / float(source_dpi)
    new_w = max(1, int(round(img.shape[1] * scale)))
    new_h = max(1, int(round(img.shape[0] * scale)))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)


def apply_morph(gray: np.ndarray, open_size: int = 0, close_size: int = 0) -> np.ndarray:
    out = gray
    if open_size > 0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (open_size, open_size))
        out = cv2.morphologyEx(out, cv2.MORPH_OPEN, k)
    if close_size > 0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size))
        out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, k)
    return out


# ------------------------- Pipeline ---------------------------------
def preprocess_pipeline(
    img_rgb: np.ndarray,
    *,
    target_dpi: Optional[int] = None,
    source_dpi: int = 180,
    grayscale: bool = True,
    binarize: Optional[str] = "otsu",
    deskew_flag: bool = True,
    denoise_flag: bool = True,
    contrast_flag: bool = True,
    remove_borders_flag: bool = False,
    morph_open: int = 0,
    morph_close: int = 0,
    adaptive_block_size: int = 25,
    adaptive_C: int = 10,
) -> Tuple[np.ndarray, Dict]:
    """High-level preprocessing for OCR.

    Returns (rgb_for_ocr, meta)
    """
    meta: Dict = {
        "scaled": False,
        "deskew_angle": 0.0,
        "binarize": binarize,
        "steps": [],
    }

    img = ensure_rgb(img_rgb)

    # Scaling to target DPI (operate in RGB space; cv2 resize is fine)
    if target_dpi is not None and target_dpi != source_dpi:
        img = resize_to_dpi(img, source_dpi=source_dpi, target_dpi=target_dpi)
        meta["scaled"] = True
        meta["source_dpi"] = source_dpi
        meta["target_dpi"] = target_dpi

    gray = to_gray(img) if grayscale else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    meta["steps"].append("gray")

    if contrast_flag:
        gray = enhance_contrast(gray)
        meta["steps"].append("clahe")

    if denoise_flag:
        gray = denoise_gray(gray)
        meta["steps"].append("denoise")

    if deskew_flag:
        gray, angle = deskew(gray)
        meta["deskew_angle"] = float(angle)
        meta["steps"].append("deskew")

    if remove_borders_flag:
        gray = remove_borders(gray)
        meta["steps"].append("remove_borders")

    # Thresholding
    if binarize == "otsu":
        gray = binarize_otsu(gray)
        meta["steps"].append("binarize_otsu")
    elif binarize == "adaptive":
        gray = binarize_adaptive(gray, block_size=adaptive_block_size, C=adaptive_C)
        meta["steps"].append("binarize_adaptive")

    # Morphology if requested
    if morph_open or morph_close:
        gray = apply_morph(gray, open_size=morph_open, close_size=morph_close)
        meta["steps"].append(f"morph(o={morph_open},c={morph_close})")

    # EasyOCR expects 3‑channel images
    rgb_for_ocr = ensure_rgb(gray)

    return rgb_for_ocr, meta


# Convenience: config-driven wrapper

def preprocess_with_config(img_rgb: np.ndarray, cfg: PipelineConfig = PipelineConfig()) -> Tuple[np.ndarray, Dict]:
    return preprocess_pipeline(
        img_rgb,
        target_dpi=cfg.target_dpi,
        source_dpi=cfg.source_dpi,
        grayscale=cfg.grayscale,
        binarize=cfg.binarize,
        deskew_flag=cfg.deskew,
        denoise_flag=cfg.denoise,
        contrast_flag=cfg.contrast,
        remove_borders_flag=cfg.remove_borders_flag,
        morph_open=cfg.morph_open,
        morph_close=cfg.morph_close,
        adaptive_block_size=cfg.adaptive_block_size,
        adaptive_C=cfg.adaptive_C,
    )


__all__ = [
    "PipelineConfig",
    "preprocess_pipeline",
    "preprocess_with_config",
    "ensure_rgb",
    "to_gray",
    "enhance_contrast",
    "denoise_gray",
    "binarize_otsu",
    "binarize_adaptive",
    "deskew",
    "remove_borders",
    "resize_to_dpi",
    "apply_morph",
]
