# AgentGate CI 优化需求文档

> **状态：** 待认领
> **提出日期：** 2026-09-22
> **来源：** Linear CI 重构经验（[原文](https://linear.app/now/ci-bottleneck-reworked)）+ 对标 AgentGate 现状分析
> **执行方：** 待分配

---

## 1. 背景

AgentGate 的 CI 负责对 AI agent 的代码变更做风险扫描、密钥扫描、MR 描述校验，是 AI coding 工作流的关键瓶颈。当前 CI 存在多处效率浪费，在 monorepo 或高频 PR 场景下问题尤为突出。

本文档参考 Linear（一家 TypeScript monorepo 公司）在 2025 年将测试套件扩大 4 倍的情况下将 PR 等候时间从 6 分钟压到 5 分钟的实战经验，提出对 AgentGate CI 的系统性优化。

---

## 2. 现状问题

### 2.1 GitHub Actions（`governance.yml`）

每个 job 独立执行以下步骤，共 3 个 job（risk-scan / secret-scan / mr-validate），完全重复：

```yaml
# 每个 job 都执行一遍：
- uses: actions/checkout@v4        # fetch-depth: 0，全量历史
  with:
    fetch-depth: 0
- git fetch -q origin "$BASE_REF"  # 再次 fetch 目标分支（与 checkout 重复）
- python3 scripts/scan_xxx.py       # Python 扫描
```

**实测浪费：**
- `fetch-depth: 0` 拉全量历史，但 risk-scan / secret-scan / mr-validate 只需要 `base..HEAD` 的 diff，全量历史对扫描无意义
- 每个 job 单独起一个 ubuntu runner（约 20~40 秒启动开销）
- 每个 job 单独 fetch，相同历史被重复拉取 3 次

### 2.2 GitLab CI（`ci/governance-ci.yml`）

结构类似 GitHub Actions，多 stage 串行，每个 stage 内的 job 也各自重复 checkout + fetch。

### 2.3 AgentGate Thin Mode（`agentgate.yml`）

GitHub thin mode 中每个 job 额外多一步：

```yaml
- uses: actions/checkout@v4
  with:
    repository: kevinwanghd/AgentGate
    ref: github-stable
    path: .agentgate
```

`.agentgate` 目录内容在所有 job 间完全相同，但每个 job 都重新拉取一次。

---

## 3. 优化方案

### P0 — 立即可做，效果显著

#### P0-A：共享 Checkout Artifact（消除重复 fetch）

**目标：** 一次 checkout + fetch，结果共享给所有 scan job。

**方案：** 用 `actions/upload-artifact` + `actions/download-artifact` 缓存 checkout 结果。

```yaml
# 单一 checkout job
jobs:
  checkout:
    runs-on: ubuntu-latest
    outputs:
      base_ref: ${{ steps.vars.base_ref }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 1
          # 按需 shallow fetch 目标分支（避免 fetch-depth: 0）
      - name: Fetch base ref
        id: vars
        run: echo "base_ref=$(git rev-parse HEAD~1)" >> $GITHUB_OUTPUT
      - name: Upload checkout
        uses: actions/upload-artifact@v4
        with:
          name: checkout
          path: .
          retention-days: 1

  risk-scan:
    needs: checkout
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: checkout
          path: .
      - name: Scan risks
        run: python3 scripts/scan_risks.py --diff-base origin/${{ needs.checkout.outputs.base_ref }} --config governance.config.yml

  secret-scan:
    needs: checkout
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: checkout
          path: .
      - name: Scan secrets
        run: python3 scripts/scan_secrets.py --diff-base origin/${{ needs.checkout.outputs.base_ref }}
```

**预期收益：** 消除 2 个 job 的重复 checkout + fetch，节省约 60~120 秒/每个被消除的 fetch。

**风险：** 无破坏性风险，artifact 下载失败不影响原有逻辑。

**验收标准：**
- [ ] 所有 job 在 PR 场景下输出与优化前一致的结果
- [ ] artifact download 失败时 job 能给出明确错误提示而非静默失败
- [ ] checkout job 本身耗时 < 30 秒

---

#### P0-B：缩短 Fetch Depth（省 50%+ checkout 时间）

**目标：** 消除无意义的全量历史拉取。

**问题分析：**
- `fetch-depth: 0` 对 diff 扫描无意义：scan_risks.py / scan_secrets.py 只看 `base..HEAD` 的 diff，不需要 commit 历史
- GitLab CI 中 `GIT_DEPTH: 1000` 仍是过度配置

**方案：** 统一改为 `fetch-depth: 1` + shallow fetch 目标分支。

```yaml
# GitHub Actions
- uses: actions/checkout@v4
  with:
    fetch-depth: 1   # 只拉当前 commit
- name: Fetch base ref for diff
  run: git fetch -q --depth=1 origin ${{ github.event.pull_request.base.ref }}
```

```yaml
# GitLab CI
variables:
  GIT_DEPTH: 1   # 而非 1000
```

**预期收益：** Checkout 时间减少 50~80%（取决于仓库历史深度）。

**风险：**
- 需要确认 scan_risks.py 不依赖 commit 历史做分析（如 blame、作者信息）
- 如有依赖，需要加 `--depth 0` 的例外 job

**验收标准：**
- [ ] 全量回归测试通过
- [ ] scan_risks.py 在长历史仓库（>10k commit）上结果一致

---

#### P0-C：AgentGate Thin Mode 缓存 `.agentgate`

**目标：** 避免每个 job 重复拉取 AgentGate 仓库。

**方案 A（推荐）：** 用 `actions/cache` 缓存 `.agentgate` 目录。

```yaml
- uses: actions/checkout@v4
  with:
    repository: ${{ inputs.agentgate-repository }}
    ref: ${{ inputs.agentgate-ref }}
    path: .agentgate
    sparse-checkout: |
      scripts/
      templates/
    sparse-checkout-cone-mode: false
```

**方案 B：** 把 `.agentgate` 也作为共享 artifact 的一部分，与 checkout 一起上传。

**预期收益：** 每个 job 节省约 5~15 秒（取决于 AgentGate 仓库大小）。

**验收标准：**
- [ ] thin mode 安装的仓库在 PR 扫描时行为与优化前一致
- [ ] AgentGate 版本更新后缓存能正确失效

---

### P1 — 中期优化

#### P1-A：Change Detection Path Filter（按需执行 job）

**目标：** PR 只改文档/配置文件时，跳过不必要的 scan job。

**方案：** 在 checkout job 之后加一个轻量 path filter job，决定后续 job 是否需要运行。

```yaml
jobs:
  should-run-risk-scan:
    needs: checkout
    runs-on: ubuntu-latest
    outputs:
      run: ${{ steps.filter.outputs.run_risk_scan }}
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: checkout
          path: .
      - name: Detect changed paths
        id: filter
        run: |
          CHANGED=$(git diff --name-only origin/${{ needs.checkout.outputs.base_ref }}...HEAD)
          # 风险扫描只对生产代码有意义
          if echo "$CHANGED" | grep -qE '^\.governance/|\.py$|\.ts$|\.js$|\.go$'; then
            echo "run_risk_scan=true" >> $GITHUB_OUTPUT
          else
            echo "run_risk_scan=false" >> $GITHUB_OUTPUT
          fi

  risk-scan:
    needs: [checkout, should-run-risk-scan]
    if: needs.should-run-risk-scan.outputs.run == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: checkout
          path: .
      - name: Scan risks
        run: python3 scripts/scan_risks.py ...
```

**预期收益：** 纯文档 PR 的 CI 时间减少约 50%（跳过 risk-scan + secret-scan）。

**风险：** Path filter 逻辑需要与业务方确认，避免漏掉边界情况（如 .yml 配置含风险指令）。

**验收标准：**
- [ ] 常见文件类型覆盖（.py / .ts / .js / .go / .md / .yml）
- [ ] 任何包含 .governance/ 的文件变化都触发对应扫描
- [ ] path filter 本身失败时不能阻断流水线（应降级为全跑）

---

#### P1-B：合并 Scan Job（减少 Runner 启动开销）

**目标：** 3 个独立的 Python scan job 合并为 1 个，减少 runner 启动次数。

**方案：** 创建一个统一的入口脚本 `scripts/governance_scan_all.py`，一次性执行所有扫描。

```python
# scripts/governance_scan_all.py
import sys
import subprocess

results = {}
base = sys.argv[1]

# 1. Risk scan
r = subprocess.run(
    ["python3", "scripts/scan_risks.py", "--diff-base", base, "--config", "governance.config.yml"],
    capture_output=True, text=True
)
results["risk_scan"] = r.returncode

# 2. Secret scan
r = subprocess.run(
    ["python3", "scripts/scan_secrets.py", "--diff-base", base],
    capture_output=True, text=True
)
results["secret_scan"] = r.returncode

# 3. MR validation
r = subprocess.run(
    ["python3", "scripts/validate_mr.py", "--diff-base", base, "--config", "governance.config.yml"],
    capture_output=True, text=True
)
results["mr_validate"] = r.returncode

# 任意一项失败则整体失败
sys.exit(max(results.values()))
```

**预期收益：** 3 个 runner → 1 个 runner，减少约 40~80 秒启动开销。

**风险：** 一个 job 失败时无法精确知道是哪一项失败（但错误信息仍会打印）。

**验收标准：**
- [ ] 合并后的输出包含所有 3 项 scan 的详细结果
- [ ] 任意一项失败时 CI 整体失败，错误信息包含是哪项失败
- [ ] 回归测试覆盖 3 种失败场景（risk-scan 失败 / secret-scan 失败 / mr-validate 失败）

---

#### P1-C：Preinstall PyYAML 镜像

**目标：** 消除 `pip install PyYAML` 的时间（当前依赖 ubuntu-latest 自带 Python 无 PyYAML）。

**方案：** 推荐用户使用预装了 `PyYAML==6.0.3` 的镜像。

```yaml
# 在 GOVERNANCE_PY_IMAGE 变量说明中更新
# 推荐镜像：swr.cn-north-4.myhuaweicloud.com/adbidding/governance/python-ci:latest
# 已预装：Python 3.10+ + PyYAML==6.0.3 + git
```

**预期收益：** 每个 job 节省约 3~8 秒（pip install 时间）。

**验收标准：**
- [ ] 确认官方推荐镜像含 PyYAML
- [ ] 在新安装的机器上验证 import yaml 无报错

---

## 4. 实施计划

### Phase 1：P0 优化（预计 1~2 人天）

| 任务 | 负责方 | 验收条件 |
|---|---|---|
| P0-A: 共享 checkout artifact | — | 见 P0-A 验收标准 |
| P0-B: fetch-depth 改 shallow | — | 见 P0-B 验收标准 |
| P0-C: thin mode .agentgate 缓存 | — | 见 P0-C 验收标准 |

### Phase 2：P1 优化（预计 2~3 人天）

| 任务 | 负责方 | 验收条件 |
|---|---|---|
| P1-A: Change detection path filter | — | 见 P1-A 验收标准 |
| P1-B: 合并 scan job | — | 见 P1-B 验收标准 |
| P1-C: PyYAML 预装镜像 | — | 见 P1-C 验收标准 |

### Phase 3：监控与迭代（持续）

- 在 AgentGate 自举 CI 中记录优化前后的 PR 等候时间
- 每季度 review 一次 CI 耗时分布

---

## 5. 参考资料

- [Linear Now — AI coding has made CI a bottleneck, so we reworked ours to keep up](https://linear.app/now/ci-bottleneck-reworked)
- AgentGate CI 文件：`governance.yml`（GitHub Actions）、`ci/governance-ci.yml`（GitLab CI）、`agentgate.yml`（thin mode）

---

## 6. 附录：Linear 四维优化框架（原始经验）

Linear 的优化方向对 AgentGate 有直接参考意义：

| 维度 | Linear 做法 | AgentGate 对应 |
|---|---|---|
| **基础设施** | 换更快 Runner（+34%），tsgo（tsc -73%） | PyYAML 预装镜像，thin mode 缓存 |
| **关键路径** | 按需 checkout（94s→20s），checkout 弹性（retry+超时） | P0-A/B 共享 artifact + shallow fetch |
| **减少重复 setup** | pnpm workspace 过滤（73s→16s），DB snapshot | P0-C 缓存 .agentgate，P1-A 按需执行 |
| **测试执行效率** | Vitest isolate:false（+17% 月省），8 shard | P1-B 合并 scan job |

Linear 的核心洞察同样适用于 AgentGate：**降低 setup 成本才能解锁更多优化空间，先把固定开销打下去，再做并行化才有意义。**
