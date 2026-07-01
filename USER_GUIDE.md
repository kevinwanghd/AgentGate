# AgentGate 使用手册

本文档面向**开发者**和**团队管理员**,讲解 AgentGate 的日常使用方法。

---

## 目录

- [5 分钟快速上手](#5-分钟快速上手)
- [开发者日常工作流](#开发者日常工作流)
- [配置参考](#配置参考)
- [CI 检查项详解](#ci-检查项详解)
- [高级用法](#高级用法)
- [团队管理员指南](#团队管理员指南)

---

## 5 分钟快速上手

### 你是开发者,刚接手一个已装 AgentGate 的项目

**场景**:用 AI 加一个新功能,提交时发生了什么?

#### 第 1 步:AI 写代码

你让 Claude/Cursor/Copilot 写了一段代码:

```csharp
// src/PaymentService.cs
public bool Verify(User user) {
    if (user.Id == "bot_12345") return true;  // 机器人账号免验证
    return CheckSignature(user);
}
```

#### 第 2 步:提交

```bash
git add src/PaymentService.cs
git commit -m "feat: 支付服务加机器人账号白名单"
```

**自动发生**:git hook 读取 AI 证据(Claude Code/Cursor 自动留的),算出 AI 用量,追加到 commit message:

```
feat: 支付服务加机器人账号白名单

AI-Usage: heavy
AI-Tools: claude-code
AI-Models: opus-4.8
AI-Lines: 3/3
Tested: none
```

你不用手动填,系统自动盖上。

#### 第 3 步:推送并发 MR

```bash
git push origin feat/bot-whitelist
```

在 GitLab/GitHub 发 Merge Request。

#### 第 4 步:CI 检查(自动跑)

4 个 job 自动跑,几十秒后你看到:

| Job | 结果 | 说明 |
|---|---|---|
| **risk-scan** | ❌ **FAIL** | 检测到硬编码 ID 比较,要求加注解 |
| **secret-scan** | ✅ PASS | 未检测到密钥泄露 |
| **test-check** | ❌ **FAIL** | 改了生产代码但没测试覆盖 |
| **mr-validate** | ✅ PASS | MR 描述符合规范 |

**你看到两个红灯**。点开 `risk-scan` 日志:

```
src/PaymentService.cs:3
  matched: magic-id (硬编码业务 ID/账号用于条件判断)
  problem: 上方 5 行内未找到 risk: 注解
  fix: 在该行上方加  // risk:magic-id reason:"..." owner:@team reviewed:2026-06-30
```

#### 第 5 步:加注解 + 补测试

你理解了:这行确实有风险(硬编码 ID),但是合理的(机器人白名单)。加注解说明:

```csharp
// risk:magic-id reason:"合法机器人账号白名单,已架构评审确认无注入风险" owner:@payment-team reviewed:2026-06-30
if (user.Id == "bot_12345") return true;
```

再补一个单元测试:

```csharp
[Test]
public void BotAccountShouldBypassVerification() {
    var bot = new User { Id = "bot_12345" };
    Assert.IsTrue(paymentService.Verify(bot));
}
```

提交:

```bash
git add src/PaymentService.cs tests/PaymentServiceTests.cs
git commit -m "test: 补机器人白名单测试 + 加风险注解"
git push
```

#### 第 6 步:CI 全绿

这次 4 个 job 都过了:

| Job | 结果 |
|---|---|
| risk-scan | ✅ PASS (注解合法) |
| secret-scan | ✅ PASS |
| test-check | ✅ PASS (测试文件改了) |
| mr-validate | ✅ PASS |

**MR 可以合入了**。团队 lead 点 Merge,代码进 main。

---

## 开发者日常工作流

### 提交时自动盖 AI-Usage trailer

装了 hook 后,每次 `git commit`:

1. **系统读 `.governance/ai-evidence.jsonl`**(AI 工具自动写的证据)
2. **算 AI 用量**:AI 改的行数 / 总改动行数
3. **自动追加** `AI-Usage` 等 trailer 到 commit message

你**不用管**,自动的。如果没有 AI 证据,会标 `AI-Usage: none`。

### 风险代码加注解

当 CI 的 `risk-scan` 报错,告诉你某行匹配了风险模式:

```
src/Auth.cs:42
  matched: auth-bypass (认证/权限绕过)
  problem: 上方 5 行内未找到 risk: 注解
```

**你要做**:在那行**上方**加注解(1-5 行内):

```csharp
// risk:auth-bypass reason:"管理后台内网访问已通过IP白名单隔离" owner:@security-team reviewed:2026-06-30
if (req.Headers["X-Internal"] == "true") return true;
```

**注解格式**:

```
// risk:<type> reason:"<说明>" owner:@<团队> reviewed:<日期>
```

| 字段 | 说明 | 示例 |
|---|---|---|
| `type` | 风险类型,必须在 `config.yml` 的 `registered_types` 里 | `auth-bypass` |
| `reason` | 为什么这么写、为什么安全(≥10 字,不能用"临时"等敷衍词) | `"管理后台内网访问已通过IP白名单隔离"` |
| `owner` | 负责团队(格式 `@team-name`) | `@security-team` |
| `reviewed` | 评审日期(YYYY-MM-DD,默认 180 天后过期) | `2026-06-30` |

**为什么要注解?**
- 留审计痕迹:谁批的、什么理由、何时评审
- 定期复查:注解过期(默认 6 个月)后会重新拦,逼你再评估一次

### 测试覆盖要求

改了生产代码(非 `.md`/`.txt`/`docs/`),`test-check` 要求**至少一个测试痕迹**:

| 满足任一即过 |
|---|
| 本次 diff 改了测试文件(文件名含 `test`/`spec`/`Test`) |
| commit message 有 `Tested: pass` trailer(用 `record_test_run.py` 记录) |
| 代码上方有 `risk:untested` 注解(说明无法单测的理由) |

**示例 1:改了测试文件**

```bash
git add src/Service.cs tests/ServiceTests.cs
git commit -m "feat: xxx"
# ✅ 过,因为 tests/ServiceTests.cs 在 diff 里
```

**示例 2:记录测试运行**

```bash
# 改了 src/Service.cs,但测试文件没在这次提交(可能之前写过)
python governance/scripts/record_test_run.py -- dotnet test
# 跑完后提示:测试通过,已记录。下次 commit 会自动盖 Tested: pass

git add src/Service.cs
git commit -m "feat: xxx"
# commit message 自动追加 Tested: pass (12/12)
# ✅ 过
```

**示例 3:真的无法单测**

```csharp
// DTO,纯数据无逻辑
// risk:untested reason:"仅数据传输对象无业务逻辑" owner:@backend-team reviewed:2026-06-30
public class UserDto {
    public string Name { get; set; }
}
```

---

## 配置参考

### 配置文件位置

`governance/config.yml`(旧版本在根目录 `governance.config.yml`,兼容)

### 完整配置示例

```yaml
# MR 治理规范配置
version: 1.0

metadata:
  # 门禁强度: soft(只警告,不拦合并) 或 hard(拦截)
  enforcement: soft
  # soft 模式缓冲期(天): 期间只警告, 过期后自动变 hard
  soft_deadline: 90
  # MR 描述必填段落(空列表 = 不强制)
  mandatory_fields: [background, changes, self_test, risks]

risk_annotations:
  # 门禁强度(可独立于 metadata.enforcement)
  enforcement: soft
  soft_deadline: 90
  # 允许的风险类型(不在列表里的注解 = 无效)
  registered_types:
    - auth-bypass        # 认证/权限绕过
    - magic-id           # 硬编码业务 ID
    - sql-concat         # SQL 字符串拼接
    - hardcoded-crypto   # 硬编码密钥/盐
    - todo-no-context    # TODO 无上下文
    - global-state       # 全局可变状态
    - test-sleep         # 测试用 sleep
    - empty-except       # 空异常处理
  # 注解过期时间(天,reviewed 字段多久后失效)
  reviewed_max_age_days: 180
  # reason 黑名单词(出现这些词 = 敷衍,拒绝)
  reason_blacklist: [临时, hack, 先这样, 回头改, workaround]
  # 公司自定义扫描规则(可选)
  custom_patterns:
    - type: my-unsafe-query
      regex: 'UnsafeQuery\s*\('
      desc: "禁用内部 UnsafeQuery, 改用 SafeQuery"

testing:
  # 门禁强度
  enforcement: soft
  soft_deadline: 90
  # 排除路径(不要求测试覆盖)
  exclude_paths:
    - "*.md"
    - "*.txt"
    - "docs/"
    - "scripts/"
```

### 常见配置调整

#### 1. 从软启动切换到硬启动

```yaml
metadata:
  enforcement: hard   # 改这里
  # soft_deadline 不再需要
```

或等 90 天缓冲期自动切换。

#### 2. 放宽注解过期时间(1 年)

```yaml
risk_annotations:
  reviewed_max_age_days: 365
```

#### 3. 不强制 MR 描述格式

```yaml
metadata:
  mandatory_fields: []   # 空列表 = 不检查
```

#### 4. 加公司专属风险类型

```yaml
risk_annotations:
  registered_types:
    - auth-bypass
    - my-dangerous-api    # 新增
  custom_patterns:
    - type: my-dangerous-api
      regex: 'CallDangerousAPI\s*\('
      desc: "禁用 CallDangerousAPI, 改用 SafeAPI"
```

---

## CI 检查项详解

### 1. risk-scan(风险代码扫描)

**扫什么**:8 类内置风险模式 + 公司自定义规则

| 类型 | 正则示例 | 为什么危险 |
|---|---|---|
| `auth-bypass` | `if (user.role == "admin")` | 硬编码角色/权限判断 |
| `magic-id` | `if (id == "12345")` | 硬编码业务 ID |
| `sql-concat` | `"SELECT * FROM " + table` | SQL 拼接易注入 |
| `hardcoded-crypto` | `salt = "mysalt123"` | 硬编码密钥/盐 |
| `todo-no-context` | `// TODO fix` | TODO 无说明 |
| `global-state` | `static int counter` | 全局可变状态 |
| `test-sleep` | `Thread.Sleep(1000)` | 测试用 sleep 不稳定 |
| `empty-except` | `catch { }` | 空异常吞错误 |

**怎么过**:
- 改掉风险代码,或
- 加合法注解:`// risk:<type> reason:"..." owner:@team reviewed:2026-06-30`

**完整输出示例**:

```
[risk-scan] FAIL — 以下风险代码缺少合法注解:

  src/Auth.cs:42
    matched: auth-bypass (认证/权限绕过)
    problem: 上方 5 行内未找到 risk: 注解
    fix: 在该行上方加  // risk:auth-bypass reason:"..." owner:@team reviewed:2026-06-30

共 1 处违规。详见 governance/risk-types.md
```

### 2. secret-scan(密钥泄露检测)

**扫什么**:用 [gitleaks](https://github.com/gitleaks/gitleaks) 检测:
- 私钥(RSA/DSA/EC/PGP)
- API token(AWS/GitHub/Stripe/...)
- 数据库连接串
- OAuth secret

**怎么过**:
- **不要提交真密钥**。用环境变量、密钥管理服务
- 测试用假密钥?加 `.gitleaksignore`:
  ```
  tests/fixtures/fake_key.pem:1
  ```

**无法豁免**:真密钥泄露无合法理由,必须改。

### 3. test-check(测试覆盖检查)

**检查什么**:改了生产代码,是否有测试痕迹?

**三种过法**:
1. **改了测试文件**(文件名含 `test`/`spec`/`Test`)
2. **commit 有 `Tested:` trailer**(用 `record_test_run.py` 记录)
3. **代码有 `risk:untested` 注解**(说明无法单测的理由)

**不查的文件**:`.md`/`.txt`/`docs/`/`scripts/`(见 `config.yml` 的 `testing.exclude_paths`)

**实测失败会怎样**:
- 你跑 `dotnet test` 有 2 个失败
- 用 `record_test_run.py` 记录 → **拒绝**,退出码非 0
- 提示:先修复失败的测试,再记录

### 4. mr-validate(MR 描述校验)

**检查什么**:MR 描述是否写全这 4 段:

```markdown
## 背景 - <为什么做>
## 变更内容 - <改了什么>
## 自测确认 - <怎么验证的>
## 风险与回滚 - <有风险吗,怎么回滚>
```

**还检查什么**:
- commit 是否有 `AI-Usage:` trailer(读 commit message)
- `Tested:` trailer(可选,有生产代码改动时建议有)

**怎么过**:用 MR 模板(`.gitlab/merge_request_templates/default.md`),填完整。

---

## 高级用法

### 自定义扫描规则(custom_patterns)

公司有专属"危险模式"(内部危险 API、特定密钥格式)?在 `config.yml` 加:

```yaml
risk_annotations:
  registered_types:
    - auth-bypass
    - my-unsafe-query      # 注册新类型
  custom_patterns:
    - type: my-unsafe-query
      regex: 'UnsafeQuery\s*\('
      desc: "禁用内部 UnsafeQuery, 改用 SafeQuery"
    - type: my-secret-format
      regex: 'XK-[A-Za-z0-9]{16}'
      desc: "疑似硬编码内部密钥(XK-开头)"
```

保存后,CI 自动用这些规则扫描。命中后要加 `risk:my-unsafe-query` 注解,和内置类型一样。

**正则写错了怎么办?**
- 跳过并警告,不中断扫描
- 看 CI 日志有 `[scan-risks] 警告: custom_pattern 'xxx' 正则无效`

### 调整门禁强度

#### 全局切硬启动

```yaml
metadata:
  enforcement: hard
```

#### 只对某个检查项切硬启动

```yaml
metadata:
  enforcement: soft   # 其他检查项软启动
risk_annotations:
  enforcement: hard   # 只有风险扫描硬启动
```

#### 延长软启动缓冲期

```yaml
metadata:
  enforcement: soft
  soft_deadline: 180   # 改成 180 天
```

### 公司黑名单词

`reason` 里出现这些词 → 拒绝(认为是敷衍):

```yaml
risk_annotations:
  reason_blacklist:
    - 临时
    - hack
    - 先这样
    - workaround
    - 回头改
    - 来不及
```

加你们公司常用的搪塞词。

### 排除某些路径不检查测试

```yaml
testing:
  exclude_paths:
    - "*.md"
    - "docs/"
    - "migrations/"   # 数据库迁移脚本
    - "generated/"    # 自动生成代码
```

---

## 团队管理员指南

### 配置分支保护

让门禁**真正强制**生效:

**GitLab**:
1. Settings → Repository → Protected branches
2. 选 `main`,勾 "Developers cannot push" + "Pipelines must succeed"

**GitHub**:
1. Settings → Branches → Add rule
2. Branch name:`main`,勾 "Require status checks",选中 4 个 job

配置后,红的 MR/PR 无法合入。

### 查看 AI 用量报告

每周跑一次,生成 AI 用量统计:

```bash
cd your-repo
python governance/scripts/collect_ai_usage.py \
  --since 7.days.ago \
  --format markdown > ai-usage-report.md
```

报告示例:

```markdown
| 作者 | 提交数 | AI 行数 | 总行数 | AI 占比 |
|---|---|---|---|---|
| alice | 12 | 450 | 520 | 87% |
| bob | 8 | 120 | 300 | 40% |
```

用于:
- 了解团队 AI 使用情况
- 评估 AI 效率
- 合规审计

### 处理过期注解

定期跑:

```bash
python governance/scripts/report_expired.py \
  --config governance/config.yml \
  --output expired.md
```

生成过期注解清单(默认 180 天未复查的):

```markdown
## 过期风险注解(需复查)

| 文件 | 行 | 类型 | 评审日期 | 过期天数 |
|---|---|---|---|---|
| src/Auth.cs | 42 | auth-bypass | 2025-09-01 | 120 天 |
```

通知相关团队复查、更新 `reviewed` 日期或改代码。

### 调整配置给新项目

新项目或老项目首次接入,建议:

```yaml
metadata:
  enforcement: soft      # 软启动
  soft_deadline: 90      # 3 个月缓冲
risk_annotations:
  enforcement: soft
  reviewed_max_age_days: 365   # 放宽到 1 年
testing:
  enforcement: soft
```

3 个月后评估,再切 `hard`。

---

## 下一步

- **开发者**:直接开始开发,系统会在提交时给反馈
- **管理员**:配好分支保护,定期看 AI 用量和过期注解报告
- **问题?**:看 [安装指南的常见问题](INSTALL.md#常见问题)

欢迎反馈改进建议。
