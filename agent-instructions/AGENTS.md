# AgentGate Workflow

所有 AI agent 在本仓库提交代码时必须走 AgentGate 流程。不要手写 commit trailer，不要手写单行 MR 描述，不要绕过本地校验。

## 提交前

1. 修改代码后，如实记录 AI 编辑证据到 `.governance/ai-evidence.jsonl`。
2. 改生产代码时，用 `governance/scripts/record_test_run.py` 包装测试命令并留下测试证据。
3. 提交前运行相关测试、风险扫描和测试痕迹检查。
4. 使用 git hook 自动写入 `AI-Usage` / `AI-Lines` / `Tested` trailer；缺失时先修 hook，不要手填。

## MR 前

MR 描述必须由 `governance/scripts/create_mr.py` 生成，一条命令完成：

```bash
python governance/scripts/create_mr.py --why "<从用户原始需求提取的任务背景>"
```

脚本自动生成规范描述并提交 MR，你只需提供 `--why`。不要手写 MR 描述，不要用单行描述。

## CI 兜底

CI 是最后防线，不是主要工作流。AI 提交前必须先在本地跑同一套校验，失败就修复后再推送。
