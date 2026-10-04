"""Offline conversion; original archives are never modified."""
import hashlib
import json
import mailbox
import os
from pathlib import Path
import re
import uuid

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

def export(source, destination):
    folders = []
    dot_marker = safe(source, '..maildir++')
    can_escape_dots = dot_marker.is_file()
    occupied = set()
    count = 0
    for base, dirs, files in walk(source):
        if not {'cur', 'new', 'tmp'} <= set(dirs):
            continue
        relative = base.relative_to(source).as_posix()
        target_rel = mailbox_path(relative, can_escape_dots).as_posix()
        if target_rel in occupied:
            raise ValueError(f'Folder mapping collision: {relative}')
        occupied.add(target_rel)
        target = safe(destination, target_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        messages = []
        with target.open('xb') as output:
            for state in ('cur', 'new'):
                for path in sorted((base / state).iterdir()):
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(f'Unsupported message entry: {path}')
                    raw = path.read_bytes()
                    flags = path.name.split(':2,', 1)[1] if ':2,' in path.name else ''
                    status = (1 if 'S' in flags else 0) | (2 if 'R' in flags else 0) | (4 if 'F' in flags else 0)
                    # Existing Mozilla headers are left untouched, avoiding duplicate headers.
                    prefix = b'' if re.search(br'(?im)^X-Mozilla-Status:', raw.split(b'\n\n', 1)[0]) else f'X-Mozilla-Status: {status:04x}\n'.encode()
                    encoded = re.sub(br'(?m)^(>*From )', br'>\1', raw)
                    output.write(b'From MAILER-DAEMON Thu Jan  1 00:00:00 1970\n')
                    offset = output.tell()
                    output.write(prefix + encoded)
                    output.write(b'\n\n')
                    messages.append({'file': f'{state}/{path.name}', 'offset': offset,
                                     'length': len(prefix + encoded), 'prefix': len(prefix),
                                     'sha256': digest(raw)})
                    count += 1
        folders.append({'source': relative, 'mbox': target_rel, 'messages': messages})
    # Thunderbird needs an mbox alongside every .sbd parent directory.
    for folder in folders:
        parent = (destination / folder['mbox']).parent
        while parent != destination:
            parent.with_name(parent.name[:-4]).touch(exist_ok=True)
            parent = parent.parent
    if not folders:
        raise ValueError('No complete Maildir folders found.')
    (destination / MANIFEST).write_text(json.dumps({'format': 'evolution-mail-extractor-v1', 'folder_mapping': 'evolution-hierarchy-v2',
        'dot_escape_marker_hex': dot_marker.read_bytes().hex() if can_escape_dots else None,
        'folders': folders}, indent=2))
    return len(folders), count

def maildir(root):
    for name in ('cur', 'new', 'tmp'):
        (root / name).mkdir(parents=True, exist_ok=True)

def restore_manifest(source, destination):
    manifest = json.loads(safe(source, MANIFEST).read_text())
    if manifest.get('format') != 'evolution-mail-extractor-v1':
        raise ValueError('Unsupported conversion manifest.')
    count = 0
    used = set()
    for folder in manifest['folders']:
        root = destination if folder['source'] == '.' else safe(destination, folder['source'])
        maildir(root)
        with safe(source, folder['mbox']).open('rb') as stream:
            for message in folder['messages']:
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
    (destination / '..maildir++').write_text('maildir++ 1\n')
    for path, parts in boxes:
        # Encode Evolution's literal dots and underscores; keep Inbox special.
        encoded_parts = [part.replace('_', '_5F').replace('.', '_2E') for part in parts]
        if parts[0].lower() == 'inbox':
            name = '.' if len(parts) == 1 else '..' + '.'.join(encoded_parts[1:])
        else:
            name = '.' + '.'.join(encoded_parts)
        root = destination if name == '.' else safe(destination, name)
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

def convert(source, destination, direction, dialect=None):
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
        result = export(source, destination)
    elif source.is_dir() and (source / MANIFEST).exists():
        result = restore_manifest(source, destination)
    else:
        result = import_external(source, destination, dialect)
    marker.unlink()
    return result
