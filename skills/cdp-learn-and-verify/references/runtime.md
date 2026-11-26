# 本地 CDP 运行协议

## 来自当前包的配置

读取 `page-bundle.json`，不要从下面的示例复制端口、路径或页签：

- `taskIntent`：`mode=learn-and-verify`、执行约定版本，以及新包的 `actionPolicy=infer-from-purpose`。策略来自页面包，不能通过在单个job中另写actionPolicy覆盖。
- `connection`：实际 `endpoint`、精确 `targetId`、规范化页面身份 `page`、完整初始 URL 的 `initialUrlSha256`。指纹用于校验页面，不是业务参数，也不需要反解。
- `runtime.command`：命令参数数组；冻结包通常只有 EXE 路径，源码运行可能是 Python 路径加 `app.py`。保留数组中的每个参数，不拼 shell 字符串。
- `learning.directory`：本包的学习目录，`learning.skillRef` 是学习技能相对路径。相对路径都从页面包所在目录解析。

`context.md` 是本地资料入口，不是 ZCode 服务地址。`handoff-...` 只用于交接日志和协议请求关联；不要把它当作 selector、Chrome 页签或 ZCode 对话 ID。选择页签只依照 `connection.targetId`，定位控件只使用包内 `captureId` 对应的定位证据。

## 执行一次请求

在页面包的 `learning/` 目录创建独立的请求 JSON。示例中的路径和编号仅说明格式，必须替换为当前包的实际值：

```json
{
  "schemaVersion": 1,
  "bundlePath": "C:/workspace/page-bundles/page-example/page-bundle.json",
  "operation": "inspect"
}
```

调用 `runtime.command` 参数数组，并追加：

```text
--cdp-task <learning/request-001.json绝对路径> --output <learning/result-001.json绝对路径>
```

按程序调用工具的参数数组接口传递；程序退出前不发下一步。读取 `result-001.json`，判断结构化状态和证据。不要仅依据 stdout 或退出码。请求与结果使用不同文件，均留在当前包 `learning/`，不覆盖包、技能或原始采集文件。

每个新的动作步骤使用一个新的job文件，已有job不就地改写。运行器在本包learning/.executions中持久记录act请求语义与执行状态，不保存原value。同一job已completed时，重复调用返回缓存结果并带duplicate=true，不重新连接或执行；同路径内容改变会blocked。原请求结果未知时，不能靠改文件名或另写相同动作绕过防重。inspect/read-page可再次调用以读取当前证据。

## 操作类型

| operation | 用途 | 额外输入 |
|---|---|---|
| `inspect` | 实际核对当前页和所有已标注控件 | 无 |
| `read-page` | 读取有限、必要的当前页面文本 | 无；不读取输入值、脚本或凭证 |
| `act` | 对一个已标注控件执行一个动作 | `captureId`、`action`、`purposeScope`；需要参数的动作另传 `value` |
| `observe-network` | 限时监听顶层浏览器页的同源 XHR/fetch 元数据 | `durationMs` 可选，`readyPath` 可选 |
| `query-json` | 在当前页面会话发送一次已验证的同源 GET，返回指定 JSON 字段 | `purposeScope` 为 `read-only`，提供 `request` |

两个网络操作需要新包的 `runtime.networkOperations` 声明。见 [网络观察与一次查询](learn-network.md)。

首次调用可不写 `expectedUrlHash`，运行器用包内初始 URL 指纹校验。后续请求使用上次工具结果中的 `page.urlSha256` 作为 `expectedUrlHash`。这允许在观察结果后继续，但不允许在路由变化后盲用旧页面身份。

动作请求示例：

```json
{
  "schemaVersion": 1,
  "bundlePath": "C:/workspace/page-bundles/page-example/page-bundle.json",
  "operation": "act",
  "expectedUrlHash": "上次工具结果中的完整指纹",
  "captureId": "cap-order-number",
  "action": "fill",
  "value": "当前场景适用的查询样本编号",
  "purposeScope": "reversible"
}
```

`action` 支持 `read`、`click`、`fill`、`select`、`check`。包的actionPolicy为infer-from-purpose时，根据用途、当前任务及实时DOM类型选择动作，旧annotation.allowedActions不作门槛；旧包无策略时，动作仍必须包含在原allowedActions中。未知策略会在连接前阻断，不默认为新策略，也不通过修改旧包策略绕过原限制。

`fill`、`select`的`value`是字符串；`check`的`value`是布尔值；`read`、`click`不传 value。AI 自行判断并填写 `purposeScope` 为 `read-only` 或 `reversible`，普通测试不要求用户逐项填写。缺参时优先从当前任务或可见样本提取，或在测试环境构造数据。会改变真实业务状态的测试在沙箱、预览或本地虚构数据上完成。只在有依据时标记只读或可撤销。两种策略都保留精确页签/路由、唯一定位、可见可用、敏感控件、实际 DOM 类型与参数校验。

新策略参与交接内容身份和动作job执行身份，旧策略回执不能证明新策略已执行。同一job路径改变策略会阻断，不能复用旧完成回执或绕过未知结果；应通过采集器导出新的任务包，并先核对原动作是否已经发生。

运行器使用包内候选重新定位；零匹配、多匹配、不可见、不可用、敏感输入或目标身份不符时，不猜坐标、不取第一项、不尝试其他浏览器。执行输入后不依赖原始输入值的回显；以运行器返回的校验信息及后续页面证据判断。

## 读结果与记录证据

结果`schemaVersion=1`；`status`为`completed`、`blocked`、`failed`或`unknown`，错误带`error.code/message`。completed退出码为0，其他状态退出码为2；仍须读结果正文。`completed`只表示本次读取或派发完成，仍不等于业务通过。`unknown`表示不能确定动作结果，不能按失败未执行来重试。

- `page`包含规范化url、origin、path、title与`urlSha256`，不输出原query/hash。
- `inspect`的`controls`逐项给出captureId、定位状态（located、not-found、ambiguous、invalid-locator或sensitive）、matchCount、visible、enabled及可用的tagName/role/name、candidateEvidence。不把采集时的匹配数当当前值。
- `read-page`的`evidence.text`最多3000字符，`truncated`说明是否截断；缺少成功判据时不能从截断文本里推定成功。
- `act`的`evidence.actionDispatched`说明动作是否派发；填写、选择和勾选分别用valueMatchesRequested、selectionMatchesRequested、checkedMatchesRequested返回参数匹配判断，不输出原始输入值。

`actionAttempted`表示是否已经进入动作调用，用于避免结果未知时重发，不代表业务成功。连接成功、定位成功、动作发出、预期结果实际出现分别记录。

动作结果未知时保留请求、结果与执行记录，先用允许的只读检查确认现状。pending或failed记录若可能已尝试动作，再次运行原job会返回unknown，不连接、不重试；不能因为换一个请求文件名就重复提交动作。duplicate回执仍是原次结果，不代表刚刚又执行成功。断开只关闭本次CDP连接，不关闭用户页签或浏览器。

学习报告的每个 `verified` 项引用实际结果文件，写明目标环境与支持结论的证据。`inferred` 说明推断来源；`blocked` 说明已尝试的解决方式及还缺的具体条件。隔离测试的结论只适用于隔离场景。
