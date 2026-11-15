# AgentGate 审查要求

## 审查要点

只报会导致门禁失效、越权、数据/密钥泄露或消费仓库回归的问题，不报风格。

- 门禁自身不可绕过：策略只从目标分支读取；PR 不得修改自己的门禁策略、覆盖 evidence、伪造 check 名。
- 自授权：不得让 gate 对「修改规则本身」的变更自动放行；critical 路径必须保留人工环节。
- 脚本安全：shell/python 参数要正确引用；不得把 PR 内容当命令或配置执行；git 命令防注入。
- Evidence 契约：checks 名称、pass/fail/skip 语义变更必须同步 gate_decision 与文档。
- 失败要可见：Skipped 只能用于「不适用」，不能吞掉真实失败；fail-closed 不得被静默放宽。
- 兼容性：可复用工作流的 inputs/secrets 变更要向后兼容，不能破坏消费仓库。
- 测试：行为变化要有对应测试；删除测试或 skip 必须说明理由。
