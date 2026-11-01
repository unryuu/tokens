# 当前状态

公共工具提供扫描、增量备份、校验、按日统计、跨机调用去重、模型排行与筛选，以及按会话名导出 Claude Markdown / 离线 HTML。原始记录、私人报告与个人统计默认被 Git 忽略，经授权的旧版三张示例图保留在 examples/。

2026-11-01：模型字段采用 schema 2，兼容旧账本；去重与计数 rules 2 不变。每日模型表和模型汇总支持 unknown / conflicting；正常备份或 --report 自动生成 reports/<快照>/statistics/ 下的私有合并可视化，不必先发布。其他电脑补全模型需拉取新版并重算自己的备份，发布仍按用户授权。

保留 --scan 只读清点入口和 ?theme=black 导出主题。会话导出的可选依赖见 requirements-export.txt。工作流详见 WORKFLOW.md；脚本不自动上传或清理快照。
