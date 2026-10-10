"""Regression coverage for the folder display failure found during native client testing."""
import json
import mailbox
from pathlib import Path
import tempfile
import unittest
from evolution_mail_extractor.convert import convert, maildir, mailbox_path, storage_component, MANIFEST

class FolderMappingTests(unittest.TestCase):
    def test_evolution_layout(self):
        expected = {
            '.': 'Inbox',
            '.Sent': 'Sent',
            '.Sent.38ANew.Council': 'Sent.sbd/38ANew.sbd/Council',
            '.Drafts': 'Drafts',
            '.Humans': 'Humans',
            '..Personal.Shopping': 'Inbox.sbd/Personal.sbd/Shopping',
            '..Sent': 'Inbox.sbd/Sent',
        }
        for source, target in expected.items():
            with self.subTest(source=source):
                self.assertEqual(mailbox_path(source).as_posix(), target)

    def test_names_never_use_percent_encoding(self):
        for name in ('100%', 'with#hash', '.hidden', '~2E', 'A&B', 'a.b', '日本語'):
            actual = storage_component(name)
            self.assertNotIn('%', actual)
            self.assertFalse(actual.startswith('.'))
            self.assertNotIn('/', actual)
        self.assertNotEqual(storage_component('a.b'), storage_component('a~2Eb'))
        self.assertEqual(storage_component("Ad's"), "Ad's")

    def test_dot_escapes_require_marker(self):
        self.assertEqual(mailbox_path('.Sent.A_2EB', True).as_posix(), 'Sent.sbd/A~2EB')
        self.assertEqual(mailbox_path('.Sent.A_2EB', False).as_posix(), 'Sent.sbd/A_2EB')

    def test_sent_inbox_and_subfolders_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / 'source'; source.mkdir()
            maildir(source)
            original = {'.': b'Subject: inbox\n\nInbox body\n'}
            (source / 'new/inbox:2,').write_bytes(original['.'])
            for name in ('.Sent', '.Sent.38ANew.Council', '..Personal', '.Humans'):
                folder = source / name; maildir(folder)
                raw = f'Subject: {name}\n\nFrom body\n'.encode()
                (folder / 'cur/msg:2,S').write_bytes(raw)
                original[name] = raw
            exported = base / 'export'
            self.assertEqual(convert(source, exported, 'to-mbox'), (5, 5))
            self.assertTrue((exported / 'Sent.sbd/38ANew').is_file())
            self.assertTrue((exported / 'Inbox.sbd/Personal').is_file())
            self.assertFalse((exported / '%2ESent').exists())
            box = mailbox.mbox(str(exported / 'Sent'), create=False)
            try:
                self.assertEqual(len(box), 1)
                self.assertEqual(box[0]['Subject'], '.Sent')
            finally:
                box.close()
            restored = base / 'restored'
            self.assertEqual(convert(exported, restored, 'to-maildir'), (5, 5))
            for name, raw in original.items():
                relative = 'new/inbox:2,' if name == '.' else f'{name}/cur/msg:2,S'
                self.assertEqual((restored / relative).read_bytes(), raw)

    def test_collision_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / 'source'; source.mkdir()
            for name in ('.Sent', 'Sent'):
                maildir(source / name)
            with self.assertRaisesRegex(ValueError, 'collision'):
                convert(source, base / 'export', 'to-mbox')

    def test_external_inbox_alias_collision_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'source'
            source.mkdir()
            raw = b'From sender Thu Jan  1 00:00:00 1970\nSubject: test\n\nbody\n\n'
            for name in ('Inbox', 'inbox'):
                (source / name).write_bytes(raw)
            destination = base / 'maildir'
            with self.assertRaisesRegex(ValueError, 'collision'):
                convert(source, destination, 'to-maildir', 'mboxrd')
            self.assertTrue((destination / 'CONVERSION-INCOMPLETE.txt').exists())
            for name in ('Inbox', 'inbox'):
                self.assertEqual((source / name).read_bytes(), raw)

    def test_legacy_export_still_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / 'source'; source.mkdir()
            maildir(source / '.Sent')
            raw = b'Subject: legacy\n\nbody\n'
            (source / '.Sent/new/msg:2,').write_bytes(raw)
            exported = base / 'export'; convert(source, exported, 'to-mbox')
            data = json.loads((exported / MANIFEST).read_text())
            (exported / 'Sent').rename(exported / '%2ESent')
            data.pop('folder_mapping')
            data['folders'][0]['mbox'] = '%2ESent'
            (exported / MANIFEST).write_text(json.dumps(data))
            convert(exported, base / 'restored', 'to-maildir')
            self.assertEqual((base / 'restored/.Sent/new/msg:2,').read_bytes(), raw)

    def test_external_sent_and_literal_dot_name_reexport(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); source = base / 'source'; source.mkdir()
            (source / 'Sent').write_bytes(b'From sender Thu Jan  1 00:00:00 1970\nSubject: sent\n\nbody\n\n')
            (source / 'Sent.sbd').mkdir()
            (source / 'Sent.sbd/a.b').write_bytes(b'From sender Thu Jan  1 00:00:00 1970\nSubject: child\n\nbody\n\n')
            restored = base / 'maildir'
            self.assertEqual(convert(source, restored, 'to-maildir', 'mboxrd'), (2, 2))
            self.assertTrue((restored / '.Sent.a_2Eb').is_dir())
            exported = base / 'export'
            self.assertEqual(convert(restored, exported, 'to-mbox'), (2, 2))
            self.assertTrue((exported / 'Sent').is_file())
            self.assertTrue((exported / 'Sent.sbd/a~2Eb').is_file())
            again = base / 'again'
            convert(exported, again, 'to-maildir')
            self.assertEqual((again / '..maildir++').read_bytes(), (restored / '..maildir++').read_bytes())
            final = base / 'final'
            convert(again, final, 'to-mbox')
            self.assertTrue((final / 'Sent.sbd/a~2Eb').is_file())

if __name__ == '__main__':
    unittest.main()
