"""Inventory and offline Maildir/mbox conversion."""
import argparse
from pathlib import Path
import sys
import sqlite3
from . import inventory
from .convert import convert

def main():
    # Retain the original single-source inventory command.
    if len(sys.argv) > 1 and sys.argv[1] not in ('to-mbox', 'to-maildir', 'prepare-evolution', '-h', '--help'):
        return inventory.main()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('to-mbox', 'to-maildir', 'prepare-evolution'):
        command = sub.add_parser(name)
        command.add_argument('source', type=Path)
        command.add_argument('destination', type=Path, help='New directory; must not already exist')
        if name == 'to-mbox':
            command.add_argument('--separate-junk', action='store_true', help='Read folders.db flags and separate Junk messages; requires metadata for every message')
            command.add_argument('--allow-missing-metadata', action='store_true', help='Keep messages without database records in original folders and report unknown Junk status')
        if name == 'to-maildir':
            command.add_argument('--mbox-dialect', choices=('mboxrd', 'mboxo'), help='Required for external mbox archives')
    args = parser.parse_args()
    try:
        if args.command == 'prepare-evolution':
            from .evolution import prepare_evolution
            prepare_evolution(args.source, args.destination)
            return 0
        folders, messages = convert(args.source, args.destination, args.command, getattr(args, 'mbox_dialect', None), getattr(args, 'separate_junk', False), getattr(args, 'allow_missing_metadata', False))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as error:
        print(f'Conversion failed: {error}', file=sys.stderr)
        return 1
    print(f'Conversion completed: {folders:,} folders; {messages:,} messages')
    print(f'Destination: {args.destination.expanduser()}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
