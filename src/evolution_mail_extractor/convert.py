"""Offline conversion; original archives are never modified."""
import hashlib
import json
import mailbox
import os
from pathlib import Path
import re
import uuid
import sqlite3
from contextlib import ExitStack, closing

MANIFEST = 'conversion-manifest.json'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def safe(root, name):
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError(f'Unsafe relative path: {name!r}')
    target = root / p
    if any(x.is_symlink() for x in [target, *target.parents]):
        raise ValueError(f'Symbolic link in path: {name!r}')
    return target

def walk(root):
    def fail(error):
        raise error
    for base, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs + files:
            if (Path(base) / name).is_symlink():
                raise ValueError(f'Symbolic link encountered: {Path(base) / name}')
        yield Path(base), sorted(dirs), sorted(files)

def folder_parts(relative, can_escape_dots=False):
    """Decode Evolution's flat hierarchy, including the Inbox special case."""
    if relative == '.':
        return ['Inbox']
    if '/' not in relative and relative.startswith('..'):
        parts = ['Inbox'] + relative[2:].split('.')
    elif '/' not in relative and relative.startswith('.'):
        parts = relative[1:].split('.')
    else:
        parts = relative.split('/')
    if can_escape_dots:
        # Evolution enables _HH escapes through its ..maildir++ marker.
        parts = [re.sub(r'_([0-9A-Fa-f]{2})',
                        lambda m: chr(int(m.group(1), 16)), part) for part in parts]
    # Legacy archives may contain empty components (for example a trailing dot).
    # Preserve their positions; storage_component supplies an unambiguous name.
    return parts

def storage_component(name):
    """Use readable names without percent or leading-dot URI ambiguity.

    ~HH byte escapes are injective because a literal tilde is escaped too.
    Original names always remain available in the archival manifest.
    """
    if name == '':
        return '~empty'
    permitted = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_()'"
    return ''.join(chr(byte) if byte in permitted else f'~{byte:02X}'
                   for byte in name.encode('utf-8'))

def mailbox_path(relative, can_escape_dots=False):
    parts = folder_parts(relative, can_escape_dots)
    encoded = [storage_component(part) for part in parts]
    return Path(*[part + '.sbd' for part in encoded[:-1]], encoded[-1])

def evolution_metadata(source):
    """Read flags from an offline recovery database; never write to it."""
    database = safe(source, 'folders.db')
    if not database.is_file():
        raise ValueError('--separate-junk requires folders.db in the source.')
    for suffix in ('-wal', '-journal'):
        sidecar = safe(source, 'folders.db' + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('Database has a pending journal. Use a recovery copy made after Evolution closes.')
    raw = database.read_bytes()
    records = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Evolution database integrity check failed.')
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (name,) in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = {row[1] for row in connection.execute('PRAGMA table_info(' + quoted + ')')}
            if not {'uid', 'flags', 'junk', 'deleted'} <= columns:
                continue
            rows = {}
            for uid, flags, junk in connection.execute('SELECT uid, flags, junk FROM ' + quoted):
                if not isinstance(uid, str) or not isinstance(flags, int):
                    raise ValueError(f'Invalid database flags in {name!r}.')
                if bool(flags & 128) != bool(junk):
                    raise ValueError(f'Inconsistent Junk flags in {name!r}, UID {uid!r}.')
                rows[uid] = flags
            records[name] = rows
    if database.read_bytes() != raw:
        raise ValueError('Database changed during reading. Close Evolution and retry with a new destination.')
    return records, raw

def export(source, destination, separate_junk=False, allow_missing_metadata=False):
    folders = []
    dot_marker = safe(source, '..maildir++')
    can_escape_dots = dot_marker.is_file()
    records, database_raw = evolution_metadata(source) if separate_junk else ({}, None)
    occupied = set()
    count = junk_count = junk_read = relocated_count = 0
    consensus_count = 0
    missing_metadata = []
    uid_index = {}
    for table, rows in records.items():
        for uid, flags in rows.items():
            uid_index.setdefault(uid, []).append((table, flags))
    with ExitStack() as stack:
        streams = {}
        def stream_for(relative):
            if relative not in streams:
                target = safe(destination, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb'):
                    pass
                streams[relative] = target
            return streams[relative]
        for base, dirs, files in walk(source):
            if not {'cur', 'new', 'tmp'} <= set(dirs):
                continue
            relative = base.relative_to(source).as_posix()
            target_rel = mailbox_path(relative, can_escape_dots).as_posix()
            if separate_junk and Path(target_rel).parts[0] in ('Junk-Recovery', 'Junk-Recovery.sbd'):
                raise ValueError('Source folder conflicts with reserved Junk-Recovery name.')
            if target_rel in occupied:
                raise ValueError(f'Folder mapping collision: {relative}')
            occupied.add(target_rel)
            stream_for(target_rel)
            database_folder = '/'.join(folder_parts(relative, can_escape_dots))
            messages = []
            for state in ('cur', 'new'):
                for path in sorted((base / state).iterdir()):
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(f'Unsupported message entry: {path}')
                    raw = path.read_bytes()
                    flags = path.name.split(':2,', 1)[1] if ':2,' in path.name else ''
                    status = (1 if 'S' in flags else 0) | (2 if 'R' in flags else 0) | (4 if 'F' in flags else 0)
                    message_mbox = target_rel
                    metadata = {}
                    if separate_junk:
                        uid = path.name.split(':2,', 1)[0]
                        db_flags = records.get(database_folder, {}).get(uid)
                        matched_folder = database_folder
                        if db_flags is None:
                            matches = uid_index.get(uid, [])
                            if not matches and allow_missing_metadata:
                                missing_metadata.append({'folder': relative, 'file': f'{state}/{path.name}', 'uid': uid})
                                metadata['database_match'] = 'missing'
                                matched_folder = None
                            elif not matches or len({flags for _, flags in matches}) != 1:
                                reason = 'Conflicting database flags' if matches else 'No database flags'
                                raise ValueError(f'{reason} for folder {database_folder!r}, UID {uid!r}; stopped to avoid misclassification.')
                            else:
                                matched_folder, db_flags = matches[0]
                                if len(matches) > 1:
                                    metadata['evolution_database_folders'] = [name for name, _ in matches]
                                    metadata['database_match'] = 'identical-flags-consensus'
                                    matched_folder = None
                                    consensus_count += 1
                                else:
                                    relocated_count += 1
                        if matched_folder is not None:
                            metadata['evolution_database_folder'] = matched_folder
                        if db_flags is not None:
                            status = (1 if db_flags & 16 else 0) | (2 if db_flags & 1 else 0) | (4 if db_flags & 8 else 0)
                            metadata['evolution_flags'] = db_flags
                            if db_flags & 128:
                                message_mbox = 'Junk-Recovery.sbd/' + target_rel
                                junk_count += 1
                                junk_read += bool(db_flags & 16)
                    header = re.split(br'\r?\n\r?\n', raw, maxsplit=1)[0]
                    existing = re.search(br'(?im)^X-Mozilla-Status:[ \t]*([0-9a-f]+)', header)
                    if separate_junk and 'evolution_flags' in metadata and re.search(br'(?im)^X-Mozilla-Status:', header):
                        if existing is None or int(existing[1], 16) != status:
                            raise ValueError(f'Existing Mozilla status conflicts with database: {path}')
                    prefix = b'' if re.search(br'(?im)^X-Mozilla-Status:', header) else f'X-Mozilla-Status: {status:04x}\n'.encode()
                    encoded = re.sub(br'(?m)^(>*From )', br'>\1', raw)
                    output_path = stream_for(message_mbox)
                    with output_path.open('ab') as output:
                        output.write(b'From MAILER-DAEMON Thu Jan  1 00:00:00 1970\n')
                        offset = output.tell()
                        output.write(prefix + encoded)
                        output.write(b'\n\n')
                    entry = {'file': f'{state}/{path.name}', 'offset': offset,
                             'length': len(prefix + encoded), 'prefix': len(prefix),
                             'sha256': digest(raw), **metadata}
                    if message_mbox != target_rel:
                        entry['mbox'] = message_mbox
                    messages.append(entry)
                    count += 1
            folders.append({'source': relative, 'mbox': target_rel, 'messages': messages})
    for relative in streams:
        parent = (destination / relative).parent
        while parent != destination:
            parent.with_name(parent.name[:-4]).touch(exist_ok=True)
            parent = parent.parent
    if not folders:
        raise ValueError('No complete Maildir folders found.')
    manifest = {'format': 'evolution-mail-extractor-v2' if separate_junk else 'evolution-mail-extractor-v1',
        'folder_mapping': 'evolution-hierarchy-v2',
        'dot_escape_marker_hex': dot_marker.read_bytes().hex() if can_escape_dots else None,
        'folders': folders}
    if separate_junk:
        (destination / 'evolution-metadata').mkdir()
        (destination / 'evolution-metadata/folders.db').write_bytes(database_raw)
        manifest['database'] = {'file': 'evolution-metadata/folders.db', 'sha256': digest(database_raw)}
        manifest['database_uid_fallback_matches'] = relocated_count
        manifest['database_consensus_matches'] = consensus_count
        manifest['missing_metadata'] = missing_metadata
        if missing_metadata:
            (destination / 'missing-metadata.json').write_text(json.dumps(missing_metadata, indent=2))
        manifest['junk_summary'] = {'messages': junk_count, 'read': junk_read, 'unread': junk_count - junk_read}
        print(f'Junk separated: {junk_count:,}; read: {junk_read:,}; unread: {junk_count - junk_read:,}')
        print(f'Database records matched by unique UID in a different folder: {relocated_count:,}')
        print(f'Database records matched by identical flags across multiple tables: {consensus_count:,}')
        print(f'Messages with unknown Junk status (kept in original folders): {len(missing_metadata):,}')
        print('Junk-Recovery is an ordinary folder, not Thunderbird spam training.')
    (destination / MANIFEST).write_text(json.dumps(manifest, indent=2))
    return len(folders), count

def maildir(root):
    for name in ('cur', 'new', 'tmp'):
        (root / name).mkdir(parents=True, exist_ok=True)

def restore_manifest(source, destination):
    manifest = json.loads(safe(source, MANIFEST).read_text())
    if manifest.get('format') not in ('evolution-mail-extractor-v1', 'evolution-mail-extractor-v2'):
        raise ValueError('Unsupported conversion manifest.')
    count = 0
    used = set()
    for folder in manifest['folders']:
        root = destination if folder['source'] == '.' else safe(destination, folder['source'])
        maildir(root)
        with ExitStack() as stack:
            streams = {}
            for message in folder['messages']:
                box_name = message.get('mbox', folder['mbox']) if manifest['format'] == 'evolution-mail-extractor-v2' else folder['mbox']
                if box_name not in streams:
                    streams[box_name] = stack.enter_context(safe(source, box_name).open('rb'))
                stream = streams[box_name]
                target = safe(root, message['file'])
                if target.parent not in (root / 'cur', root / 'new'):
                    raise ValueError('Invalid message location in manifest.')
                offset, length, prefix = (message[k] for k in ('offset', 'length', 'prefix'))
                if not all(isinstance(v, int) and v >= 0 for v in (offset, length, prefix)) or prefix > length:
                    raise ValueError('Invalid message offsets.')
                if offset + length > os.fstat(stream.fileno()).st_size:
                    raise ValueError('Truncated mbox file or invalid message offsets.')
                stream.seek(offset)
                encoded = stream.read(length)
                if len(encoded) != length:
                    raise ValueError('Truncated mbox file.')
                raw = re.sub(br'(?m)^>(>*From )', br'\1', encoded[prefix:])
                if digest(raw) != message['sha256']:
                    raise ValueError('Message checksum mismatch; archive changed or damaged.')
                with target.open('xb') as output:
                    output.write(raw)
                count += 1
        if root in used:
            raise ValueError('Duplicate folder in manifest.')
        used.add(root)
    marker_hex = manifest.get('dot_escape_marker_hex')
    if marker_hex is not None:
        with (destination / '..maildir++').open('xb') as output:
            output.write(bytes.fromhex(marker_hex))
    if 'database' in manifest:
        data = safe(source, manifest['database']['file']).read_bytes()
        if digest(data) != manifest['database']['sha256']:
            raise ValueError('Database checksum mismatch.')
        with (destination / 'folders.db').open('xb') as output:
            output.write(data)
    return len(used), count

def import_external(source, destination, dialect):
    if not dialect:
        raise ValueError('External mbox import requires --mbox-dialect mboxrd or mboxo.')
    boxes = []
    if source.is_file():
        boxes = [(source, [source.name])]
    else:
        for base, dirs, files in walk(source):
            for name in files:
                if name.endswith(('.msf', '.json')) or name in ('msgFilterRules.dat', 'popstate.dat'):
                    continue
                rel = (base / name).relative_to(source)
                if any(not p.endswith('.sbd') for p in rel.parts[:-1]):
                    continue
                boxes.append((base / name, [p[:-4] for p in rel.parts[:-1]] + [name]))
    count = 0
    occupied = set()
    (destination / '..maildir++').write_text('maildir++ 1\n')
    for path, parts in boxes:
        # Encode Evolution's literal dots and underscores; keep Inbox special.
        encoded_parts = [part.replace('_', '_5F').replace('.', '_2E') for part in parts]
        if parts[0].lower() == 'inbox':
            name = '.' if len(parts) == 1 else '..' + '.'.join(encoded_parts[1:])
        else:
            name = '.' + '.'.join(encoded_parts)
        root = destination if name == '.' else safe(destination, name)
        if root in occupied:
            raise ValueError(f'External folder mapping collision: {path}')
        occupied.add(root)
        maildir(root)
        box = mailbox.mbox(str(path), create=False)
        try:
            if path.stat().st_size and not len(box):
                raise ValueError(f'Not a recognizable mbox: {path}')
            for key in box.iterkeys():
                raw = box.get_bytes(key)
                pattern = br'(?m)^>(>*From )' if dialect == 'mboxrd' else br'(?m)^>(From )'
                raw = re.sub(pattern, br'\1', raw)
                message = mailbox.mboxMessage(raw)
                try:
                    status = int(message.get('X-Mozilla-Status', '0'), 16)
                except ValueError:
                    status = 0
                flags = ''.join(flag for bit, flag in ((1, 'S'), (2, 'R'), (4, 'F')) if status & bit)
                if 'R' in message.get('Status', '') and 'S' not in flags:
                    flags += 'S'
                state = 'cur' if flags else 'new'
                filename = uuid.uuid4().hex + ':2,' + ''.join(sorted(flags))
                (root / state / filename).write_bytes(raw)
                count += 1
        finally:
            box.close()
    if not boxes:
        raise ValueError('No mbox files found.')
    return len(boxes), count

def convert(source, destination, direction, dialect=None, separate_junk=False, allow_missing_metadata=False):
    if allow_missing_metadata and not separate_junk:
        raise ValueError('--allow-missing-metadata requires --separate-junk.')
    source = source.expanduser()
    destination = destination.expanduser()
    if source.is_symlink() or not source.exists():
        raise ValueError('Source must exist and must not be a symbolic link.')
    if direction == 'to-mbox' and not source.is_dir():
        raise ValueError('Maildir source must be a directory.')
    if source.is_dir() and (source / 'CONVERSION-INCOMPLETE.txt').exists():
        raise ValueError('Source is an incomplete conversion.')
    # Check every ancestor before resolving, including destination parent links.
    if any(p.is_symlink() for p in [source, *source.parents, destination, *destination.parents]):
        raise ValueError('Symbolic links in source or destination paths are not supported.')
    src, dst = source.resolve(), destination.resolve()
    if dst == src or src in dst.parents or dst in src.parents:
        raise ValueError('Source and destination must be separate, non-nested locations.')
    destination.mkdir(parents=True, exist_ok=False)
    marker = destination / 'CONVERSION-INCOMPLETE.txt'
    marker.write_text('Conversion did not finish. Do not import this directory. Use a new destination when retrying.\n')
    if direction == 'to-mbox':
        result = export(source, destination, separate_junk, allow_missing_metadata)
    elif source.is_dir() and (source / MANIFEST).exists():
        result = restore_manifest(source, destination)
    else:
        result = import_external(source, destination, dialect)
    marker.unlink()
    return result
