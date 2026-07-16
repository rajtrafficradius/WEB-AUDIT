"""Prepare or finalize the Kakawa v19 professional package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from exporters.package_builder import finalize_package, prepare_package

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = PROJECT_ROOT / "fixtures" / "replay" / "kakawa_acceptance_data.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--phase", choices=("prepare", "finalize"), default="prepare")
    args = parser.parse_args()
    if not args.data.resolve().is_relative_to(PROJECT_ROOT):
        parser.error("Data must remain inside the project root")
    with args.data.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if args.phase == "prepare":
        package_root = prepare_package(data, PROJECT_ROOT)
        print(json.dumps({"phase": "prepare", "package_root": str(package_root)}))
    else:
        zip_path, zip_checksum, internal_checksums = finalize_package(data, PROJECT_ROOT)
        print(
            json.dumps(
                {
                    "phase": "finalize",
                    "zip": str(zip_path),
                    "zip_checksum": str(zip_checksum),
                    "internal_checksums": str(internal_checksums),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
