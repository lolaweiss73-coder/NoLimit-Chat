"""Create a point-in-time backup of the chat database and uploaded images."""

import argparse
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def backup(data_dir: Path, destination: Path) -> Path:
    data_dir = data_dir.resolve()
    source = data_dir / "chat.db"
    if not source.is_file():
        raise FileNotFoundError(f"No chat database found in {data_dir}")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / "chat.db"
        with sqlite3.connect(source) as current, sqlite3.connect(snapshot) as copy:
            current.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Database snapshot failed integrity check")
            photos = [row[0] for row in copy.execute("SELECT id FROM photos")]
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "chat.db")
            for photo_id in photos:
                path = data_dir / "uploads" / photo_id
                if not path.is_file():
                    raise FileNotFoundError(f"Photo data missing: {photo_id}")
                archive.write(path, f"uploads/{photo_id}")
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Backup ZIP failed integrity check")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("CHAT_DATA_DIR", Path(__file__).resolve().parent)))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    name = f"no-limit-chat-data-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.zip"
    print(backup(args.data_dir, args.output or args.data_dir / "backups" / name))
