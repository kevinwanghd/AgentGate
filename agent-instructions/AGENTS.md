# AgentGate Workflow

## 强制 MR 入口

所有 AI agent 创建 MR 时必须走统一入口，不允许手写空描述或绕过本地校验。原则：AgentGate 可以阻止不合规合并，但不能因为缺 token、缺 CLI 或旧版 GitLab 能力不足而阻碍代码提交到分支。

```bash
python governance/scripts/create_mr.py --why "<用中文说明本次任务背景>"
```

该命令会生成中文 MR 描述并本地运行 `validate_mr.py`、`scan_risks.py` 和配置的测试命令；有 GitLab token 时自动走 API，有 `glab`/`gh` 时走 CLI，都不可用时降级打印 MR 链接和描述，供人工创建 MR，不得卡住代码提交。

兼容旧安装或仅需生成 branch manifest 时，才使用 fallback：

```bash
python governance/scripts/agentgate.py mr prepare \
  --why "<用中文说明本次任务背景>"
```

校验失败时必须修复描述、测试记录或风险回滚说明，不允许改低门禁；提交 MR 通道失败时走降级路径，不允许因为工具链不可用阻断分支提交。

所有 AI agent 在本仓库提交代码时必须走 AgentGate 流程。不要手写 commit trailer，不要手写单行 MR 描述，不要绕过本地校验。

## 提交前

1. 修改代码后，如实记录 AI 编辑证据到 `.governance/ai-evidence.jsonl`。
2. 改生产代码时，用 `governance/scripts/record_test_run.py` 包装测试命令并留下测试证据。
3. 提交前运行相关测试、风险扫描和测试痕迹检查。
4. 使用 git hook 自动写入 `AI-Usage` / `AI-Lines` / `Tested` trailer；缺失时先修 hook，不要手填。

## MR 前

MR 描述必须由 AgentGate 统一入口生成：

```bash
python governance/scripts/create_mr.py \
  --why "<从用户原始需求提取的任务背景>"
```

脚本自动生成规范中文描述并完成本地校验，你只需提供 `--why`。它会按“GitLab API → glab/gh CLI → 打印 MR 链接和描述”的顺序降级；最后一种情况需要人工创建 MR，但不能让代码提交停在本地。

## CI 兜底

CI 是最后防线，不是主要工作流。AI 提交前必须先在本地跑同一套校验；真实测试失败、风险注解缺失、描述不合规要修复，token/CLI/API 不可用则走降级创建 MR，不阻断分支提交。
