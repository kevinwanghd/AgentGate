# 风险注解契约

CI 扫描到下表任一模式时，要求**该代码上方 5 行内**有结构化注解；缺失、字段不全、理由含黑名单词、或 `reviewed:` 距今超 3 个月 → 拒合。

> 这份文档是 AI agent 提交前的**唯一参考**。新增风险类型 → 改本文档 → 走 MR 评审进目录。

---

## 注解格式

### 单行（推荐）

```csharp
// risk:auth-bypass reason:"机器人用户做数据同步, 无人工登录路径" owner:@ad-platform reviewed:2026-06-15
if (adminUserId == "626786582b50ab8ec08b0fa0" || adminUserId == "64918ccaeb21944ec3ecf952")
```

### 多行（理由复杂时）

```csharp
// risk-begin
// type: auth-bypass
// reason: 机器人用户做数据同步, 不走登录, 业务方确认见 REQ-1234
// owner: @data-sync
// reviewed: 2026-06-15
// review-cycle: 6m
// risk-end
```

支持的注释前缀：`//`（C#/JS/TS/Java/Go）、`#`（Python/YAML/Shell）、`<!--  -->`（HTML/XML）。

---

## 字段要求

| 字段 | 必填 | 说明 |
|---|---|---|
| `risk:<type>` | 是 | 必须命中下表"已注册类型"，未注册视为无效 |
| `reason:"..."` | 是 | ≥ 10 个字符，说明业务/安全权衡，禁用黑名单词 |
| `owner:@person` 或 `@team` | 是 | 该豁免的负责人 |
| `reviewed:YYYY-MM-DD` | 是 | 上次确认仍合理的日期，距今 ≤ 90 天 |
| `review-cycle:6m\|12m` | 否 | 自定义复审周期（默认 6m） |

**reason 黑名单词**（出现即视为无效理由）：

```
临时   先这样   历史原因   TODO   待确认   不知道   随便   暂时
quick fix   temp   wip   hack   for now   not sure
```

---

## 理由模板（reason 怎么写）

好的 reason 应该回答三个问题：**做什么**、**为什么不能按常规方式做**、**有什么后果**。

### 模板公式

```
// risk:<type> reason:"<动作/场景>，因为<约束/特殊情况>，风险/影响是<后果>（<确认人/依据>）"
```

### 场景模板示例

#### auth-bypass（鉴权旁路）

```
// risk:auth-bypass reason:"内部定时任务使用服务账号做数据同步，无人工登录路径，已在 SecurityReview-123 确认" owner:@data-platform reviewed:2026-08-26
```

```
// risk:auth-bypass reason:"健康检查端点需要绕过登录以便 K8s liveness probe 调用，已在 infra-456 确认" owner:@infra reviewed:2026-08-26
```

#### magic-id（业务硬编码 ID）

```
// risk:magic-id reason:"种子数据预置的系统管理员账号，ID 来自历史数据迁移，业务方确认见 TICKET-789" owner:@backend reviewed:2026-08-26
```

```
// risk:magic-id reason:"测试夹具中硬编码的 Mock 用户 ID，用于集成测试隔离，由 QA 团队确认" owner:@qa reviewed:2026-08-26
```

#### swallowed-exception（异常吞没）

```
// risk:swallowed-exception reason:"finally 块 cleanup 阶段的日志记录异常，吞没以避免掩盖主业务错误，上层已重试兜底" owner:@backend reviewed:2026-08-26
```

```
// risk:swallowed-exception reason:"优雅退出时的信号处理，忽略所有异常以确保进程干净终止（已有监控告警兜底）" owner:@infra reviewed:2026-08-26
```

#### suppressed-warning（静态检查抑制）

```
// risk:suppressed-warning reason:"CA2000 规则在此处误报（对象生命周期由容器管理），已与 SecurityTeam 确认无需修改" owner:@security reviewed:2026-08-26
```

```
// risk:suppressed-warning reason:"禁用特定行号 eslint-disable/no-unused-vars 用于占位 API 参数，短期内会重构" owner:@api-team reviewed:2026-08-26
```

#### skipped-test（测试跳过）

```
// risk:skipped-test reason:"Flaky 网络测试，已提交 ISSUE-123 跟踪，计划在测试环境稳定后恢复" owner:@qa reviewed:2026-08-26
```

```
// risk:skipped-test reason:"需要外部支付沙箱的集成测试，当前环境不可用，在 STAGING-456 环境配置好后恢复" owner:@payment-team reviewed:2026-08-26
```

#### time-bypass（时间硬编码）

```
// risk:time-bypass reason:"迁移期双写窗口，截止到 2026-09-30，届时删除（已在 MIGRATION-789 记录）" owner:@migration reviewed:2026-08-26
```

```
// risk:time-bypass reason:"Feature Flag 临时硬编码为 true 用于 A/B 测试验证，验证完成后删除" owner:@product reviewed:2026-08-26
```

#### env-hardcode（环境硬编码）

```
// risk:env-hardcode reason:"调试模式下输出详细日志用于排查问题，已在 DEBUG-123 确认，发布前删除" owner:@backend reviewed:2026-08-26
```

```
// risk:env-hardcode reason:"构建期产物注入，CI 编译时替换占位符，源码中无真实环境信息" owner:@ci-team reviewed:2026-08-26
```

#### todo-no-context（无主 TODO）

```
// risk:todo-no-context reason:"遗留代码，待重构但当前无带宽；已在 TECH-DEBT-456 记录，计划 Q4 处理" owner:@team reviewed:2026-08-26
```

#### test-removal（测试删除）

```
// risk:test-removal reason:"用例已合并到 IntegrationTests.A，后者覆盖更全面" owner:@qa reviewed:2026-08-26
```

#### untested（无法单测）

```
// risk:untested reason:"纯 DTO 无业务逻辑，由集成测试间接覆盖" owner:@team reviewed:2026-08-26
```

```
// risk:untested reason:"启动引导代码，依赖框架初始化，无法单测" owner:@infra reviewed:2026-08-26
```

### 模板要点

| 要点 | 错误示例 | 正确示例 |
|------|----------|----------|
| 说清做什么 | `临时修改` | `内部服务账号做数据同步` |
| 说明约束 | `历史原因` | `无人工登录路径，SecurityReview-123 确认` |
| 量化后果 | `可能有风险` | `已在 STAGING 验证无数据泄露风险` |
| 提供依据 | `先这样` | `业务方 @张三 确认，TICKET-456` |

---



---

## 已注册风险类型（8 类模式 + test-removal + untested 两个特殊类型）

### 1. `auth-bypass` — 鉴权旁路

**模式**：字面量 ID/角色字符串与认证字段比较。

```csharp
// 命中
if (userId == "626786582b50ab8ec08b0fa0") ...
if (role == "admin" || role == "superuser") ...
```

**典型例外**：内部机器人账号、健康检查端点、CI 测试桩。

---

### 2. `magic-id` — 业务硬编码 ID

**模式**：业务代码出现 ObjectId（24 位 hex）、UUID、或 ≥ 12 位连续数字字面量。

```csharp
// 命中
var advertiserId = "1733456789012345";
var tenantOid = "626786582b50ab8ec08b0fa0";
```

**典型例外**：种子数据、迁移脚本、测试夹具。

---

### 3. `swallowed-exception` — 异常吞没

**模式**：`catch` 块既不重新抛出也不记日志。

```csharp
// 命中
try { ... } catch { }
try { ... } catch (Exception) { return null; }
```

**典型例外**：明确的可忽略错误（cleanup 阶段）、轮询重试中间态。

---

### 4. `suppressed-warning` — 静态检查抑制

**模式**：`#pragma warning disable`、`[SuppressMessage]`、`// nolint`、`// eslint-disable`、`# noqa` 等。

**例外说明要点**：抑制了哪条规则、为什么这里不适用。

---

### 5. `skipped-test` — 测试跳过

**模式**：`[Fact(Skip="...")]`、`[Ignore]`、`it.skip`、`xit`、`@pytest.mark.skip`、`-tags=skip` 等。

**例外说明要点**：跳过原因 + 何时恢复（贴 issue 链接）。

---

### 6. `time-bypass` — 时间硬编码

**模式**：`DateTime.Now` / `UtcNow` / `time.Now()` 与字面量日期 / 字面量时间窗比较。

```csharp
// 命中
if (DateTime.UtcNow > new DateTime(2026, 9, 30)) ...
```

**典型例外**：feature flag 临时切换、迁移期双写窗口。

---

### 7. `env-hardcode` — 环境硬编码

**模式**：硬编码环境字符串决定行为。

```csharp
// 命中
if (env == "production") { ... }
if (Environment.GetEnvironmentVariable("ASPNETCORE_ENVIRONMENT") == "Development") { ... }
```

**典型例外**：构建期产物注入、调试模式开关（建议改为配置项）。

---

### 8. `todo-no-context` — 无主 TODO

**模式**：`TODO` / `FIXME` / `HACK` 不含 `(owner, YYYY-MM-DD)` 元数据。

```csharp
// 不命中（合规）
// TODO(@alice, 2026-08-01): 切到新 API 后删除
// 命中
// TODO: fix later
```

**例外说明要点**：无法立即修复的根因。

---

### 9. `hardcoded-url` — 硬编码 URL

**模式**：代码中出现带协议前缀的 URL 字面量。

```csharp
// 命中
var apiUrl = "https://api.example.com/v1/users";
// 命中
logger.Info($"Calling https://internal.service.local/health");
```

**典型例外**：本地开发环境 URL、测试环境 URL、明确的配置文件替代方案。

---

### 10. `sensitive-log` — 敏感字段明文打印

**模式**：日志打印语句中包含敏感字段名（password/token/secret/key 等）。

```csharp
// 命中
logger.Info($"password={user.Password}");
// 命中
console.log("token:", accessToken);
```

**典型例外**：脱敏后的日志（如 `password=***`）、安全审计专用日志系统。

---

### 11. `sql-string-concat` — SQL 字符串拼接

**模式**：SQL 语句使用字符串拼接或字符串格式化。

```csharp
// 命中
var sql = "SELECT * FROM users WHERE id=" + userId;
// 命中
query = f"SELECT * FROM orders WHERE status='{status}'"
```

**典型例外**：参数化查询内部实现、仅拼接表名/列名（无用户输入）、ORM 内部实现。

---

### 12. `command-injection` — 命令注入风险

**模式**：系统命令调用中可能包含外部输入。

```python
# 命中
os.system(f"grep {user_input} logs.txt")
# 命中
subprocess.run(f"rm -rf {directory}", shell=True)
```

**典型例外**：命令字面量全为内部常量、无外部输入路径、明确的输入校验。

---

### 13. `weak-crypto` — 弱加密算法

**模式**：使用 MD5/SHA1/DES/RC4/ECB 等弱加密算法。

```python
# 命中
hashlib.md5(password.encode()).hexdigest()
# 命中
Cipher.getInstance("DES/ECB/PKCS5Padding")
```

**典型例外**：仅用于非安全场景（如 checksum、测试数据）、明确注释说明"仅用于兼容旧系统"。

---

## 测试删除保护

`test-removal` 是一个特殊类型，专用于**删除已有测试**的场景。CI 检测到 `[Fact]` / `[Test]` / `it(` 等被删除时，要求 commit message 或 MR 描述含：

```
risk:test-removal reason:"用例已合并到 IntegrationTests.A" owner:@team reviewed:2026-06-15
```

理由黑名单词同样适用。

---

## 测试缺失豁免

`untested` 是一个特殊类型，专用于**改动的生产代码确实无法/不必单测**的场景（如纯 DTO、启动引导、迁移脚本）。`check_tested.py` 检测到改动的生产文件没有测试痕迹时，可加注解豁免：

```
// risk:untested reason:"纯数据传输对象无业务逻辑，由集成测试间接覆盖" owner:@team reviewed:2026-06-26
```

字段要求与其他风险注解一致（reason ≥10 字、不含黑名单词、reviewed 3 个月有效期）。

更优先的放行方式是真正写测试：用 `record_test_run.py` 跑单元测试 + 本次 MR 改动测试文件。整目录免检（DTO/迁移/生成代码）配在 `governance.config.yml` 的 `testing.exclude_paths`。

> 提醒：`untested` 注解能让 CI 放行，但它声明的是"这段没单测"。带失败测试记录（`Tested: fail`）则**无法用任何注解豁免**，必须修复。

---

## 注解过期机制

- `reviewed:` 自带 3 个月有效期（`reviewed_max_age_days: 90`，可在 `governance.config.yml` 调整）。
- **过期不立即阻断**：每周 CI 跑全仓扫描，把"30 天内将过期"和"已过期"的注解列入 `governance/reports/expired-annotations.md`。
- **触碰即触发**：过期注解所在文件被任何 MR 修改时，CI 强制要求把 `reviewed:` 更新到当天，否则拒合。

这套机制的目标是：**不需要人主动周期性 review，业务自然会触碰相关代码，顺手刷新**。

---

## 新增风险类型流程

1. 在本文件追加新章节，写明：模式、命中示例、典型例外。
2. 提 MR，标题 `governance: add risk type <name>`。
3. CODEOWNERS 中治理负责人 approve 后合入。
4. 新规则在 MR 合入下一周生效（给各事业部留 buffer）。
