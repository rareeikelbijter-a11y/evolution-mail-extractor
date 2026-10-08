"""Apply archived read/replied/starred/Junk flags to an isolated Maildir copy.

Requires the previously verified v2 export manifest. Never installs the copy.
Deleted flags are deliberately not applied, so recovered mail stays accessible.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import warnings

from .convert import convert, folder_parts

MASK = 1 | 2 | 8 | 16 | 128
RECOVERED_MASK = MASK & ~2  # Keep known recovered messages visible.


def fail(message):
    raise RuntimeError(message)


def scan(root):
    result = {}
    def error(e):
        raise e
    for base, dirs, files in os.walk(root, onerror=error):
        base = Path(base)
        if any((base / n).is_symlink() for n in dirs + files):
            fail(f'Symbolic link found: {base}')
        if not {'cur', 'new', 'tmp'} <= set(dirs):
            continue
        relative = base.relative_to(root).as_posix()
        messages = {}
        for state in ('cur', 'new'):
            for path in (base / state).iterdir():
                if not path.is_file():
                    fail(f'Unexpected message entry: {path}')
                uid = path.name.split(':2,', 1)[0]
                if uid in messages:
                    fail(f'Duplicate UID in {relative}: {uid}')
                messages[uid] = hashlib.sha256(path.read_bytes()).hexdigest()
        result[relative] = messages
    return result


def load_camel():
    try:
        import gi
        gi.require_version('Camel', '1.2')
        from gi.repository import Camel
        return Camel
    except (ImportError, ValueError) as error:
        raise RuntimeError('Camel bindings unavailable. Use system python3 with PyGObject and Camel 1.2 introspection installed; the project venv may not expose them.') from error


def prepare_evolution(source, destination, camel=None):
    """Create a new isolated work directory from an unchanged v2 mbox export."""
    export = source.expanduser().absolute()
    work = destination.expanduser().absolute()
    for root in (work, export):
        if any(p.is_symlink() for p in (root, *root.parents)):
            fail(f'Symbolic link in path: {root}')
    work, export = work.resolve(), export.resolve()
    if not export.is_dir() or (export / 'CONVERSION-INCOMPLETE.txt').exists():
        fail('Source must be a complete exported archive.')
    if work.exists():
        fail('Destination must not already exist; use a new directory.')
    if work == export or work in export.parents or export in work.parents:
        fail('Source and destination must be separate, non-nested locations.')
    live_mail = Path.home() / '.local/share/evolution/mail'
    if work == live_mail or live_mail in work.parents or work in live_mail.parents:
        fail('Destination must be outside the live Evolution mail directory.')
    manifest = json.loads((export / 'conversion-manifest.json').read_text())
    if manifest.get('format') != 'evolution-mail-extractor-v2':
        fail('Expected an unchanged Junk-aware v2 export made with --separate-junk.')
    if not manifest.get('dot_escape_marker_hex') or 'database' not in manifest:
        fail('Native preparation requires the original Evolution Maildir marker and database.')
    Camel = camel if camel is not None else load_camel()
    work.mkdir(parents=True, exist_ok=False)
    marker = work / 'PREPARATION-INCOMPLETE.txt'
    ready = work / 'PREPARATION-VERIFIED.json'
    marker.write_text('Do not install this directory until preparation succeeds. Use a new destination after failure.\n')
    target = work / 'mail/local'
    print('Restoring archived message bytes...', flush=True)
    convert(export, target, 'to-maildir')
    for folder in ('.Drafts', '.Outbox', '.Sent', '.Templates'):
        for state in ('cur', 'new', 'tmp'):
            (target / folder / state).mkdir(parents=True, exist_ok=True)
    print('Checking restored message contents...', flush=True)
    before = scan(target)
    expected = {}
    entries = {}
    for folder in manifest['folders']:
        relative = folder['source']
        if relative in expected:
            fail('Duplicate manifest folder.')
        messages = {}
        for message in folder['messages']:
            uid = Path(message['file']).name.split(':2,', 1)[0]
            if uid in messages:
                fail('Duplicate manifest UID.')
            messages[uid] = message['sha256']
        expected[relative] = messages
        entries[relative] = folder['messages']
    if any(before.get(k) != v for k, v in expected.items()) or any(v for k, v in before.items() if k not in expected):
        fail('Staged messages do not match the verified archive.')
    total = sum(map(len, expected.values()))
    known = sum('evolution_flags' in m for f in entries.values() for m in f)
    junk = sum(bool(m.get('evolution_flags', 0) & 128) for f in entries.values() for m in f)
    for name in ('cache', 'certs'):
        (work / name).mkdir(exist_ok=True)
    if Camel.init(str(work / 'certs'), False) != 0:
        fail('Camel initialisation failed.')
    Camel.provider_init()
    Session = type('RecoveryPreparedSession', (Camel.Session,), {'__gtype_name__': 'RecoveryPreparedSession'})
    session = Session(user_data_dir=str(work / 'mail'), user_cache_dir=str(work / 'cache'), online=False)
    store = session.add_service('local', 'maildir', Camel.ProviderType.STORE)
    if store is None or Path(store.get_user_data_dir()).resolve() != target.resolve():
        fail('Unexpected Camel provider directory.')
    settings = store.ref_settings()
    settings.set_property('path', str(target))
    properties = {p.name for p in settings.list_properties()}
    for name in ('filter-junk', 'filter-inbox'):
        if name in properties:
            settings.set_property(name, False)
    warnings.filterwarnings('ignore', category=DeprecationWarning, module=__name__)
    completed = 0
    opened = set()
    # Each folder must contain exactly the archived UID set before any flags
    # are applied. Aliased names are rejected rather than silently merged.
    for index, (relative, messages) in enumerate(entries.items(), 1):
        if not messages:
            continue
        name = '/'.join(folder_parts(relative, manifest.get('dot_escape_marker_hex') is not None))
        if name in opened:
            fail(f'Native folder-name collision: {relative!r} -> {name!r}')
        opened.add(name)
        folder = store.get_folder_sync(name, Camel.StoreGetFolderFlags(0), None)
        if folder is None:
            fail(f'Camel could not open {relative!r} as {name!r}')
        actual_uids = set(folder.get_uids())
        if actual_uids != set(expected[relative]):
            fail(f'Camel folder UID mismatch: {relative!r} -> {name!r}; expected {len(expected[relative])}, found {len(actual_uids)}')
        for message in messages:
            if 'evolution_flags' not in message:
                continue
            uid = Path(message['file']).name.split(':2,', 1)[0]
            desired = message['evolution_flags'] & RECOVERED_MASK
            folder.set_message_flags(uid, MASK, desired)
        if not folder.synchronize_sync(False, None):
            fail(f'Could not save {name!r}')
        for message in messages:
            if 'evolution_flags' not in message:
                continue
            uid = Path(message['file']).name.split(':2,', 1)[0]
            if folder.get_message_flags(uid) & MASK != message['evolution_flags'] & RECOVERED_MASK:
                fail(f'Flag verification failed: {name!r}, {uid!r}')
            completed += 1
        quoted = '"' + name.replace('"', '""') + '"'
        with closing(sqlite3.connect((target / 'folders.db').as_uri()+'?mode=ro', uri=True)) as db:
            saved = {u: (f, j) for u, f, j in db.execute('SELECT uid, flags, junk FROM '+quoted)}
        for message in messages:
            if 'evolution_flags' not in message:
                continue
            uid = Path(message['file']).name.split(':2,', 1)[0]
            desired = message['evolution_flags'] & RECOVERED_MASK
            record = saved.get(uid)
            if record is None or record[0] & MASK != desired or bool(record[1]) != bool(desired & 128):
                fail(f'Saved database flag verification failed: {name!r}, {uid!r}')
        del folder
        if index % 50 == 0:
            print(f'Processed {index:,}/{len(entries):,} folders; verified flags for {completed:,} messages', flush=True)
    print('Rechecking every message checksum...', flush=True)
    after = scan(target)
    if before != after:
        fail('Message checksum or folder-content verification failed.')
    report = {'messages': total, 'source_folders': len(entries), 'flags_verified': completed,
              'junk': junk, 'unknown_metadata': total-known, 'message_bytes_unchanged': True,
              'restored_flags': ['read', 'replied', 'starred', 'junk'],
              'deleted_flags_applied': False}
    if completed != known:
        fail('Not all known flags were verified.')
    ready.write_text(json.dumps(report, indent=2)+'\n')
    marker.unlink()
    print(json.dumps(report, indent=2), flush=True)
    print('Evolution preparation verified. Prepared Maildir:', target)
    print('Review this copy before installing it in a closed Evolution profile.')
    return report


