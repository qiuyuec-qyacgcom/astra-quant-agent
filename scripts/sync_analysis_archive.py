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
    from astra_backend.analysis_sync import sync_archive, _resolve_active_venue
    if not enabled():
        return 0
    try:
        with cycle_lock(str(db_path().with_suffix(".archive.lock"))):
            venue = _resolve_active_venue()
            if venue == "binance":
                # 币安：adapter 直接取持仓（positions_history 已预聚合平仓周期），
                # 凭证未配置时由 adapter 抛异常 → fault + 返回 1（fail-closed）。
                from astra_backend.exchanges import registry
                adapter = registry.get_adapter("binance")
                positions = adapter.positions() or []
                sync_archive(positions=positions, venue="binance")
            else:
                from scripts.okx_runtime import current_environment
                from scripts import okx_rest
                env = current_environment()
                if not env.configured:
                    raise RuntimeError("Analysis archive requires configured OKX credentials")
                # Current positions are required to preserve partial/holding evidence.
                positions = okx_rest.positions()
                if not isinstance(positions, list):
                    raise ValueError("Invalid current positions response")
                sync_archive(positions=positions, venue="okx")
        return 0
    except LockContended:
        print("[analysis_archive] skipped: another archive job is running")
        return 0
    except Exception as exc:
        fault(exc, "standalone_archive_sync")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
