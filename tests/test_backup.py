import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))

spec = importlib.util.spec_from_file_location('backup', Path(__file__).resolve().parents[1]/'scripts/backup.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def event(t, total, last):
    def usage(n): return {'input_tokens':n-2,'output_tokens':2,'cached_input_tokens':n-3,'total_tokens':n}
    return {'timestamp':t,'type':'event_msg','payload':{'type':'token_count','info':{
        'total_token_usage':usage(total),'last_token_usage':usage(last)}}}


class BackupTests(unittest.TestCase):
    def test_scan_writes_inventory_without_backing_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.jsonl'
            source.write_text('{"message":"keep original"}\n')
            probes = [{'app': 'claude', 'path': str(source), 'exists': True}]
            with mock.patch.object(b, 'ROOT', root), mock.patch.object(b, 'discover', return_value=([('claude', source)], probes)), mock.patch.object(b, 'backup') as backup, mock.patch.object(sys, 'argv', ['backup.py', '--scan']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(b.main(), 0)
            backup.assert_not_called()
            self.assertFalse((root / 'backups').exists())
            self.assertEqual(source.read_text(), '{"message":"keep original"}\n')
            self.assertEqual(json.loads((root / 'reports/discovery.json').read_text()), {
                'probes': probes, 'files': [{'app': 'claude', 'source': str(source)}]})

    def test_usage_dedup_inheritance_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            original_root = b.ROOT
            b.ROOT = Path(directory)
            self.addCleanup(setattr,b,'ROOT',original_root)
            snapshot = b.ROOT/'snapshot'; snapshot.mkdir()
            manifest={'files':[], 'errors':[]}
            def fixture(name,app,rows):
                p=snapshot/name
                p.write_text(''.join(json.dumps(x)+'\n' for x in rows))
                manifest['files'].append({'app':app,'path':name,'source':name,'bytes':p.stat().st_size})
            a=event('1',1000,10); second=event('2',1020,20)
            fixture('a.jsonl','codex',[a,second,event('3',1020,20)])
            fixture('fork.jsonl','codex',[a,second,event('4',1050,30)])
            fixture('claude.jsonl','claude',[
                {'message':{'id':'same','usage':{'input_tokens':1,'output_tokens':2,'cache_creation_input_tokens':3,'cache_read_input_tokens':4}}},
                {'message':{'id':'same','usage':{'input_tokens':1,'output_tokens':5,'cache_creation_input_tokens':3,'cache_read_input_tokens':4}}}])
            fixture('workbuddy.jsonl','workbuddy',[
                {'id':str(i),'providerData':{'conversationRequestId':'r','messageId':'m','stepSeq':1},
                 'message':{'usage':{'input_tokens':100,'output_tokens':10,'total_tokens':110,'cache_read_input_tokens':90}}} for i in range(2)])
            with contextlib.redirect_stdout(io.StringIO()): result=b.usage_report(snapshot,manifest)
            self.assertEqual(result['usage']['codex']['total_tokens'],60)
            self.assertEqual(result['usage']['claude']['total_tokens'],13)
            self.assertEqual(result['usage']['workbuddy']['total_tokens'],110)
            self.assertEqual(result['observed_total_tokens'],183)

    def test_sqlite_backup_includes_uncheckpointed_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.sqlite'; target=Path(directory)/'backup.sqlite'
            c=sqlite3.connect(source)
            try:
                c.execute('pragma journal_mode=wal')
                c.execute('pragma wal_autocheckpoint=0')
                c.execute('create table messages(body text)')
                c.execute("insert into messages values('original conversation')");c.commit()
                b.copy_file(source,target)
                with contextlib.closing(sqlite3.connect(target)) as copy:
                    self.assertEqual(copy.execute('select body from messages').fetchone()[0],'original conversation')
            finally:c.close()

    @unittest.skipUnless(shutil.which('zstd'), 'zstd not installed')
    def test_compressed_jsonl_is_read_without_modifying_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive=Path(directory)/'old.jsonl.zst'
            result=subprocess.run(['zstd','-q','-c'],input=b'{"type":"user"}\n',capture_output=True,check=True)
            archive.write_bytes(result.stdout)
            before=b.digest(archive)
            self.assertEqual(list(b.records(archive,[])),[{'type':'user'}])
            self.assertEqual(b.digest(archive),before)

    def test_raw_bytes_preserve_incomplete_line(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.jsonl';target=Path(directory)/'copy.jsonl'
            source.write_bytes(b'{"type":"user"}\n{"partial":')
            b.copy_file(source,target)
            self.assertEqual(source.read_bytes(),target.read_bytes())
            errors=[]; self.assertEqual(len(list(b.records(target,errors))),1)
            self.assertEqual(errors[0]['line'],2)

if __name__=='__main__': unittest.main()
