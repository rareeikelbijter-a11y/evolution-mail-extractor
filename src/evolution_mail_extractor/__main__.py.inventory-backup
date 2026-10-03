"""Read-only inventory of a recovered Evolution Maildir archive."""

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Recovery copy to scan")
    args = parser.parse_args()
    source = args.source.expanduser()

    if source.is_symlink() or not source.is_dir():
        parser.error("Source must be an existing directory, not a symlink.")

    folders = 0
    candidates = 0
    total_bytes = 0
    skipped_links = 0
    errors = []

    def record_error(error):
        errors.append(str(error))

    for root, dirs, files in os.walk(
        source, followlinks=False, onerror=record_error
    ):
        root = Path(root)

        for name in dirs[:]:
            if (root / name).is_symlink():
                dirs.remove(name)
                skipped_links += 1

        if {"cur", "new", "tmp"}.issubset(dirs):
            folders += 1

        if root.name not in {"cur", "new"}:
            continue

        # Only count files belonging to a complete Maildir structure.
        siblings = [root.parent / name for name in ("cur", "new", "tmp")]
        if not all(p.is_dir() and not p.is_symlink() for p in siblings):
            continue

        for name in files:
            path = root / name
            try:
                if path.is_symlink():
                    skipped_links += 1
                elif path.is_file():
                    total_bytes += path.stat().st_size
                    candidates += 1
            except OSError as error:
                record_error(error)

    print(f"Source: {source}")
    print(f"Maildir folders: {folders:,}")
    print(f"Candidate message files: {candidates:,}")
    print(f"Candidate file size: {total_bytes / (1024 ** 2):,.1f} MiB")
    print(f"Skipped symbolic links: {skipped_links:,}")
    print(f"Errors: {len(errors):,}")
    for error in errors[:10]:
        print(f"  {error}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
