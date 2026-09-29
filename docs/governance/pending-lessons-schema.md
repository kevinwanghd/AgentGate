# Pending Lessons Schema

**Version:** 2.0（2026-09 起统一为 YAML，旧 JSON 格式已废弃）
**Location:** `.governance/pending-lessons/`
**隔离目的:** 此目录与 `lessons/` 物理隔离，不被 `validate_lessons.py` 的 lessons/v1 校验器扫描。

**字段权威定义见 [`.governance/pending-lessons/SCHEMA.md`](../../.governance/pending-lessons/SCHEMA.md)**，本文只补充审核流程约定。

---

## 工具链

| 环节 | 脚本 |
|------|------|
| 写入 / 去重合并 | `scripts/pending_writer.py`（文件名 `<pattern_type>-<fingerprint>.yml`） |
| 校验 | `scripts/validate_pending.py`；`scripts/pending_lessons_schema.py` 为 CI 兼容入口 |
| 审核 / 应用 | `scripts/lessons_review.py` |
| 跨仓聚合 / 指标 | `scripts/aggregate_pending.py`、`scripts/governance_metrics.py` |

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
