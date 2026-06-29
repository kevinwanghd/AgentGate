<!--
MR 治理规范 v1 · 默认模板
保留所有段落标题（## 开头的行），CI 会按标题解析。
v1 阶段，本模板检查为软提示，不阻断合并；soft_deadline 后转硬阻断。
详细规范见: docs/governance/mr-spec.md
-->

## 背景

<!-- 为什么需要这个变更？口头需求也可，把背景写清楚即可。 -->

## 变更内容

<!-- 这个 MR 改了什么。建议 3-7 条要点。 -->

-
-
-

## 不包含的内容

<!-- 明确没有处理什么，避免 reviewer 误解范围。没有可写"无"。 -->

-

<!--
关于 AI 使用声明：本规范不在 MR 描述里手填 AI-Usage。
AI 使用程度由 git hook 在提交时自动采集并写入 commit trailer
(AI-Usage / AI-Tools / AI-Lines)，CI 直接从 commit 读取。
一次性安装 hook：bash governance/scripts/install-hooks.sh
-->

## 自测确认

<!-- 至少跑了什么、看到了什么。不要只写"已测试"。 -->

- [ ] 本地构建通过：`命令`
- [ ] 单元测试通过：`命令`
- [ ] 手动验证场景：
  1.

## 风险与回滚

<!--
变更超过阈值（500 行 / 触及 ci|CODEOWNERS|charts|secret / 含 schema 变更）必填，否则可写"低风险, 无需特别说明"。
-->

- 风险点：
- 应对/回滚：

## 关联

<!--
可选。有就贴：
- Issue: #
- Requirement-ID: REQ-xxxx 或 CR-xxxx (DeliverHQ)
- Spec / Design:
-->

-

---

<!--
合并前自检（不阻断，仅提醒）：
- [ ] 标题符合 <type>: <描述> 规范（feat/fix/refactor/perf/test/docs/chore/ci/security）
- [ ] 无调试代码、无明文密钥
- [ ] 触发风险注解扫描的代码已加 risk:* 注解 (见 docs/governance/risk-types.md)
- [ ] commit trailer 的 AI-Usage 由 hook 自动生成 (装 hook: bash governance/scripts/install-hooks.sh)
-->
