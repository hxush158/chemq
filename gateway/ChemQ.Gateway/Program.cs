// ChemQ 网关（W5）
//
// 职责边界 —— 这是本周要想清楚的架构问题：
//   网关做：认证(Cognito JWT) · 数据权限(user -> region) · 审计 · 限流 · 幂等
//   AI 服务做：编排与取数，只信任网关传来的 X-Chemq-User / X-Chemq-Region
//
// 为什么不把认证放进 Python 服务：企业里认证/授权/审计通常已有统一的一层，
// AI 服务应该是可替换的组件，而不是另起一套权限体系。

using System.Security.Claims;
using Microsoft.AspNetCore.Authentication.JwtBearer;

var builder = WebApplication.CreateBuilder(args);

// 部署到 Lambda 时生效；本地 dotnet run 时是 no-op
builder.Services.AddAWSLambdaHosting(LambdaEventSource.HttpApi);

var cognitoAuthority = builder.Configuration["Cognito:Authority"]
    ?? "https://cognito-idp.ap-southeast-2.amazonaws.com/REPLACE_ME";

builder.Services
    .AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
    .AddJwtBearer(o =>
    {
        o.Authority = cognitoAuthority;
        o.TokenValidationParameters.ValidateAudience = false; // Cognito access token 无 aud
    });
builder.Services.AddAuthorization();

builder.Services.AddHttpClient("ai", c =>
{
    c.BaseAddress = new Uri(builder.Configuration["AiService:BaseUrl"] ?? "http://localhost:8000");
    c.Timeout = TimeSpan.FromSeconds(30);
});

builder.Services.AddRateLimiter(_ => { });   // TODO(W5): 按用户限流

var app = builder.Build();
app.UseAuthentication();
app.UseAuthorization();

app.MapGet("/healthz", () => Results.Ok(new { ok = true }));

app.MapPost("/ask", async (AskRequest req, ClaimsPrincipal user, IHttpClientFactory http) =>
{
    // TODO(W5): 从 Cognito group 映射到数据权限
    //   业务规则：大区销售 -> 本大区；总部 -> ALL
    //   关键点：region 由网关决定，绝不能由客户端传 —— 否则用户自己改个 header 就越权了
    var userId = user.FindFirstValue("sub") ?? throw new InvalidOperationException("no sub");
    var region = MapRegion(user);

    var client = http.CreateClient("ai");
    client.DefaultRequestHeaders.Add("X-Chemq-User", userId);
    if (region is not null) client.DefaultRequestHeaders.Add("X-Chemq-Region", region);

    // TODO(W5): 审计日志（who/when/what/结果摘要）落 CloudWatch，并关联 trace_id
    var resp = await client.PostAsJsonAsync("/ask", req);
    return Results.Stream(await resp.Content.ReadAsStreamAsync(), "application/json");
})
.RequireAuthorization();

app.Run();

static string? MapRegion(ClaimsPrincipal user)
{
    var groups = user.FindAll("cognito:groups").Select(c => c.Value).ToHashSet();
    if (groups.Contains("hq")) return "ALL";
    foreach (var g in groups)
        if (g.StartsWith("region:")) return g["region:".Length..];
    return null;   // 无授权大区 -> AI 服务不设 chemq.region -> RLS 返回空集
}

record AskRequest(string Question, string AsOf = "2026-09-14");
