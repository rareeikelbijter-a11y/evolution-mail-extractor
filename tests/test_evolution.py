"""Fake Camel tests cover orchestration; native compatibility needs a real run."""
from contextlib import closing, redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from evolution_mail_extractor.convert import convert, maildir
from evolution_mail_extractor.evolution import prepare_evolution


def fake_camel(mismatch=False, corrupt=False, bad_save=False):
    class Settings:
        def set_property(self, name, value): pass
        def list_properties(self): return []
    class Folder:
        def __init__(self, root, name):
            self.root,self.name=root,name
            self.base=root if name=='Inbox' else root/('.'+name.replace('/','.'))
            self.files={p.name.split(':2,',1)[0]:p for state in ('cur','new') for p in (self.base/state).iterdir()}
            self.flags={u:196608 for u in self.files}
        def get_uids(self): return list(self.flags)+(['wrong'] if mismatch else [])
        def set_message_flags(self, uid, mask, value): self.flags[uid]=(self.flags[uid]&~mask)|value
        def get_message_flags(self, uid): return self.flags[uid]
        def synchronize_sync(self, expunge, cancellable):
            assert expunge is False
            q='"'+self.name.replace('"','""')+'"'
            with closing(sqlite3.connect(self.root/'folders.db')) as c, c:
                for uid,f in self.flags.items():
                    c.execute('INSERT OR REPLACE INTO '+q+' (uid,flags,junk,deleted) VALUES (?,?,?,0)',(uid,0 if bad_save else f,bool(f&128)))
            if corrupt: next(iter(self.files.values())).write_bytes(b'changed')
            return True
    class Store:
        def __init__(self, root): self.root=root
        def get_user_data_dir(self): return str(self.root)
        def ref_settings(self): return Settings()
        def get_folder_sync(self, name, flags, cancellable): return Folder(self.root,name)
    class Session:
        def __init__(self, **kw): self.root=Path(kw['user_data_dir'])/'local'
        def add_service(self, *args): return Store(self.root)
    return SimpleNamespace(init=lambda *a:0,provider_init=lambda:None,Session=Session,ProviderType=SimpleNamespace(STORE=1),StoreGetFolderFlags=int)


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.src=self.root/'source'; maildir(self.src)
        (self.src/'..maildir++').write_bytes(b'maildir++ 1\n')
        (self.src/'cur/junk:2,').write_bytes(b'Subject: junk\n\nOriginal bytes\n')
        (self.src/'cur/unknown:2,S').write_bytes(b'Subject: unknown\n\nKeep me\n')
        with closing(sqlite3.connect(self.src/'folders.db')) as c, c:
            c.execute('CREATE TABLE Inbox (uid TEXT PRIMARY KEY,flags INTEGER,junk INTEGER,deleted INTEGER)')
            c.execute('INSERT INTO Inbox VALUES (?,?,?,?)',('junk',128|1|8|2,1,1))
        self.export=self.root/'export'; self.dest=self.root/'prepared'
        with redirect_stdout(io.StringIO()): convert(self.src,self.export,'to-mbox',separate_junk=True,allow_missing_metadata=True)
    def prepare(self, **kw):
        with redirect_stdout(io.StringIO()): return prepare_evolution(self.export,self.dest,camel=fake_camel(**kw))
    def test_flags_unknown_and_bytes(self):
        original=(self.export/'conversion-manifest.json').read_bytes()
        r=self.prepare()
        self.assertEqual((r['messages'],r['flags_verified'],r['unknown_metadata'],r['junk']),(2,1,1,1))
        self.assertFalse((self.dest/'PREPARATION-INCOMPLETE.txt').exists())
        self.assertTrue((self.dest/'PREPARATION-VERIFIED.json').exists())
        for p in self.src.rglob('*'):
            if p.is_file() and p.name!='folders.db': self.assertEqual(p.read_bytes(),(self.dest/'mail/local'/p.relative_to(self.src)).read_bytes())
        with closing(sqlite3.connect(self.dest/'mail/local/folders.db')) as c:
            f,j=c.execute('SELECT flags,junk FROM Inbox WHERE uid=?', ('junk',)).fetchone()
        self.assertEqual(f&(1|2|8|16|128),1|8|128); self.assertEqual(j,1)
        self.assertEqual(original,(self.export/'conversion-manifest.json').read_bytes())
    def test_uid_mismatch_stops(self):
        with self.assertRaisesRegex(RuntimeError,'UID mismatch'): self.prepare(mismatch=True)
        self.assertTrue((self.dest/'PREPARATION-INCOMPLETE.txt').exists())
        self.assertFalse((self.dest/'PREPARATION-VERIFIED.json').exists())
    def test_saved_flags_mismatch_stops(self):
        with self.assertRaisesRegex(RuntimeError,'Saved database'): self.prepare(bad_save=True)
        self.assertFalse((self.dest/'PREPARATION-VERIFIED.json').exists())
    def test_content_damage_stops(self):
        with self.assertRaisesRegex(RuntimeError,'checksum'): self.prepare(corrupt=True)
        self.assertFalse((self.dest/'PREPARATION-VERIFIED.json').exists())
    def test_existing_destination(self):
        self.dest.mkdir()
        with self.assertRaisesRegex(RuntimeError,'already exist'): self.prepare()
    def test_nested_destination(self):
        self.dest=self.export/'nested'
        with self.assertRaisesRegex(RuntimeError,'non-nested'): self.prepare()
    def test_missing_bindings_no_output(self):
        with patch('evolution_mail_extractor.evolution.load_camel',side_effect=RuntimeError('Camel bindings unavailable')):
            with self.assertRaisesRegex(RuntimeError,'bindings'): prepare_evolution(self.export,self.dest)
        self.assertFalse(self.dest.exists())
