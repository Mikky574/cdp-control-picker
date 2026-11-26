# 网络观察与一次查询

## 学会业务逻辑

从实际任务记下输入、业务对象、前置校验、页面动作、请求、响应及业务完成判据。标注的顺序帮助初次理解。后续调用只保留经验证有必要的步骤。比如填订单编号、点查询、再点详情，可能只是把同一个编号传给一个只读详情接口。用不同的样本编号做对照，证明结果随输入改变，再把它固化为参数。

接口证据优先用维护方文档、项目源码或现成接口契约。需要现场验证时固定当前授权的账号、租户、对象范围和准确页签，先监听，再触发一次已知的只读动作。按触发窗口、方法、路径、参数含义和实际结果关联。时间相近不是因果证据。区分核心查询、菜单加载、埋点、预取、分页或异步轮询。不能把查询和写入合并，也不要为了变成一次请求而跳过服务器要求的必要步骤。

## 用已打包的程序观察

从当前页面包读取已选的连接和 `runtime.command`。确认 `runtime.networkOperations` 列出了对应操作。用原来的 `--cdp-task`、`--output` 形式启动并使用独立结果文件。

观察请求例子：

```json
{
  "schemaVersion": 1,
  "bundlePath": "C:/workspace/page-bundles/page-example/page-bundle.json",
  "operation": "observe-network",
  "durationMs": 10000,
  "readyPath": "C:/workspace/page-bundles/page-example/learning/observe-ready.json"
}
```

从同一学习目录启动后台观察进程，等独立就绪文件出现，再用正常页面动作触发一次。`durationMs` 范围为 250 至 20000，默认 8000。输出只包含当前顶层页签同源 XHR/fetch 的方法、路径模板、查询字段名、响应状态、类型和完成状态。不会保存参数值、请求头、响应头、请求体或响应体。匹配值或响应结构不足以判断时，优先用项目接口契约，或在授权的开发工具中只检查该条请求的必要业务字段，输出脱敏摘要。跨域、子框架、Worker、SSE 和 WebSocket 不一定出现在这个观察结果里。

## 验证并复用一次查询

已确认为只读的同源 GET 可做同一对象的最小对照。使用业务编号或已确认的项目、租户、记录编号；浏览器按其自己的会话规则携带同源 Cookie。HTTP 200 之后继续核对业务字段、对象归属和完成状态。

```json
{
  "schemaVersion": 1,
  "bundlePath": "C:/workspace/page-bundles/page-example/page-bundle.json",
  "operation": "query-json",
  "purposeScope": "read-only",
  "request": {
    "path": "/api/orders/detail",
    "query": {"orderNo": "DEMO-1042"},
    "fields": {"orderNo": "data.orderNo", "status": "data.status"}
  }
}
```

一次运行执行一次 GET。字段由业务需要明确列出，可用点路径和一个数组通配符，例如 `items.*.status`。只返回标量或标量列表，最多 20 个投影字段，每组数组最多 50 条。接口收回 HTML、跳转、返回错误状态或必需字段不再存在时，按结果核对登录或契约。禁止盲目重试；会话有效但接口无法验证，按实际情况回到经验证的页面路径。独立 HTTP、跨源、POST 查询或服务身份请依据维护方支持的正式接口验证，采集器的单请求入口只处理同源 GET。

将已验证的 `api-contract.json` 写成可复用说明，包括方法和路径、参数来源与类型、可用业务范围、预期结果、成功与失败判据、对照证据、需要的登录会话及最短页面备用路径。后续同类任务先根据当前用户请求组装参数，直接执行这条已验证查询。在其它账号或租户、接口版本变化、业务范围不符时，先确认契约仍适用。

对象身份与凭据分开。业务编号可作为参数。Cookie、Bearer、API Key、CSRF 值或带签名的 URL 不进入任务文件、生成技能、接口约定或日志。使用站点正常登录和服务端授权。同源入口没有自定义认证头，失败时不从网页复制凭据或扩大账号权限。

参考 [CDP Network 协议](https://chromedevtools.github.io/devtools-protocol/tot/Network/) 和 [Fetch 会话规则](https://fetch.spec.whatwg.org/#concept-request-credentials-mode)。
