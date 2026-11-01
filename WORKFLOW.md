# 给本机 Agent 的执行流程

## 第一次使用

用户让你参考本仓库备份时，先 clone / pull `https://github.com/unryuu/tokens`，检查工作区改动，不覆盖用户的修改。阅读 README；不要创建或改写 AGENTS.md。备份、统计和本地可视化可直接执行，**提交/上传统计必须获得用户授权**。本公共仓库提供工具；个人统计默认被忽略，不向上游提交个人账本。

确认 Python 3.9+。macOS 使用 `python3`，Windows 使用 `py -3`；已有 `.venv` 时优先用它。若 `.jsonl.zst` 无法解压，先查 `zstd` 命令和 Python 内置 `compression.zstd`，否则在项目虚拟环境安装可选依赖，不改系统 Python：

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-optional.txt
```

Windows 的安装目标改为 `.venv\Scripts\python.exe`。如果本机没有 uv，先按本机约定配置依赖。Windows 长路径报错时，可把仓库放在较短路径，例如 `C:\agent-backup`。macOS 和 Windows 已有实际使用记录；每台新电脑仍需检查本次扫描和备份结果。

探测本机实际安装过的 agent，运行默认备份，再查看 `backups/LATEST` 对应清单中的 probes / errors。关注终端、编辑器、桌面版、不同 provider / profile，以及 WSL 内独立的 Linux 主目录。默认路径不完整时，继续检查软件自己的会话目录，添加被 Git 忽略的 `local-sources.json` 后重跑，不复制 auth、配置密钥、浏览器 Cookies 等无关文件。扩展配置例：

```json
[{"app":"claude","path":"D:/old-profile/.claude/projects","suffixes":[".jsonl",".json"]},
 {"app":"codex","path":"D:/old-profile/.codex/sessions","suffixes":[".jsonl",".zst"]},
 {"app":"other-agent","path":"D:/agent/transcripts","suffixes":[".jsonl"]}]
```

`app` 为 claude / codex / workbuddy / codebuddy 时进入对应解析器，其他标签只备份。遇到新格式，先检查记录结构再扩充解析器及测试，不能把所有 `usage` 字段简单相加。不要用文件修改日期代替调用日期。

## 每次备份

首次先运行 `python3 scripts/backup.py --scan`，检查本地 `reports/discovery.json` 的探测目录和文件分类，补全额外来源；扫描不创建备份。再运行 `python3 scripts/backup.py`，检查退出码、清单 errors、SHA-256 校验和 `reports/<快照>/daily.csv`。快照通过时间戳定位，原文不改写。文件级增量不等于追加几个字节就只保存几个字节：变化的 JSONL 和数据库保存完整新文件，未变化文件共享。脚本保留上一快照里已从源目录消失的文件，但同一路径被截短的旧版本仍要到旧快照找。随后按下面的授权规则检查可清理内容。

告诉用户本次复用/新增字节、累计及最近逐日的输入＋输出总量与 LLM 输出量、无法解析的行或 unknown 日期、未统计的软件。日期统一 UTC+08:00。使用报告里的实际数字，不把数据库 counters 再加到调用统计里。原始记录有缓存等重复输入，token 合计可以远大于对话文本字数。

`.local/device.json` 是本机随机 ID；换电脑不要复制这个文件，原设备重装想延续自己的统计分片时可从自己的旧本机副本恢复。每台电脑拥有独立分片。

## 每次备份后的清理

先完成只读覆盖检查；用户授权清理后，将已证实重复的内容移到系统回收站。有明确持续授权时，后续不再逐次询问；此公共流程本身不代表授权。保留最新完整快照及旧版本独有的数据；不清空回收站，不删除 agent 的源记录。此步骤由执行工作流的 agent 完成，当前 `backup.py` / 双击入口不会自行删除旧快照。

1. 新备份必须成功、清单无错误并通过校验；先保存清理前本机 `.local/events.csv` 的副本与统计合计，供清理后比较。新备份失败就保留全部旧快照。
2. 检查旧快照清单及 SHA-256。相同哈希可认定重复；旧 JSONL 是新文件完整字节前缀时可认定已覆盖。截短、改写或不相同的记录不能仅凭文件名或日期认定重复。SQLite 用只读模式逐表比较主键和记录内容，不能只比较行数；旧库有独有或不同版本的记录就保留旧库。
3. 将旧快照独有的文件、历史版本和日志库先保留在 `backups/retained-<时间>/`，可在本机使用硬链接。写入 `manifest.json`：保持原来的 `app` 分类，保存 `source`、相对 `path`、`bytes`、`sha256`、`method` 等字段，以及 `files`、`errors`、`probes`。校验保留文件，使统计器还能读取其中的会话，而不是只留一个无清单目录。已有 retained 目录也遵循相同覆盖检查，不因名字像临时文件就移走。
4. 确认最新版和独有数据均已妥善保留后，才把被替代的旧快照整目录移入 macOS 回收站 / Windows 回收站。记录来源、回收站位置和保留位置到被 Git 忽略的 `.local/cleanup-plan.json`；不能使用永久删除作为替代。
5. 重新校验保留文件并运行 `--report`，比较清理前后的调用哈希、日期和各项用量，应完全一致。出现变化时停止进一步清理并排查，不能把用量下降当作正常结果。若发现备份内容被用户改过，按仓库约定先询问，不自行恢复。
6. 汇报项目实际占用、逻辑大小、移走内容与保留内容。说明回收站中的独有数据要等用户清空回收站后才释放磁盘空间；硬链接的逻辑大小不能当作实际占用。

## 用户授权上传统计后

使用用户自己的统计仓库（建议私有），不要把个人分片提交到 `unryuu/tokens`。在独立工作副本中配置统计仓库为 origin，保留脚本与工作流。仅在这个统计仓库中移除 `.gitignore` 的 `/statistics/*` 和 `!/statistics/README.md` 两行，使脱敏账本可提交；其他忽略规则保持不变。新电脑克隆这个统计仓库，但不要复制其他电脑的 `.local/`。调用哈希不等于匿名化，公开统计前确认日期和使用习惯也可以公开。

1. 确认本地备份成功，再运行 `python3 scripts/backup.py --publish-stats`。这只生成 `statistics/devices/<本机ID>/` 和合并统计，不发送网络请求。
2. 用 `git status` / `git diff` 检查，只提交工作流与 `statistics/`。原始对话、manifest、本机配置和私人报告不能强制加入 Git。统计 CSV 的调用 ID 已哈希，字段有固定白名单。
3. 提交自己的更改后 `git fetch origin`、`git rebase origin/main`。不同设备的分片各自保留；如果仅 `statistics/daily.csv` / `daily-total.csv` / `daily-model.csv` / `summary.json` / `OVERVIEW.md` / `token-charts.html` / `usage-card.html` 冲突，运行 `--merge-stats` 重建它们，标记解决并继续 rebase，不丢弃任一设备的分片。若同一个设备分片冲突，先核实是否错误复用了 device ID；不要直接 ours/theirs 覆盖。
4. 再运行 `--merge-stats`，检查去重合计及 `git diff --check`，如有派生统计变化则提交，最后 push。脚本不会替你自动 commit 或 push。拒绝发布或未授权时，工作仍完整保留在本地。

## 工作目录＋备份区＋多台电脑的对账去重

工作目录和备份区都要扫描，不能为避免重复而排除整个备份区，因为其中可能有独有记录。识别实际的会话文件并按原软件分类解析，保留原始 ID、时间和用量；不要按文件所在位置改写软件类别，不把本仓库的 backups 再作为普通扫描来源递归备份。统计器已单独读取本机保留快照。

去重单位是每次模型调用，不是文件或整段聊天。相同文件可用 SHA-256 识别；不同版本、部分重叠的会话仍要解析到调用级，重复调用只计一次，独有调用全部保留。文件路径、文件名、电脑名称和 device ID 不参与调用身份，不能因 A 的文件复制到 B 的其他路径就生成新身份。重新压缩或解压后也应保持同一批调用身份。

当前身份规则：Claude 主要使用消息 ID；WorkBuddy 使用提供商请求/消息标识；Codex 使用原始事件时间与用量信息。软件类别和调用身份生成 SHA-256，作为账本的 event_id。同一调用的流式片段或后续完整版本，按现有规则保留最大计数，不把几个版本相加。这里只去除同一次消耗的副本；两台电脑各自调用模型做同一项工作，是真实的两次消耗，即使文字相同也应分别统计。

本地先合并工作目录、备份区和保留快照的调用；授权发布后，每台电脑更新自己的 `statistics/devices/<随机ID>/events/` 月度账本。GitHub 保存这些分片作为共同对账依据，`--merge-stats` 对所有分片再次按 event_id 取并集，生成逐日表和合计。不得直接相加各电脑的总数。重复导出、复制原始记录或重复运行合并，都不应增加同一调用的计数。各设备保持独立 device ID，使用同一统计规则版本。

发布的对账元数据仅包含调用哈希、日期、软件类别、记录中的模型名和 token 计数；不上传原文、原始消息 ID、标题、本机路径或密钥。源文件位置等追溯信息留在本机清单。若导入过程重新生成 ID 或修改时间，必须先核对导入映射，当前哈希规则不能保证自动识别这种重复。跨工具身份映射需明确实现和验证后才能用于扣重；无法确认时报告影响范围，不按“文字相似”擅自合并。

验收例：A 原目录有甲、乙，B 工作目录有乙、丙，B 备份区有甲、乙、丁。B 本地应得到甲、乙、丙、丁各一次；与 A 上传的账本合并后仍是这四次，丁不能因来自备份区而漏掉。移动文件、增加一份副本、重复发布均不改变结果。扩展解析器或导入支持时，用这个例子验证，并核对输入＋输出总量与 LLM 输出量两项口径。

统计去重不代表可以删除用户的项目备份区。源文件保持不动；本仓库旧快照的回收站清理只按前面的授权流程执行。

## 存储与恢复

旧快照重复文件可用 `--compact` 共享存储：先逐一校验旧文件，再用相同内容的硬链接替换重复副本，目录和内容不变。若发现校验不符，停止并询问用户是否改过备份，不自动恢复。压缩/增量不等于可以放心改快照中的文件；读取 SQLite 快照应使用只读模式，做实验先复制到别处。

查看快照内 `files/` 即可直接读取或复制原始文件。SQLite 是一致性备份，不是原文件字节镜像。程序失败时保留已有快照；不要清空 backups 重试。每次备份按上面的授权规则清理重复快照；超出已证实重复的范围不删。

## 本地可视化和图片

备份或 --report 后先打开 `reports/<快照>/statistics/usage-card.html` 查看私有合并图表；不必发布账本。本公共工具仓库默认忽略这些产物。需要更新本地发布分片时运行 --publish-stats（不联网），已有多机发布账本用 --merge-stats 重新去重汇总；仅重画已发布图表可运行 `python3 scripts/charts.py`。

用浏览器打开 `statistics/usage-card.html`，包括概览热力图、累计趋势与每日柱、软件和模型占比，支持模型筛选及全部/30天/7天。`statistics/token-charts.html` 是可嵌入的 D3 图表片段，依赖 CDN；独立用量卡无需外部依赖。日期统一 UTC+08:00，曲线和热力图采用输入＋输出总量，单独显示模型输出。

导出黑底图片：打开用量卡的 `?theme=black` 地址（本地 HTTP 或 file URL 均可），选择“全部”，依次切换概览、趋势、软件，截取卡片及四周黑色边距，保存 PNG。保持三张尺寸相同，检查文字、图例和日期完整。示例见 `examples/`。使用浏览器截图，无需生成式图片工具。

## 按模型统计与旧账本升级

每次备份或 `python3 scripts/backup.py --report` 会从当前及保留的历史快照提取模型信息，写入私有 `.local/events.csv` 和 `reports/<快照>/daily-model.csv`。同时将本机最新调用与已发布的多机账本按调用 ID 合并，生成 `reports/<快照>/statistics/usage-card.html`；不改写已发布分片，不联网。概览、热力图、趋势和软件分布共用模型筛选，“模型”页按读写总 token 排序，并展示输出量与模型识别覆盖率。重新统计不会扫描未备份的新会话；需要纳入新记录时先执行正常备份及清理。

Claude 使用消息的 model；Codex 按 token_count 所处轮次的 turn_context.model 归属，不能用会话最后选中的模型覆盖整段历史；WorkBuddy / CodeBuddy 优先使用消息模型，其次提供商 model / requestModelId。缺失字段归为 unknown，副本的已知模型可补全未知模型；两个已知模型不一致时归为 conflicting，保留用量并报告冲突。模型标签按日志原样区分，不擅自合并第三方别名，不宣称可以证明实际底层模型。

账本 schema_version 升为 2，新增 model 列；rules_version 仍为 2，调用 ID、日期和 token 计数规则不变，模型不参与去重身份。新版同时读取旧、新表头；旧分片归入 unknown，与新版副本合并时只补模型、各计数仍取最大值，不能将模型分组再相加到原合计。其他电脑先拉取新版流程，再用 --report 从自己保留的原始备份重算。发布仍须用户授权：--publish-stats 升级本机分片并保留既有历史，--merge-stats 合并所有电脑；新旧分片可混用，不能因其他电脑还未升级而丢掉它们的用量。

新增 daily-model.csv（日期、软件、模型、调用数及两种 token 口径），summary.json 提供 metrics_by_model 和模型覆盖量。核对升级前后每条调用的 ID、日期及原有计数字段完全一致，模型分组的总和应等于全量；重复合并、复制文件及发布不应增加调用。原始记录、私人报告和新的模型统计不因工具升级自动上传；公开 tokens 仓库只同步通用实现、合成测试和流程。

## 按会话名导出 Claude 对话

用户说“导出 Claude 会话《某个名字》”时，在本工作区运行 `scripts/export_claude.py`，生成与纪念存档相同风格的 Markdown 和离线 HTML，不需要重新备份或调用模型。没有项目虚拟环境时先运行 `uv venv .venv`，再安装 `requirements-export.txt`；macOS 使用 `uv pip install --python .venv/bin/python -r requirements-export.txt`，Windows 使用 `.venv\Scripts\python.exe`。随后均用该环境的 Python 运行：

```sh
python scripts/export_claude.py "会话名称" --list
python scripts/export_claude.py "会话名称"
python scripts/export_claude.py --session-id "确认后的会话ID"
python scripts/export_claude.py --source "会话.jsonl"
```

搜索覆盖本机 `.claude*` profile、`CLAUDE_CONFIG_DIR`、`local-sources.json` 中分类为 claude 的来源，以及本仓库备份清单中的 Claude JSONL。额外迁移目录可用 `--search-root "目录"` 补充。优先完整标题，其次唯一的部分标题；同名但会话 ID 不同，先向用户确认候选及日期，再用 `--session-id`，不能自行猜选。同一 ID 多副本优先本机原记录，原记录缺失时选消息时间较新的备份；不拼接不同版本，必要时用 `--source` 指定版本。没有自定义标题时可用会话 ID 或文件路径定位。当前只导出 Claude Code JSONL，不能把 Codex 或普通 Claude 网页记录当作兼容格式。

默认输出到被 Git 忽略的 `reports/exports/<日期>/<标题>-<时间>/`，每次新建，不覆盖旧导出。正文保留用户与 Claude 原话、顺序和北京时间；工具调用、返回、自动通知及分支发言放入附录，HTML 可展开。日志实际包含的图片内嵌到 HTML，并另存 assets；源 JSONL 保留字节副本，同名会话目录中的子代理和工具附件一并存档。外部引用文件不自动打包，未写入日志的内容无法凭空恢复。损坏或仍在写入的 JSONL 行在清单及阅读版提示，原字节仍保留；系统提示、思考签名和重复请求载荷留在原始记录，不混入阅读正文。

完成后核对 `导出清单.json` 的 complete 状态、消息条数、警告和 SHA-256，在浏览器检查开篇、结尾及附件，再给用户 HTML、Markdown 和目录链接。导出文件、截图和清单均不提交 Git；只有通用脚本、模板、依赖说明、合成数据测试和工作流同步到公开 tokens 仓库，具体会话内容须另获公开授权。
