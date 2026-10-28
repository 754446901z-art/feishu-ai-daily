# feishu-ai-daily

每天北京时间 09:00，使用 GitHub Actions 和现有飞书企业自建应用机器人，向个人私聊推送最多 3 种未推送过的 AI 使用方法。优先选用核实并审核通过的方法，不足时从原有 42 种策略中补充，明确标明“经典策略，非最新发布”。

**零付费依赖：** Python 3.12 标准库、公开技术网页、公开仓库的标准 GitHub 托管 runner。无需模型 API、试用额度、搜索服务账号或服务器，不上传 Actions artifact、占用其单独存储额度；不会读取或修改 GitHub Secrets 的配置。已有 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_OPEN_ID` 继续使用。

## 内容如何进入推送

1. `discover.py` 从 OpenAI Cookbook、Anthropic Engineering、Google Developers Blog 发现实践类文章，优先最近 7 天，允许近期内容补充。它保存原始链接、标题、明确的发布日期依据、短原文摘录和内容指纹；没有发布日期就保留未知，抓取时间不冒充发布日期。
2. 自动发现的内容只进入 `new_methods.json` 的 `pending_review` 候选库。免费抓取和关键词规则无法可靠地自动总结、核实完整方法，因此不会自动编造原理、步骤或提示词，也不会把候选直接发到私聊。
3. 人工阅读原文后，填写方法名称、原理、场景、步骤、示例提示词及逐字段证据。`review.py` 重新抓取原文，检查证据和指纹后才允许标为 `verified`。来源修改后需重新审核。
4. `send.py` 优先选择审核有效且未发送的方法。日期明确且在最近 7 天的才标记为最近发布；其他已审核方法标明实际日期及较早实践，日期无法核实的文章继续留在候选库。已知日期不会因文章更新、重复抓取而刷新。

“来源可访问、摘录匹配”仅能证明资料依据存在；实际适用性和示例是否忠于原文仍需要审核人判断。待审核库是本方案有意保留的真实性边界。

## 文件与状态

| 文件 | 用途 |
| --- | --- |
| `discover.py`、`source_utils.py` | 有界抓取、官方来源限制、候选筛选和原文核验 |
| `new_methods.json` | 候选及审核通过的方法；不含凭证 |
| `review.py` | 生成审核草稿、核验并批准候选 |
| `send.py` | 固定 ID 去重、旧状态迁移、选择方法、飞书私聊 |
| `state.json` | 已发送 ID、日期、待发送批次、发送结果 |
| `persist.py` | 将候选及发送检查点提交回仓库 |
| `.github/workflows/daily.yml` | 每日采集、测试、发送和状态保存 |
| `.github/workflows/ci.yml` | 离线测试；不调用飞书 |

旧 `next_index` 和 `last_sent` 会迁移并保留：已有推送不会重发。去重针对方法本身，原库的同一方法换个场景也不会再次发送。审核跨来源的同一种方法时，需使用相同 `method_key`；与原策略重合时，使用 `--list-builtins` 列出的对应 ID。自动规则无法保证识别所有语义相同的方法，审核时须检查重复概念。

## 手动运行一次测试

代码进入默认分支后：

1. 打开仓库的 **Actions → Daily AI Strategies to Feishu → Run workflow**。
2. 分支选择默认分支 `main`，保留 `dry_run` 勾选，点击 **Run workflow**。
3. 查看 `Check syntax and run offline tests`、`Discover public practice candidates`、`Preview without sending` 日志。此模式不发飞书消息，也不提交远程状态；候选变化是本次临时预览。
4. 需要验证真实私聊时，再运行一次并取消勾选 `dry_run`。三个原有 Secrets 无需重新配置。若今天已经推送，日志会说明跳过，不会为了测试重复发送；下一天再运行即可。
5. 真实推送成功后，核对飞书收到的内容和仓库 `state.json` 的日期、固定 ID。Actions 成功本身不能代替私聊实际收件确认。

不要删除 `last_sent`、回退 `state.json` 或清空 `sent_ids` 来强行测试，否则会绕过去重记录。

## 审核候选

在本地仓库运行，无需飞书凭证：

```powershell
python discover.py
python review.py --template source_候选ID --output review_draft.json
python send.py --list-builtins
```

从 `new_methods.json` 复制真实候选 ID，替换上面的 `source_候选ID`。打开草稿，阅读原文，填写 `name`、`principle`、`scenarios`、`steps`、`example_prompt`、`method_key` 和 `review` 中的审核人及各字段原文证据。示例提示词是人工整理的、原理有来源支持的使用示例，发送时会明确标为非原文引用；不得把自己补写的操作当作原文方法。没有足够依据时，保留待审核。

```powershell
python review.py --approve review_draft.json
python -m unittest discover -s tests -v
python send.py --dry-run
git add new_methods.json
git commit -m "Review AI practice candidates"
git push
```

审核草稿默认被 `.gitignore` 忽略。正式方法应保存在 `new_methods.json` 后提交。不要在草稿、日志或库中填写任何凭证、个人业务数据。

## 去重、失败与恢复

- 采集某个来源失败：保留已有方法和记录，继续其他来源；无法使用新库时，以原策略库回退。
- 推送前先把批次保存到 GitHub；保存失败就停止发送，避免只有本机知道进度。
- 请求飞书消息接口前，再持久化“即将发送”检查点。发生超时、进程中断或结果不确定时，后续运行会暂停相关批次，避免自动重复发送。
- 明确发送成功后记录固定 ID；同日重跑、跨日重跑及原库场景变体均不会重复发送。飞书消息 UUID 只作为额外保护，不能代替持久状态。
- 如果保存最终结果失败，仓库仍保留发送请求前的检查点，下一次运行会暂停。核对飞书实际收件结果后再人工处理，不用猜测或重复发送来恢复。

出现不确定批次时，先检查飞书个人私聊和 Actions 日志。确认实际收件结果后，在最新本地仓库中执行对应一条命令，并提交状态：

```powershell
# 已确认收到：登记为已发送，不再次发消息
python send.py --resolve sent --note "已核对飞书私聊，确认收到对应批次"

# 已确认没有收到：解除保留，后续正常运行才会重新选择/发送
python send.py --resolve not_sent --note "已核对飞书私聊及运行日志，确认未收到对应批次"
```

上述恢复命令本身不发送消息。无法确认是否收到时，保持暂停并继续核实。

完成核对和状态处理后再保存：

```powershell
git add state.json
git commit -m "Resolve Feishu delivery outcome"
git push
```

## 运行边界

- 原库只有 42 种不同方法。初始记录已发送 3 种，因此剩余 39 种只够补充 13 个完整批次。若长期无人审核新内容，库会耗尽；届时少发或停止并明确提示，不会重复旧方法或捏造新方法来凑数。需要持续审核，才能长期保持每天 3 条。
- cron 为 `0,17,37,57 1 * * *`（UTC），即北京时间 09:00 首次触发，09:17、09:37、09:57 兜底。所有运行共用串行控制和持久去重记录；当天成功后跳过发送，结果不明则停止自动重发。GitHub Actions 的定时任务可能延迟或漏触发，兜底只能降低漏发概率，不能保证精确到分钟；公开仓库长期无活动时，GitHub 也可能停用定时工作流。
- 当前公开仓库的标准 GitHub 托管 runner 免费；工作流发现仓库为私有时会跳过执行，且不使用 artifact/cache 存储。本项目没有收费服务调用路径；GitHub、飞书和来源站点的未来政策及可用性不由项目控制。保持公开仓库和标准 runner，避免改用收费 runner 或接入收费服务。
- 可发现技术实践可能涉及收费模型或服务；发现系统自身不购买或调用它们。审核与使用方法时，按实际工具条件选择。
- `contents: write` 用于提交普通 JSON 状态，不增加或更改 Secrets。分支保护若禁止工作流提交，发送前检查点会失败并停止，请先按仓库规则解决写入权限。
- 本地和 Actions 均只需 Python 3.12 标准库；当前北京时间按 UTC+8 计算，无需另装时区数据包。

参考：[GitHub Actions 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)、[定时工作流说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。
