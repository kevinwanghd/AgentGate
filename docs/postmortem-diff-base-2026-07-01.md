# 复盘：多人协作 diff-base 误拦 bug

> **版本**：v1.1.1 修复
> **日期**：2026-07-01
> **严重程度**：高（正常开发流程中高频触发）

---

## 一、问题现象

开发者报告：正常提交代码时被 CI 误拦，报告"生产代码缺测试痕迹"，但被点名的文件**不是自己改的**，是同事的代码。

典型场景：

```
1. 你从 main 拉代码，创建分支 feature，开始改 fileA.cs
2. 期间同事把 fileB.cs 改好并合入了 main
3. 你 git pull（或 merge main）→ 本地 feature 现在也包含了 fileB.cs
4. 你提交、发 MR
5. CI 的 test-check 报错：「fileB.cs 缺测试痕迹」
   → 但 fileB.cs 是同事写的，你没碰过，也没法为它补测试
   → 被误拦 ❌
```

**关键痛点**：多人协作中频繁 `git pull` 是标准操作，所以这个 bug 会**频繁触发**，严重影响正常开发。

---

## 二、根因分析

### 表层：diff 范围算错了

`test-check`（以及 `risk-scan`、`mr-validate`、`secret-scan`）检查的是「本次 MR 引入的改动」，通过 `git diff <base>...HEAD` 计算。

问题出在 **`<base>` 取错了值**。

### 深层：base 用了"创建时的旧快照"

CI 传给扫描脚本的 diff-base 是：

| 平台 | 变量 | 问题 |
|---|---|---|
| GitHub | `github.event.pull_request.base.sha` | PR **创建时**目标分支的 commit SHA，不随 main 更新 |
| GitLab | `CI_MERGE_REQUEST_DIFF_BASE_SHA` | 同理，MR pipeline 触发时的快照 |

这些值是**静态快照**。当你 `git pull` 把同事的新提交 merge 进 feature 分支后，用这个**旧 base** 做三点 diff：

```
git diff <旧base>...HEAD
```

三点 diff 的语义是 `merge-base(旧base, HEAD)..HEAD`。因为旧 base 是同事提交**之前**的老 main，merge-base 就落在老分叉点上，于是**同事后来 merge 进来的改动全被算进了你的 diff**。

### 复现验证

```bash
# A: 共同起点，创建 feature 分支
git checkout -b feature
echo "..." > myfeature.cs && git commit -am "my work"

# 同事往 main 提交
git checkout main
echo "..." > colleague.cs && git commit -am "colleague work"

# 你 merge main 进来
git checkout feature && git merge main

# ❌ 用旧 main（同事提交前）做 base：
git diff <old_main>...HEAD --name-only
#   colleague.cs   ← 同事的文件被误算进来
#   myfeature.cs

# ✅ 用当前 main tip 做 base：
git diff main...HEAD --name-only
#   myfeature.cs   ← 只有你的改动
```

---

## 三、修复方案

### 核心：base 改用"目标分支最新 tip"

不用创建时的旧快照，改用**目标分支的当前最新状态**（CI 里先 `git fetch` 拉最新）。

| 平台 | 修复前 | 修复后 |
|---|---|---|
| GitHub | `base.sha`（旧快照） | `origin/<base.ref>`（fetch 后的最新 tip） |
| GitLab | `CI_MERGE_REQUEST_DIFF_BASE_SHA` | `origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME` |

### 为什么这样能修好

三点 diff `origin/main...HEAD` = `merge-base(origin/main, HEAD)..HEAD`。

因为同事的提交现在**既在 main 也在 feature**（你 merge 进来了），所以 merge-base 就是当前 main tip（含同事提交），diff 自然**排除**了同事的改动，只剩你真正引入的部分。

### GitHub workflow 示例

```yaml
# 修复前
BASE="${{ github.event.pull_request.base.sha }}"
python scripts/scan_risks.py --diff-base "$BASE" ...

# 修复后
BASE_REF="${{ github.event.pull_request.base.ref }}"
git fetch -q origin "$BASE_REF"
python scripts/scan_risks.py --diff-base "origin/$BASE_REF" ...
```

### 修复覆盖范围

- **AgentGate 主仓库**（PR #9）：GitHub workflow 4 个 job + `install.sh` 生成的 GitLab ci-snippet 4 个 job
- **UseGEO 部署实例**（PR #12）：GitHub workflow 4 个 job

---

## 四、验证

### 回归测试（selftest 新增 2 用例）

在 `selftest.sh` 加了端到端多人协作场景，用真实 git 分支/merge 验证：

| 用例 | 期望 | 结果 |
|---|---|---|
| merge 他人代码后只检查自己的改动 | 不误拦（exit 0） | ✅ |
| 硬模式下自己改生产代码没测 | 仍拦截（exit 1） | ✅ |

第二个用例是关键的**反向验证**——证明修复没有把门禁弄失效（不能为了不误拦就放过真正该拦的）。

selftest 总数：42 → **44，全过**。

### 端到端验证

两个仓库的修复 PR 自己用新逻辑跑 CI，全绿合入。

---

## 五、经验教训

### 1. "快照"变量的陷阱

CI 平台提供的 `base.sha` / `DIFF_BASE_SHA` 看起来方便，但它们是**创建时的静态快照**。任何依赖"目标分支当前状态"的逻辑，都应显式 `fetch` 并用 `origin/<branch>` 最新 tip，而不是快照。

### 2. 三点 diff 的语义要吃透

`A...B` = `merge-base(A,B)..B`。它的正确性**完全取决于 A 是否是最新的目标分支**。A 陈旧 → merge-base 陈旧 → 混入他人改动。三点 diff 不是银弹，喂给它的 base 必须新鲜。

### 3. 门禁类工具尤其要防"误拦"

误拦比漏拦更伤——它直接阻断正常开发，让团队对工具失去信任。修复时必须同时验证两个方向：
- 该放的要放（不误拦他人改动）
- 该拦的仍拦（自己的未测代码）

### 4. 部署实例要同步修复

AgentGate 是中心化脚本 + 各仓库独立 workflow。主仓库修了，**每个部署实例的 workflow 也要同步**（UseGEO 就是独立的一份）。这提示未来可以考虑把 workflow 也做成可复用引用，避免逐个修。

---

## 六、相关链接

- AgentGate 修复 PR：#9（v1.1.1）
- UseGEO 同步修复 PR：#12
- 修改文件：`.github/workflows/governance.yml`、`install.sh`、`scripts/selftest.sh`
