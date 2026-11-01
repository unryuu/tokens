import csv
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import charts

class ChartDataTests(unittest.TestCase):
    def test_cumulative_uses_input_output_and_fills_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'statistics').mkdir()
            (root/'statistics/daily-total.csv').write_text('date,input_output_total_tokens,output_tokens\n2026-10-01,100,2\n2026-10-03,300,3\nunknown,500,4\n')
            (root/'statistics/daily.csv').write_text('date,app,calls,input_output_total_tokens,output_tokens\n')
            text=charts.build(root).read_text(encoding='utf-8')
            data=json.loads(re.search(r'id="agent-token-history-data">(.*?)</script>',text).group(1))
            self.assertEqual([r['tokens'] for r in data['rows']],[100,0,300])
            self.assertEqual([r['cumulative'] for r in data['rows']],[100,100,400])
            self.assertEqual(data['unknown_date_tokens'],500)
            self.assertEqual(data['metric'],'input_output_total_tokens')
            self.assertNotIn('__TOKEN_CHART_DATA__',text)

    def test_card_merges_apps_per_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'statistics').mkdir()
            (root/'statistics/daily.csv').write_text('date,app,calls,input_output_total_tokens,output_tokens\n2026-10-01,claude,2,100,5\n2026-10-01,codex,3,50,1\nunknown,codex,1,9,9\n')
            text=charts.build_card(root).read_text(encoding='utf-8')
            data=json.loads(re.search(r'id="usage-data">(.*?)</script>',text).group(1))
            self.assertEqual(len(data['days']),1)
            day=data['days'][0]
            self.assertEqual((day['tokens'],day['output'],day['calls']),(150,6,5))
            self.assertEqual(day['apps']['codex']['tokens'],50)
            self.assertNotIn('__USAGE_CARD_DATA__',text)
            self.assertEqual(day['models']['unknown']['tokens'],150)

    def test_model_filters_keep_app_splits_and_escape_embedded_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'statistics';base.mkdir()
            (base/'daily.csv').write_text('date,app,calls,input_output_total_tokens,output_tokens\n2026-10-01,claude,2,100,5\n2026-10-01,codex,3,50,1\n')
            name='model</script><img src=x>'
            with (base/'daily-model.csv').open('w',newline='') as f:
                w=csv.writer(f);w.writerow(['date','app','model','calls','input_output_total_tokens','output_tokens'])
                w.writerows([['2026-10-01','claude',name,2,100,5],['2026-10-01','codex','unknown',3,50,1]])
            text=charts.build_card(root).read_text()
            data=json.loads(re.search(r'id="usage-data">(.*?)</script>',text).group(1))
            day=data['days'][0]
            self.assertEqual(day['models'][name]['apps']['claude']['tokens'],100)
            self.assertEqual(sum(m['tokens'] for m in day['models'].values()),day['tokens'])
            self.assertNotIn(name,text)
            self.assertIn('data-model-filter',text)
            self.assertIn('data-view="models"',text)
            (base/'daily-model.csv').write_text('date,app,model,calls,input_output_total_tokens,output_tokens\n')
            with self.assertRaisesRegex(ValueError,'Model breakdown'):charts.build_card(root)

if __name__=='__main__':unittest.main()
