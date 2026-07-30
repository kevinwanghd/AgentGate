<!-- agentgate-pr-bind {"base_ref": "origin/main", "changed_paths": ["install.sh", "scripts/rollout_github.py", "tests/test_rollout_github.py"], "diff_fingerprint": "7a18a0ce48cfdc2e1c2aab352dbbc81987bcbdc28f4b2230d886a5bbac547715", "prepared_from_sha": "ea7765d5ea299f3398e473c96371fd02e669d367", "schema_version": "agentgate.io/pr-description-binding/v1"} -->

## 背景

GitHub rollout 在 Windows Git Bash 环境下直接执行 install.sh 时，Bash/MSYS 工具链解析不稳定；同时 install.sh 文件头存在 UTF-8 BOM，会导致 shebang 被错误识别。这个 follow-up 修复 rollout 自动化在 Windows 上安装 AgentGate 门禁时的可靠性。

## 变更内容

移除 install.sh 文件头 BOM；将 rollout_github.py 的安装命令改为通过 bash -lc 执行，并对脚本路径、目标路径和参数做 shell quoting；补充回归测试覆盖 Windows/MSYS 场景。

## 不包含的内容

无

## 自测确认

pass - python -m unittest tests.test_rollout_github -v；pass - python -m unittest discover -s tests -v；pass - git diff --cached --check

## 风险与回滚

低风险。变更只影响 GitHub rollout 安装命令和脚本编码；如出现问题可回滚本提交，已由专项和全量 unittest 覆盖。

## 关联

-

---

<details>
<summary>📊 治理元数据（CI 自动采集）</summary>

- **AI-Usage**: assisted
- **Tested**: pass - python -m unittest tests.test_rollout_github -v

</details>
