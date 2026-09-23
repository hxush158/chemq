# ChemQ 网关（W5）

本地起：

```bash
cd gateway/ChemQ.Gateway
dotnet run
```

W5 之前这个目录只是骨架，不用管它。

## 本周要想清楚的问题

1. `region` 为什么必须由网关决定，不能由客户端传？
2. 如果 AI 服务超时了，网关该返回什么？重试吗？
3. 审计日志记在网关还是 AI 服务？还是两边都记？记什么？
