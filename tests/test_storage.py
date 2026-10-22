import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import backup
import storage


class StorageTests(unittest.TestCase):
    def test_incremental_preserves_original_and_retains_missing_sources(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'repo';root.mkdir();source=Path(d)/'conversation.jsonl';source.write_bytes(b'{"type":"user"}\n')
            with patch.object(backup,'ROOT',root),patch.object(backup,'discover',return_value=([('claude',source)],[])),contextlib.redirect_stdout(io.StringIO()):
                first,m1=backup.backup();second,m2=backup.backup()
                f1=first/m1['files'][0]['path'];f2=second/m2['files'][0]['path']
                self.assertTrue(os.path.samefile(f1,f2))
                self.assertEqual(m2['incremental']['new_bytes'],0)
                source.write_bytes(b'{"type":"user"}\n{"type":"assistant"}\n')
                third,m3=backup.backup()
                self.assertFalse(os.path.samefile(f1,third/m3['files'][0]['path']))
                self.assertEqual(f1.read_bytes(),b'{"type":"user"}\n')
            with patch.object(backup,'ROOT',root),patch.object(backup,'discover',return_value=([],[])),contextlib.redirect_stdout(io.StringIO()):
                fourth,m4=backup.backup()
                self.assertEqual(len(m4['files']),1)
                self.assertEqual((fourth/m4['files'][0]['path']).read_bytes(),source.read_bytes())
                backup.verify(fourth,m4)

    def test_compaction_does_not_touch_modified_backup(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'backups/old';p.mkdir(parents=True);f=p/'record';f.write_bytes(b'original')
            sha=backup.digest(f)
            (p/'manifest.json').write_text(json.dumps({'files':[{'path':'record','sha256':sha,'bytes':8}]}))
            f.write_bytes(b'user edit')
            with self.assertRaises(RuntimeError):storage.compact(root,backup.digest)
            self.assertEqual(f.read_bytes(),b'user edit')


class WindowsDiscoveryTests(unittest.TestCase):
    def test_windows_roaming_and_home_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)/'home';roaming=Path(d)/'Roaming';home.mkdir()
            source=home/'.claude-provider/projects/project/session.jsonl';source.parent.mkdir(parents=True);source.write_text('{}')
            editor=roaming/'Code/User/globalStorage/emptyWindowChatSessions/chat.jsonl';editor.parent.mkdir(parents=True);editor.write_text('{}')
            with patch.object(backup,'HOME',home),patch.object(backup,'ROOT',Path(d)),patch.object(backup.sys,'platform','win32'),patch.dict(os.environ,{'APPDATA':str(roaming)},clear=True):
                found,_=backup.discover()
            paths={p for _,p in found}
            self.assertIn(source,paths);self.assertIn(editor,paths)

if __name__=='__main__':unittest.main()
