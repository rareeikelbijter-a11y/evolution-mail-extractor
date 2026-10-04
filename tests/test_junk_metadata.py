from contextlib import closing
import json
import mailbox
from pathlib import Path
import sqlite3
import tempfile
import unittest
from evolution_mail_extractor.convert import convert, maildir, MANIFEST

class JunkMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.src = self.base / 'source'
        self.src.mkdir()
        maildir(self.src)
        maildir(self.src / '.Sent')
        self.raw = b'Subject: unread junk\r\n\r\nFrom body\r\n>From quote\r\n'
        (self.src / 'cur/junk:2,').write_bytes(self.raw)
        (self.src / 'cur/read:2,').write_bytes(b'Subject: read junk\n\nbody')
        (self.src / 'new/normal:2,').write_bytes(b'Subject: normal\n\nbody')
        (self.src / '.Sent/cur/sent:2,').write_bytes(b'Subject: sent\n\nbody')
        with closing(sqlite3.connect(self.src / 'folders.db')) as c, c:
            for table in ('Inbox', 'Sent'):
                c.execute(f'CREATE TABLE "{table}" (uid TEXT PRIMARY KEY, flags INTEGER, junk INTEGER, deleted INTEGER, read INTEGER)')
            c.executemany('INSERT INTO Inbox VALUES (?, ?, ?, ?, ?)', [('junk', 65664, 1, 0, 1), ('read', 144, 1, 0, 1), ('normal', 9, 0, 0, 0)])
            c.execute('INSERT INTO Sent VALUES (?, ?, ?, ?, ?)', ('sent', 16, 0, 0, 1))
        self.out = self.base / 'export'

    def export(self):
        return convert(self.src, self.out, 'to-mbox', separate_junk=True)

    def test_separation_flags_and_exact_restore(self):
        original_db = (self.src / 'folders.db').read_bytes()
        self.assertEqual(self.export(), (2, 4))
        manifest = json.loads((self.out / MANIFEST).read_text())
        self.assertEqual(manifest['junk_summary'], {'messages': 2, 'read': 1, 'unread': 1})
        for name, statuses in [('Inbox', ['0006']), ('Sent', ['0001']), ('Junk-Recovery.sbd/Inbox', ['0000', '0001'])]:
            box = mailbox.mbox(str(self.out / name), create=False)
            try:
                self.assertEqual([m['X-Mozilla-Status'] for m in box], statuses)
            finally:
                box.close()
        self.assertTrue((self.out / 'Junk-Recovery').is_file())
        restored = self.base / 'restored'
        self.assertEqual(convert(self.out, restored, 'to-maildir'), (2, 4))
        for file in self.src.rglob('*'):
            if file.is_file():
                self.assertEqual(file.read_bytes(), (restored / file.relative_to(self.src)).read_bytes())
        self.assertEqual((self.src / 'folders.db').read_bytes(), original_db)

    def test_missing_uid_stops(self):
        (self.src / 'new/unknown:2,').write_bytes(b'Subject: unknown\n\nbody')
        with self.assertRaisesRegex(ValueError, 'No database flags'):
            self.export()
        self.assertTrue((self.out / 'CONVERSION-INCOMPLETE.txt').exists())

    def test_missing_database_stops(self):
        (self.src / 'folders.db').unlink()
        with self.assertRaisesRegex(ValueError, 'requires folders.db'):
            self.export()

    def test_database_damage_detected_on_restore(self):
        self.export()
        with (self.out / 'evolution-metadata/folders.db').open('ab') as f:
            f.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'Database checksum'):
            convert(self.out, self.base / 'restore', 'to-maildir')

    def test_reserved_folder_collision_stops(self):
        maildir(self.src / '.Junk-Recovery')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            self.export()

    def test_inconsistent_junk_flags_stops(self):
        with closing(sqlite3.connect(self.src / 'folders.db')) as c, c:
            c.execute('UPDATE Inbox SET junk=0 WHERE uid=?', ('junk',))
        with self.assertRaisesRegex(ValueError, 'Inconsistent Junk'):
            self.export()

    def test_existing_status_conflict_stops(self):
        (self.src / 'cur/junk:2,').write_bytes(b'Subject: junk\nX-Mozilla-Status: 0001\n\nbody')
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            self.export()

    def test_pending_journal_stops(self):
        (self.src / 'folders.db-wal').write_bytes(b'pending')
        with self.assertRaisesRegex(ValueError, 'pending journal'):
            self.export()

    def test_unique_uid_in_renamed_folder(self):
        with closing(sqlite3.connect(self.src / 'folders.db')) as c, c:
            c.execute('ALTER TABLE Sent RENAME TO "Old Sent"')
        self.export()
        data = json.loads((self.out / MANIFEST).read_text())
        self.assertEqual(data['database_uid_fallback_matches'], 1)
        sent = next(f for f in data['folders'] if f['source'] == '.Sent')
        self.assertEqual(sent['messages'][0]['evolution_database_folder'], 'Old Sent')
        restored = self.base / 'restored'
        convert(self.out, restored, 'to-maildir')
        self.assertEqual((self.src / '.Sent/cur/sent:2,').read_bytes(), (restored / '.Sent/cur/sent:2,').read_bytes())

    def test_ambiguous_uid_in_renamed_folder_stops(self):
        with closing(sqlite3.connect(self.src / 'folders.db')) as c, c:
            c.execute('ALTER TABLE Sent RENAME TO "Old Sent"')
            c.execute('INSERT INTO Inbox VALUES (?, ?, ?, ?, ?)', ('sent', 0, 0, 0, 0))
        with self.assertRaisesRegex(ValueError, 'Conflicting database flags'):
            self.export()

    def test_identical_flags_consensus(self):
        with closing(sqlite3.connect(self.src / 'folders.db')) as c, c:
            c.execute('ALTER TABLE Sent RENAME TO "Old Sent"')
            c.execute('INSERT INTO Inbox VALUES (?, ?, ?, ?, ?)', ('sent', 16, 0, 0, 1))
        self.export()
        data = json.loads((self.out / MANIFEST).read_text())
        self.assertEqual(data['database_consensus_matches'], 1)
        sent = next(f for f in data['folders'] if f['source'] == '.Sent')
        message = sent['messages'][0]
        self.assertEqual(set(message['evolution_database_folders']), {'Inbox', 'Old Sent'})
        self.assertNotIn('evolution_database_folder', message)
        restored = self.base / 'restored'
        convert(self.out, restored, 'to-maildir')
        self.assertEqual((self.src / 'folders.db').read_bytes(), (restored / 'folders.db').read_bytes())

    def test_allow_missing_preserves_message_and_reports_unknown(self):
        raw = b'Subject: unknown\n\nFrom body'
        (self.src / 'cur/unknown:2,S').write_bytes(raw)
        self.assertEqual(convert(self.src, self.out, 'to-mbox', separate_junk=True, allow_missing_metadata=True), (2, 5))
        report = json.loads((self.out / 'missing-metadata.json').read_text())
        self.assertEqual(report, [{'folder': '.', 'file': 'cur/unknown:2,S', 'uid': 'unknown'}])
        data = json.loads((self.out / MANIFEST).read_text())
        self.assertEqual(data['junk_summary']['messages'], 2)
        inbox = next(f for f in data['folders'] if f['source'] == '.')
        unknown = next(m for m in inbox['messages'] if m['file'] == 'cur/unknown:2,S')
        self.assertEqual(unknown['database_match'], 'missing')
        self.assertNotIn('evolution_flags', unknown)
        restored = self.base / 'restored'
        convert(self.out, restored, 'to-maildir')
        self.assertEqual((restored / 'cur/unknown:2,S').read_bytes(), raw)

    def test_allow_missing_does_not_allow_conflicting_flags(self):
        with closing(sqlite3.connect(self.src / 'folders.db')) as c, c:
            c.execute('ALTER TABLE Sent RENAME TO "Old Sent"')
            c.execute('INSERT INTO Inbox VALUES (?, ?, ?, ?, ?)', ('sent', 0, 0, 0, 0))
        with self.assertRaisesRegex(ValueError, 'Conflicting database flags'):
            convert(self.src, self.out, 'to-mbox', separate_junk=True, allow_missing_metadata=True)

    def test_allow_missing_requires_metadata_mode(self):
        with self.assertRaisesRegex(ValueError, 'requires --separate-junk'):
            convert(self.src, self.out, 'to-mbox', allow_missing_metadata=True)
        self.assertFalse(self.out.exists())
