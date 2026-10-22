#!/usr/bin/env python3
"""Local-only, versioned agent transcript backups; Python standard library."""
import argparse
import collections
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import sys
import subprocess
import time
import daily
import storage

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
SKIP = {'node_modules', '.git', '.venv', 'Cache', 'Caches', 'Code Cache',
        'GPUCache', 'plugins', 'skills', 'binaries', 'packages', 'vm_bundles'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def walk(root):
    if root.is_file():
        yield root
    elif root.is_dir():
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP)
            for name in sorted(files):
                p = Path(d) / name
                if p.is_file():
                    yield p


def discover():
    found = {}
    probes = []
    def add(app, root, suffixes=None):
        if sys.platform == 'win32' and str(root).startswith('\\\\?\\'):
            root = str(root)[4:]
            if root.startswith('UNC\\'):
                root = '\\\\' + root[4:]
        root = Path(root).expanduser()
        probes.append({'app': app, 'path': str(root), 'exists': root.exists()})
        for p in walk(root):
            if suffixes is None or p.suffix in suffixes:
                found.setdefault(str(p.resolve()), (app, p))
    claude_roots = {HOME/'.claude', Path(os.getenv('CLAUDE_CONFIG_DIR',str(HOME/'.claude'))).expanduser()}
    claude_roots.update(p for p in HOME.glob('.claude*') if p.is_dir())
    for base in sorted(claude_roots):
        add('claude', base / 'projects', {'.jsonl', '.json'})
        add('claude', base / 'history.jsonl')
    codex_roots = {HOME/'.codex', Path(os.getenv('CODEX_HOME',str(HOME/'.codex'))).expanduser()}
    codex_roots.update(p for p in HOME.glob('.codex*') if p.is_dir())
    for base in sorted(codex_roots):
        for sub in ['sessions', 'archived_sessions']:
            add('codex', base / sub, {'.jsonl', '.zst'})
        for database in sorted(base.glob('*.sqlite')):
            if database.name.startswith(('state_', 'thread_history_', 'logs_')):add('codex',database)
        for name in ['session_index.jsonl', 'history.jsonl', 'external_agent_session_imports.json',
                     'state_5.sqlite', 'thread_history_1.sqlite', 'logs_2.sqlite']:
            add('codex', base / name)
        state = base / 'state_5.sqlite'
        if state.exists():
            try:
                with sqlite3.connect(state.resolve().as_uri() + '?mode=ro', uri=True) as c:
                    for (p,) in c.execute('select rollout_path from threads'):
                        if Path(p).exists(): add('codex', p)
            except sqlite3.Error as error:
                probes.append({'app':'codex','path':str(state),'error':str(error)})
    support = Path(os.getenv('APPDATA',str(HOME/'AppData/Roaming'))) if sys.platform == 'win32' else (HOME/'Library/Application Support' if sys.platform == 'darwin' else HOME/'.config')
    for app in ['Claude', 'Claude-3p']:
        for sub in ['claude-code-sessions', 'local-agent-mode-sessions']:
            add('claude-desktop-metadata', support / app / sub, {'.json', '.jsonl'})
    for base in [HOME / '.workbuddy-ai', HOME / '.workbuddy', HOME / '.codebuddy']:
        for sub in ['projects', 'traces']:
            add('workbuddy' if 'workbuddy' in str(base) else 'codebuddy', base / sub, {'.jsonl'})
        for sub in ['workbuddy.db', 'usage-log.json']:
            add('workbuddy-metadata', base / sub)
    for base in [HOME / '.gemini', HOME / '.qwen']:
        add('gemini', base / 'tmp', {'.json', '.jsonl'})
    add('doubao-work', support / 'DoubaoWork/Default/.doubaowork/agent_mode/workspace/.sessions', {'.jsonl', '.json', '.md'})
    add('antigravity', HOME / '.gemini/antigravity/conversations')
    add('antigravity', HOME / '.gemini/antigravity/conversation_summaries.db')
    for app in ['Code', 'Code - Insiders', 'Cursor', 'Windsurf', 'Antigravity']:
        for sub in ['globalStorage', 'workspaceStorage']:
            base = support / app / 'User' / sub
            probes.append({'app': app, 'path': str(base), 'exists': base.exists()})
            for p in walk(base):
                low = str(p).replace('\\','/').lower()
                if (p.suffix in {'.json', '.jsonl'} and any(x in low for x in
                    ['chatsessions', 'chat_sessions', '/tasks/', '/conversations/'])) or p.name in {
                        'state.vscdb', 'session-store.db', 'agent-host.db'}:
                    found.setdefault(str(p.resolve()), ('editor-chat', p))
    for base in ['.cursor/chats', '.continue/sessions', '.local/share/opencode/storage',
                 '.local/share/opencode/opencode.db', '.aider.chat.history.md']:
        add('other-agent', HOME / base)
    config = ROOT / 'local-sources.json'
    if config.exists():
        for entry in json.loads(config.read_text(encoding='utf-8')):
            add(entry['app'], entry['path'], set(entry['suffixes']) if entry.get('suffixes') else None)
    return sorted(found.values(), key=lambda x: str(x[1])), probes


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix in {'.sqlite', '.db', '.vscdb'}:
        try:
            connection = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
            connection.execute('select count(*) from sqlite_master').fetchone()
        except sqlite3.OperationalError:
            if 'connection' in locals(): connection.close()
            # Some abandoned WAL-mode DBs cannot create SHM in their original folder.
            # Copy the full SQLite bundle, then recover only this private copy.
            bundle = target.parent / (target.name + '.raw-bundle')
            bundle.mkdir(exist_ok=True)
            for attempt in range(3):
                parts = [p for p in [source, Path(str(source)+'-wal'), Path(str(source)+'-shm'),
                                     Path(str(source)+'-journal')] if p.exists()]
                before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in parts]
                for p in parts: shutil.copy2(p, bundle / p.name)
                after = [(p.stat().st_size, p.stat().st_mtime_ns) for p in parts]
                if before == after: break
            else: raise RuntimeError('SQLite bundle changed repeatedly during copy')
            connection = sqlite3.connect(bundle / source.name)
        deadline = time.monotonic() + 45
        def progress(*_):
            if time.monotonic() > deadline: raise TimeoutError('SQLite snapshot exceeded 45 seconds')
        try:
            with contextlib.closing(sqlite3.connect(target)) as dest:
                connection.backup(dest, pages=1024, progress=progress, sleep=0.05)
                result = dest.execute('pragma quick_check').fetchone()[0]
                if result != 'ok': raise RuntimeError(result)
        finally: connection.close()
        return 'sqlite-online-snapshot', False
    # Capture exactly the bytes present at open time, including any incomplete last line.
    with source.open('rb') as src, target.open('wb') as out:
        before = os.fstat(src.fileno())
        remaining = before.st_size
        while remaining:
            block = src.read(min(1024 * 1024, remaining))
            if not block: raise RuntimeError('source truncated during copy')
            out.write(block)
            remaining -= len(block)
        after = os.fstat(src.fileno())
    shutil.copystat(source, target)
    return 'raw-bytes', (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)


def backup():
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
    snapshot = ROOT / 'backups' / stamp
    snapshot.mkdir(parents=True)
    sources, probes = discover()
    known = storage.index(ROOT)
    previous = None
    latest = ROOT/'backups/LATEST'
    if latest.exists():
        previous_path = ROOT/'backups'/latest.read_text(encoding='utf-8').strip()
        previous = json.loads((previous_path/'manifest.json').read_text(encoding='utf-8'))
    manifest = {'created_at': stamp, 'files': [], 'errors': [], 'probes': probes, 'incremental':{'reused_files':0,'reused_bytes':0,'new_bytes':0}}
    for app, source in sources:
        try:
            relative = source.relative_to(HOME) if source.is_relative_to(HOME) else Path('_external') / source.anchor.replace(':','').replace('\\','_').replace('/','_') / Path(*source.parts[1:])
            target = snapshot / 'files' / relative
            target.parent.mkdir(parents=True,exist_ok=True)
            linked = False
            if source.suffix not in {'.sqlite','.db','.vscdb'}:
                before = source.stat()
                sha = digest(source)
                after = source.stat()
                old = known.get((sha,before.st_size))
                if old and (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns):
                    try:
                        if digest(old)!=sha:raise RuntimeError('Archived file content changed: '+str(old))
                        os.link(old,target); linked=True
                    except OSError:pass
            if linked:method,changed='reused-backup-hardlink',False
            else:method,changed=copy_file(source,target)
            manifest['files'].append({'app': app, 'source': str(source),
                'path': str(target.relative_to(snapshot)), 'bytes': target.stat().st_size,
                'sha256': digest(target), 'method': method, 'source_changed_during_copy': changed})
        except Exception as e:
            manifest['errors'].append({'source': str(source), 'error': str(e)})
    # Preserve disappeared source files from the preceding snapshot as well.
    present = {e['source'] for e in manifest['files']}
    if previous:
        for e in previous['files']:
            if e['source'] in present or e['source']=='recovery-copy':continue
            old = previous_path/e['path']; target=snapshot/e['path']
            if target.exists():continue
            if digest(old)!=e['sha256']:raise RuntimeError('Archived file content changed: '+str(old))
            target.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(old,target)
            except OSError:shutil.copy2(old,target)
            manifest['files'].append({**e,'method':'retained-previous-source','retained_from':previous_path.name})
    known_paths = {e['path'] for e in manifest['files']}
    for p in walk(snapshot / 'files'):
        rel = str(p.relative_to(snapshot))
        if rel not in known_paths:
            manifest['files'].append({'app':'sqlite-raw-bundle', 'source':'recovery-copy', 'path':rel,
                'bytes':p.stat().st_size, 'sha256':digest(p), 'method':'sqlite-recovery-bundle',
                'source_changed_during_copy':False})
    for e in manifest['files']:
        target=snapshot/e['path']
        old=known.get((e['sha256'],e['bytes']))
        if old and digest(old)!=e['sha256']:raise RuntimeError('Archived file content changed: '+str(old))
        reused=storage.reuse(target,e['sha256'],known)
        e['storage_reused']=reused
        if reused:
            manifest['incremental']['reused_files']+=1
            manifest['incremental']['reused_bytes']+=e['bytes']
        else:manifest['incremental']['new_bytes']+=e['bytes']
    (snapshot / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2),encoding='utf-8')
    (ROOT / 'backups/LATEST').write_text(snapshot.name + '\n',encoding='utf-8')
    print(f'Backup: {snapshot}; files={len(manifest["files"])}; errors={len(manifest["errors"])}', flush=True)
    return snapshot, manifest


def records(path, errors):
    process = None
    if path.name.endswith('.jsonl.zst'):
        zstd = shutil.which('zstd') or ('/opt/homebrew/bin/zstd' if Path('/opt/homebrew/bin/zstd').exists() else None)
        if zstd:
            process = subprocess.Popen([zstd, '-dc', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stream = process.stdout
        else:
            try:
                from compression import zstd as pyzstd
                stream = pyzstd.open(path,'rb')
            except ImportError:
                try:
                    import zstandard
                    import io
                    stream = io.BufferedReader(zstandard.ZstdDecompressor().stream_reader(path.open('rb')))
                except ImportError:
                    raise RuntimeError('Reading .jsonl.zst requires zstd, Python compression.zstd, or the zstandard package.')
    else:
        stream = path.open('rb')
    try:
        for lineno, line in enumerate(stream, 1):
            try:
                o = json.loads(line)
                if isinstance(o, dict): yield o
            except (ValueError, UnicodeError):
                errors.append({'path': str(path), 'line': lineno})
    finally:
        stream.close()
        if process:
            error = process.stderr.read()
            process.stderr.close()
            if process.wait() != 0: raise RuntimeError('zstd decompression failed: ' + error.decode(errors='replace'))


def usage_report(snapshot, manifest):
    events, malformed, coverage, codex_ids = daily.collect_history(ROOT, snapshot, manifest, records)
    totals = collections.defaultdict(collections.Counter)
    dates = []
    for event in events.values():
        totals[event['app']].update({k:event[k] for k in daily.FIELDS})
        if event['date'] != 'unknown':dates.append(event['date'])
    db_summary = []
    for entry in manifest['files']:
        if not (Path(entry['source']).name.startswith('state_') and Path(entry['source']).suffix=='.sqlite'): continue
        with sqlite3.connect((snapshot/entry['path']).resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as c:
            rows = c.execute('select id,model_provider,tokens_used,rollout_path from threads').fetchall()
        history_path = (snapshot/entry['path']).parent/'thread_history_1.sqlite'
        history_ids = set()
        if history_path.exists():
            with sqlite3.connect(history_path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as hc:
                history_ids = {r[0] for r in hc.execute('select distinct thread_id from thread_items')}
        db_summary.append({'threads_with_database_items':len(history_ids),
            'threads_without_any_local_transcript':sum(r[0] not in codex_ids | history_ids for r in rows),
            'threads':len(rows), 'stored_counter_sum_not_added_to_total':sum(r[2] for r in rows),
            'threads_without_jsonl':sum(r[0] not in codex_ids for r in rows),
            'without_jsonl_stored_counter_sum_not_added':sum(r[2] for r in rows if r[0] not in codex_ids),
            'providers':dict(collections.Counter(r[1] for r in rows))})
    result = {'snapshot':snapshot.name, 'usage':dict(totals), 'observed_total_tokens':sum(u.get('total_tokens',0) for u in totals.values()),
        'input_output_total_tokens':sum(u.get('input_tokens',0)+u.get('output_tokens',0) for u in totals.values()),
        'output_tokens':sum(u.get('output_tokens',0) for u in totals.values()),
        'coverage':dict(coverage),'codex_database':db_summary,'malformed_jsonl_lines':malformed,
        'record_timestamp_range': [min(dates),max(dates)] if dates else [],
        'notes':['Only locally recorded usage is counted; this is not a lifetime billing total.',
                 'Imported total-only context baselines are excluded. Daily dates use UTC+08:00; invalid dates are unknown.',
                 'Claude raw input + cache creation + cache read + output; exported input includes caches. Nested usage iterations are not added again.',
                 'Codex: first observed last_token_usage, then differences of cumulative counters; identical timestamp+usage events across copied/forked histories counted once. Cached input and reasoning output are subsets, not added again.',
                 'Codex database counters may inherit parent usage. They are reported separately, never added to JSONL usage.',
                 'WorkBuddy: recorded total_tokens (cache read is included in input); traces are preserved but excluded from totals.',
                 'Conversation files and detailed reports stay local. Publishing exports only hashed event IDs, dates, app labels and counters.']}
    report_dir = ROOT / 'reports' / snapshot.name; report_dir.mkdir(parents=True, exist_ok=True)
    daily.write_local(ROOT, report_dir, events)
    (report_dir / 'usage.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
    lines = ['# 本地备份与 Token 统计', '', f'快照：{snapshot.name}',
        f'成功备份 {len(manifest["files"])} 个文件，共 {sum(x["bytes"] for x in manifest["files"]):,} 字节；失败 {len(manifest["errors"])} 个。',
        f'本轮复用 {manifest.get("incremental",{}).get("reused_bytes",0):,} 字节，新增文件数据 {manifest.get("incremental",{}).get("new_bytes",0):,} 字节。统计合并本机保留的历史版本，不重复相加快照。', '',
        '| 来源 | 输入＋输出总量 | LLM 输出总量 |', '|---|---:|---:|']
    lines += [f'| {app} | {u.get("input_tokens",0)+u.get("output_tokens",0):,} | {u.get("output_tokens",0):,} |' for app,u in totals.items()]
    lines += ['',f'LLM 输出总量：**{result["output_tokens"]:,}** token。它反映生成规模，可能包含推理与工具调用，不等于最终交付字数。']
    lines += ['', f'现存调用记录合计（含压缩归档）：**{result["observed_total_tokens"]:,}**。这不是全部历史账单总量。', '',
        '每日统计见同目录 daily.csv，统一按北京时间（UTC+08:00）分日。无有效日期的用量归入 unknown，不猜日期。',
        f'已排除导入历史等仅有 total 的起始计数：{coverage.get("codex_total_only_tokens_excluded",0):,} token。',
        '标准化输入已包含缓存写入和读取，缓存列是输入的子项；推理输出不重复相加。',
        '原文按源路径存放在快照 files/ 下；manifest.json 包含源路径、SHA-256 和复制状态。SQLite 使用一致性快照，原始 JSONL 不改写。',
        f'JSONL 无法解析的行：{len(malformed)}（详见 usage.json，可能是仍在写入的末行）。']
    for db in db_summary:
        lines += ['',f'Codex 数据库有 {db["threads"]} 个会话，其中 {db["threads_without_jsonl"]} 个没有现存 JSONL（含压缩归档）；其中 {db["threads_without_any_local_transcript"]} 个在对话数据库中也没有内容。数据库累计计数为 {db["stored_counter_sum_not_added_to_total"]:,}；缺少 JSONL 的会话计数为 {db["without_jsonl_stored_counter_sum_not_added"]:,}。这些计数可能含继承上下文，不并入上表。对话内容另存于 thread_history_1.sqlite。']
    (report_dir / '报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(report_dir),'tokens':result['observed_total_tokens'], 'usage':result['usage'], 'codex_database':db_summary},ensure_ascii=False),flush=True)
    return result


def verify(snapshot, manifest):
    failures = [e['path'] for e in manifest['files'] if digest(snapshot/e['path']) != e['sha256']]
    if failures: raise RuntimeError(f'Checksum mismatch: {failures}')
    print(f'Verified {len(manifest["files"])} SHA-256 checksums.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scan', action='store_true', help='Discover sources without copying logs; write private reports/discovery.json')
    parser.add_argument('--publish-stats',action='store_true',help='Explicitly write sanitized statistics into tracked statistics/; does not push Git')
    parser.add_argument('--merge-stats',action='store_true',help='Merge published devices by hashed event identity')
    parser.add_argument('--compact',action='store_true',help='Deduplicate identical old backup files with hardlinks, preserving every snapshot')
    parser.add_argument('--verify', action='store_true', help='Verify latest snapshot without backing up')
    parser.add_argument('--report', action='store_true', help='Recompute latest usage report')
    args = parser.parse_args()
    if args.scan:
        files, probes = discover()
        destination = ROOT / 'reports/discovery.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({'probes': probes, 'files': [
            {'app': app, 'source': str(path)} for app, path in files
        ]}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'files': len(files), 'by_app': dict(collections.Counter(app for app, _ in files)),
                          'report': str(destination)}, ensure_ascii=False))
        return 0
    if args.compact:
        print(json.dumps(storage.compact(ROOT,digest)));return 0
    if args.merge_stats:
        print(json.dumps(daily.merge(ROOT)));return 0
    if args.verify or args.report or args.publish_stats:
        snapshot = ROOT/'backups'/(ROOT/'backups/LATEST').read_text(encoding='utf-8').strip()
        manifest = json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
    else: snapshot, manifest = backup()
    verify(snapshot, manifest)
    if not args.verify or args.report: usage_report(snapshot, manifest)
    if args.publish_stats:
        if manifest['errors']:raise RuntimeError('Backup contains errors; inspect the local report before publishing')
        print('Published sanitized statistics locally:',daily.export(ROOT))
    if manifest['errors']:
        print(json.dumps(manifest['errors'],ensure_ascii=False),file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__': sys.exit(main())
