"""Independent read-only exchange archive job; never submits trading orders."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    from scripts.file_lock import cycle_lock, LockContended
    from astra_backend.analysis_store import db_path
    from astra_backend.analysis_capture import enabled, fault
    from astra_backend.analysis_sync import sync_archive
    from scripts.okx_runtime import current_environment
    from scripts import okx_rest
    if not enabled():
        return 0
    try:
        with cycle_lock(str(db_path().with_suffix(".archive.lock"))):
            env = current_environment()
            if not env.configured:
                raise RuntimeError("Analysis archive requires configured OKX credentials")
            # Current positions are required to preserve partial/holding evidence.
            positions = okx_rest.positions()
            if not isinstance(positions, list):
                raise ValueError("Invalid current positions response")
            sync_archive(positions=positions)
        return 0
    except LockContended:
        print("[analysis_archive] skipped: another archive job is running")
        return 0
    except Exception as exc:
        fault(exc, "standalone_archive_sync")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
