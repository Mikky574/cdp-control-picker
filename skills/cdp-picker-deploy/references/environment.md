# 环境字段与可采用的部署记录

> 本页保留CLI兼容安装/路径细节。默认原生桌面扩展接入、学习项目和会话模型先读[desktop-connection.md](desktop-connection.md)；CLI诊断不替代原生会话或GUI同步。

本页以采集器 0.3.1 当前实现为准。先盘点事实，再填写 [JSON 模板](deployment-record.example.json)。模板是部署记录及本技能生成器的输入；**采集器启动时不会自动加载它**，`inventory` 只是记录，不是隐藏配置接口。

## 路径与身份分层

| 字段 | 含义 / 如何核对 | 不能混淆的对象 |
| --- | --- | --- |
| `paths.pickerExe` | 实际采集器 EXE，核对对应 BUILD-INFO 与文件哈希 | 不是 ZCode.exe；不要仅按文件名判版本 |
| `paths.nodeExe` | Node bundle 所需的实际 Node，可运行 `--version` | 与系统 PATH 的 node 可能不是同一份；原生 CLI 无此依赖 |
| `paths.cliEntryScript` | 与上列 Node 配对的完整 zcode.cjs/mjs/js 入口 | 不是网页服务启动器、源 ts 文件或任意 Electron GUI |
| `paths.workspaceDir` | 已存在的采集/交接记录目录，非磁盘根 | 传给 CLI 的 cwd、页面包与会话身份依赖它；不等于 CLI 安装目录 |
| 生成的 `zcode-backend.cmd` | 只有绝对 Node + 脚本 + `%*` 的标准包装器 | 解析器提取 argv，不执行整个 CMD |
| `connection.cdpEndpoint` | 本机 Chrome CDP 的 HTTP 端点，如 127.0.0.1:9222 | 不是 ZCode app-server；app-server 是子进程 stdio，无网络监听 URL |
| `inventory.chromeProfileDir` | 采集器专用持久 Chrome profile 实际位置 | 不是浏览器密码导出；登录仍由本人在 Chrome 完成 |
| `inventory.desktopExe` | 当前用户真正安装的 ZCode 桌面程序与 ProductVersion；需要覆盖时用 `CDP_PICKER_DESKTOP_EXE` | 独立 CLI 即使同名 ZCode.exe，也不是桌面索引适配目标 |
| `inventory.desktopIndexPath` | 桌面对话列表 tasks-index.sqlite 实际路径；需要覆盖时用 `CDP_PICKER_DESKTOP_INDEX` | 不是 CLI 会话正文库；不能手工复制/覆盖来“接上”会话 |
| `inventory.cliSessionDbPath` | 同一运行环境真实 CLI 会话库路径；需要覆盖时用 `CDP_PICKER_CLI_DATABASE` | sessionId 必须来自原生 CLI，不编造、不凭标题匹配 |
| `inventory.providerConfigPath` | 正在使用的配置文件路径及格式版本，**不记录内容** | 模型 provider 与数据根、桌面安装路径分别核对 |
| `inventory.sourceCommit` / `cliVersion` | 本次构建/检测得到的事实 | README 最新源码不证明旧已安装 0.16.9 行为一致 |
| `pathEnvironment` | 启动采集器时仅对子进程继承的可选路径变量 | 不是系统环境修改；不是凭据传输渠道 |

冻结成品默认 profile 为 EXE 旁 `CDP-Chrome-Profile`，记录目录为 EXE 旁 `CDP-Picker-Workspace`；源码默认使用其 `.local/`。以实际启动参数/设置为准。日志在记录工作区 `.logs/picker.log`，交接状态及索引备份在 `.handoffs/`；这些历史、页面与会话元数据不提交 Git。专用 profile 保留会话，不承诺永久登录；过期由用户在同一 Chrome 重新登录。

## 实际发现与选择

1. 设置“ZCode CLI”留空：优先当前采集器进程 PATH 的 `zcode-cli`，其次 `zcode`，最后尝试已安装桌面的捆绑 CLI。PATH 入口损坏时会明确失败，不悄悄换另一版本。
2. 可填命令名或选择 `.exe/.cmd/.bat/.cjs/.mjs/.js`。脚本优先同目录 Node，再找采集器继承 PATH 的 Node；固定版本可选本技能生成的包装器。
3. 桌面回退检查用户安装目录 `%LOCALAPPDATA%\Programs\ZCode\ZCode.exe`、App Paths 注册项与 Program Files 常见目录，并要求 `resources\glm\zcode.cjs` 存在。真正的桌面 bundle 用 `ELECTRON_RUN_AS_NODE=1` 运行该脚本；直接执行 GUI EXE 加 `-p` 可能只有 Electron 日志且 exit 0，不能当任务成功。
4. 独立 CLI 与桌面索引分别解析。索引默认定位当前用户的已安装桌面，记录实际ProductVersion并校验必要表/字段/约束与会话身份；**3.16.3.8992是历史实测，不是硬门槛**，3.18.2及其他版本通过相同能力检查即可继续。普通独立同名 EXE 不会被当成桌面程序。非默认安装可用下表三个 `CDP_PICKER_*` 路径变量，仅作用当前采集器进程；它们是本工具参数，不是 ZCode 官方变量。`inventory` 本身不改变 adapter，须把要采用的路径填到 `pathEnvironment` 并运行相应启动器。兼容新增字段不因“不是旧版精确schema”而拒绝；缺必要字段或破坏写入安全的约束才停止并报告，不能用换路径绕过。

## 数据与 provider 环境如何对齐

本应用索引 adapter 的数据根取 `ZCODE_DATA_BASE_DIR`，否则继承 `HOME`，再回退 Python 当前用户目录；在该根下定位 `.zcode\v2\tasks-index.sqlite` 与 `.zcode\cli\db\db.sqlite`。CLI 自身也可能受版本、用户目录与其他存储覆盖影响：必须核对当前 CLI 真实行为。仅改变 `ZCODE_STORAGE_DIR` / `ZCODE_SESSION_DB_PATH` 不保证桌面/模型/正文库一并改变，不能靠索引路径碰巧存在判断对齐。

官方当前源码把个人模型放 `~/.zcode/v2/provider_config.json`，运行/扩展放 `~/.zcode/cli/config.json`；[配置文档](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/03-config.md)还说明个人配置路径覆盖与旧配置迁移。旧 0.16.9、桌面捆绑版本和新 main 不一定同格式。先确认所选 CLI 版本，再使用该版本受支持的配置方式；不改文件内容来“试到能跑”，不打印完整配置。

生成器接受以下**可选路径**（null 表示不设置、保留调用者环境）：

| 变量 | 本技能用途与限制 |
| --- | --- |
| `CDP_PICKER_DESKTOP_EXE` | 真正已安装桌面 ZCode.exe 的已有绝对路径，与后台 CLI 入口独立；记录版本并检查实际能力，不自动启动/安装桌面 |
| `CDP_PICKER_DESKTOP_INDEX` | 已有 tasks-index.sqlite 绝对路径；仍校验 schema、项目与 ownership，不新建或迁移数据库 |
| `CDP_PICKER_CLI_DATABASE` | 与本次 CLI 数据 home 对齐的已有正文库绝对路径；只是告诉 adapter 到哪里核对，**不改变 CLI 自己写哪份库** |
| `ZCODE_DATA_BASE_DIR` | 显式对齐既有数据基目录；须已存在且确认为目标数据，不自动新建/迁移 |
| `ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` | 当前源码的个人 provider 路径覆盖；须该版本支持，文件存在，不读出内容 |
| `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE` / `ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE` | 构建配套的 builtin provider 文件路径；不是随意套用的个人 Key 格式 |
| `TEMP` / `TMP` | 需要时对子进程指定已存在临时目录，不设置全局环境 |

桌面捆绑入口的 `command_environment` 若未继承 builtin 路径，会尝试其 `resources\config\provider\zcode-builtin.json`；独立 Node CLI不会自动套用另一安装的 builtin。个人配置与 builtin 不是同一层，不为了消除报错盲目指向彼此。

索引 adapter 路径优先级：调用方显式构造参数高于上述 `CDP_PICKER_*` 环境，再回退原默认。显式桌面环境覆盖存在时，不根据后台 bundled CLI 推导另一份 GUI。主窗没有把 `inventory` 自动变为设置的功能；三个路径须由启动环境明确传入。后台使用独立CLI时，也必须让 adapter 检查同一 SID 实际所在的库，而不是随意选一个已有 sqlite。

**包装器 SET 的边界：** 解析器读取包装器中的赋值只为展开 Node/脚本路径，不会把所有 SET 作为进程环境。`command_environment` 会单独把选中 Node 的目录补入其子进程 PATH。provider/data/TEMP 等环境必须由 `Start-Picker.cmd`、`Check-Backend.cmd` 或启动采集器前的父 shell 传入；不要只写进 `zcode-backend.cmd`。

生成器不设置 HOME / USERPROFILE，不修改系统 PATH、默认模型、登录或凭据。若机器原本有用户目录重定向，记录现状并核对桌面与 CLI 是否同一用户环境；不要为了本次工具重新定位整套历史。

## 使用启动器生成器

先复制并填写记录模板，所有必填路径改为本机已存在的实际文件/目录。示例只采用 **Node + bundle** 模式；独立原生 CLI 在设置中直接选 EXE 即可。

```powershell
python '<技能目录>\scripts\prepare_deployment.py' --record 'D:\Deployment\deployment-record.json' --output 'D:\Deployment\launchers'
```

生成器是部署辅助源码，需要执行它的机器已有 Python 3.9+，不是采集器成品运行依赖。没有 Python 时，按 [手工启动器结构](relocate.md#手工启动器结构)填写等价 CMD；无需为成品安装 Python。

输出 `zcode-backend.cmd`、`Start-Picker.cmd`、`Check-Backend.cmd`。首次拒绝覆盖同名文件；确认替换这些生成文件后加 `--overwrite`。生成动作不运行 EXE、不检验模型认证、不确认相邻资源完整性，也不创建工作区。检查文件后手动运行 `Check-Backend.cmd` 做无模型检测，再运行 `Start-Picker.cmd`。环境仅影响本次启动的采集器及其子进程。改后须结束旧采集器并用新启动器打开，旧进程不会自动获得新环境。

启动器专供 Windows CMD：Start/Check 使用 `chcp 65001` 读取UTF-8中文路径，`setlocal DisableDelayedExpansion` 避免感叹号展开；Check 使用 `start "" /wait` 等待窗口式EXE退出并保留退出码。Node包装器供采集器UTF-8解析，不依赖CMD执行；不在Linux/macOS shell中运行这三个文件。
