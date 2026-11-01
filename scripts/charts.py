"""Build a reusable chart fragment from published daily totals; no raw logs needed."""
import csv
import datetime as dt
import json
from pathlib import Path


def json_for_html(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


def build(root, directory=None):
    root=Path(root)
    base=Path(directory) if directory is not None else root/'statistics'
    with (base/'daily-total.csv').open(encoding='utf-8',newline='') as f:
        rows=list(csv.DictReader(f))
    daily={r['date']:int(r['input_output_total_tokens']) for r in rows if r['date']!='unknown'}
    points=[];cumulative=0
    if daily:
        date=dt.date.fromisoformat(min(daily));end=dt.date.fromisoformat(max(daily))
        while date<=end:
            key=date.isoformat();value=daily.get(key,0);cumulative+=value
            points.append({'date':key,'tokens':value,'cumulative':cumulative})
            date+=dt.timedelta(days=1)
    data={'metric':'input_output_total_tokens','timezone':'UTC+08:00','rows':points,
          'unknown_date_tokens':sum(int(r['input_output_total_tokens']) for r in rows if r['date']=='unknown')}
    template=Path(__file__).parent/'templates/token-charts.html'
    content=template.read_text(encoding='utf-8').replace('__TOKEN_CHART_DATA__',json_for_html(data))
    out=base/'token-charts.html';out.write_text(content,encoding='utf-8')
    build_card(root,directory=base)
    return out

def build_card(root, directory=None):
    """Standalone card with per-app totals and optional per-model breakdown."""
    root=Path(root);days={}
    base=Path(directory) if directory is not None else root/'statistics'
    path=base/'daily.csv'
    with path.open(encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            if r['date']=='unknown':continue
            day=days.setdefault(r['date'],{'date':r['date'],'tokens':0,'output':0,'calls':0,'apps':{},'models':{}})
            app={'tokens':int(r['input_output_total_tokens']),'output':int(r['output_tokens']),'calls':int(r['calls'])}
            day['apps'][r['app']]=app
            for k in ('tokens','output','calls'):day[k]+=app[k]
    model_path=base/'daily-model.csv'
    if model_path.exists():
        with model_path.open(encoding='utf-8',newline='') as f:
            for r in csv.DictReader(f):
                if r['date']=='unknown':continue
                day=days[r['date']]
                model=day['models'].setdefault(r['model'],{'tokens':0,'output':0,'calls':0,'apps':{}})
                app={'tokens':int(r['input_output_total_tokens']),'output':int(r['output_tokens']),'calls':int(r['calls'])}
                model['apps'][r['app']]=app
                for k in ('tokens','output','calls'):model[k]+=app[k]
        for day in days.values():
            for field in ('tokens','output','calls'):
                if sum(m[field] for m in day['models'].values())!=day[field]:
                    raise ValueError('Model breakdown does not match daily totals: '+day['date'])
    else:
        for day in days.values():
            day['models']={'unknown':{k:day[k] for k in ('tokens','output','calls','apps')}}
    data={'metric':'input_output_total_tokens','timezone':'UTC+08:00','days':[days[k] for k in sorted(days)]}
    template=Path(__file__).parent/'templates/usage-card.html'
    content=template.read_text(encoding='utf-8').replace('__USAGE_CARD_DATA__',json_for_html(data))
    out=base/'usage-card.html';out.write_text(content,encoding='utf-8')
    return out

if __name__=='__main__':print(build(Path(__file__).resolve().parents[1]))
