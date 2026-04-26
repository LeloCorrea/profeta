"""
Daily SQLite backup — keeps last 7 snapshots in data/backups/.
Run via: python -m app.backup
Scheduled via systemd profeta-backup.timer
"""
import logging
import shutil
from datetime import datetime
from pathlib import Path

from app.config import PROJECT_ROOT

BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
DB_PATH = PROJECT_ROOT / "data" / "profeta.db"
MAX_BACKUPS = 7

logger = logging.getLogger("profeta.backup")


def run_backup() -> Path | None:
    if not DB_PATH.exists():
        logger.error("Banco não encontrado em %s — backup abortado.", DB_PATH)
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"profeta_{timestamp}.db"

    shutil.copy2(DB_PATH, dest)
    logger.info("Backup criado: %s (%.1f KB)", dest.name, dest.stat().st_size / 1024)

    _prune_old_backups()
    return dest


def _prune_old_backups() -> None:
    backups = sorted(BACKUP_DIR.glob("profeta_*.db"))
    for old in backups[:-MAX_BACKUPS]:
        old.unlink()
        logger.info("Backup antigo removido: %s", old.name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    result = run_backup()
    if not result:
        raise SystemExit(1)
