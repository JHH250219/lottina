from pathlib import Path
from werkzeug.utils import secure_filename
import uuid

ALLOWED_IMG = {"png","jpg","jpeg","webp"}
ALLOWED_PDF = {"pdf"}

def allowed(filename: str) -> bool:
    if not filename or "." not in filename: return False
    ext = filename.rsplit(".",1)[-1].lower()
    return ext in (ALLOWED_IMG | ALLOWED_PDF)

def save_upload(file_storage, subdir: Path) -> Path:
    subdir.mkdir(parents=True, exist_ok=True)
    fname = secure_filename(file_storage.filename or "")
    ext = (fname.rsplit(".",1)[-1] or "bin").lower()
    path = subdir / f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(path)
    return path
