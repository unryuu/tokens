# 当前状态

公共工具版已从个人备份项目提取，包含扫描、增量快照、校验、按日统计、调用级跨机去重和三视图可视化。原始记录及个人统计默认被 Git 忽略；示例只提供经授权的三张黑底图片。

新增 `--scan` 只读清点入口及 `?theme=black` 图片导出主题。执行流程见 [WORKFLOW.md](WORKFLOW.md)，使用与统计范围见 [README.md](README.md)。脚本不会自动上传统计或清理旧快照。

`scripts/export_claude.py` 支持按会话名、ID 或 JSONL 路径导出原话 Markdown、离线 HTML、工具附录及原始记录。可搜索本机 profile、额外来源与已有备份，同名会话需明确选择；导出默认留在被忽略的 reports/exports/。HTML 渲染依赖单列于 requirements-export.txt，测试只用合成记录，不包含用户会话。
