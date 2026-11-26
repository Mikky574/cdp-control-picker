---
name: cdp-page-contract
description: 通过固定专用 Google Chrome 的 CDP 连接和页面约定，复用本人已登录身份定位、解释并操作授权网页。适用于用 selector 说明网页区域及业务含义、通过前端读取或使用服务；不用于桌面坐标控制或绕过登录。
---

# CDP 页面理解与定位

本技能的主线固定为：**本人先登录一个专用 Google Chrome → Chrome 用固定本地 profile 保留站点登录状态 → AI 通过 CDP 复用这个浏览器 → 在前端点击、输入并读取结果。** 本技能提供操作约定，不会自行授予网页权限。采集包提供runtime时优先使用其现成EXE后台入口；没有该入口时才核实已有浏览器工具或Node.js/Playwright环境，不为阅读技能额外安装工具。若只能读文件，说明所缺能力，不声称已连接。

## 固定浏览器与身份

使用用户或项目已指定的 Chrome 程序、固定 `user-data-dir`、profile 子目录、CDP 端点和目标站点账号/组织。不得因连接失败临时换 profile、创建匿名/无痕上下文，或改用新开的无身份浏览器。已有配置不全时，先读取项目配置及已有启动方式；仍无法确定身份时报告缺项。

Cookie 和站点存储由这个 Chrome profile 管理。保留本地 profile 可以复用仍有效的登录状态，但不保证永久登录；Cookie 到期、主动退出、清理站点数据或站点会话失效后，需本人回到同一个专用 Chrome 重新登录。AI 不导出 token、Cookie 或存储状态，不切换到凭证导出或后台接口路线。

## 读取当前任务所需信息

使用项目提供的页面约定；尚无约定时参考 [page-contract.example.json](references/page-contract.example.json)，并从实际 DOM 校验。示例订单后台和地址均为占位，不代表真实网站。需要手动建立调试浏览器或 Playwright 样例时，读 [connect-and-inspect.md](references/connect-and-inspect.md)。

约定至少应说明：目标 URL/页签、frame、页面就绪标志、元素定位策略、业务含义、可执行动作和动作后验证。selector 只回答“找到哪个元素”；“这个输入框是订单号过滤条件”“详情是只读入口”等含义来自页面文案、应用约定或用户说明，不能从一个 CSS 类名推定。

若资料来自控件采集器，sequence中的默认排列不代表业务流程；ordered=true只表示用户声明前后，executionValidated=false仍是原采集未验证。前置条件、预期结果和人的控件名称属于说明，不替代真实页面证据或当前任务授权。taskIntent为learn-and-verify时读取learning.skillRef并实际验证可执行范围；context-only或无新意图旧包仅理解并确认。学习结论写新报告，不改原始采集状态。

## 连接与定位

1. 连接配置中的固定 CDP 端点，并核对它属于既定的专用 Google Chrome。网页 URL 与 CDP 端点不同；打开某网页不代表浏览器开启了调试端口。不要为了接入而关闭、重启用户的共享浏览器。首次建立专用实例才配置固定的非默认数据目录；端口保持 loopback，不开放到公网。后续启动始终沿用这组配置，不每次生成新目录。
2. 在该 Chrome 的既有普通 profile 上列出页签 URL，按约定精确匹配 origin、path 和必要 query/业务标识。零个匹配时先报告未打开目标页；多个匹配就补充目标身份，不能默认选第一个。根据页面可见的账号/组织标志及用户已确认的登录信息核对当前身份；重定向到登录页、账号不符或权限不足时，停止依赖登录的操作，由本人在这个 Chrome 内处理后再继续。不要输出登录表单敏感内容、Cookie 或整浏览器的所有 DOM。
3. 确认 frame 和页面就绪标志后定位。优先应用明确约定的稳定属性或 role/可访问名称；CSS 可以使用稳定 id、`data-testid`、业务键和父区域。role locator 是 Playwright 的定位方法，不是 CSS selector，也不是 CDP 原生命令。
4. 在“订单表格 → 订单号 A-1042 的行 → 详情按钮”中逐步缩小范围。动作前确认唯一匹配、可见，且动作要求时可用。不要用 `nth()`、第几行、长串 `div > ...` 或屏幕坐标代替业务身份。
5. 0 个匹配：核对 URL/frame、加载、筛选/分页和实际 DOM。多个匹配：补充区域或业务键。页面重渲染、导航后重新定位，不复用旧 CDP NodeId/ElementHandle；不要通过盲点点击消除报错。

iframe 必须先定位 frame。常规 CSS 不跨越 iframe；Playwright 许多 locator 可穿透开放 shadow root，但 XPath 不穿透，closed shadow root 也不受支持。只在实际页面需要时处理这些边界。

## 执行动作和判断结果

- 依据用户任务和约定执行。查询、打开详情也要核对页面含义；按钮叫“确认”不能证明它是只读。示例仅允许过滤列表、读取行、查看详情，不允许付款、删除、退款或修改订单。
- 输入前核对目标及是否会覆盖已有内容；不要为学习盲目清空用户输入，敏感值不读取或输出。提交前核对目标业务键与预期动作。用户已授权的常规步骤继续完成；任务未覆盖的数据变更先明确具体目标和影响。
- 用可观察结果验证：例如详情标题包含 A-1042、状态字段已加载、页面出现明确成功状态。一次 `click()` 返回不等于业务成功。
- 超时或网络断开后先检查当前页面、URL 和业务状态；无法判断提交是否已发生时，不重复提交。
- 操作结束记录目标 URL/业务键、实际读取或执行内容、验证结果及未完成原因。连接成功、定位成功、业务成功分别报告。工具返回的 DOM/文本才进入模型上下文；技能和路径本身不会让模型自动看到网页。
- 日常使用仅沿前端界面完成任务。登录失效时保留既有 Chrome 与 profile，说明需要本人重登；不要把“重新创建浏览器/上下文”当作登录修复方法。
- 不用 `page.close()`、关闭浏览器进程或重启来清理共享页面。断开方式按当前工具的文档处理；不要把针对自行 launch 的 `browser.close()` 模板照搬到共享浏览器。

## 官方参考

- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)：协议域、命令和兼容性边界。
- [Chrome 调试端口与独立数据目录](https://developer.chrome.com/blog/remote-debugging-port)：Chrome 136 起默认数据目录的限制。
- [Chromium 用户数据目录](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md)：profile、Cookie 和本地数据的位置；可在 `chrome://version` 查看 Profile Path。
- [Chrome Cookie 帮助](https://support.google.com/chrome/answer/95647?hl=en)：Cookie 能保持登录，清理后可能退出站点；本地持久化不等于永久登录。
- [Playwright connectOverCDP](https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp)：只支持 Chromium；与 Playwright 协议连接的能力存在差异。
- [Playwright locators](https://playwright.dev/docs/locators)、[FrameLocator](https://playwright.dev/docs/api/class-framelocator)：定位与 frame 范围。
