# AgentGate Workflow

## 强制 MR 入口

所有 AI agent 创建 MR 时必须走统一入口，不允许手写空描述或绕过本地校验。

```bash
python governance/scripts/agentgate.py mr prepare \
  --why "<用中文说明本次任务背景>"
```

已配置本地 GitLab API 凭据时，仍可使用原自动创建入口：

```bash
python governance/scripts/create_mr.py \
  --gitlab-api \
  --why "<用中文说明本次任务背景>"
```

**不要用 glab**：glab 在自托管 GitLab（11.x）有两个已知问题——token 写不进 config、project 路径解析返回 404。有本地 token 时用 `agentgate.py mr create --gitlab-api` 直连 REST API；无本地 token 时提交 `.agentgate/mr-description.md` 后手工同步 MR 描述正文。

可选环境变量（一次性配置，用于自动创建/更新 MR）：
```bash
export AGENTGATE_GITLAB_TOKEN="你的token"       # GitLab Access Token，scope: api
export AGENTGATE_GITLAB_PROJECT_ID="123"        # 数字 project id（不要用 owner/repo 路径）
export AGENTGATE_GITLAB_URL="https://gitlab.example.com"  # 不填时自动从 git remote 推导
```

校验失败时必须修复描述、测试记录或风险回滚说明，不允许改低门禁。

所有 AI agent 在本仓库提交代码时必须走 AgentGate 流程。不要手写 commit trailer，不要手写单行 MR 描述，不要绕过本地校验。

## 仓库 Lessons 必读

开始任何修改前，必须读取 `governance/lessons/*.yml`。其中 `enforcement: hard` 是不可违反约束；`enforcement: soft` 是仓库经验和操作提醒。

如果本次修复来自重复错误、门禁失败、线上事故或仓库约束误解，必须补充 `governance/lessons/repository.yml`。能用测试、扫描或门禁验证的写成 `hard` 并绑定可执行检查；暂时只能提醒的写成 `soft`。

## 提交前

1. 修改代码后，如实记录 AI 编辑证据到 `.governance/ai-evidence.jsonl`。
2. 改生产代码时，用 `governance/scripts/record_test_run.py` 包装测试命令并留下测试证据。
3. 提交前运行相关测试、风险扫描和测试痕迹检查。
4. 使用 git hook 自动写入 `AI-Usage` / `AI-Lines` / `Tested` trailer；缺失时先修 hook，不要手填。

## MR 前

MR 描述必须由 AgentGate 统一入口生成：

无本地 token 时：

```bash
python governance/scripts/agentgate.py mr prepare \
  --why "<从用户原始需求提取的任务背景>"
git add .agentgate/mr-description.md
git commit
git push
python governance/scripts/agentgate.py mr body
# 将输出正文手工复制到 GitLab MR 描述
```

有本地 token 时：

```bash
python governance/scripts/agentgate.py mr prepare \
  --why "<从用户原始需求提取的任务背景>"
git add .agentgate/mr-description.md
git commit
git push
python governance/scripts/agentgate.py mr create \
  --gitlab-api \
  --why "<从用户原始需求提取的任务背景>"
```

`--gitlab-api` 模式直连 GitLab REST API，不依赖 glab/gh CLI。只需提供 `--why`，其余全自动。若源分支已有 open MR，自动更新描述。CI 会校验真实 GitLab MR 描述的必填模块，并校验 `.agentgate/mr-description.md` 是否由当前分支更新；不要求两者逐字一致。

## CI 兜底

CI 是最后防线，不是主要工作流。AI 提交前必须先在本地跑同一套校验；真实测试失败、风险注解缺失、描述不合规要修复，token/CLI/API 不可用则走降级创建 MR，不阻断分支提交。
