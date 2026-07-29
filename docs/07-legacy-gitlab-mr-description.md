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
| 2 | `.agentgate/mr-description.md` | 否，只验证分支清单 | 是 |
| 3 | GitLab 项目接口 | 是 | 否 |

分支清单必须相对目标分支发生变化，防止新分支复用上一次 MR 的旧描述。

项目接口回退只能通过 `--allow-api-fallback` 显式启用，并且只读取 `AGENTGATE_GITLAB_READ_TOKEN`。以下变量永远不能被描述校验模块读取：

```text
GOVERNANCE_MERGE_BOT_TOKEN
PRIVATE_TOKEN
AGENTGATE_GITLAB_TOKEN
GOVERNANCE_MR_VALIDATE_TOKEN
```

## 证据语义

结果文件至少包含：

```json
{
  "status": "pass",
  "source": "repository-manifest",
  "actual_mr_verified": false,
  "description_sha256": "..."
}
```

`actual_mr_verified=false` 表示结构化描述已随代码提交并通过规范校验，但 AgentGate 没有证明 GitLab 网页中的描述与该清单一致。旧版平台接口不可用时，这个限制不能通过 CI 脚本消除。

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

- 清单缺失：失败；
- 清单未由当前分支更新：失败；
- 清单格式不合规：失败；
- 项目接口回退未显式启用：不访问接口；
- 显式回退但专用只读 token 缺失：失败；
- 默认分支自身流水线：跳过，因为不存在待校验 MR。
