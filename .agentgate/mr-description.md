<!-- agentgate-pr-bind {"base_ref": "origin/main", "changed_paths": ["docs/08-github-rollout-kit.md", "scripts/create_mr.py", "tests/test_regressions.py"], "diff_fingerprint": "121f324cebceab223b209a519ac65b51f6084c85a068efdbe3c1bb00e7e0f76f", "prepared_from_sha": "3ee26f201b7e6d70eb576aae14896462296b22bd", "schema_version": "agentgate.io/pr-description-binding/v1"} -->

## 背景

修复 AgentGate 自身 PR 流程可绕过治理的问题：PR 描述必须由工具生成、绑定当前代码 diff，并在 rollout 推送前强制校验；同时拒绝未提交的非 manifest 改动，避免 PR 正文遗漏本地代码变更。

## 变更内容

- `tests/test_regressions.py` (+52/-1)
- `scripts/create_mr.py` (+48/-2)
- `.agentgate/mr-description.md` (+6/-23)
- `docs/08-github-rollout-kit.md` (+10/-2)

## 不包含的内容

不修改业务仓库内容；不改变 GitLab 私仓现有 CI 模板和 token 使用方式；不直接合并到 main。

## 自测确认

已运行 python -m py_compile scripts\create_mr.py tests\test_regressions.py；已运行 python -m unittest tests.test_regressions.CreateMrManifestTests -v，8 个测试通过；已运行 python -m unittest discover -s tests -v，160 个测试通过。

## 风险与回滚

风险点：新增绑定校验会让缺少 .agentgate/mr-description.md、diff 已变化、或存在未提交非 manifest 改动的分支在推送/创建 PR 前失败；prepared_from_sha 字段只用于审计生成时机，真正阻断依据是排除 manifest 自身后的 diff 指纹。应对：先提交代码改动，再重新运行 agentgate.py pr prepare；rollout 脚本默认 dry-run 且 push 前自动 verify。回滚方式：回滚本 PR 即可恢复旧流程。

## 关联

-

---

<details>
<summary>📊 治理元数据（CI 自动采集）</summary>

（未检测到治理 trailer，建议安装 hook: bash governance/scripts/install-hooks.sh）

</details>
