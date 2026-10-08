import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from evolution_mail_extractor.convert import maildir

class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'evolution_mail_extractor', *map(str, args)], capture_output=True, text=True)

    def test_legacy_and_conversion_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'mail'
            maildir(source)
            (source / 'new/a').write_bytes(b'Subject: test\n\nbody\n')
            result = self.run_cli(source)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Candidate message files: 1', result.stdout)
            result = self.run_cli('to-mbox', source, base / 'mbox')
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_cli('to-maildir', base / 'mbox', base / 'restored')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((base / 'restored/new/a').read_bytes(), (source / 'new/a').read_bytes())
            result = self.run_cli('prepare-evolution', base / 'mbox', base / 'prepared')
            self.assertEqual(result.returncode, 1)
            self.assertIn('Junk-aware v2', result.stderr)
            self.assertFalse((base / 'prepared').exists())

    def test_external_requires_dialect(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            box = base / 'mailbox'
            box.touch()
            result = self.run_cli('to-maildir', box, base / 'out')
            self.assertEqual(result.returncode, 1)
            self.assertIn('--mbox-dialect', result.stderr)

    def test_help(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('to-mbox', result.stdout)
        self.assertIn('prepare-evolution', result.stdout)

if __name__ == '__main__':
    unittest.main()
