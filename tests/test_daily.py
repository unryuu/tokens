import json
import itertools
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
            self.assertEqual(set(json.loads((dest/'device.json').read_text())),{'device_id','platform','timezone','schema_version','rules_version','generated_at'})
            text=''.join(p.read_text() for p in (root/'statistics').rglob('*') if p.is_file())
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


class ModelUsageTests(unittest.TestCase):
    def event(self, model='unknown'):
        return dict(event_id=daily.event_id('claude','same-call'),date='2026-10-22',app='claude',
                    model=model,input_tokens=10,output_tokens=2,cache_read_tokens=3,cache_write_tokens=0,total_tokens=12)

    def test_model_enrichment_is_order_independent_and_conflicts_do_not_split_calls(self):
        for order in itertools.permutations(['unknown','model-a','model-b']):
            merged=None
            for model in order:merged=daily.combine(merged,self.event(model))
            self.assertEqual(merged['model'],'conflicting')
            self.assertEqual(merged['total_tokens'],12)
            self.assertEqual(merged['event_id'],self.event()['event_id'])
        self.assertEqual(daily.combine(self.event(),self.event('model-a'))['model'],'model-a')
        self.assertEqual(daily.combine(self.event('model-a'),self.event())['model'],'model-a')

    def test_models_are_attributed_per_call_and_codex_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manifest={'files':[]}
            def add(app,rows):
                path=root/(app+'.jsonl');path.write_text('\n'.join(json.dumps(r) for r in rows))
                manifest['files'].append({'app':app,'path':path.name,'source':path.name})
            def token(n):
                return {'timestamp':f'2026-10-22T01:00:0{n}Z','type':'event_msg','payload':{'type':'token_count',
                    'info':{'total_token_usage':{'input_tokens':10*n,'output_tokens':2*n,'total_tokens':12*n}}}}
            add('codex',[token(1),{'type':'turn_context','payload':{'model':'gpt-first'}},token(2),
                         {'type':'turn_context','payload':{'model':'gpt-second'}},token(3),
                         {'type':'turn_context','payload':{}},token(4)])
            add('claude',[{'timestamp':'2026-10-22T01:00:00Z','message':{'id':'msg','model':'claude-test',
                          'usage':{'input_tokens':1,'output_tokens':2,'cache_read_input_tokens':3}}}])
            add('workbuddy',[{'timestamp':'2026-10-22T01:00:00Z','message':{'id':'wb','usage':{'input_tokens':4,'output_tokens':2}},
                              'providerData':{'model':'deepseek-alias','requestModelId':'different-alias'}}])
            events,errors,coverage,_=daily.collect(root,manifest,backup.records)
            self.assertFalse(errors)
            self.assertEqual(sorted(e['model'] for e in events.values()),
                             sorted(['unknown','gpt-first','gpt-second','unknown','claude-test','deepseek-alias']))
            self.assertEqual(coverage['events_without_model'],2)
            self.assertEqual(sum(e['input_tokens']+e['output_tokens'] for e in events.values()),60)

    def test_old_and_new_device_ledgers_merge_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old=root/'statistics/devices/old/events/2026-10.csv'
            new=root/'statistics/devices/new/events/2026-10.csv'
            e=self.event();legacy={k:v for k,v in e.items() if k!='model'}
            other={**legacy,'event_id':daily.event_id('claude','old-only')}
            daily.write_csv(old,daily.LEGACY_EVENT_FIELDS,[legacy,other])
            daily.write_csv(new,daily.EVENT_FIELDS,[self.event('model-a')])
            for path in (old,new):
                (path.parent.parent/'device.json').write_text(json.dumps({'schema_version':1 if path==old else 2,'rules_version':2}))
            summary=daily.merge(root)
            self.assertEqual(summary['unique_calls'],2)
            self.assertEqual(summary['metrics_by_model']['model-a']['input_output_total_tokens'],12)
            self.assertEqual(summary['model_coverage_input_output_tokens'],{'known':12,'unknown':12,'conflicting':0})
            original=old.read_bytes()
            private=daily.merge(root,extra_events={e['event_id']:self.event('model-a')},output_root=root/'reports')
            self.assertEqual(private,summary)
            self.assertEqual(old.read_bytes(),original)
            self.assertTrue((root/'reports/statistics/usage-card.html').exists())
            self.assertEqual(daily.merge(root),summary)

    def test_model_daily_totals_equal_existing_counters_and_export_upgrades_own_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);events={'x':self.event('model-a')}
            daily.write_local(root,root/'reports',events)
            device=daily.export(root)
            self.assertEqual(json.loads((device/'device.json').read_text())['schema_version'],2)
            self.assertTrue((device/'daily-model.csv').exists())
            self.assertEqual(daily.aggregate(events,by_model=True)[0]['input_output_total_tokens'],12)
            self.assertEqual(daily.load_events((device/'events').glob('*.csv'))[self.event()['event_id']]['model'],'model-a')

if __name__=='__main__':unittest.main()
