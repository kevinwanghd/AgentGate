# 04 · AI Agent 提交流程

面向 **AI agent 和使用 agent 的开发者**。这是每次提交代码都要走的标准流程。

> agent 工具已通过 `CLAUDE.md` / `.hermes.md` / `AGENTS.md` 等自动加载本规范摘要，本文档是完整版参考。

---

## 提交前四步（每次必做）

```
┌──────────────────────────────────────────────┐
│ 0. 记证据     每次编辑后写 ai-evidence.jsonl    │
│ 0.5 跑测试    改生产码就写测试, record_test_run │
│ 1. 风险扫描   对照 risk-types.md 检查 diff      │
│ 2. 加注解     命中的代码上方加 risk: 注解        │
│ 3. 提交       AI-Usage / Tested trailer 自动写  │
│ 4. 创建 MR    create_mr.py --why 自动生成提交    │
└──────────────────────────────────────────────┘
```

---

## 第 0.5 步：跑测试留痕（改了生产代码必做）

没测过的生产代码不该提交。本规范不靠"声称测过"，而靠真实运行留痕：用记录器包装测试命令，按真实退出码记结果，提交时 hook 汇总成 `Tested:` trailer，CI 读 trailer 判断。

```bash
# 写完/改完测试后, 用记录器跑 (别直接 dotnet test)
python governance/scripts/record_test_run.py -- dotnet test X.Flow.sln --filter Category=Unit
# 本地预检
python governance/scripts/check_tested.py --staged
```

放行任一即可：
- 有全绿测试记录，**且**本次 MR 改动了测试文件
- 确无法单测的代码加注解：`// risk:untested reason:"..." owner:@team reviewed:<今天>`
- DTO/迁移/生成代码等命中 `governance.config.yml` 的 `testing.exclude_paths` 白名单

**硬规则**：测试记录里有失败用例（`Tested: fail`）→ 任何模式下都拒合。

---

## 第 0 步：记录 AI 证据（AI 占比自动算的基础）

本规范**不让任何人主观判断"这次用了多少 AI"**。agent 开发时把每次编辑的客观证据追加到 `.governance/ai-evidence.jsonl`，提交时脚本对照真实 diff 自动算等级。

每次成功 Edit/Write 一个源码文件后追加一行：

```json
{"ts":"2026-06-26T10:00:00Z","tool":"claude-code","model":"<模型>","file":"src/Foo.cs","added":80,"removed":3}
```

该文件已 gitignore，是会话产物。等级按"AI 改动行 / 总改动行"占比自动映射：0=none、(0,20%]=light、(20%,60%]=medium、(60%,100%]=heavy；补全类工具（无精确行数）记 `used`。

---

## 第 1 步：风险扫描（硬门禁，CI 会拦）

对照 `docs/governance/risk-types.md`，检查你的 diff 是否命中这 8 类模式：

| 类型 | 一句话特征 |
|---|---|
| `auth-bypass` | 字面量 ID / 角色与认证字段比较 |
| `magic-id` | 业务代码硬编码 ObjectId / UUID / 长数字串 |
| `swallowed-exception` | catch 块既不 throw 也不 log |
| `suppressed-warning` | `#pragma warning disable` / `[SuppressMessage]` 等 |
| `skipped-test` | `[Fact(Skip=...)]` / `[Ignore]` 等 |
| `time-bypass` | DateTime 与字面量日期比较 |
| `env-hardcode` | `if (env == "production")` 等 |
| `todo-no-context` | TODO / FIXME 不含 `(owner, 日期)` |

另外：**删除已有测试**需要加 `risk:test-removal` 注解。

---

## 第 2 步：加注解

命中模式的代码，在它**上方一行**（不是行内）加结构化注解。

### 格式

```
// risk:<type> reason:"<理由，≥10字>" owner:@<团队或个人> reviewed:<YYYY-MM-DD>
```

### 真实示例（最常见的免登录场景）

```csharp
// risk:auth-bypass reason:"机器人账号用于数据同步, 无人工登录路径" owner:@ad-platform reviewed:2026-06-25
if (adminUserId == "626786582b50ab8ec08b0fa0" || adminUserId == "64918ccaeb21944ec3ecf952")
{
    // 跳过登录校验
}
```

### 四个字段，缺一不可

| 字段 | 要求 |
|---|---|
| `risk:<type>` | 必须是 8 类已注册类型之一（或 `test-removal`） |
| `reason:"..."` | ≥ 10 字，说明业务/安全权衡 |
| `owner:@xxx` | 该豁免的负责团队或个人 |
| `reviewed:YYYY-MM-DD` | 填今天日期，有效期 6 个月 |

### reason 禁止用的词（用了视为无效，CI 仍拦）

```
临时   先这样   历史原因   TODO   待确认
quick fix   temp   wip   hack   for now
```

理由必须说清"为什么这样是合理的"，而不是"暂时这样"。

### 多行格式（理由复杂时）

```csharp
// risk-begin
// type: auth-bypass
// reason: 机器人账号做数据同步, 不走登录, 业务方确认见 REQ-1234
// owner: @data-sync
// reviewed: 2026-06-25
// review-cycle: 6m
// risk-end
```

---

## 第 3 步：提交（trailer 自动生成）

跨事业部报表只读 commit trailer，不解析自由文本。装了 hook 后（`bash governance/scripts/install-hooks.sh`），提交时自动追加：

```
feat: 给投放任务加重试机制

实现指数退避重试，最多 3 次。

AI-Usage: heavy
AI-Tools: claude-code
AI-Models: <模型>
AI-Lines: 92/127
Tested: pass (42/42)
Requirement-ID: REQ-1234
```

`AI-Usage` / `AI-Lines` / `Tested` 全部自动算出，**无需手填**。只有 `Requirement-ID:` 是可选的人工/DeliverHQ 字段。

未装 hook 时手动预览/补算：
```bash
python governance/scripts/collect_ai_usage.py --staged --trailer-only
```

---

## 第 4 步：自动创建 MR（不手填描述）

MR 描述是开发过程的产物，不是事后填表。你完成任务时已经知道"为什么改、改了什么、怎么测的"——提交完用一条命令自动生成并提交 MR：

```bash
python governance/scripts/create_mr.py --why "用户需求: 给投放任务加重试机制"
```

脚本自动拼装各段落：

| 段落 | 来源 |
|---|---|
| ## 背景 | `--why`（你从用户原始需求提取，唯一需提供的） |
| ## 变更内容 | 从 git diff 自动生成文件清单 + 增删行数 |
| ## 自测确认 | 从 `.governance/test-evidence.jsonl` 读测试结果 |
| ## 风险与回滚 | 自动判断大变更/敏感路径/schema |
| 治理元数据 | 从 commit trailer 读 AI-Usage/Tested，放折叠块 |

常用选项：
```bash
python governance/scripts/create_mr.py --why "..." --dry-run        # 先预览描述不提交
python governance/scripts/create_mr.py --why "..." --interactive    # 生成草稿后打开编辑器
python governance/scripts/create_mr.py --why "..." --link REQ-1234   # 补关联项
```

支持 GitLab（`glab`）和 GitHub（`gh`）CLI，自动检测。未装 CLI 时打印描述供手动创建。

> DeliverHQ 用户：`--link CR-xxxx`，或让 hook 写的 `Requirement-ID:` 自动带上。

---

## 让 agent 自动完成全流程

如果你用的 agent 已加载规范，可以直接下指令：

```
我已经完成了代码修改。请在提交前：
1. 把每次编辑的证据补进 .governance/ai-evidence.jsonl
2. 改了生产代码就写测试，用 record_test_run.py 跑
3. 对照 docs/governance/risk-types.md 扫描我的 diff，给命中处加 risk: 注解
4. 正常提交，AI-Usage / Tested trailer 由 hook 自动写入
5. 调 create_mr.py --why "<这个任务的背景>" 自动生成并提交 MR
然后告诉我有没有遗漏。
```

---

## 常见错误与排查

| CI 报错 | 原因 | 修复 |
|---|---|---|
| `risk annotation missing for auth-bypass at file:line` | 命中风险模式但没加注解 | 在该行上方加 `risk:` 注解 |
| `risk annotation invalid: reason too short` | reason 少于 10 字 | 把理由写充分 |
| `risk annotation invalid: blacklisted word "临时"` | 理由用了黑名单词 | 换成实质性说明 |
| `risk annotation expired (reviewed > 180d)` | 注解超过 6 个月 | 把 `reviewed:` 更新到今天 |
| `test removed without risk:test-removal` | 删了测试没注解 | 加 `risk:test-removal` 或恢复测试 |
| `secret detected by gitleaks` | 提交了密钥 | 移除密钥，改用 CI variables |
| `[warn] MR description missing ## 自测确认` | 软门禁警告 | v1 不拦，但建议补上（到期会拦） |

---

## 注解会过期，怎么处理

- `reviewed:` 日期超过 6 个月后，该注解标记为"过期"。
- 过期本身**不立即拦你**——只进每周的 `governance/reports/expired-annotations.md`。
- 但只要你的 MR **触碰了**含过期注解的文件，CI 就要求你把 `reviewed:` 更新到当天。
- 这意味着：你不需要专门去维护这些注解，自然会在改到相关代码时顺手刷新。

---

## 下一步

如果想深入理解 CI 门禁如何工作、如何申请豁免，看 `05-ci-reference.md`。
