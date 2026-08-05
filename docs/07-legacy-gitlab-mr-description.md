# 07 · 旧版 GitLab MR 描述来源设计

## 目标

GitLab 11.x 的 branch pipeline 不提供 MR 描述变量，部分部署还会在 nginx 层禁止项目接口。MR 描述门禁必须满足：

- 普通开发者不配置 Personal Access Token；
- source branch pipeline 不接触合并凭据；
- 缺少可信输入时默认失败；
- 结果不夸大已经验证的事实；
- 现代 GitLab 与旧版 GitLab 共用同一个校验实现。

## 模块与接口

`gitlab_mr_compat.py` 是描述解析与校验模块。CI 调用方只提供：

```text
diff_base + target_branch + config + output
```

来源选择、缺失处理、证据字段和安全策略都隐藏在模块内部，避免每个仓库重新拼装 `fail-if-*` 参数。

## 来源顺序

| 顺序 | Adapter | 是否验证真实 MR | 默认启用 |
|---|---|---:|---:|
| 1 | `CI_MERGE_REQUEST_DESCRIPTION` | 是 | 是 |
| 2 | GitLab 项目接口 | 是 | 是（branch pipeline） |
| 辅助 | `.agentgate/mr-description.md` | 否，只做绑定与一致性校验 | 可选 |

分支清单不能作为 MR 描述的替代输入。清单存在时，必须相对目标分支发生变化，并且去除绑定头后的内容必须与 GitLab 实际 MR 描述一致。

旧版 branch pipeline 必须通过 GitLab 项目接口读取实际打开的 MR。AgentGate 会按通用顺序读取可用的 GitLab token 环境变量，最终由 GitLab API 判断该 token 是否有权限；接口、项目 ID、token 或实际 MR 读取失败时一律失败。支持的常见变量包括：

API 返回的 `target_branch` 是 diff 和 manifest 新鲜度校验的权威目标分支，不能用默认分支代替。一个 source branch 同时对应多个打开 MR 且流水线没有提供目标分支时，AgentGate 因歧义失败。

```text
AGENTGATE_GITLAB_READ_TOKEN
AGENTGATE_GITLAB_TOKEN
GITLAB_TOKEN
GLAB_TOKEN
GOVERNANCE_MR_VALIDATE_TOKEN
GOVERNANCE_MERGE_BOT_TOKEN
PRIVATE_TOKEN
```

## 证据语义

结果文件至少包含：

```json
{
  "status": "pass",
  "source": "gitlab-api",
  "actual_mr_verified": true,
  "description_sha256": "..."
}
```

`status=pass` 时必须同时满足 `actual_mr_verified=true`。AgentGate 不再允许仅验证仓库清单后通过。旧版平台接口不可用时，门禁失败并要求修复 API 或只读 token 配置。

## 开发流程

```bash
python governance/scripts/agentgate.py mr prepare \
  --why "<用中文说明任务背景>"

git add .agentgate/mr-description.md
git commit -m "docs: prepare merge request description"
git push
```

手工创建 MR 时，将清单原文粘贴到 GitLab 描述框。现代 MR pipeline 会直接校验网页中的实际描述。

## 失败策略

- 实际 MR 描述为空或不合规：失败；
- 项目接口、项目 ID 或 GitLab token 缺失：失败；
- 找不到当前 source branch 对应的打开 MR：失败；
- 清单存在但未由当前分支更新：失败；
- 清单存在但与实际 MR 描述不一致：失败；
- 默认分支自身流水线：跳过，因为不存在待校验 MR。
