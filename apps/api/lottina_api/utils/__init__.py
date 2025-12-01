# apps/api/lottina_api/utils/__init__.py
# Nur "leichte" Utils hier importieren – keine schweren Computer-Vision-Abhängigkeiten!
# Hintergrund: Das Modul wird beim App-Start geladen. Alles Rechenintensive
# sollte direkt in den Endpoints geladen werden.

from .uploads import allowed, save_upload  # schlanke Upload-Helfer
from .parsers import extract_addr_city_from_text, extract_fields  # Textauswertung

__all__ = ["allowed", "save_upload", "extract_addr_city_from_text", "extract_fields"]
