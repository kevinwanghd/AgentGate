# 05 · CI 门禁参考

面向**想理解 CI 为什么拦我、如何排查、如何豁免**的所有人。

---

## CI 里有哪些 governance job

接入后，每个 MR pipeline 在 `governance` 阶段（test 之前）运行以下 job：

| Job | 内容 | v1 模式 | allow_failure |
|---|---|---|---|
| `governance:risk-scan` | 扫描 8 类风险模式 + 注解校验 | **硬阻断** | `false` |
| `governance:secret-scan` | gitleaks 密钥扫描 | **硬阻断** | `false` |
| `governance:mr-validate` | 校验 MR 描述必填段落 + 读 AI-Usage trailer | **软警告** | `false`* |
| `governance:test-check` | 改动的生产代码是否做过测试（读 Tested trailer） | **软警告** / 失败测试硬拦 | `false`* |
| `governance:expired-report` | 过期注解周报（定时/手动） | 不阻断 | `true` |

*`mr-validate` / `test-check` 设 `allow_failure: false`，但软模式期内脚本自身返回 0（仅警告），`soft_deadline` 后才返回非 0 真正阻断。`test-check` 的例外：只要测试记录里有失败用例（`Tested: fail`），任何时候都返回 1 硬拦。

`allow_failure: false` = 这个 job 红了，MR 合不进去（前提：管理员开了 "Pipelines must succeed"）。
`allow_failure: true` = 这个 job 红了只显示黄色警告，不阻断合并。

---

## 两层 enforcement 回顾

```
硬阻断（第一天起拦）          软提示（v1 不拦，soft_deadline 后转硬）
├─ risk-scan 风险注解        ├─ ## 背景
├─ secret-scan 密钥          ├─ ## 变更内容
├─ 测试删除保护              ├─ AI-Usage（自动从 commit trailer 读）
└─ 失败测试 (Tested:fail)    ├─ ## 自测确认
                            └─ test-check 未测代码（自动从 Tested trailer 读）
```

`soft_deadline` 在 `governance.config.yml` 里，默认安装日 + 90 天。到期后 `mr-validate` 的 `allow_failure` 自动变 `false`（由脚本读 config 中的日期判断），软警告升级为硬阻断。

---

## risk-scan 做了什么

1. 取 MR 的 diff（相对 base 分支）。
2. 用正则 + 轻量解析扫描新增/修改的代码行，匹配 8 类风险模式。
3. 对每个命中点，检查上方 5 行内是否有合法的 `risk:` 注解：
   - 类型在已注册列表里
   - reason ≥ 10 字且不含黑名单词
   - owner、reviewed 字段存在
   - reviewed 距今 ≤ 180 天
4. 任一命中点缺合法注解 → job 失败，打印 `文件:行号` 和缺失原因。

> 它只扫 **diff 里新增/修改的行**，不会因为仓库里历史遗留的未注解代码而拦你。历史代码在你**触碰它所在文件**时才会被要求补注解。

---

## 如何读懂 risk-scan 的输出

典型失败输出：

```
[risk-scan] FAIL
  X.SelfAutoAd.Service/Tasks/SyncTask.cs:142
    matched: auth-bypass (literal id compared to auth field)
    problem: no risk annotation found in 5 lines above
    fix: add  // risk:auth-bypass reason:"..." owner:@team reviewed:2026-06-25

  X.Core/Helpers/EnvHelper.cs:30
    matched: env-hardcode
    problem: annotation reason contains blacklisted word "临时"
    fix: rewrite reason to explain the actual tradeoff
```

照着 `fix:` 提示改即可。改完 push，pipeline 重跑。

---

## secret-scan 做了什么

1. 用官方 `gitleaks` 镜像扫描**本次 MR 引入的提交**（`base..HEAD`），不扫历史全量。
2. 命中任何密钥/凭据模式（API key、token、私钥、连接串等）→ job 失败，**硬阻断**。
3. 输出用 `--redact` 脱敏，不在日志里回显密钥明文，只显示文件、行号、规则名。

> 它只看本次 MR 新引入的提交。历史里早已存在的密钥不会反复拦你（那是另一码事，应单独做一次全量清理 + 轮换）。
>
> 命中后正确做法：**把密钥从代码里移除、改走环境变量/密钥管理，并轮换已泄露的凭据**。不要试图加注解豁免——密钥泄露没有"经过说明的例外"。
>
> 如确属误报（如测试用的假 token），在仓库根加 `.gitleaks.toml` 配置 `[allowlist]` 规则，该改动会触及被 CODEOWNERS 锁定的范围、需治理负责人审批。

---

## mr-validate 做了什么

1. 读取 MR 描述（通过 GitLab API 或 CI 变量）。
2. 检查 MR 描述含 3 个必填段落：`## 背景`、`## 变更内容`、`## 自测确认`。
3. **AI-Usage 不从描述读**：优先从本次 MR 的 commit trailer 读取（由 `collect_ai_usage.py` 在提交时自动写入）；trailer 缺失时退回看描述（兼容老 MR），并提示安装 hook。
4. 判断是否"大变更"，是则要求 `## 风险与回滚`。
5. v1 软模式：缺失项只打 `[warn]`，job 仍绿；`soft_deadline` 后转硬阻断。

> 该 job 设 `GIT_DEPTH: 0`，以便读取完整 commit 历史里的 AI-Usage trailer。

软模式期内输出示例：

```
[mr-validate] WARN (soft 模式: 软模式, deadline 2026-09-30)
  ⚠ 缺少 ## 自测确认 段落 (或内容为空)
  ⚠ 未检测到 AI-Usage (应由 git hook 自动写入 commit trailer)
  软模式: 暂不阻断。这些项将在 2026-09-30 后阻断合并, 请尽早补全。
```

---

## test-check 做了什么（改动代码有没有做过测试）

它**不重跑测试**，只查痕迹，diff-only 秒级：

1. 找出本次 diff 改动的**生产代码**文件（排除测试文件，排除 config `testing.exclude_paths` 白名单）。
2. 对每个改动的生产文件，满足其一即放行：
   - 有一条全绿测试运行记录，**且**本次 MR 改动了测试文件（双重信号）
   - `record_test_run.py --covers` 显式声明覆盖了该文件
   - 文件里有合法 `// risk:untested reason:"..." owner:@ reviewed:` 注解
3. **硬规则**：测试记录里有失败用例（`Tested: fail`）→ 任何模式下都拒合，注解也豁免不了。

**痕迹从哪来**：开发时用 `record_test_run.py` 包装测试命令，结果记到 `.governance/test-evidence.jsonl`（gitignore）。CI 看不到这个文件，所以提交 hook 把结果汇总成 commit 的 `Tested:` trailer，CI 读 trailer（该 job 设 `GIT_DEPTH: 0`）。

```bash
# 开发时跑测试留痕
python governance/scripts/record_test_run.py -- dotnet test X.Flow.sln --filter Category=Unit
# 本地预检
python governance/scripts/check_tested.py --staged
```

> **诚实边界**：静态痕迹证明"有没有测"，不证明"测得对不对、是否真跑过"。`record_test_run.py` 把痕迹绑到测试命令真实退出码，让造假需要刻意为之，但仍依赖 agent 如实运行。要真正的证明——"**这次改的这些行**有没有被测试执行到"——需要 CI 差异覆盖率（dotnet test + coverlet + diff-cover），那是完整 build+test 的慢路径，留作 `soft_deadline` 后的可选硬化。

---

## 如何申请豁免

### 想豁免某条风险规则的某次命中

不能豁免——正确做法是**加合法注解说明清楚**。注解本身就是"经过说明的豁免"。如果某段代码确实安全（如机器人免登录），把理由写充分即可通过。

### 想临时关掉某个 job（紧急情况）

只有治理负责人能改。流程：

1. 提 MR 修改 `governance.config.yml` 或 CI 模板。
2. 因为这些文件被 CODEOWNERS 锁定，需要治理负责人 approve。
3. 在 MR 描述里写明原因、影响范围、恢复时间。
4. 合入后生效。

**不能**用 `[skip ci]`、改 `allow_failure`、删 job 等方式绕过——这些改动同样触发 CODEOWNERS 审批。

### 想延长 soft_deadline

同样走 MR 改 `governance.config.yml` 的 `soft_deadline`，治理负责人审批。注意 install.sh 限制 deadline 距今最多 90 天，手动设更远的日期需要治理负责人判断是否合理。

---

## 注解过期的周报机制

`governance:expired-report` job 每周定时全仓扫描所有 `risk:` 注解，把"30 天内将过期"和"已过期"的注解写入 `governance/reports/expired-annotations.md` 并作为 CI artifact 存储30天。

**启用定时触发（每周一 08:00）：**
GitLab → 项目 → Build → Schedules → New schedule
- Description：`Governance: weekly expired annotation report`
- Cron：`0 8 * * 1`
- Target branch/tag：`master`（或你的默认分支）

MR 流水线里也可以手动点击这个 job 随时生成快照（`when: manual`，不影响流水线状态）。

> 周报只是提醒，不阻断任何 MR。真正的强制发生在"有人触碰过期注解所在文件"时（risk-scan 会在 MR diff 里检测到注解已过期，拦截提交）。

本地随时可生成报告：
```bash
python governance/scripts/report_expired.py --root . --output governance/reports/expired-annotations.md
```

---

## 性能

- governance 阶段是 **diff-only**，只看本次变更，秒级完成。
- 不跑全仓扫描（那是每周定时任务的事），不拖慢日常 pipeline。

---

## v1 阶段的实现状态

> ✅ **三个 job 全部就绪**：`risk-scan`（`scan_risks.py`，硬门禁）、`mr-validate`（`validate_mr.py`，软门禁）已实现并通过自测；`secret-scan` 用官方 `gitleaks` 镜像扫本次 MR 范围（硬门禁）。install.sh 生成的 `governance/ci-snippet.yml` 直接调用它们，不再是占位 echo。
>
> 运行环境要求：
> - `risk-scan` / `mr-validate`：镜像需有 `python3` + `pyyaml`（ci-snippet 用 `python:3.11-slim`，`before_script` 里 `pip install pyyaml`）。
> - `secret-scan`：用 `zricethezav/gitleaks:latest` 镜像，无需额外依赖；需 `GIT_DEPTH: 0` 拉完整历史以便按范围扫描。
>
> 本地随时可验证脚本行为：
> ```bash
> bash governance/scripts/selftest.sh      # 跑 10 个内置用例
> ```
>
> 两个脚本都支持本地预跑：
> ```bash
> python governance/scripts/scan_risks.py --diff-base origin/master
> python governance/scripts/validate_mr.py --file 你的MR描述.md
> ```

---

## 排查速查表

| 现象 | 检查 |
|---|---|
| governance job 没跑 | `.gitlab-ci.yml` 是否 include 了 CI 片段；pipeline 是否在 MR event 触发 |
| secret-scan 报 `no commits found` 或漏扫 | 多半是 `GIT_DEPTH` 太浅；确认 job 里设了 `GIT_DEPTH: 0` |
| risk-scan 报 `ModuleNotFoundError: yaml` | CI 镜像缺 pyyaml；脚本会退化用内置默认配置，但建议 `pip install pyyaml` |
| risk-scan 该拦没拦 | 确认 diff-base 正确（看的是本次变更）；本地用 `selftest.sh` 验证脚本本身 |
| MR 合不了但 CI 全绿 | 管理员的分支保护 / approval 规则，不是 governance 问题 |
| 改了 governance.config.yml 被要求 approve | 正常，CODEOWNERS 锁定，需治理负责人审批 |
| 历史代码报风险但我没碰 | 不应发生，risk-scan 只扫 diff；若发生检查 diff-base 配置 |

---

## 相关文档

- 风险类型完整规则：`../risk-types.md`
- MR 规范完整说明：`../mr-spec.md`
- 提交流程：`04-ai-agent-workflow.md`
- 管理员配置（分支保护、CODEOWNERS）：`01-gitlab-admin-setup.md`
