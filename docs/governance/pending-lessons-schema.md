# Pending Lessons Schema

**Version:** 1.0
**Location:** `.governance/pending-lessons/`
**隔离目的:** 此目录与 `lessons/` 物理隔离，不被 `validate_lessons.py` 的 lessons/v1 校验器扫描。

---

## 文件命名规范

```
{YYYYMMDD}_{fingerprint}.json
```

- `YYYYMMDD`: 发现日期
- `fingerprint`: 基于 `pattern_type + normalized_code_shape` 的哈希值（不含仓库/CR 标识）
- 示例: `20260830_a1b2c3d4.json`

---

## Schema Fields

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 全局唯一 ID，格式 `{fingerprint}_{timestamp}` |
| `pattern_type` | string | 是 | 风险类型，如 `command-injection`、`sql-string-concat` |
| `source_repo` | string | 是 | 来源仓库名，如 `deliverhq`、`agentgate` |
| `source_ref` | string | 是 | 触发扫描的 ref，如 commit SHA、MR ID |
| `detected_at` | string | 是 | ISO 8601 时间戳，格式 `YYYY-MM-DDTHH:MM:SS+08:00` |
| `failure_context` | object | 是 | 失败上下文详情（见下） |
| `evidence` | object | 是 | 证据详情（见下） |
| `regression` | string | 是 | 回归测试建议（与 lessons/v1 保持一致） |
| `status` | string | 是 | 状态：`pending` \| `confirmed` \| `rejected` \| `promoted` |
| `fingerprint` | string | 是 | 跨仓库聚合指纹 |
| `discovered_by` | string | 是 | `agent` \| `human` |
| `review` | object | 否 | 审核信息（状态非 `pending` 时必填） |

---

## failure_context

| 字段 | 类型 | 说明 |
|------|------|------|
| `file` | string | 文件路径 |
| `line` | integer | 行号 |
| `code_snippet` | string | 风险代码片段（原始） |
| `language` | string | 编程语言 |
| `pattern_description` | string | 命中的风险模式描述 |

---

## evidence

| 字段 | 类型 | 说明 |
|------|------|------|
| `diff_base` | string | 扫描基准 |
| `scan_command` | string | 触发扫描的命令 |
| `scan_output_hash` | string | 扫描输出的 SHA256 哈希（防篡改） |
| `raw_violation` | object | scan_risks.py 的原始违规对象 |

---

## review

| 字段 | 类型 | 必填条件 | 说明 |
|------|------|----------|------|
| `reviewer` | string | 非 `pending` | 审核人 |
| `reviewed_at` | string | 非 `pending` | 审核时间 |
| `decision` | string | 非 `pending` | `confirmed` \| `rejected` \| `promoted` |
| `reason` | string | `rejected` | 拒绝原因 |
| `classification` | string | `confirmed` | 分类：`code-pattern`（可正则化）\| `process-lesson`（流程教训） |
| `target_path` | string | `confirmed` | 写入目标路径，如 `patterns/python.yml` 或 `lessons/process.yml` |
| `suggested_regex` | string | `code-pattern` | 建议的正则表达式 |
| `enforcement` | string | `confirmed` | `hard` \| `soft`（自动生成内容默认 `soft`） |

---

## 状态流转

```
pending → confirmed → promoted
            ↓
          rejected
```

- `pending`: 待审核
- `confirmed`: 人工确认有效，等待应用
- `promoted`: 已写入 patterns/ 或 lessons/，规则生效
- `rejected`: 人工判断为误报，记录原因后可统计废弃率

---

## 校验规则

1. **id 唯一性**: 同一 fingerprint + source_repo 只能有一条 pending 记录
2. **status 枚举**: 必须是上述四种状态之一
3. **review 必填**: status 非 `pending` 时必须有 review 对象
4. **rejected 必填 reason**: 拒绝必须保存原因
5. **confirmed 必填 classification**: 确认必须指定分类

---

## 与 lessons/v1 的隔离保证

- pending 文件放在 `.governance/pending-lessons/`，不在 `lessons/` 目录下
- `validate_lessons.py` 使用 `directory.glob("*.yml")` 非递归扫描，不进入子目录
- 任何人将 pending 文件移入 `lessons/` 会因为缺少必需字段或格式不兼容而校验失败
