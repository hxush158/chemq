# 架构说明

## 数据流

```
用户问题
   │
   ▼
┌─────────────┐   意图路由（Haiku + structured outputs + prompt cache）
│  Router     │   → QueryPlan{intent, metrics, time_range, needs_knowledge}
└─────┬───────┘
      │ out_of_scope ──────────────────────────────► 拒答（带理由）
      ▼
┌─────────────┐        ┌──────────────┐
│ SQL Agent   │        │ Knowledge    │   hybrid 检索（向量 + BM25 + RRF）
│ (Sonnet)    │        │ Agent        │   → Citation[]
└─────┬───────┘        └──────┬───────┘
      │ SqlCandidate          │
      ▼                       │
┌─────────────┐               │
│ sqlguard    │ AST 校验 · 白名单 · 强制 LIMIT
└─────┬───────┘               │
      │ 通过                  │
      ▼                       │
┌─────────────┐               │
│ 只读账号执行 │ chemq_ro + RLS + statement_timeout
└─────┬───────┘               │
      │ rows                  │
      └───────────┬───────────┘
                  ▼
           ┌─────────────┐
           │ Narrator    │  只拿到 rows，无任何工具 —— 物理上无法自己去查
           └─────┬───────┘
                 ▼
           ┌─────────────┐
           │ Critic      │  独立调用，只核对数字是否出现在 rows 里
           └─────┬───────┘
                 ▼
            Answer + qa_audit 留痕
```

## 三条贯穿全局的设计原则

**1. 数值由 SQL 算，模型不算数**
Narrator 拿不到数据库工具，Critic 逐个核对数字。任何模型参与算数的地方都是 bug。

**2. 口径由语义层定，模型不定口径**
模型只能查 `v_*` 视图，查不到 `fact_*` 明细表 —— 这是数据库权限强制的，不是 prompt 约定的。
折标煤系数等参数从维度表取，禁止硬编码。

**3. 纵深防御**
| 层 | 挡什么 | 失效时谁兜底 |
|---|---|---|
| 意图分类 | 问题本身不对（越权、注入、超范围） | sqlguard |
| sqlguard | SQL 不对（写操作、越表、无 LIMIT） | 数据库权限 |
| 只读角色 + RLS | 前两层有 bug | — |

## 为什么不用 Bedrock

项目选了 Anthropic API 直连 + AWS 只做基础设施：

- Bedrock **不在 always-free 里**，会吃掉本就有限的 credits
- 直连能第一时间用到最新的 API 特性（structured outputs、context 管理等）
- Agent SDK 支持 `CLAUDE_CODE_USE_BEDROCK=1` 切换，**迁移成本很低**

这个取舍本身值得在报告里写一段 —— 企业客户会问"为什么不用云厂商的"。
答案是：先用能力最强、迭代最快的接口把方案验证出来，合规要求明确后再切到 Bedrock，
切换成本是一个环境变量。
