# AgentGate Workflow

所有 AI agent 在本仓库提交代码时必须走 AgentGate 流程。不要手写 commit trailer，不要手写单行 MR 描述，不要绕过本地校验。

## 提交前

1. 修改代码后，如实记录 AI 编辑证据到 `.governance/ai-evidence.jsonl`。
2. 改生产代码时，用 `governance/scripts/record_test_run.py` 包装测试命令并留下测试证据。
3. 提交前运行相关测试、风险扫描和测试痕迹检查。
4. 使用 git hook 自动写入 `AI-Usage` / `AI-Lines` / `Tested` trailer；缺失时先修 hook，不要手填。

## MR 前

MR 描述必须由 `governance/scripts/create_mr.py` 生成，并通过 `validate_mr.py` 校验。

```bash
python governance/scripts/create_mr.py \
  --dry-run \
  --target-branch master \
  --why "<从用户原始需求提取的任务背景>" \
  > .governance/mr.md

sed '1,2d' .governance/mr.md \
  | python governance/scripts/validate_mr.py \
      --diff-base origin/master \
      --config governance.config.yml
```

校验通过后再创建 MR。原始 Markdown 必须保留这些二级标题：

```markdown
## 背景
## 变更内容
## 自测确认
## 风险与回滚
```

普通文本标题如 `背景 xxx` 不合规。GitLab 渲染后可能隐藏 `##`，但原始 MR 描述必须包含 `##`。

## CI 兜底

CI 是最后防线，不是主要工作流。AI 提交前必须先在本地跑同一套校验，失败就修复后再推送。
