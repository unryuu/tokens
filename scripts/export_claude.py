#!/usr/bin/env python3
"""Find a local Claude Code session by title and archive it as Markdown/HTML.

No network calls, model calls, or changes to source sessions. Exported transcripts
belong under the ignored reports/ directory, never in the public tools repository.
"""
import argparse
import base64
import collections
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import string
import sys
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
TZ = dt.timezone(dt.timedelta(hours=8))
SKIP = {'.git', 'node_modules', '.venv', 'subagents', 'tool-results'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def captured_bytes(path):
    """Read the exact length present at open, allowing a live session to append."""
    with Path(path).open('rb') as stream:
        length = os.fstat(stream.fileno()).st_size
        data = stream.read(length)
    if len(data) != length:
        raise ValueError('Source was truncated while reading; retry: ' + str(path))
    return data


def read_rows(data):
    rows, warnings = [], []
    for line_number, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('record is not an object')
            rows.append((line_number, row))
        except (ValueError, UnicodeError) as error:
            warnings.append('源 JSONL 第 {} 行无法解析：{}'.format(line_number, error))
    return rows, warnings


def timestamp(value):
    if not value:
        return None
    try:
        date = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if date.tzinfo is None:
            return None
        return date.astimezone(TZ).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return None


@dataclass
class Session:
    source: Path
    session_id: str
    title: str
    last_timestamp: str
    live: bool = True


def session_info(path, live=True, title_override=None):
    """Inspect metadata without loading tool payloads or binary attachments."""
    title, session_id, last = '', '', ''
    with path.open('rb') as stream:
        for line in stream:
            # JSONL keys vary in order; only title/message records matter here.
            if not any(key in line for key in (b'"custom-title"', b'"sessionId"')):
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(row, dict):
                continue
            if row.get('sessionId'):
                session_id = str(row['sessionId'])
            if row.get('type') == 'custom-title' and row.get('customTitle'):
                title = str(row['customTitle'])
            if row.get('type') in {'user', 'assistant'}:
                last = max(last, timestamp(row.get('timestamp')) or '')
    sidecar = path.with_suffix('')/'custom-title.json'
    if sidecar.is_file() and not sidecar.is_symlink():
        try:
            custom = json.loads(sidecar.read_text(encoding='utf-8'))
            title = custom.get('customTitle') or custom.get('title') or title
        except (ValueError, OSError):
            pass
    if not session_id:
        return None
    return Session(path, session_id, str(title_override or title or session_id), last, live)


def walk_sessions(folder):
    folder = Path(folder).expanduser()
    if folder.is_file():
        if folder.suffix == '.jsonl':
            yield folder
        return
    for current, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(current)/d).is_symlink())
        for name in sorted(files):
            path = Path(current)/name
            if path.suffix == '.jsonl' and not path.is_symlink():
                yield path


def discover(root=ROOT, home=None, extra_roots=(), environ=None):
    home = Path.home() if home is None else Path(home)
    environ = os.environ if environ is None else environ
    profiles = {home/'.claude'} | {p for p in home.glob('.claude*') if p.is_dir()}
    if environ.get('CLAUDE_CONFIG_DIR'):
        profiles.add(Path(environ['CLAUDE_CONFIG_DIR']).expanduser())
    sources = {p/'projects' for p in profiles} | {Path(p).expanduser() for p in extra_roots}
    config = root/'local-sources.json'
    if config.is_file():
        for entry in json.loads(config.read_text(encoding='utf-8')):
            if entry.get('app') == 'claude':
                sources.add(Path(entry['path']).expanduser())
    paths = {}
    for folder in sorted(sources):
        for path in walk_sessions(folder):
            paths.setdefault(path.resolve(), True)
    # Only manifest-classified Claude files; never recursively back up exports.
    for manifest in sorted((root/'backups').glob('*/manifest.json')):
        for entry in json.loads(manifest.read_text(encoding='utf-8')).get('files', []):
            if entry.get('app') == 'claude' and str(entry.get('path', '')).endswith('.jsonl'):
                path = manifest.parent/entry['path']
                if path.is_file() and 'subagents' not in path.parts:
                    paths.setdefault(path.resolve(), False)
    # Unchanged hardlinks need only be inspected once; prefer a live pathname.
    sessions, inodes = [], set()
    for path, live in sorted(paths.items(), key=lambda item: (not item[1], str(item[0]))):
        stat = path.stat()
        key = (stat.st_dev, stat.st_ino)
        if key in inodes:
            continue
        inodes.add(key)
        session = session_info(path, live)
        if session:
            sessions.append(session)
    return sessions


def group_sessions(sessions):
    grouped = collections.defaultdict(list)
    for session in sessions:
        grouped[session.session_id].append(session)
    # Current originals take precedence over snapshots; archived-only sessions
    # use the latest recorded message time, then the most complete byte length.
    return [max(copies, key=lambda s: (s.live, s.last_timestamp, s.source.stat().st_size, str(s.source)))
            for copies in grouped.values()]


def select_sessions(sessions, title=None, session_id=None):
    candidates = group_sessions(sessions)
    if session_id:
        candidates = [s for s in candidates if s.session_id == session_id]
    if title:
        exact = [s for s in candidates if s.title.casefold() == title.casefold()]
        candidates = exact or [s for s in candidates if title.casefold() in s.title.casefold()]
    return sorted(candidates, key=lambda s: (s.last_timestamp, s.session_id), reverse=True)


def safe_name(title):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')[:80].rstrip(' .')
    if not value:
        value = 'conversation'
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL'} | {'COM'+str(i) for i in range(1, 10)} | {'LPT'+str(i) for i in range(1, 10)}:
        value = '_'+value
    return value


def fence(value, language=''):
    delimiter = '`' * (max([len(s) for s in re.findall(r'`+', value)] + [2]) + 1)
    return '{}{}\n{}\n{}'.format(delimiter, language, value, delimiter)


class Transcript:
    def __init__(self, output):
        self.output = output
        self.assets = {}
        self.counts = collections.Counter()
        self.dialogue = []
        self.technical = []
        self.warnings = []

    def image(self, block):
        source, original = block.get('source', {}), block.get('file', {})
        encoded = source.get('data') or original.get('base64')
        if not encoded:
            return '[图片引用；图像数据未存入该记录]'
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            self.warnings.append('一张图片的 Base64 无法解码；原始数据仍在 JSONL 中。')
            return '[图片数据无法解码，见原始 JSONL]'
        # Only inert raster formats can be embedded in the offline document.
        mime = source.get('media_type') or original.get('type', '')
        formats = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp'}
        if mime not in formats:
            self.warnings.append('未内嵌图片格式 {}；原始数据仍在 JSONL 中。'.format(mime))
            return '[未内嵌的图片格式：{}]'.format(mime)
        key = digest(data)
        if key not in self.assets:
            path = self.output/'assets'/(key+formats[mime])
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
            self.assets[key] = (path.relative_to(self.output).as_posix(), mime)
        return '![记录中保存的附件图片]({})'.format(self.assets[key][0])

    def blocks(self, value):
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return fence(json.dumps(value, ensure_ascii=False, indent=2), 'json')
        result = []
        for block in value:
            if not isinstance(block, dict):
                result.append(fence(json.dumps(block, ensure_ascii=False)))
                continue
            kind = block.get('type')
            if kind == 'text':
                result.append(block.get('text', ''))
            elif kind == 'image':
                result.append(self.image(block))
            elif kind == 'tool_use':
                result.append('**工具：{}**\n\n{}'.format(block.get('name', '未知'), fence(json.dumps(block.get('input', {}), ensure_ascii=False, indent=2), 'json')))
            elif kind == 'tool_result':
                result.append('**工具结果：{}{}**\n\n{}'.format(block.get('tool_use_id', ''), '（失败）' if block.get('is_error') else '', self.blocks(block.get('content', ''))))
            elif kind in {'thinking', 'redacted_thinking'}:
                continue
            else:
                result.append(fence(json.dumps(block, ensure_ascii=False, indent=2), 'json'))
        return '\n\n'.join(result)

    def collect(self, rows):
        seen = set()
        versions = {}
        for line, row in rows:
            identity = row.get('uuid')
            key = (identity, digest(json.dumps(row, ensure_ascii=False, sort_keys=True).encode('utf-8')))
            if identity and key in seen:
                continue
            if identity and identity in versions and versions[identity] != key[1]:
                self.warnings.append('消息 UUID {} 存在不同版本，按源顺序全部保留。'.format(identity))
            if identity:
                versions[identity] = key[1]
            seen.add(key)
            kind = row.get('type')
            item = {'time': timestamp(row.get('timestamp')) or '时间未记录', 'id': str(identity or 'line-'+str(line)), 'source_line': line}
            if kind in {'user', 'assistant'}:
                body = row.get('message', {}).get('content', '')
                content = body if isinstance(body, list) else []
                texts = [body] if isinstance(body, str) else [c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text']
                plain = '\n\n'.join(texts)
                automated = bool(row.get('isMeta') or row.get('isCompactSummary')) or (kind == 'user' and plain.lstrip().startswith(('<task-notification>', '[Image: original ')))
                branch = bool(row.get('isSidechain'))
                if texts and not automated and not branch:
                    self.dialogue.append(dict(item, role='你' if kind == 'user' else 'Claude', body=plain))
                    self.counts[kind] += 1
                elif texts:
                    self.technical.append(dict(item, label='分支发言' if branch else '系统插入的通知', body=plain))
                nontext = [c for c in content if not isinstance(c, dict) or c.get('type') not in {'text', 'thinking', 'redacted_thinking'}]
                if nontext:
                    body = self.blocks(nontext)
                    if kind == 'user' and any(isinstance(c, dict) and c.get('type') == 'image' for c in nontext) and not branch and not automated:
                        if texts:
                            self.dialogue[-1]['body'] += '\n\n'+self.blocks([c for c in nontext if isinstance(c, dict) and c.get('type') == 'image'])
                        else:
                            self.dialogue.append(dict(item, role='你', body=self.blocks([c for c in nontext if isinstance(c, dict) and c.get('type') == 'image'])))
                            self.counts['user'] += 1
                    self.technical.append(dict(item, label='Claude · 工具调用' if kind == 'assistant' else '工具与附件返回', body=body))
                    self.counts.update(c.get('type', 'unknown') for c in nontext if isinstance(c, dict))
            elif kind == 'system' and row.get('subtype') in {'api_error', 'compact_boundary'}:
                self.technical.append(dict(item, label='系统记录 · '+row['subtype'], body=fence(json.dumps(row, ensure_ascii=False, indent=2), 'json')))
            elif kind == 'attachment' and row.get('attachment', {}).get('type') in {'edited_text_file', 'file'}:
                attachment = row['attachment']
                text = '**文件：** '+str(attachment.get('filename', ''))
                if attachment.get('snippet'):
                    text += '\n\n'+fence(attachment['snippet'])
                self.technical.append(dict(item, label='文件附件记录', body=text))
        return self


def make_renderer(transcript):
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        raise ValueError('HTML 导出需要 Markdown 渲染依赖。请在项目虚拟环境安装 requirements-export.txt，再使用其中的 Python 运行。') from None
    renderer = MarkdownIt('commonmark', {'html': False, 'breaks': True}).enable('table')
    known = {path: mime for path, mime in transcript.assets.values()}

    def image(tokens, index, options, env):
        token = tokens[index]
        src, alt = token.attrGet('src') or '', token.content or '图片'
        if src not in known:
            return '<span class="attachment-note">[原文图片引用：'+html.escape(alt+' · '+src)+']</span>'
        data = (transcript.output/src).read_bytes()
        url = 'data:'+known[src]+';base64,'+base64.b64encode(data).decode('ascii')
        return '<img loading="lazy" src="'+url+'" alt="'+html.escape(alt, quote=True)+'">'

    renderer.renderer.rules['image'] = image
    return renderer


def copy_companions(source, output, warnings):
    companion = source.with_suffix('')
    if not companion.is_dir() or companion.is_symlink():
        return
    for directory, dirs, files in os.walk(companion):
        dirs[:] = sorted(d for d in dirs if not (Path(directory)/d).is_symlink())
        for name in sorted(files):
            path = Path(directory)/name
            if path.is_symlink():
                warnings.append('未跟随会话附件的符号链接：'+str(path))
                continue
            destination = output/'原始记录/会话附件'/path.relative_to(companion)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(captured_bytes(path))


def export_session(session, root=ROOT, output=None):
    data = captured_bytes(session.source)
    rows, warnings = read_rows(data)
    ids = {str(r['sessionId']) for _, r in rows if r.get('sessionId')}
    if ids != {session.session_id}:
        raise ValueError('源文件包含不同或缺失的会话 ID；请核对 --source。')
    # Check dependency before creating any output files.
    import importlib.util
    if importlib.util.find_spec('markdown_it') is None:
        raise ValueError('请在项目虚拟环境安装 requirements-export.txt，再使用其中的 Python 导出。')
    now = dt.datetime.now(TZ)
    output = Path(output) if output else root/'reports/exports'/now.strftime('%Y-%m-%d')/(safe_name(session.title)+'-'+now.strftime('%H%M%S-%f'))
    if output.resolve().is_relative_to(session.source.with_suffix('').resolve()):
        raise ValueError('导出目录不能位于源会话附件目录中，以免递归复制自身。')
    transcript = Transcript(output)
    # Assets are written during collection, so create a fresh directory first.
    output.mkdir(parents=True, exist_ok=False)
    transcript.warnings.extend(warnings)
    transcript.collect(rows)
    if not transcript.dialogue:
        raise ValueError('没有找到可阅读的用户/Claude 发言；原文未修改，导出目录未完成。')
    raw = output/'原始记录'/session.source.name
    raw.parent.mkdir()
    raw.write_bytes(data)
    copy_companions(session.source, output, transcript.warnings)
    renderer = make_renderer(transcript)
    dates = [item['time'] for item in transcript.dialogue if item['time'] != '时间未记录']
    date_range = [min(dates), max(dates)] if dates else []
    date_label = ' — '.join(date_range) if dates else '时间未记录'
    counts = transcript.counts
    count_label = '{} 条用户发言 · {} 条 Claude 回复'.format(counts['user'], counts['assistant'])
    tool_counts = '{} 次工具调用 · {} 条工具返回'.format(counts['tool_use'], counts['tool_result'])
    intro = ['# '+session.title, '', '对话存档 · Claude Code', '', date_label+'（北京时间）', '', count_label, '', '正文保持原话和记录顺序；工具与自动通知另存附录。原始 JSONL 和会话附件随档保存。', '']
    if transcript.warnings:
        intro.extend(['导出提示：'+'；'.join(transcript.warnings), ''])
    article_html = []
    for index, item in enumerate(transcript.dialogue, 1):
        intro.extend(['## {:02d} · {} · {}'.format(index, item['role'], item['time']), '', item['body'], '', '---', ''])
        article_html.append('<article class="message {}" id="message-{}" data-uuid="{}"><div class="message-meta"><span>{:02d} · {}</span><time>{}</time><a href="#message-{}" aria-label="此消息链接">#</a></div><div class="message-body">{}</div></article>'.format('user' if item['role']=='你' else 'assistant', index, html.escape(item['id'], quote=True), index, item['role'], item['time'], index, renderer.render(item['body'])))
    reading = '\n'.join(intro)
    (output/'对话原文.md').write_text(reading, encoding='utf-8')
    appendix = ['# 工具与附件附录', '', tool_counts+'；通过时间、UUID、源行号与正文对应。', '']
    technical_html = []
    for index, item in enumerate(transcript.technical, 1):
        label = '{:03d} · {} · {}'.format(index, item['label'], item['time'])
        note = '源 JSONL 第 {} 行 · 消息 UUID：{}'.format(item['source_line'], item['id'])
        appendix.extend(['## '+label, '', note, '', item['body'], '', '---', ''])
        technical_html.append('<details class="technical-entry"><summary>'+html.escape(label)+'</summary><p class="source-note">'+html.escape(note)+'</p>'+renderer.render(item['body'])+'</details>')
    (output/'工具与附件附录.md').write_text('\n'.join(appendix), encoding='utf-8')
    template = string.Template((Path(__file__).parent/'templates/conversation.html').read_text(encoding='utf-8'))
    page = template.substitute(title=html.escape(session.title), date_range=html.escape(date_label), counts=count_label,
                               warnings='<p class="subtitle">'+html.escape('；'.join(transcript.warnings))+'</p>' if transcript.warnings else '',
                               last_anchor='#message-'+str(max(1, len(transcript.dialogue)-1)), dialogue='\n'.join(article_html),
                               tool_counts=tool_counts, technical='\n'.join(technical_html), session_id=html.escape(session.session_id),
                               source_sha256=digest(data), exported_at=now.isoformat(timespec='seconds'))
    (output/'对话存档.html').write_text(page, encoding='utf-8')
    (output/'说明.txt').write_text('对话存档.html：离线阅读；工具过程在底部展开。打印样式默认只打印正文。\n对话原文.md：保留原话的 Markdown。\n工具与附件附录.md：工具输入/返回、自动通知与文件片段。\nassets/：日志实际包含的图片，HTML 中已内嵌。\n原始记录/：源 JSONL 的字节副本与会话目录附件（包括子代理日志）。\n导出清单.json：消息与源行号对应关系、时间、警告和 SHA-256 校验值。\n\n正文不包括系统提示、思考签名、重复 API 请求载荷及用量提醒；这些仍在原始 JSONL。外部引用文档不自动打包。仅导出所选会话文件，不拼接其他副本或分支；分支发言列入附录，子代理原文保存在会话附件。默认目录 reports/ 被 Git 忽略，不自动上传。\n', encoding='utf-8')
    # Verify original bytes and every exported body before marking the archive complete.
    if raw.read_bytes() != data or any(item['body'] not in reading for item in transcript.dialogue):
        raise ValueError('导出内容校验失败；保留目录供检查。')
    manifest = {'schema_version':1, 'status':'complete', 'title':session.title, 'session_id':session.session_id,
                'timezone':'UTC+08:00', 'source':str(session.source), 'source_sha256':digest(data),
                'source_bytes':len(data), 'source_lines':len(data.splitlines()), 'date_range':date_range,
                'exported_at':now.isoformat(timespec='seconds'), 'counts':dict(counts), 'embedded_images':len(transcript.assets),
                'warnings':transcript.warnings, 'dialogue_messages':[{k:v for k,v in item.items() if k!='body'} for item in transcript.dialogue], 'files':[]}
    for path in sorted(output.rglob('*')):
        if path.is_file():
            content = path.read_bytes()
            manifest['files'].append({'path':path.relative_to(output).as_posix(), 'bytes':len(content), 'sha256':digest(content)})
    (output/'导出清单.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return output, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('title', nargs='?', help='Exact title preferred, otherwise unique partial match')
    parser.add_argument('--list', action='store_true', help='List matching sessions without exporting')
    parser.add_argument('--session-id', help='Select an exact session ID when titles are ambiguous')
    parser.add_argument('--source', type=Path, help='Use an explicit JSONL instead of discovery')
    parser.add_argument('--search-root', type=Path, action='append', default=[], help='Additional session directory, repeatable')
    parser.add_argument('--output', type=Path, help='New output directory; never overwrites')
    args = parser.parse_args(argv)
    if not (args.title or args.session_id or args.source or args.list):
        parser.error('请提供会话标题，或使用 --list / --session-id / --source。')
    try:
        if args.source:
            session = session_info(args.source, title_override=args.title)
            if not session:
                raise ValueError('JSONL 中没有 Claude 会话 ID。')
            if args.session_id and session.session_id != args.session_id:
                raise ValueError('--source 与 --session-id 不一致。')
            candidates = [session]
        else:
            candidates = select_sessions(discover(extra_roots=args.search_root), args.title, args.session_id)
        listings = [{'title':s.title, 'session_id':s.session_id, 'last_timestamp':s.last_timestamp, 'source':str(s.source)} for s in candidates]
        if args.list:
            print(json.dumps(listings, ensure_ascii=False, indent=2))
            return 0
        if not candidates:
            raise ValueError('没有找到匹配会话。可用 --list、--search-root 或 --source 检查其他位置。')
        if len(candidates) > 1:
            print(json.dumps(listings, ensure_ascii=False, indent=2), file=sys.stderr)
            raise ValueError('找到多个会话，请确认后使用 --session-id；不会自动选择同名会话。')
        output, manifest = export_session(candidates[0], output=args.output)
        print(json.dumps({'directory':str(output.resolve()), 'title':manifest['title'], 'counts':manifest['counts'],
                          'embedded_images':manifest['embedded_images'], 'warnings':manifest['warnings'], 'status':'complete'}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print('导出失败：'+str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
