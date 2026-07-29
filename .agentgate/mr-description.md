## 背景

避免 GitLab 治理 job 在 Python slim 镜像中每次运行时安装 git / PyYAML，降低 CI 冷启动耗时，并保证 zhuishu-flutter 接入新版 AgentGate 后，不会因为门禁镜像缺少 git 或依赖下载过慢而阻塞正常提交。

## 变更内容

- `ci/governance-ci.yml` 改为使用 `GOVERNANCE_PY_IMAGE` / `GOVERNANCE_SECRET_IMAGE` 预构建镜像，并增加 git / Python / PyYAML / gitleaks 快速预检。
- 移除 GitLab 新版 `rules` / `needs` 依赖，改用 GitLab 11.4 兼容的 `only` + `dependencies` + artifacts。
- 将语言测试跳过逻辑从 `rules` 条件下沉到脚本内部，确保跳过时仍产出结果文件。
- `install.sh` 不再内嵌旧 CI 模板，改为安装中心 `ci/governance-ci.yml`，避免模板漂移。
- 增加回归测试，防止 `apt-get`、`python:3.11-slim`、`rules` / `needs` 回流。
- `agent-instructions/CLAUDE.md` 与 `agent-instructions/AGENTS.md` 统一默认 MR 入口为 `create_mr.py --why "..."`，并明确 AgentGate 不得因 token、CLI 或旧版 GitLab 能力不足阻碍分支提交。

## 不包含的内容

无。

## 自测确认

- PASS: `PYTHONIOENCODING=utf-8 python scripts/validate_yaml.py --ci`
- PASS: `git diff --check`
- PASS: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -v`（147 tests）
- PASS: `python scripts/agentgate.py mr prepare ... --prepare`
- PASS: `PYTHONIOENCODING=utf-8 python -m unittest tests.test_regressions.AgentGateCliTests tests.test_regressions.GitLabAutoMergeTemplateTests -v`

## 风险与回滚

- 风险：默认治理镜像必须在内网镜像仓库可拉取，并预装 git / Python / PyYAML；密钥扫描镜像必须预装 git / gitleaks。
- 回滚：将 `GOVERNANCE_PY_IMAGE` / `GOVERNANCE_SECRET_IMAGE` CI 变量临时覆盖为可用镜像，或回退本次模板变更。

## 关联

- PR #41

---

<details>
<summary>📊 治理元数据（CI 自动采集）</summary>

（未检测到治理 trailer，建议安装 hook: `bash governance/scripts/install-hooks.sh`）

</details>
