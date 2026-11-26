# 分阶段检测、定位与验收

先确定用户授权到哪层；部署请求通常可完成本地文件检查和无模型检测。涉及新模型费用、登录或网站业务的后续阶段，在已有明确授权范围内进行，不把检测后端当作模型任务已经授权或完成。

## 默认原生路线分层验收

| 层级 | 实际检查 | 不可替代的证据 |
|---|---|---|
| 安装/启用 | 当前发布确认的扩展安装、热加载结果 | 文件存在不等于Extension Host已加载 |
| 原生授权 | 用户在ZCode批准sessions.write/sessions.fullAccess | 不自动代点，不改授权库绕过 |
| health/模型目录 | 原生桥能力、固定workspace、同源ctx.models.list | 不为读目录临时建session；不能仅凭版本号拒绝 |
| 会话/发送 | owner创建真实SID，send受理给出本次operation | ACK不等于完成 |
| 打开/GUI | 原生open导航回执，实际同项目同会话观察 | 不能凭数据库行或CLI完成声称已打开/直播 |
| 运行/终态 | wait精确operation，持久运行/完成/失败与公开回执 | 不用另一轮消息代替，不造新会话重发unknown |
| 页面学习 | 精确CDP目标、实际步骤与结果证据 | 虚构页通过不代表任意业务站点 |

资料目录与native项目cwd分别记录；相同可集中管理，不同则prompt绝对引用实际context。切换资料目录不搬旧资料，不能把采集目录当完整聊天备份。模型选择来自真实目录、每次输入传精确modelID，不写Key或改全局默认。桥发现文件与token不交给模型、不进日志/任务。

安装、热加载、导航与实时广播以当前build实际验收为准；未验证明确列出，不推断所有桌面build均已支持。能力不足明确报告，不偷偷升级、切CLI或直写索引制造成功。完成已有授权范围的验证，不为清单重复发模型。

## CLI兼容路线的分层结果

下列仅适用于显式CLI兼容诊断；不能替代上述原生桌面验收。

| 阶段 | 实际检查 | 能证明什么 / 不能证明什么 |
| --- | --- | --- |
| 0 静态部署 | 文件存在、版本来源、架构、Node/script/相邻资源、cwd与环境记录 | 只证明部署候选成立，不能证明可执行或模型可达 |
| 1 CLI doctor | 用**选中的同一入口/环境**运行 `--version`、`doctor --json`，保留退出码与有效JSON字段 | 证明所选程序/平台自检；不证明账号、模型网络、工具或GUI |
| 2 采集器检测后端 | 设置“CLI 兼容诊断”的“检测连接”或生成的 `Check-Backend.cmd` | 实际 `app-server --stdio` 和只读 `session/list`；不创建会话，不发模型，不写桌面索引 |
| 3 已授权的最小模型 | 在隔离虚构工作区发一次最小任务，核对ACK、原生终态、最终公开回复与SID | 证明该模型轮次；不能只凭进程退出/idle/产物认定成功 |
| 4 桌面对话 | 正常工具交接流程的索引预检/备份/登记与readback，再在正确项目观察同SID对话和继续交流 | 仅后台成功或索引行存在不等于GUI已显示；不直接手写数据库验证 |
| 5 Chrome与采集 | 指定端点真实Chrome、固定profile、目标页、右键保存虚构控件 | 证明采集链路；不代表登录永久有效或真实网站业务通过 |

可从已有层级继续，不为“完整清单”重复调用模型。不要把源码、纯fake单测、冻结包自检、虚构网页和用户真实站点混为同一证明。

## 无模型的实际检测入口

在采集器设置中选择已存在的记录目录及 CLI，点击“检测连接”。它报告 `runtimeType`、解析后的 `command`、`cliVersion`、`transport=app-server-stdio` 和阶段；`ok=true` 只代表这一层。

版本只作记录，协议决定连通。`--version`格式不识别或返回非零时可记`cliVersion=unknown`与`versionNote`，仍继续真实app-server/session/list检查；不凭版本前缀或文本格式拒绝。协议不兼容、必要响应结构错误仍明确失败，不保存原始异常stdout/stderr。

也可用生成器产生的 `Check-Backend.cmd`。等价接口：

```powershell
& '<采集器.exe>' --workspace '<已存在记录目录>' --zcode-cli '<CLI命令或路径>' --backend-check '<已存在报告目录>\backend-report.json'
```

报告父目录必须先存在。成功退出码0，失败2；核对 JSON 的 `ok` 和 `phase`，不是仅看窗口消失。报告应有 `modelCalled=false`、`sessionCreated=false`、`indexWritten=false`。检测读取会话列表时使用一次性不存在的ID筛选，不读取私人历史正文。

CLI `doctor --json` 的入口若是 Node bundle，应使用固定 Node + 脚本，例如 `& '<node.exe>' '<zcode.cjs>' doctor --json`；若是独立原生 CLI，可用其绝对EXE路径。桌面 bundle 必须用采集器已识别的 Electron-as-Node结构，不能直接把任意 `ZCode.exe --json -p ...` 当 doctor 或模型接口。手动运行 doctor 的父shell环境应与 Start/Check 一致；否则两个测试不可直接对比。

## 按失败阶段定位

| 现象 | 优先核对 | 停止条件/处理 |
| --- | --- | --- |
| `phase=resolve`、找不到CLI | 采集器继承的PATH；显式路径；包装器是否仅为标准Node/script调用；文件是否移动 | 不发模型，选正确入口后重做无模型检测 |
| WinError5 / ENOENT / 版本不匹配 | 实际文件、执行权限、架构、Node是否所选版本、资源是否保留 | 进程启动失败与模型工具权限拒绝分开记录；不盲目管理员启动 |
| 只有Electron日志、exit0无JSON | 是否把GUI EXE当CLI；真实bundle与Electron-as-Node | 记协议/入口未确认，不当成功，不自动重发模型 |
| doctor通过但stdio失败 | 所选版本是否支持`app-server --stdio`；启动输出污染、资源和transport | 只读协议检测失败；不能靠更改模型来修stdio |
| stdio通过但模型失败 | 当前版本支持的provider配置、登录、网络/代理、模型能力、权限请求 | 不打印凭据；定位实际报错，不擅自切模型或复制整套配置 |
| Bash工具提示没有permission client | 是否仍走旧`-p --mode edit`分支；当前app-server连接与会话权限 | 本工具正常交接使用app-server及用户已选会话权限；不把独立CLI-p当GUI接入，不改全局默认 |
| CLI可用但索引预检失败 | 记录实际桌面版本，检查必要schema/约束、同数据根、CLI正文库/SID/cwd身份 | 3.16.3.8992仅历史实测；3.18.2等不按版本号拒绝。缺必要能力时明确“CLI已解析，桌面对话索引不可用”及具体缺项；模型发送前停止，不回退隐形任务 |
| GUI列表未见对话 | 正确ZCode安装、项目/工作区、真实SID、登记/readback与桌面刷新事实 | `--surface desktop`、`--cwd`或`session/create`本身均不能保证列表吸纳；不改私有DB试错 |
| Chrome连不上/不是登录页 | 指定loopback端口、进程owner、专用profile、目标页签 | 不重启共享Chrome、不复制默认profile；登录过期由用户在同一窗口处理 |
| 长时间处理中/结果未知 | 工作区`.handoffs/`状态阶段、最后事件时间、是否已提交；公开错误 | 未知结果不重发；不因超时或没看到窗口再创建同任务 |
| 点击发送立即“已发送过” | 当前内容指纹与已有持久回执；标注/顺序/意图/动作策略/连接范围是否相同 | 如实“本次未重复调用”，不用伪造等待；不要删除journal强行重发 |

网络需按**该版本**支持的方式核对。当前官方CLI源码文档使用 `network.httpProxy` / `ZCODE_HTTP_PROXY`；当前官网桌面设置说明有独立代理规则，不能拿新桌面规则推旧CLI。只记录代理方式及脱敏目标，不保存含认证信息的URL，不关闭证书校验来“打通”。[官方CLI配置](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/03-config.md)、[官方桌面安装/代理说明](https://zcode.z.ai/cn/docs/install)（核查2026-11-26）。

## 验收记录

记录所用build、实际入口、资料目录/native项目、所选模型身份、已验证层级和证据位置，不记录Key、桥token、思维、完整私人历史或provider正文。真实模型、GUI、CLI兼容、源码测试与包检查分别报告；历史结果不能替代当前路线。技能/生成器校验只证明其交付与生成行为，不证明原生安装或GUI同步已通过。
