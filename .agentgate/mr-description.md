<!-- agentgate-pr-bind {"base_ref": "origin/main", "changed_paths": ["docs/08-github-rollout-kit.md", "rollout.repos.yml", "scripts/agentgate.py", "scripts/create_mr.py", "scripts/rollout_github.py", "tests/test_regressions.py", "tests/test_rollout_github.py"], "diff_fingerprint": "013869700606d8b4fed58adf2b63e4912f923845f8bd89eb5eeea6599c027742", "head_sha": "0db1359c95b442e0ffcc78482588612b6cc10cd6", "schema_version": "agentgate.io/pr-description-binding/v1"} -->

## 背景

修复 AgentGate 自身 PR 流程可绕过治理的问题：PR 描述必须由工具生成、绑定当前 diff，并在 rollout 推送前强制校验，避免再次出现只推分支但 PR 正文不合规的情况。

## 变更内容

- `scripts/rollout_github.py` (+312/-0)
- `scripts/create_mr.py` (+142/-3)
- `tests/test_regressions.py` (+101/-0)
- `tests/test_rollout_github.py` (+81/-0)
- `docs/08-github-rollout-kit.md` (+77/-0)
- `.agentgate/mr-description.md` (+25/-24)
- `rollout.repos.yml` (+22/-0)
- `scripts/agentgate.py` (+10/-2)

## 不包含的内容

不修改业务仓库内容；不改变 GitLab 私仓现有 CI 模板和 token 使用方式；不直接合并到 main。

## 自测确认

已运行 python -m py_compile scripts\create_mr.py scripts\agentgate.py scripts\rollout_github.py tests\test_regressions.py tests\test_rollout_github.py；已运行 python -m unittest discover -s tests -v，156 个测试通过；已运行 python scripts\rollout_github.py --repo UseGEO 预演；已运行 git diff --check。

## 风险与回滚

风险点：新增绑定校验会让缺少 .agentgate/mr-description.md 或 diff 已变化的分支在推送/创建 PR 前失败。应对：失败时重新运行 agentgate.py mr prepare；rollout 脚本默认 dry-run 且 push 前自动 verify。回滚方式：回滚本 PR 即可恢复旧流程。

## 关联

-

---

<details>
<summary>📊 治理元数据（CI 自动采集）</summary>

（未检测到治理 trailer，建议安装 hook: bash governance/scripts/install-hooks.sh）

</details>
