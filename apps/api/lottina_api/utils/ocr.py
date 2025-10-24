import numpy as np
import fitz  # PyMuPDF
import easyocr
import re
from typing import Tuple, List, Dict, Optional
from .preprocessing import preprocess_pipeline

_reader = None


def normalize_ocr_text(s: str) -> str:
    if not s: return s
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\\s*\\.\\s*", ".", s)          # „17 . 09 . 2025“ -> „17.09.2025“
    s = re.sub(r"(\\d)\\s*:\\s*(\\d)", r"\\1:\\2", s)  # „16 : 30“ -> „16:30“
    s = re.sub(r"\\bUhr\\b", " Uhr", s, flags=re.I)     # sicherheitshalber
    return s

def get_reader():
    global _reader
    if _reader is None:
        # Sprachen anpassen, GPU nach Bedarf aktivieren
        _reader = easyocr.Reader(['de', 'en'], gpu=False)
    return _reader

def pdf_to_images(pdf_path: str, dpi: int = 180) -> List[np.ndarray]:
    """Render PDF-Seiten als RGB-ndarrays."""
    imgs: List[np.ndarray] = []
    # fitz kann als Kontextmanager genutzt werden
    with fitz.open(pdf_path) as doc:
        for p in doc:
            pix = p.get_pixmap(dpi=dpi, alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            imgs.append(img)  # RGB
    return imgs

def ocr_image(img_rgb: np.ndarray, *, mode: str | None = None):
    """
    mode: 'photo' | 'scan' | None (auto)
    Returns: text, confs, meta
    """
    # Heuristik: wenn vom PDF-Renderer -> scan, sonst photo
    if mode is None:
        # einfache Heuristik: sehr große Auflösung, viele Farben -> photo
        is_colorful = (img_rgb.std(axis=2).mean() < 60) is False  # grob
        mode = "photo" if is_colorful else "scan"

    if mode == "photo":
        pp_kwargs = dict(
            target_dpi=None,       # kein künstlicher DPI-Scale
            binarize=None,         # WICHTIG: keine Otsu/Adaptive
            deskew_flag=False,     # Fotos sind selten „Druck-schief“
            denoise_flag=False,    # Denoise lässt feine Kanten verschwinden
            contrast_flag=False,   # CLAHE färbt Kanten „hart“
        )
        mag_ratio = 2.0  # EasyOCR interner Upscale-Faktor
    else:  # scan
        pp_kwargs = dict(
            target_dpi=240,
            binarize="otsu",
            deskew_flag=True,
            denoise_flag=True,
            contrast_flag=True,
        )
        mag_ratio = 1.5

    img_pp, meta = preprocess_pipeline(img_rgb, **pp_kwargs)

    r = get_reader()
    result = r.readtext(
        img_pp,
        detail=1,
        paragraph=True,
        # ein bisschen aggressiver skalieren für kleine Schrift
        mag_ratio=mag_ratio,
        # toleranter bei Kontrast (hilft bei Fotos)
        contrast_ths=0.05 if mode == "photo" else 0.1,
        adjust_contrast=0.7 if mode == "photo" else 0.5,
    )

    texts = [x[1] for x in result]
    confs = []
    for x in result:
        try: confs.append(float(x[2]))
        except: pass

    text = "\n".join(t for t in texts if t).strip()
    meta["avg_conf"] = (sum(confs)/len(confs)) if confs else 0.0
    meta["mode"] = mode

    # Fallback: wenn Foto + schwache Conf → einmal mit leichter Schwellung probieren
    if mode == "photo" and meta["avg_conf"] < 0.55:
        img_pp2, _ = preprocess_pipeline(img_rgb, binarize=None, contrast_flag=True, denoise_flag=False)
        result2 = r.readtext(img_pp2, detail=1, paragraph=True, mag_ratio=2.0, contrast_ths=0.05, adjust_contrast=0.7)
        texts2 = [x[1] for x in result2]
        confs2 = []
        for x in result2:
            try: confs2.append(float(x[2]))
            except: pass
        avg2 = (sum(confs2)/len(confs2)) if confs2 else 0.0
        if avg2 > meta["avg_conf"]:
            text = "\n".join(t for t in texts2 if t).strip()
            confs = confs2
            meta["avg_conf"] = avg2
            meta["variant"] = "photo+clahe"

    return text, confs, meta


def ocr_pdf(
    pdf_path: str,
    *,
    render_dpi: int = 180,
    preprocess: bool = True,
    preprocess_kwargs: Optional[Dict] = None,
    paragraph: bool = True
) -> Tuple[str, List[float], Dict]:
    """
    OCR für ein gesamtes PDF.
    Returns: (joined_text, all_confidences, meta)
    """
    pages = pdf_to_images(pdf_path, dpi=render_dpi)
    all_texts: List[str] = []
    all_confs: List[float] = []
    per_page_meta: List[Dict] = []

    for img in pages:
        txt, confs, meta = ocr_image(
            img,
            preprocess=preprocess,
            preprocess_kwargs=preprocess_kwargs,
            paragraph=paragraph
        )
        if txt:
            all_texts.append(txt)
        all_confs.extend(confs)
        per_page_meta.append(meta)

    joined = "\n\n".join(all_texts).strip()
    meta_summary: Dict = {
        "pages": len(pages),
        "render_dpi": render_dpi,
        "avg_conf": (sum(all_confs) / len(all_confs)) if all_confs else 0.0,
        "per_page": per_page_meta,
    }
    return joined, all_confs, meta_summary
