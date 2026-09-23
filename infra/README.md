# 基础设施（W5）

用 **C# 版 CDK** —— 和网关同一套技术栈。

```bash
cd infra
cdk bootstrap        # 每个账号+区域只需一次
cdk diff
cdk deploy
```

## 开工前第一件事

**先建 Budget 告警，再写任何一行 IaC。**

```bash
aws budgets create-budget --account-id <ACCOUNT_ID> \
  --budget file://budget.json --notifications-with-subscribers file://notify.json
```

模板在 `infra/budget.json` / `infra/notify.json`。

## Stack 内容

| 资源 | 免费套餐 | 备注 |
|---|---|---|
| Lambda × 2（网关 + AI 服务） | always-free 100 万次/月 | AI 服务用容器镜像 |
| API Gateway HTTP API | always-free | 比 REST API 便宜很多 |
| Cognito User Pool | always-free | |
| S3（知识库原文） | always-free 额度内 | |
| DynamoDB（会话） | always-free 25GB | |
| ECR | always-free 500MB | 镜像要瘦身 |
| CloudWatch Logs | always-free 5GB | **设保留期，别让日志无限增长** |
| Parameter Store SecureString | 免费 | Secrets Manager 每个 secret 约 $0.40/月 |

数据库 W5 仍在本地。要上云的话是 Aurora Serverless v2（最小 0 ACU 自动暂停），
用 credits 覆盖 —— 那是 W5 的加分项。

## 最小权限清单

- AI 服务 Lambda：读 S3 知识库桶（只读）、读写 DynamoDB 会话表、读 Parameter Store
  指定路径、写 CloudWatch Logs。**不要给 `s3:*` 或 `dynamodb:*`。**
- 网关 Lambda：调用 AI 服务 Lambda、写 CloudWatch Logs。
