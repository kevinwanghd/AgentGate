## 背景

实现 P0/P1 级别治理增强，修复 AgentGate 门禁系统的关键漏洞，提升测试证据采集自动化程度。

## 变更内容

### P0-1: Hook 安装降级方案
- 当 prepare-commit-msg hook 缺失时，pre-commit hook 作为降级方案接管 AI-Usage trailer 写入
- 新增 post-commit hook，清理 pending_trailer 并自动采集本次 commit 的 AI evidence
- 确保即使 hook 链失败，AI-Usage trailer 也能正确写入

### P0-2: 测试证据绕过检测
- 新增 governance:test-bypass CI job
- 检测生产文件变更但无对应测试执行记录的情况
- 防止通过直接执行 pytest 等命令绕过 record_test_run.py 的记录机制

### P0-3: 风险注解验证加固
- reviewed 日期过期时触发软提醒（继承的注解不因日期硬阻断）
- 黑名单扩展至 16 个术语，覆盖更多临时性表述
- 新增语义验证，防止理由简单重复风险类型名称

### P1-1: AI evidence 自动采集
- 新增 --collect-commit 模式，供 post-commit hook 调用
- 自动采集 diff 行数到 ai-evidence.jsonl，无需手动写入
- 仅存在 auto tool marker 时降级为 'used'

### P1-3: 新增风险模式类型
- hardcoded-url: 检测代码中的生产 API URL
- sensitive-log: 检测日志中的明文敏感字段
- sql-string-concat: 检测 SQL 注入风险
- command-injection: 检测含外部输入的 shell 命令
- weak-crypto: 检测 MD5/SHA1/DES/RC4/ECB 等弱加密算法

## 风险与回滚

**风险**：新增的 test-bypass CI job 可能在某些情况下产生误报（如未正确配置测试记录）。

**回滚**：如有问题，可通过 `git revert` 回滚单个 commit，或在 CI 中禁用相关 job。

## 自测确认

- [x] 本地运行 `python scripts/scan_risks.py` 通过
- [x] 本地运行 `python -m pytest tests/` 全部通过
- [x] `bash scripts/selftest.sh` 通过
