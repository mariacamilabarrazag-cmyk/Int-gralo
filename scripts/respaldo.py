"""Copia de seguridad de los datos de Integralo (base de datos + logos y archivos de cada tienda).

    python -m scripts.respaldo            # crea data/respaldos/AAAA-MM-DD_HHMM.tar.gz
    python -m scripts.respaldo --dias 30  # conserva 30 días (por defecto 14)

La base de datos se copia con la API de respaldo de SQLite, así que es seguro
ejecutarlo con la app funcionando.
"""
import argparse
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=14)
    args = ap.parse_args()

    out_dir = config.DATA_DIR / "respaldos"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    target = out_dir / f"integralo_{stamp}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "integralo.db"
        src = sqlite3.connect(config.DB_PATH)
        dst = sqlite3.connect(db_copy)
        src.backup(dst)
        dst.close()
        src.close()
        with tarfile.open(target, "w:gz") as tar:
            tar.add(db_copy, arcname="integralo.db")
            if config.TENANTS_DIR.exists():
                tar.add(config.TENANTS_DIR, arcname="tiendas")
            key = config.DATA_DIR / "secret.key"
            if key.exists():
                tar.add(key, arcname="secret.key")

    cutoff = time.time() - args.dias * 86400
    for old in out_dir.glob("integralo_*.tar.gz"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
    print(f"✓ Respaldo creado: {target} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
