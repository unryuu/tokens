import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import daily
import backup


class DailyTests(unittest.TestCase):
    def test_timezone_and_invalid_timestamps(self):
        self.assertEqual(daily.day('2026-10-21T16:00:00Z'),'2026-10-22')
        self.assertEqual(daily.day(1792598400000),'2026-10-22')
        self.assertEqual(daily.day('1976-03-31T16:00:51.725Z'),'unknown')
        self.assertEqual(daily.day(None),'unknown')

    def test_imported_baseline_not_charged_but_continuation_is(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'rollout-example.jsonl'
            def row(total,inp,out,t):
                return {'timestamp':t,'type':'event_msg','payload':{'type':'token_count','info':{
                    'total_token_usage':{'input_tokens':inp,'output_tokens':out,'total_tokens':total},
                    'last_token_usage':{'input_tokens':inp,'output_tokens':out,'total_tokens':inp+out if inp else total}}}}
            p.write_text('\n'.join(json.dumps(x) for x in [row(500,0,0,'2026-10-21T15:00:00Z'),row(610,100,10,'2026-10-21T16:00:00Z')]))
            events,_,coverage,_=daily.collect(root,{'files':[{'path':p.name,'source':p.name,'app':'codex'}]},backup.records)
            self.assertEqual(len(events),1)
            self.assertEqual(next(iter(events.values()))['total_tokens'],110)
            self.assertEqual(next(iter(events.values()))['date'],'2026-10-22')
            self.assertEqual(coverage['codex_total_only_tokens_excluded'],500)

    def test_cross_device_union_is_idempotent_and_keeps_max_stream_update(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);eid=daily.event_id('claude','message123')
            e=dict(event_id=eid,date='2026-10-22',app='claude',input_tokens=30,output_tokens=1,cache_read_tokens=20,cache_write_tokens=0,total_tokens=31)
            for name,amount in [('a',31),('b',35)]:
                dest=root/'statistics/devices'/name
                daily.write_csv(dest/'events/2026-10.csv',daily.EVENT_FIELDS,[{**e,'total_tokens':amount}])
                (dest/'device.json').write_text(json.dumps({'rules_version':daily.RULES_VERSION}))
            result=daily.merge(root)
            self.assertEqual(result['total_tokens'],35)
            self.assertEqual(result['unique_calls'],1)
            self.assertEqual(result['input_output_total_tokens'],31)
            self.assertEqual(result['output_tokens'],1)
            self.assertEqual(result['metrics_by_app']['claude']['output_tokens'],1)
            before=(root/'statistics/daily.csv').read_bytes()
            self.assertEqual(daily.merge(root),result)
            self.assertEqual((root/'statistics/daily.csv').read_bytes(),before)

    def test_public_export_contains_no_transcript_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);e=dict(event_id=daily.event_id('codex','secret-original-id'),date='2026-10-22',app='codex',**{k:1 for k in daily.FIELDS})
            daily.write_local(root,root/'reports',{'x':e})
            dest=daily.export(root)
            self.assertEqual(set(json.loads((dest/'device.json').read_text(encoding='utf-8'))),{'device_id','platform','timezone','schema_version','rules_version','generated_at'})
            text=''.join(p.read_text(encoding='utf-8') for p in (root/'statistics').rglob('*') if p.is_file())
            self.assertNotIn('secret-original-id',text)
            self.assertNotIn(str(Path.home()),text)
            self.assertEqual(set(next(iter(daily.load_events((dest/'events').glob('*.csv')).values()))),set(daily.EVENT_FIELDS))


class HistoricalUsageTests(unittest.TestCase):
    def test_truncated_source_keeps_old_calls_in_daily_totals(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def make(name,identity):
                snapshot=root/'backups'/name;snapshot.mkdir(parents=True)
                p=snapshot/'record.jsonl';p.write_text(json.dumps({'timestamp':'2026-10-22T01:00:00Z','message':{'id':identity,'usage':{'input_tokens':10,'output_tokens':2}}})+'\n')
                m={'files':[{'app':'claude','path':p.name,'source':'same-source.jsonl','sha256':backup.digest(p),'bytes':p.stat().st_size}]}
                (snapshot/'manifest.json').write_text(json.dumps(m));return snapshot,m
            make('old','old-message');snapshot,m=make('new','new-message')
            events,_,_,_=daily.collect_history(root,snapshot,m,backup.records)
            self.assertEqual(len(events),2)
            self.assertEqual(sum(e['total_tokens'] for e in events.values()),24)

if __name__=='__main__':unittest.main()
