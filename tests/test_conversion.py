import json
import mailbox
from pathlib import Path
import tempfile
import unittest
from evolution_mail_extractor.convert import convert, maildir, MANIFEST

class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.src = self.base / 'source'
        self.src.mkdir()

    def sample(self):
        root = self.src / '..Personal.Shopping.Orders'
        maildir(root)
        messages = [
            b'Subject: sample\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=x\n\n--x\nContent-Type: text/plain\n\nFrom someone\n>From quoted\n>>From twice\n--x\nContent-Type: application/octet-stream\nContent-Transfer-Encoding: base64\n\nAAECA//+\n--x--\n',
            b'Subject: CRLF\r\n\r\nFrom body\r\n\r\n',
            b'Subject: no final newline\n\nbinary\x00\xff',
            b'Subject: existing status\nX-Mozilla-Status: 0001\n\nbody\n',
            b'',
        ]
        for i, raw in enumerate(messages):
            (root / ('cur' if i % 2 else 'new') / f'{i}:2,SFR').write_bytes(raw)
        maildir(self.src / '..Empty')
        return root, messages

    def test_exact_round_trip(self):
        root, messages = self.sample()
        out = self.base / 'export'
        self.assertEqual(convert(self.src, out, 'to-mbox'), (2, 5))
        box = mailbox.mbox(str(out / 'Inbox.sbd/Personal.sbd/Shopping.sbd/Orders'), create=False)
        self.addCleanup(box.close)
        self.assertEqual(len(box), 5)
        self.assertEqual(box[0]['X-Mozilla-Status'], '0007')
        restored = self.base / 'restored'
        self.assertEqual(convert(out, restored, 'to-maildir'), (2, 5))
        for p in root.rglob('*'):
            if p.is_file():
                self.assertEqual(p.read_bytes(), (restored / p.relative_to(self.src)).read_bytes())
        self.assertTrue((restored / '..Empty/tmp').is_dir())

    def test_refuse_existing_or_nested_destination(self):
        self.sample()
        existing = self.base / 'existing'
        existing.mkdir()
        with self.assertRaises(FileExistsError):
            convert(self.src, existing, 'to-mbox')
        # Parent destination fails the non-nested safeguard first (ValueError).

    def test_nested(self):
        with self.assertRaises(ValueError):
            convert(self.src, self.src / 'out', 'to-mbox')

    def test_symlink(self):
        root, _ = self.sample()
        (root / 'new/link').symlink_to(root / 'new/0:2,SFR')
        with self.assertRaises(ValueError):
            convert(self.src, self.base / 'export', 'to-mbox')
        self.assertTrue((self.base / 'export/CONVERSION-INCOMPLETE.txt').exists())

    def test_damage_detected(self):
        self.sample()
        out = self.base / 'export'
        convert(self.src, out, 'to-mbox')
        manifest = json.loads((out / MANIFEST).read_text())
        folder = next(f for f in manifest['folders'] if f['messages'])
        with (out / folder['mbox']).open('r+b') as stream:
            stream.seek(folder['messages'][0]['offset'] + folder['messages'][0]['prefix'])
            stream.write(b'!')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            convert(out, self.base / 'restored', 'to-maildir')

    def test_external(self):
        box = self.base / 'Inbox'
        box.write_bytes(b'From sender Thu Jan  1 00:00:00 1970\nSubject: external\nX-Mozilla-Status: 0007\n\n>From escaped\n>>From quoted\n\n')
        out = self.base / 'import'
        self.assertEqual(convert(box, out, 'to-maildir', 'mboxrd'), (1, 1))
        messages = list((out / 'cur').iterdir())
        self.assertTrue(messages[0].name.endswith(':2,FRS'))
        self.assertIn(b'\nFrom escaped\n>From quoted', messages[0].read_bytes())

    def test_manifest_path_traversal(self):
        self.sample()
        out = self.base / 'export'
        convert(self.src, out, 'to-mbox')
        data = json.loads((out / MANIFEST).read_text())
        data['folders'][0]['source'] = '../escape'
        (out / MANIFEST).write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            convert(out, self.base / 'restore', 'to-maildir')
        self.assertFalse((self.base / 'escape').exists())

if __name__ == '__main__':
    unittest.main()
