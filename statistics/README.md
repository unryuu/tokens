# 已发布的逐日统计

- `token-charts.html`：累计折线图与逐日贡献热力图，均按输入＋输出总 token 绘制，随统计合并自动更新。
- `usage-card.html`：仿 Claude desktop 风格的用量卡片，可直接用浏览器打开，含全部/30天/7天切换与按软件拆分，随统计合并自动更新。
- `OVERVIEW.md`：并列展示输入＋输出总量与 LLM 输出总量。
- `daily-total.csv`：所有电脑合并去重后的每日用量，用于年度总结。
- `daily.csv`：每日按 Claude / Codex / WorkBuddy 等来源细分。
- `summary.json`：两种总量、按软件拆分、时间范围、有效天数和设备数。
- `devices/<随机ID>/`：该设备的逐日表、平台元数据，以及按月保存的调用哈希账本。重新合并时读取各设备账本，不把它们的累计数相加。

主要看两列：`input_output_total_tokens` 是输入＋输出之和，包含反复读取的上下文和缓存；`output_tokens` 是 LLM 生成量，不含缓存输入，更适合观察生成规模。输出可能包含推理和工具调用，不等于最终交付字数或成果价值。

日期统一 UTC+08:00。`input_tokens` 统一包含缓存输入，`cache_read_tokens` 和 `cache_write_tokens` 是其中的子项，不能再加到 total 上；`output_tokens` 包含已记录的推理输出。`total_tokens` 沿用提供商记录或该格式定义，可能与子项有少量差异。`calls` 是去重后的用量事件数，接近调用次数，不保证等于实际网络请求数。

只发布固定字段：调用身份的 SHA-256、日期、软件标签、token 数。平台元数据不含主机名或用户名。原始对话、本机源路径和 manifest 不上传。重复调用按同一哈希合并，流式修订取最大计数；这用于消费趋势，不作精确账单。

本机原文备份与其他电脑接入步骤见 [WORKFLOW.md](../WORKFLOW.md)。
