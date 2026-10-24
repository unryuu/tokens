import base64
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import export_claude as exporter

HAS_MARKDOWN = importlib.util.find_spec('markdown_it') is not None


def write_session(path, session_id, title, messages=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{'type':'custom-title', 'sessionId':session_id, 'customTitle':title}]
    rows += [{'sessionId':session_id, **message} for message in messages]
    data = ('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows)+'\n').encode('utf-8')
    path.write_bytes(data)
    return data


def message(role, uuid, content, **extra):
    return {'type':role, 'uuid':uuid, 'timestamp':'2026-10-23T17:33:36Z',
            'message':{'role':role, 'content':content}, **extra}


class SessionDiscoveryTests(unittest.TestCase):
    def test_profiles_extra_sources_and_snapshots_group_by_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root/'home'
            source = home/'.claude/projects/project/same.jsonl'
            write_session(source, 'same', '同名窗口', [message('user','u1','甲')])
            copy = root/'backups/old/files/old.jsonl'
            write_session(copy, 'same', '旧名字', [message('user','u2','副本', timestamp='2026-10-24T01:00:00Z')])
            (root/'backups/old/manifest.json').write_text(json.dumps({'files':[{'app':'claude','path':'files/old.jsonl'}]}), encoding='utf-8')
            env_root = root/'profile'
            other = env_root/'projects/project/another.jsonl'
            write_session(other, 'other', '同名窗口')
            archived = root/'external/third.jsonl'
            write_session(archived, 'third', '第三个窗口')
            (root/'local-sources.json').write_text(json.dumps([{'app':'claude', 'path':str(archived.parent)}]), encoding='utf-8')
            write_session(source.with_suffix('')/'subagents/child.jsonl', 'child', '同名窗口')
            sessions = exporter.discover(root, home, environ={'CLAUDE_CONFIG_DIR':str(env_root)})
            matches = exporter.select_sessions(sessions, '同名窗口')
            self.assertEqual({s.session_id for s in matches}, {'same','other'})
            self.assertEqual(next(s for s in matches if s.session_id=='same').source, source.resolve())
            self.assertEqual(len(exporter.select_sessions(sessions, '同名', 'same')), 1)
            self.assertEqual(exporter.select_sessions(sessions, '第三个')[0].source, archived.resolve())
            # A removed original still remains discoverable from its snapshot.
            archived_only = exporter.select_sessions([s for s in sessions if not s.live], '旧名字')
            self.assertEqual(archived_only[0].source, copy.resolve())

    def test_sidecar_title_exact_match_and_same_name_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'a.jsonl'
            write_session(source, 'a', '旧标题')
            source.with_suffix('').mkdir()
            (source.with_suffix('')/'custom-title.json').write_text('{"customTitle":"新标题"}', encoding='utf-8')
            a = exporter.session_info(source)
            b_path = root/'b.jsonl'
            write_session(b_path, 'b', '新标题附注')
            b = exporter.session_info(b_path)
            self.assertEqual(exporter.select_sessions([a,b], '新标题'), [a])
            b.title = '新标题'
            with patch.object(exporter, 'discover', return_value=[a,b]), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(exporter.main(['新标题']), 1)
            self.assertIn('--session-id', error.getvalue())
            self.assertFalse((root/'reports').exists())

    def test_safe_windows_names_and_output_fences(self):
        for title in ['../CON', 'NUL', 'a:b?c*', '\x00<>', 'LPT1.txt', '..']:
            name = exporter.safe_name(title)
            self.assertTrue(name)
            self.assertFalse(any(c in name for c in '<>:"/\\|?*\x00'))
            self.assertFalse(name.endswith(('.', ' ')))
        value = '```json\n{\"value\":\"````\"}\n```'
        self.assertTrue(exporter.fence(value).startswith('`````'))


@unittest.skipUnless(HAS_MARKDOWN, 'Install requirements-export.txt for HTML export tests')
class ExportArchiveTests(unittest.TestCase):
    def test_lossless_dialogue_raw_assets_and_safe_offline_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'project/session.jsonl'
            image = b'fixture raster bytes'
            block = {'type':'image', 'source':{'type':'base64','data':base64.b64encode(image).decode('ascii'),'media_type':'image/png'}}
            text = '原话 **加粗**\n\n| 名称 | 值 |\n|---|---|\n| 甲 | 乙 |\n\n<script>alert(1)</script>\n\n![外部](https://example.invalid/track.png)\n\n$dialogue'
            messages = [message('user','u1', '保留原话'),
                        message('assistant','a1',[{'type':'text','text':text}, {'type':'tool_use','id':'t1','name':'Read','input':{'file_path':'notes.md'}}]),
                        message('user','r1',[{'type':'tool_result','tool_use_id':'t1','content':[{'type':'text','text':'工具返回原话'},block]}]),
                        message('user','notify','<task-notification>完成</task-notification>'),
                        message('user','meta','自动上下文',isMeta=True),
                        message('assistant','side',[{'type':'text','text':'分支原话'}],isSidechain=True),
                        {'type':'api-request-blob','message':{'content':[{'type':'text','text':'请求缓存不再展示'}]}},
                        message('user','u2',[{'type':'text','text':'图片说明'},block]),
                        message('assistant','a2',[{'type':'text','text':'晚安，保留结尾。'}])]
            messages.append(messages[-1])  # exact repeated UUID is not another reply
            original = write_session(source, 'session', '测试 <窗口>', messages)
            companion = source.with_suffix('')/'subagents/agent.jsonl'
            companion.parent.mkdir(parents=True)
            companion.write_text('{"type":"user"}\n', encoding='utf-8')
            output = root/'reports/export'
            result, manifest = exporter.export_session(exporter.session_info(source), root, output)
            self.assertEqual(result, output)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((output/'原始记录/session.jsonl').read_bytes(), original)
            self.assertEqual((output/'原始记录/会话附件/subagents/agent.jsonl').read_bytes(), companion.read_bytes())
            self.assertEqual(manifest['counts']['user'], 2)
            self.assertEqual(manifest['counts']['assistant'], 2)
            self.assertEqual(manifest['counts']['tool_use'], 1)
            self.assertEqual(manifest['counts']['tool_result'], 1)
            self.assertEqual(manifest['embedded_images'], 1)
            self.assertEqual(manifest['date_range'], ['2026-10-24 01:33:36']*2)
            reading = (output/'对话原文.md').read_text(encoding='utf-8')
            appendix = (output/'工具与附件附录.md').read_text(encoding='utf-8')
            page = (output/'对话存档.html').read_text(encoding='utf-8')
            self.assertIn(text, reading)
            for phrase in ['工具返回原话', '分支原话', '<task-notification>', '自动上下文']:
                self.assertNotIn(phrase, reading)
                self.assertIn(phrase, appendix)
            self.assertNotIn('请求缓存不再展示', reading+appendix)
            self.assertIn('<table>', page)
            self.assertIn('&lt;script&gt;', page)
            self.assertNotIn('<script>', page)
            self.assertNotIn('src="https://', page)
            self.assertIn('data:image/png;base64,', page)
            self.assertIn('$dialogue', page)  # literal user text never becomes template code
            self.assertIn('<details class="technical-entry">', page)
            self.assertEqual(manifest['status'], 'complete')
            self.assertTrue(any(item['path']=='说明.txt' for item in manifest['files']))
            for item in manifest['files']:
                data = (output/item['path']).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
                self.assertEqual(len(data), item['bytes'])
            with self.assertRaises(FileExistsError):
                exporter.export_session(exporter.session_info(source), root, output)
            self.assertEqual((output/'原始记录/session.jsonl').read_bytes(), original)

    def test_partial_last_line_is_visible_warning_and_preserved_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'session.jsonl'
            data = write_session(source, 'session', '断行', [message('user','u1','完整的一行')])+b'{"type":'
            source.write_bytes(data)
            output, manifest = exporter.export_session(exporter.session_info(source), root, root/'output')
            self.assertEqual((output/'原始记录/session.jsonl').read_bytes(), data)
            self.assertEqual(len(manifest['warnings']), 1)
            self.assertIn('第 3 行无法解析', manifest['warnings'][0])
            self.assertIn('无法解析', (output/'对话存档.html').read_text(encoding='utf-8'))

    def test_empty_and_mixed_session_files_cannot_be_exported_as_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'session.jsonl'
            write_session(source,'one','混合',[message('user','u1','甲',sessionId='two')])
            with self.assertRaisesRegex(ValueError, '会话 ID'):
                exporter.export_session(exporter.session_info(source), root, root/'output')
            self.assertFalse((root/'output').exists())

    def test_output_cannot_be_inside_companions_and_conflicting_uuid_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'session.jsonl'
            write_session(source,'one','版本',[message('user','same','版本甲'), message('user','same','版本乙')])
            session = exporter.session_info(source)
            with self.assertRaisesRegex(ValueError, '递归复制'):
                exporter.export_session(session, root, source.with_suffix('')/'export')
            self.assertFalse(source.with_suffix('').exists())
            output, manifest = exporter.export_session(session, root, root/'output')
            self.assertEqual(manifest['counts']['user'], 2)
            self.assertIn('不同版本', manifest['warnings'][0])
            reading = (output/'对话原文.md').read_text(encoding='utf-8')
            self.assertIn('版本甲', reading)
            self.assertIn('版本乙', reading)

    def test_image_only_user_message_and_missing_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'session.jsonl'
            block = {'type':'image','source':{'type':'base64','data':base64.b64encode(b'image').decode(),'media_type':'image/jpeg'}}
            write_session(source,'one','只有图片',[message('user','u1',[block], timestamp=None)])
            output, manifest = exporter.export_session(exporter.session_info(source), root, root/'output')
            self.assertEqual(manifest['counts']['user'], 1)
            self.assertEqual(manifest['date_range'], [])
            self.assertIn('时间未记录', (output/'对话原文.md').read_text(encoding='utf-8'))
            self.assertIn('data:image/jpeg;base64,', (output/'对话存档.html').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
