# 更换路径、补充安装与恢复

> 本页保留CLI兼容安装/路径细节。默认原生桌面扩展接入、学习项目和会话模型先读[desktop-connection.md](desktop-connection.md)；CLI诊断不替代原生会话或GUI同步。

目标是替换 CLI/Node 的程序入口，同时保留原工作区、数据根与真实 sessionId。更换可执行程序不等于迁移用户历史；不移动/克隆 Chrome 登录profile，不复制覆盖 ZCode 会话库，不删除旧配置触发反复迁移。

## 选择替换方式

| 情况 | 实际方法 |
| --- | --- |
| 已有独立CLI命令，只是采集器没找到 | 在设置中明确填 `zcode-cli` / `zcode` 或实际文件，点击检测；确认启动采集器时的PATH，不只看另一个终端 |
| 同时有多份CLI | 显式选择本次版本的绝对EXE/包装器路径，记录resolved argv；留空会按PATH优先选择 |
| 只更换Node位置 | 更新标准包装器中的绝对Node路径，保留对应CLI与相邻资源；重新生成或手工编辑明确文件后检测 |
| CLI bundle搬到新位置 | 整体保留构建要求的相邻资源，更新脚本绝对路径；不单拷cjs |
| 直接选择脚本 | 同目录Node优先，其次继承PATH的Node；要消除歧义，用固定Node包装器 |
| 换数据/provider路径 | 先核对目标已有文件、所选版本格式与桌面索引映射，再在Start/Check启动器设置仅本进程的路径变量；不是改后台包装器SET |
| 独立CLI升级而桌面保留 | 重新检查实际协议、正文库和索引必要能力；不从CLI或桌面版本号直接判断可接入 |
| 桌面更换安装目录 | 设置当前启动器的 `CDP_PICKER_DESKTOP_EXE`；CLI入口仍单独选择，记录版本并重做必要schema/约束检查 |
| 索引或正文库原本在自定义位置 | 设置 `CDP_PICKER_DESKTOP_INDEX` / `CDP_PICKER_CLI_DATABASE` 指向**已有且身份匹配**的文件；这不迁移数据库，也不改变CLI写入位置 |
| 桌面升级 | 包括3.18.2及其他版本，按实际接口与必要schema/约束判断；兼容就继续，不要求降级。三个路径覆盖不能绕过能力/身份检查，勿用CLI路径假扮桌面路径 |

PATH 修改不会反向进入已运行的采集器。通常不必修改系统PATH：设置中填绝对路径，或使用启动器。确需用户自行调整PATH时只按官方安装方式处理，重新启动采集器并检查解析结果，不把本技能当作已执行的环境更改。

## 推荐的最小替换流程

1. 记录旧入口、版本、原工作区和数据根；保留原安装作为可回退候选，不运行两个采集器同时发送同一工作区。
2. 将可信的新CLI/Node完整产物放到新程序目录。记录来源/commit；对照该版本配置，暂不迁移数据库或重写默认模型。
3. 在部署记录中只更新程序路径；若原环境正确，`pathEnvironment`保持null继承原值。运行生成器输出到新的启动器目录，先查看生成内容。
4. 手动运行 `Check-Backend.cmd`，核对解析到的是新Node/入口与预期版本。只通过无模型检测时，结果写到这一层。
5. 关闭旧采集器，再从新 `Start-Picker.cmd` 打开；设置与检测确认一致。新模型/GUI/网页动作仅按已有授权验收，未知回执不能通过换目录避开去重。
6. 出错时保留状态，恢复旧程序入口再做无模型检测。不要恢复/覆盖旧数据库备份来回滚CLI程序；索引备份是故障恢复证据，具体恢复需独立判断影响。

## 手工启动器结构

下列为可填模板。替换尖括号为真实绝对路径；生成器可自动处理中文、空格、CRLF与基础校验。为兼容当前解析器，Node/脚本路径不使用字面量 `%` 或双引号/换行。不要把任务正文拼进 CMD。

`zcode-backend.cmd`（采集器**解析**此文件，不执行其初始化逻辑）：

```bat
@echo off
"<绝对路径\node.exe>" "<绝对路径\zcode.cjs>" %*
```

`Start-Picker.cmd`（由用户运行，因此环境可继承给采集器）：

```bat
@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
rem 仅确有需要时取消下一行rem，路径须指向原本要用的数据根。
rem set "ZCODE_DATA_BASE_DIR=<已核实数据基目录>"
rem set "CDP_PICKER_DESKTOP_EXE=<已安装桌面ZCode.exe>"
rem set "CDP_PICKER_DESKTOP_INDEX=<已有tasks-index.sqlite>"
rem set "CDP_PICKER_CLI_DATABASE=<同一CLI实际会话库db.sqlite>"
start "" /D "<采集器目录>" "<采集器.exe>" --workspace "<已存在记录目录>" --endpoint "http://127.0.0.1:9222" --zcode-cli "%~dp0zcode-backend.cmd"
```

`Check-Backend.cmd` 使用**相同路径环境**，运行无模型检测；报告保存在启动器目录：

```bat
@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
rem 与Start-Picker.cmd保持相同的可选路径环境。
start "" /wait /D "<采集器目录>" "<采集器.exe>" --workspace "<已存在记录目录>" --zcode-cli "%~dp0zcode-backend.cmd" --backend-check "%~dp0backend-report.json"
exit /b %errorlevel%
```

仅当前采集器支持上述 `--workspace`、`--endpoint`、`--zcode-cli`、`--backend-check`。生成器不会执行这些命令，也不会创建/信任ZCode项目。独立原生CLI可将启动器的 `--zcode-cli` 改为其绝对EXE路径并省略Node包装器；桌面 bundle 走程序已识别入口，不能移除其资源目录。
