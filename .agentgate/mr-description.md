## 背景

修复 AgentGate 自举问题：创建 PR/MR 前自动生成中文合规模板，并在本地通过 MR 描述校验、风险扫描和回归测试后再提交，避免 CI 事后才暴露模板、测试删除或风险注解问题。

## 变更内容

- `scripts/create_mr.py` (+143/-57)
- `tests/test_regressions.py` (+67/-9)
- `docs/07-legacy-gitlab-mr-description.md` (+7/-4)
- `scripts/gitlab_mr_compat.py` (+5/-4)

## 不包含的内容

无

## 自测确认

- [x] python -m unittest tests.test_regressions.AgentGateCliTests -v：通过，5 个测试通过。
- [x] python -m unittest discover -s tests -v：通过，146 个测试通过。
- [x] python -m compileall -q scripts tests：通过。
- [x] python scripts/scan_risks.py --diff-base origin/main --config governance.config.yml：通过。
- [x] git diff --check：通过。

## 风险与回滚

- 风险：create_mr.py 的提交前检查会让不合规描述、风险扫描失败或配置的测试命令失败时提前阻断 PR/MR 创建。
- 应对：保留 --skip-local-validate、--skip-risk-scan、--skip-tests 作为迁移/调试逃生口，但默认闭环开启。
- 回滚：如某消费仓库测试命令不适配，可先配置 create_mr.preflight_test_command 或临时使用 --skip-tests，不影响既有 CI 门禁。

## 关联

-

---

<details>
<summary>📊 治理元数据（CI 自动采集）</summary>

（未检测到治理 trailer，建议安装 hook: bash governance/scripts/install-hooks.sh）

</details>
