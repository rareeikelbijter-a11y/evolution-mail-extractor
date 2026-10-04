import tempfile
from pathlib import Path
import unittest
from evolution_mail_extractor.convert import convert, maildir, mailbox_path, storage_component

class EmptyComponentTests(unittest.TestCase):
    def test_reported_trailing_dot(self):
        self.assertEqual(mailbox_path('..Personal.Financial.Bank.CUA.Years.2223.').as_posix(),
            'Inbox.sbd/Personal.sbd/Financial.sbd/Bank.sbd/CUA.sbd/Years.sbd/2223.sbd/~empty')

    def test_placeholder_does_not_alias_real_name(self):
        self.assertNotEqual(storage_component(''), storage_component('~empty'))
        self.assertEqual(storage_component('~empty'), '~7Eempty')

    def test_empty_components_round_trip_without_merge(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source = base / 'source'; source.mkdir()
            names = ['..Personal.Years.2223.', '..Personal.Years.2223',
                     '.Sent..Council', '.Sent.~empty.Council']
            for i, name in enumerate(names):
                folder = source / name; maildir(folder)
                (folder / 'cur/msg:2,S').write_bytes(f'Subject: {i}\n\nbody {i}\n'.encode())
            exported = base / 'export'; restored = base / 'restored'
            self.assertEqual(convert(source, exported, 'to-mbox'), (4, 4))
            self.assertEqual(convert(exported, restored, 'to-maildir'), (4, 4))
            for name in names:
                self.assertEqual((source / name / 'cur/msg:2,S').read_bytes(),
                                 (restored / name / 'cur/msg:2,S').read_bytes())

if __name__ == '__main__':
    unittest.main()
