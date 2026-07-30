## 背景

合并 GitHub 上当前尚未进入 main 的 AgentGate 分支，保留 GitHub rollout kit、GitLab MR 兼容路径、CI 治理模板优化，以及 Dart 扫描支持等并行变更。

## 变更内容

- 新增 GitHub rollout kit 文档、仓库清单和 `scripts/rollout_github.py`，并在 rollout 推送前校验绑定的 PR 描述。
- `scripts/create_mr.py` 增加 MR 描述 manifest 绑定校验、GitLab API/浏览器 fallback，以及按远端平台选择 `gh`/`glab` 的提交路径。
- 优化 GitLab 治理 CI 模板，使用预构建治理镜像并保持旧版 GitLab 兼容。
- 将 `.dart` 纳入测试/扫描扩展名，并补充 YAML 配置校验与相关回归测试。

## 不包含的内容

不修改业务仓库内容；不删除远端分支。

## 自测确认

- 待运行: `python -m unittest discover -s tests -v`
- 待运行: `git diff --check`

## 风险与回滚

- 风险：多个分支同时修改 MR 创建与治理流程，合并后需重点验证 manifest 绑定校验、GitLab fallback 和 rollout 推送校验。
- 回滚：回退本次 merge commit，或按具体功能回退对应分支提交。

## 关联

- origin/feature/github-rollout-kit-20260730
- origin/fix/gitlab-compat-url-derivation
- origin/feat/centralized-distribution
- origin/fix/add-dart-to-scan-extensions

---

<details>
<summary>📊 治理元数据（CI 自动采集）</summary>

（未检测到治理 trailer，建议安装 hook: `bash governance/scripts/install-hooks.sh`）

</details>
