# 安装来源与依赖

> 本页保留CLI兼容安装/路径细节。默认原生桌面扩展接入、学习项目和会话模型先读[desktop-connection.md](desktop-connection.md)；CLI诊断不替代原生会话或GUI同步。

核查日期：**2026-11-26**。以下链接是官方主源；下载安装前再次确认版本、架构与所选 commit，不把网页“最新版”当作本工具的兼容保证。

## 先选部署层

| 组件 | 从哪里取得 | 使用者是否需要开发环境 |
| --- | --- | --- |
| 采集器 Windows EXE | 本项目对应 Release，核对其 BUILD-INFO 与哈希 | 成品运行无需 Python、Node、pnpm 或 csc；Chrome 与 ZCode 另备 |
| Google Chrome | [Google 官方 Chrome 下载入口](https://www.google.com/intl/zh-CN/chrome/) | 由用户安装；采集器使用固定专用 profile 与本地 CDP |
| ZCode 桌面 | [官方安装页](https://zcode.z.ai/cn/docs/install) | 桌面包不要求用户另装 Node；记录实际版本，按CLI与索引必要能力检测，不按版本号拒绝 |
| 独立 ZCode CLI | [官方 CLI 仓库说明](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/README.md)与所选版本产物 | Node bundle 需要匹配 Node；SEA 自带 Node |
| CLI 源码构建 | [官方仓库](https://github.com/zai-org/ZCode) | 构建机准备 Git、所选 commit 指定的 Node 与 pnpm |

本技能不包含采集器 EXE、Chrome、ZCode、Node 或模型凭据。技能只是操作说明，不能提供缺失的执行能力。官方 CLI 文档提供源码构建与打包方法；本次没有核实可通用采用的 `npm install -g zcode`，不要编造该安装入口或安装同名陌生包。

官网安装页本次显示桌面 **v4.0.0**，提供 Windows x64 / ARM64 下载。**3.16.3.8992只是本项目历史实测版本**，不构成安装白名单；用户已有3.18.2或其他版本时，先对该安装做CLI接口与索引必要字段/约束检查，兼容则继续，不要求降级。官网“最新版”同样不等于已经在本工具完成实测。安装包按官方安装向导操作，模型接入按官方连接模型说明进行，凭据不要写进部署材料；不提供未经核实的旧版下载链接。

需要明确选择桌面3.18.2时，可从[官方历史Release v3.18.2](https://github.com/zai-org/ZCode/releases/tag/v3.18.2)核对系统/架构资产及附带SHA256SUMS，不必追随官网通用页的最新版。该tag的[CLI源码package.json](https://github.com/zai-org/ZCode/blob/v3.18.2/apps/zcode-cli/package.json)显示 **0.17.1**：桌面与CLI使用不同版本体系，不要求两个号码相等；源码声明也不替代用户实际安装产物的版本。最终以所选入口的`--version`、doctor与app-server协议检测为准，并分别记录桌面和CLI版本。

## Windows 源码构建独立 CLI

1. 在 [Git for Windows 官方页面](https://git-scm.com/install/windows)选择本机架构的安装包或便携包；记录 `git --version`。
2. 取得可信的 ZCode 仓库并选定 tag/commit。记录 `git rev-parse HEAD`；不要把开发分支当成已经验证的发行版。
3. **先读该 checkout 根目录的 [mise.toml](https://github.com/zai-org/ZCode/blob/main/mise.toml)**，再从 [Node.js 官方下载/历史版本](https://nodejs.org/en/download)取得对应 Windows 架构版本。可将 ZIP 解压到固定工具目录，也可按 MSI 安装向导安装。实际使用绝对 `node.exe` 路径，避免机器另一份 Node 抢先。
4. 依据 [pnpm 官方安装说明](https://pnpm.io/installation)准备该 commit 指定的 pnpm；不要不加版本地安装“最新”并假设兼容。若已有 mise，可使用仓库工具链；否则安装明确版本后核对 `pnpm --version`。本次 `main/mise.toml` 为 **Node 24.14.0 / pnpm 10.33.2**，这里只是核查快照，不是永久要求。
5. 从仓库根目录执行官方构建，再检查实际 bundle：

```powershell
pnpm install --frozen-lockfile
pnpm --filter @zcode/cli... build
node apps/zcode-cli/packages/cli/dist/zcode.cjs --version
node apps/zcode-cli/packages/cli/dist/zcode.cjs doctor --json
```

首次依赖安装及资源准备需要访问相应下载源；失败时保留错误并修复来源/代理，不跳过安装脚本后把目录称为完整包。以上安装与构建命令只在用户授权的构建目录执行，本技能不会自动执行安装器。

### 从空程序目录开始的 Windows 示例

下面是**供部署侧按授权执行**的PowerShell步骤，不是本技能已经执行的安装。先填写所选tag/commit与路径，逐段检查退出结果；已有可用工具链时不必重复安装。不使用管理员终端、不改系统PATH、不删除已有目录。示例采用当前核查的24.14.0/10.33.2及x64；若所选commit的mise或机器架构不同，须同步改版本、文件名与目录。

先从官方地址clone到明确目录并固定源码身份。Git须已按上文安装；目标目录已有仓库时先核对，不覆盖或清空：

```powershell
$deployRepo = 'C:\Tools\ZCode-source'
$deployRef = '<填写已选tag或完整commit，不要照抄此占位>'
git clone https://github.com/zai-org/ZCode.git $deployRepo
if ($LASTEXITCODE -ne 0) { throw 'clone失败，停止构建' }
git -C $deployRepo checkout --detach $deployRef
if ($LASTEXITCODE -ne 0) { throw '未切到指定源码版本，停止' }
git -C $deployRepo rev-parse HEAD
Get-Content -LiteralPath (Join-Path $deployRepo 'mise.toml')
```

从 [Node 24.14.0官方历史目录](https://nodejs.org/download/release/v24.14.0/)取与机器一致的ZIP及SHASUMS并核对来源/哈希；其他版本使用对应官方目录。以下假设已将x64 ZIP保存到明确位置，解压只写程序目录，不移动ZCode用户数据。ZIP内还有一层`node-v版本-win-x64`目录，`node.exe`与`npm.cmd`应在这一层：

```powershell
$deployNodeVersion = '24.14.0'  # 改成刚才mise确认值
$deployPnpmVersion = '10.33.2'  # 改成刚才mise确认值
$deployNodeZip = 'C:\Downloads\node-v24.14.0-win-x64.zip'
$deployNodeParent = 'C:\Tools\Node'
Expand-Archive -LiteralPath $deployNodeZip -DestinationPath $deployNodeParent
$deployNodeHome = Join-Path $deployNodeParent ("node-v$deployNodeVersion-win-x64")
$deployNodeExe = Join-Path $deployNodeHome 'node.exe'
$deployNpmCmd = Join-Path $deployNodeHome 'npm.cmd'
if (!(Test-Path -LiteralPath $deployNodeExe -PathType Leaf) -or !(Test-Path -LiteralPath $deployNpmCmd -PathType Leaf)) { throw '核对ZIP目录层级与版本' }
$env:PATH = "$deployNodeHome;$env:PATH"  # 只影响当前PowerShell及其子进程
& $deployNodeExe --version
& $deployNpmCmd --version
```

[pnpm 10官方安装页](https://pnpm.io/10.x/installation)支持npm安装pnpm；这里固定mise版本，并用本次命令的`--prefix`写到单独目录，不修改永久npm配置。[npm官方目录规则](https://docs.npmjs.com/cli/v11/configuring-npm/folders/)说明，Windows全局模式的可执行包装器位于prefix本身，不是其`bin`子目录：

```powershell
$deployPnpmHome = 'C:\Tools\pnpm-10.33.2'  # 随已选版本调整
& $deployNpmCmd install --global --prefix $deployPnpmHome "pnpm@$deployPnpmVersion"
if ($LASTEXITCODE -ne 0) { throw 'pnpm安装失败，停止' }
$deployPnpmCmd = Join-Path $deployPnpmHome 'pnpm.cmd'
& $deployPnpmCmd --version
$env:PATH = "$deployPnpmHome;$env:PATH"
where.exe node
where.exe pnpm
```

这里安装的是**pnpm**，不安装名为zcode的npm包。若使用既有mise/Corepack则依其当前help与仓库配置选择固定版本，不需要再执行这段。确认显式`pnpm.cmd --version`与mise一致后，在同一PowerShell中构建，保留退出码：

```powershell
Push-Location -LiteralPath $deployRepo
try {
    & $deployPnpmCmd install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw '依赖/资源准备失败' }
    & $deployPnpmCmd --filter '@zcode/cli...' build
    if ($LASTEXITCODE -ne 0) { throw 'CLI构建失败' }
} finally { Pop-Location }
$deployCliEntry = Join-Path $deployRepo 'apps\zcode-cli\packages\cli\dist\zcode.cjs'
& $deployNodeExe $deployCliEntry --version
& $deployNodeExe $deployCliEntry doctor --json
```

将`$deployNodeExe`、`$deployCliEntry`的实际值填入部署记录。新开终端不会自动继承上述临时PATH；运行采集器时用绝对Node包装器即可。依赖安装和doctor需要各自记录结果；doctor不证明模型、GUI或网站可用。

官方默认产物是 `apps/zcode-cli/packages/cli/dist/zcode.cjs` 加**相邻运行资源**，请保留整个对应产物目录结构，不能只拷一个 cjs。其依赖不等于本项目的 Python requirements。可直接保留完整 checkout，使用绝对 Node 与入口脚本路径，或按所选版本的打包规则部署完整产物。

官方还支持可选 SEA：构建后运行所选源码支持的 `pnpm --filter @zcode/cli sea --target win-x64`（ARM64 用其对应目标）；[官方快速开始](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/01-quickstart.md)说明 Windows 产物名为 `zcode-windows-<arch>.exe`。SEA 不依赖系统 Node，外部 MCP/Hook/工具所需程序仍单独准备；交叉打包不等于目标机实测。没有确认某版本 SEA 可下载时，不虚构下载资产地址。

## 放置原则

采用稳定绝对路径，例如 `C:\Tools\ZCode-<commit>\...` 与 `C:\Tools\Node-<version>\node.exe`；示例不表示必须放 C 盘。支持中文与空格。部署后不随意移动 bundle、相邻资源或记录工作区；需要更换时走 [路径替换流程](relocate.md)。不要把工具程序、Chrome profile、ZCode 数据根和业务记录目录混成一个目录。
