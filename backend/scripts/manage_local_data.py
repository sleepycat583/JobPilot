"""Backup or restore the single-user local runtime data.

Examples:
    uv run python scripts/manage_local_data.py backup --output ..\career-backup.zip
    uv run python scripts/manage_local_data.py restore --input ..\career-backup.zip --force
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services.local_data import backup_local_data, local_data_paths, restore_local_data


def main() -> int:
    parser = argparse.ArgumentParser(description="管理求职工作台的本地数据备份")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup", help="创建备份 zip")
    backup.add_argument("--output", required=True, type=Path)
    restore = subparsers.add_parser("restore", help="恢复备份 zip")
    restore.add_argument("--input", required=True, type=Path)
    restore.add_argument("--force", action="store_true", help="将现有目标移到 .pre-restore-* 后再替换")
    args = parser.parse_args()
    paths = local_data_paths(get_settings())
    if args.command == "backup":
        manifest = backup_local_data(paths, args.output)
        print(f"backup complete: entries={len(manifest['entries'])}")
    else:
        result = restore_local_data(paths, args.input, force=args.force)
        print(f"restore complete: entries={len(result['restored'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
