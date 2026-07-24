---
applyTo: "**"
---

# MR 治理规范 v1 — Agent 指令

本仓库要求所有 AI agent 在提交代码前完成以下检查。

## 提交前必做

1. 每次编辑源码文件后，向 `.governance/ai-evidence.jsonl` 追加一行证据（见下）
2. 改了生产代码就写测试，用 `python governance/scripts/record_test_run.py -- <测试命令>` 跑，留痕
3. 扫描 diff，对照 `docs/governance/risk-types.md` 检查 8 类风险模式
4. 命中的代码上方加 `risk:*` 注解（单行格式）
5. 提交时 `AI-Usage` 和 `Tested` trailer 由 git hook 自动写入，无需手填
6. 创建 MR：用 `create_mr.py --dry-run` 自动生成描述，并用 `validate_mr.py` 校验通过后再提交，**不手填描述**

```bash
python governance/scripts/create_mr.py \
  --dry-run \
  --target-branch master \
  --why "<任务背景>" \
  > .governance/mr.md

sed '1,2d' .governance/mr.md \
  | python governance/scripts/validate_mr.py \
      --diff-base origin/master \
      --config governance.config.yml
```

> MR 描述自动拼装：背景(--why)、变更内容(从 diff)、自测(从测试证据)、风险(自动评估)、AI 元数据(从 trailer)。
> 原始 Markdown 必须保留 `## 背景`、`## 变更内容`、`## 自测确认`、`## 风险与回滚` 二级标题；普通文本标题不合规。
> 测试放行：有全绿记录且本次改了测试文件 / 加 `// risk:untested reason:"..." owner:@team reviewed:<今天>` / 命中 config 白名单。带失败测试（`Tested: fail`）一律拒合。

## AI 证据采集（自动算占比，不靠人工判断）

每次成段生成代码后向 `.governance/ai-evidence.jsonl` 追加一行：

```json
{"ts":"2026-06-26T10:00Z","tool":"copilot","model":"<模型>","file":"src/Foo.cs","added":40,"removed":2}
```

提交时 `collect_ai_usage.py` 对照实际 diff 自动算 none/light/medium/heavy。

> Copilot **内联 Tab 补全**混在手敲里、无法精确测行数：这种情况只写工具标记（省略 added/removed），脚本判为 `used`（程度未知），不伪造比例。成段生成（如 Copilot Chat 整段插入）则照实记行数。

## 风险注解格式

```
// risk:<type> reason:"<理由≥10字>" owner:@<团队> reviewed:<YYYY-MM-DD>
```

reason 禁止含：临时 / 先这样 / 历史原因 / TODO / hack / wip / temp / for now

8 类类型：`auth-bypass` `magic-id` `swallowed-exception` `suppressed-warning`
`skipped-test` `time-bypass` `env-hardcode` `todo-no-context`

删测试额外加 `risk:test-removal`。

## AI-Usage 等级（由脚本按占比自动判定，无需手填）

| 占比 (AI改动行/总改动行) | 等级 |
|---|---|
| 0 | none |
| (0, 20%] | light |
| (20%, 60%] | medium |
| (60%, 100%] | heavy |
| 仅补全标记、无可信行数 | used |

## 硬禁止（CI 直接拒合）

- 明文密钥 / token
- 删测试无 risk:test-removal 注解
- catch 静默吞异常无注解
- 改 ci/ CODEOWNERS .gitlab-ci.yml governance.config.yml 须人工 approve

详细规则：`docs/governance/risk-types.md` | `docs/governance/mr-spec.md`
