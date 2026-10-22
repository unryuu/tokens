"""Usage events contain only hashed identities, dates, source labels and counters."""
import collections
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import platform
import uuid

TZ = dt.timezone(dt.timedelta(hours=8))
FIELDS = ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'total_tokens']
EVENT_FIELDS = ['event_id', 'date', 'app'] + FIELDS
DAILY_FIELDS = ['date', 'app', 'calls'] + FIELDS + ['input_output_total_tokens']
RULES_VERSION = 2


def event_id(app, identity):
    return hashlib.sha256(json.dumps([app, identity], sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def day(value):
    try:
        if isinstance(value, (int, float)):
            value = dt.datetime.fromtimestamp(value / 1000 if value > 1e11 else value, dt.timezone.utc)
        else:
            value = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if value.tzinfo is None: value = value.replace(tzinfo=TZ)
        value = value.astimezone(TZ)
        if not 2020 <= value.year <= dt.datetime.now(TZ).year + 1: return 'unknown'
        return value.date().isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return 'unknown'


def normalized(app, usage):
    def n(*names):
        for name in names:
            v = usage.get(name)
            if isinstance(v, int) and not isinstance(v, bool): return max(0, v)
        return 0
    out = dict(input_tokens=n('input_tokens'), output_tokens=n('output_tokens'),
               cache_read_tokens=n('cached_input_tokens', 'cache_read_input_tokens'),
               cache_write_tokens=n('cache_write_input_tokens', 'cache_creation_input_tokens'))
    if app == 'claude':
        out['input_tokens'] += out['cache_read_tokens'] + out['cache_write_tokens']
        out['total_tokens'] = out['input_tokens'] + out['output_tokens']
    else: out['total_tokens'] = n('total_tokens') or out['input_tokens'] + out['output_tokens']
    return out


def combine(old, new):
    """Idempotent union; streaming updates retain final counts, not their sum."""
    if old is None: return dict(new)
    if old['app'] != new['app']: raise ValueError('event ID has conflicting app')
    result = dict(old)
    valid = sorted(x for x in [old['date'], new['date']] if x != 'unknown')
    result['date'] = valid[0] if valid else 'unknown'
    for field in FIELDS: result[field] = max(int(old[field]), int(new[field]))
    return result


def collect(snapshot, manifest, read_records):
    events = {}; errors = []; coverage = collections.Counter(); codex_ids = set()
    import_ids = set()
    for e in manifest['files']:
        if Path(e['path']).name == 'external_agent_session_imports.json':
            data = json.loads((snapshot/e['path']).read_text(encoding='utf-8'))
            import_ids.update(x['imported_thread_id'] for x in data.get('records', []))
    coverage['claude_imported_codex_threads'] = len(import_ids)
    for entry in manifest['files']:
        p = snapshot / entry['path']; app = entry['app']
        if app not in {'claude','codex','workbuddy','codebuddy'}: continue
        if not p.name.endswith(('.jsonl', '.jsonl.zst')) or '/traces/' in str(entry['source']).replace('\\','/'): continue
        previous = {}; sid = p.name.removesuffix('.zst').removesuffix('.jsonl')[-36:]
        if app == 'codex' and p.name.startswith('rollout-'): codex_ids.add(sid)
        for row in read_records(p, errors):
            timestamp = row.get('timestamp'); payload = row.get('payload') or {}
            if app == 'codex':
                if row.get('type') == 'session_meta':
                    sid = payload.get('id', payload.get('session_id', sid)); codex_ids.add(sid)
                if row.get('type') != 'event_msg' or payload.get('type') != 'token_count': continue
                info = payload.get('info') or {}; current = info.get('total_token_usage')
                if not isinstance(current, dict): continue
                last = info.get('last_token_usage') or {}
                delta = {k:max(0, v-previous.get(k,0)) for k,v in current.items() if isinstance(v,int)}
                if current.get('total_tokens',0) < previous.get('total_tokens',0):
                    delta = dict(last or current)
                    coverage['codex_counter_resets'] += 1
                if not previous and last and current.get('total_tokens',0) != last.get('total_tokens',0):
                    coverage['codex_initial_counter_excluded'] += max(0,current.get('total_tokens',0)-last.get('total_tokens',0))
                    delta = dict(last); coverage['codex_initial_counter_gaps'] += 1
                previous = current
                # Imported Claude history has a synthetic total-only context-size event.
                # This is not a model call. Advance the baseline but do not count it.
                if delta.get('total_tokens',0) and not delta.get('input_tokens',0) and not delta.get('output_tokens',0):
                    coverage['codex_total_only_events_excluded'] += 1
                    coverage['codex_total_only_tokens_excluded'] += delta['total_tokens']
                    continue
                if not delta.get('total_tokens',0): continue
                identity = [timestamp, info]
                usage = delta
            else:
                message = row.get('message') or {}
                if not isinstance(message,dict): continue
                usage = message.get('usage')
                if not isinstance(usage,dict):
                    if row.get('type') == 'assistant': coverage[app+'_assistant_rows_without_usage'] += 1
                    continue
                identity = message.get('id') or row.get('requestId') or row.get('id') or row.get('uuid')
                provider = row.get('providerData') or {}
                if app in {'workbuddy','codebuddy'} and provider.get('messageId'):
                    identity = [provider.get('conversationRequestId'),provider['messageId'],provider.get('stepSeq')]
                if not identity:
                    identity = event_id('fallback',row); coverage[app+'_usage_rows_without_id'] += 1
            eid = event_id(app, identity)
            e = {'event_id':eid, 'date':day(timestamp), 'app':app, **normalized(app,usage)}
            if not e['total_tokens']: continue
            events[eid] = combine(events.get(eid), e)
    coverage['events_without_valid_date'] = sum(e['date']=='unknown' for e in events.values())
    coverage['unique_usage_events'] = len(events)
    return events, errors, coverage, codex_ids


def collect_history(root, snapshot, manifest, read_records):
    events, errors, coverage, ids = collect(snapshot,manifest,read_records)
    seen = {e['sha256'] for e in manifest['files'] if 'sha256' in e}
    for path in sorted((root/'backups').glob('*/manifest.json')):
        if path.parent == snapshot:continue
        older = json.loads(path.read_text(encoding='utf-8'))
        selected = []
        for entry in older['files']:
            if entry['app'] not in {'claude','codex','workbuddy','codebuddy'}:continue
            if not entry['path'].endswith(('.jsonl','.jsonl.zst')):continue
            sha=entry['sha256']
            if sha in seen:continue
            seen.add(sha);selected.append(entry)
        if not selected:continue
        more, problems, _, _ = collect(path.parent,{'files':selected},read_records)
        coverage['historical_file_versions_read'] += len(selected)
        errors.extend(problems)
        for eid,event in more.items():events[eid]=combine(events.get(eid),event)
    coverage['unique_usage_events']=len(events)
    coverage['events_without_valid_date']=sum(e['date']=='unknown' for e in events.values())
    return events,errors,coverage,ids


def aggregate(events):
    buckets = collections.defaultdict(collections.Counter)
    for event in events.values():
        bucket = buckets[(event['date'],event['app'])]; bucket['calls'] += 1
        for field in FIELDS: bucket[field] += event[field]
    return [dict(date=date,app=app,**dict(value),input_output_total_tokens=value['input_tokens']+value['output_tokens']) for (date,app),value in sorted(buckets.items())]


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f,fieldnames=fields,lineterminator="\n"); writer.writeheader();writer.writerows(rows)


def load_events(paths):
    events = {}
    for path in sorted(paths):
        with path.open(newline='',encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != EVENT_FIELDS: raise ValueError(f'Unexpected event schema: {path}')
            for row in reader:
                if len(row['event_id']) != 64 or any(c not in '0123456789abcdef' for c in row['event_id']):
                    raise ValueError(f'Invalid event ID: {path}')
                if row['app'] not in {'claude','codex','workbuddy','codebuddy'}: raise ValueError('Invalid app')
                if row['date'] != 'unknown': dt.date.fromisoformat(row['date'])
                for field in FIELDS:
                    row[field] = int(row[field])
                    if row[field] < 0: raise ValueError('Negative usage')
                events[row['event_id']] = combine(events.get(row['event_id']),row)
    return events


def write_local(root, report_dir, events):
    # This private ledger allows export without re-reading transcripts.
    cache = root/'.local/events.csv'
    write_csv(cache, EVENT_FIELDS, sorted(events.values(),key=lambda e:(e['date'],e['app'],e['event_id'])))
    write_csv(report_dir/'daily.csv',DAILY_FIELDS,aggregate(events))


def export(root):
    local = root/'.local'; local.mkdir(exist_ok=True)
    path = local/'device.json'
    if path.exists(): device = json.loads(path.read_text(encoding='utf-8'))
    else:
        device = {'device_id':str(uuid.uuid4()),'platform':platform.system(),'timezone':'UTC+08:00'}
        path.write_text(json.dumps(device,indent=2)+'\n',encoding='utf-8')
    destination = root/'statistics/devices'/device['device_id']
    prior_metadata=destination/'device.json'
    if prior_metadata.exists() and json.loads(prior_metadata.read_text(encoding='utf-8')).get('rules_version') != RULES_VERSION:
        raise ValueError('Existing export uses different counting rules; rebuild that device export from retained backups before merging.')
    # Existing published events survive local app cleanup or a partial source scan.
    events = load_events(list((destination/'events').glob('*.csv'))+[local/'events.csv'])
    months = collections.defaultdict(list)
    for e in events.values(): months[e['date'][:7] if e['date']!='unknown' else 'unknown'].append(e)
    for month, rows in months.items():
        write_csv(destination/'events'/f'{month}.csv',EVENT_FIELDS,sorted(rows,key=lambda e:(e['date'],e['app'],e['event_id'])))
    write_csv(destination/'daily.csv',DAILY_FIELDS,aggregate(events))
    metadata = {**device,'schema_version':1,'rules_version':RULES_VERSION,
                'generated_at':dt.datetime.now(TZ).isoformat(timespec='seconds')}
    (destination/'device.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    merge(root)
    return destination


def merge(root):
    base = root/'statistics'
    metadata = list((base/'devices').glob('*/device.json'))
    for p in metadata:
        if json.loads(p.read_text(encoding='utf-8')).get('rules_version') != RULES_VERSION:
            raise ValueError(f'Usage rules differ for {p}; regenerate that device export first')
    paths = list((base/'devices').glob('*/events/*.csv'))
    events = load_events(paths); daily = aggregate(events)
    write_csv(base/'daily.csv',DAILY_FIELDS,daily)
    by_date=collections.defaultdict(collections.Counter)
    for row in daily:
        for field in ['calls']+FIELDS:by_date[row['date']][field]+=row[field]
    write_csv(base/'daily-total.csv',['date','calls']+FIELDS+['input_output_total_tokens'],[dict(date=date,**dict(values),input_output_total_tokens=values['input_tokens']+values['output_tokens']) for date,values in sorted(by_date.items())])
    valid_dates=sorted(date for date in by_date if date!='unknown')
    totals = collections.Counter()
    for e in events.values():totals[e['app']] += e['total_tokens']
    by_app_metrics=collections.defaultdict(collections.Counter)
    for event in events.values():
        for field in FIELDS:by_app_metrics[event['app']][field]+=event[field]
    for metrics in by_app_metrics.values():metrics['input_output_total_tokens']=metrics['input_tokens']+metrics['output_tokens']
    summary = {'schema_version':1,'rules_version':RULES_VERSION,'timezone':'UTC+08:00',
               'devices':len(metadata),'unique_calls':len(events),'days_with_usage':len(valid_dates),
               'date_range':[valid_dates[0],valid_dates[-1]] if valid_dates else [],
               'total_tokens':sum(totals.values()),'by_app':dict(sorted(totals.items())),
               'input_output_total_tokens':sum(m['input_output_total_tokens'] for m in by_app_metrics.values()),
               'output_tokens':sum(m['output_tokens'] for m in by_app_metrics.values()),
               'metrics_by_app':{app:dict(m) for app,m in sorted(by_app_metrics.items())},
               'unknown_date_tokens':sum(e['total_tokens'] for e in events.values() if e['date']=='unknown')}
    (base/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# Token 用量总览','',
        f'统计范围：{valid_dates[0]} 至 {valid_dates[-1]}（北京时间），{len(valid_dates)} 个有用量的日期。' if valid_dates else '暂无有效日期。','',
        '| 口径 | Token |','|---|---:|',
        f'| 输入＋输出总量（包含缓存输入） | {summary["input_output_total_tokens"]:,} |',
        f'| LLM 输出总量 | {summary["output_tokens"]:,} |','',
        '| 软件 | 输入＋输出总量 | LLM 输出总量 |','|---|---:|---:|']
    lines += [f'| {app} | {m["input_output_total_tokens"]:,} | {m["output_tokens"]:,} |' for app,m in sorted(by_app_metrics.items())]
    lines += ['', '输入＋输出体现模型累计处理量，包含每轮再次读取的上下文和缓存。输出总量只计本地 usage 中的 output_tokens，适合观察生成规模；可能包含推理和工具调用，不等于最终交付字数或工作价值。', '',
        '逐日数据见 [daily-total.csv](daily-total.csv)，按软件拆分见 [daily.csv](daily.csv)。两表都提供 input_output_total_tokens 与 output_tokens。total_tokens 保留提供商上报值，可能与输入输出之和有少量差异。']
    (base/'OVERVIEW.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    from charts import build
    build(root)
    return summary
