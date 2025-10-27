# apps/api/lottina_api/utils/__init__.py
# Nur "leichte" Utils hier importieren – KEIN OCR / easyocr / cv2!
# Hintergrund: Das Modul wird beim App-Start geladen. Alles Schwere (OCR)
# muss lazy direkt in den Endpoints importiert werden.

from .uploads import allowed, save_upload  # schlanke Upload-Helfer
from .parsers import extract_fields, confidence_stats, extract_addr_city_from_text  # Textauswertung

__all__ = ["allowed", "save_upload", "extract_fields", "confidence_stats", "extract_addr_city_from_text"]
