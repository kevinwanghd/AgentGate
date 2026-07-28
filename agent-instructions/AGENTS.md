# AgentGate Workflow

## 强制 MR 入口

所有 AI agent 创建 MR 时必须走统一入口，不允许手写空描述或绕过本地校验。旧版 GitLab 在推送前先生成可提交的描述清单：

```bash
python governance/scripts/agentgate.py mr prepare \
  --why "<用中文说明本次任务背景>"
git add .agentgate/mr-description.md
git commit -m "docs: prepare merge request description"
```

兼容旧安装时可使用等价命令：

```bash
python governance/scripts/create_mr.py \
  --prepare \
  --why "<用中文说明本次任务背景>"
```

该命令会先生成规范 MR 描述并本地运行 AgentGate 描述校验。CI 校验提交到分支的 `.agentgate/mr-description.md`，不依赖开发者 Personal Access Token。校验失败时必须修复描述、测试记录或风险回滚说明。

所有 AI agent 在本仓库提交代码时必须走 AgentGate 流程。不要手写 commit trailer，不要手写单行 MR 描述，不要绕过本地校验。

## 提交前

1. 修改代码后，如实记录 AI 编辑证据到 `.governance/ai-evidence.jsonl`。
2. 改生产代码时，用 `governance/scripts/record_test_run.py` 包装测试命令并留下测试证据。
3. 提交前运行相关测试、风险扫描和测试痕迹检查。
4. 使用 git hook 自动写入 `AI-Usage` / `AI-Lines` / `Tested` trailer；缺失时先修 hook，不要手填。

## MR 前

MR 描述必须由 AgentGate 统一入口生成：

```bash
python governance/scripts/agentgate.py mr prepare \
  --why "<从用户原始需求提取的任务背景>"
```

脚本自动生成规范描述清单，你只需提供 `--why`。将清单与代码一起提交并推送；手工创建 MR 时将清单原文粘贴到描述框。

## CI 兜底

CI 是最后防线，不是主要工作流。AI 提交前必须先在本地跑同一套校验，失败就修复后再推送。
