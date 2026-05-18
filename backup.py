"""
Backup automático de la base de datos.
Crea una copia con timestamp en data/backups/.
Mantiene los últimos N (env BACKUP_KEEP, default 30).

Uso:
    python backup.py
o llamar a `do_backup()` desde código.
"""
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data" / "urgencias.db"
BACKUP_DIR = BASE / "data" / "backups"
KEEP = int(os.environ.get("BACKUP_KEEP", "30"))


def do_backup() -> Path:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"No existe la BD: {DB_PATH}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"urgencias_{ts}.db"
    # backup online (no requiere apagar el server)
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(dest))
    src.backup(dst)
    dst.close()
    src.close()
    _rotate()
    return dest


def _rotate():
    files = sorted(BACKUP_DIR.glob("urgencias_*.db"))
    for f in files[:-KEEP]:
        try:
            f.unlink()
        except OSError:
            pass


def listar() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    out = []
    for f in sorted(BACKUP_DIR.glob("urgencias_*.db"), reverse=True):
        stat = f.stat()
        out.append({"nombre": f.name, "size_kb": stat.st_size // 1024,
                    "ts": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
    return out


if __name__ == "__main__":
    dest = do_backup()
    print(f"Backup creado: {dest}  ({dest.stat().st_size / 1024:.1f} KB)")
    print(f"Backups disponibles: {len(list(BACKUP_DIR.glob('urgencias_*.db')))}")
