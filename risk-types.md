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
