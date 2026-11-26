# 准备 Chrome 与本机 CDP

CDP 是 Chrome 的本机调试入口。ZCode 连接桥与 Chrome 需要分别验收。Windows 默认入口为 `http://127.0.0.1:9222`；这个端口可以由其他应用共用的 Chrome 提供，也可以由采集器打开自己的专用 Chrome。Chrome 136 起，普通 Chrome 的远程调试参数必须搭配非默认 `--user-data-dir`。见 [Google 的调试端口说明](https://developer.chrome.com/blog/remote-debugging-port)。

## 选择部署方式

| 现场状态 | 操作 |
|---|---|
| 已有可达的 Chrome CDP | 把实际本机入口填入采集器的「设置 → 学习工作区 → CDP 地址」。点「启动 Chrome」复用窗口，或直接「刷新」。确认登录账号和目标网页。 |
| 没有 CDP，但装了 Google Chrome | 保留默认可用端口，Chrome 路径留空，点「启动 Chrome」。也可显式选择 Chrome 的实际 `chrome.exe`；点保存后再次启动。安装普通 Chrome 可用 [Google 官方下载页](https://www.google.com/chrome/)。 |
| Windows x64 没有 Chrome | 完整解压便携包，在与 EXE 同级的目录双击 `Prepare-Chrome.cmd`。它从 [Google 的 Stable 目录](https://googlechromelabs.github.io/chrome-for-testing/)下载和校验完整的 `chrome-win64.zip`，解压到 `optional-dependencies/chrome-win64/`。结果显示 `ready` 或 `already-ready` 后，回采集器点「启动 Chrome」。 |
| 目标机不能下载 | 在能联网的 Windows x64 电脑完成上一行，再把便携程序和整个 `optional-dependencies/chrome-win64/` 一起带到目标机。也可下载 [Google 官方桌面 Chrome ZIP](https://googlechromelabs.github.io/chrome-for-testing/)，按目录结构完整解压。保留 DLL、资源及语言文件。 |
| 9222 被别的服务占用 | 保存该服务的运行状态，将采集器改用空闲本机端口，例如 `http://127.0.0.1:9223`，点「保存并完成」再启动。独立设置的端口下次打开仍会使用。 |

浏览器选择顺序为明确指定的 Chrome 路径、系统已安装 Chrome、便携可选 Chrome。迁移后旧 `chrome.exe` 路径失效时，先清空它以启用自动查找，或重新选择目标机的程序文件。要主动使用可选版本，即使系统也装了 Chrome，也可选 `optional-dependencies/chrome-win64/chrome.exe`。Windows x64 可选完整浏览器是 Chrome for Testing 的 `chrome` 资产，带桌面窗口；ChromeDriver 和 Headless Shell 不用于这条流程。该版本不自动更新，按需要从官方重新准备版本，选择有正常更新机制的常规 Chrome 也可。见 [Google 下载说明](https://developer.chrome.com/docs/automation-and-testing/download-test-binaries)。

## 固定专用登录资料

- 采集器新启动专用 Chrome 时使用 EXE 同级 `CDP-Chrome-Profile`。在专用窗口里自行登录网站，后续继续使用同一个浏览器资料目录。换电脑时在目标机自行登录；程序、网页标注和浏览器登录资料是三类不同文件。
- 复用他人创建的 CDP 时，以现有 Chrome 窗口和实际账号为准。入口可达只证明 Chrome 会响应，不能识别账号或说明网站已经登录。
- 使用本机回环端口。遇到端口冲突，改用空闲端口，别停止其他应用正在用的 Chrome。避免把浏览器资料目录、凭据和认证状态放进部署包。

## 按层检查

1. 用浏览器打开 `http://127.0.0.1:9222/json/version`，端口跟随当前设置。确认返回 Chrome 的 `Browser`、`Protocol-Version`，以及同一端口的 `webSocketDebuggerUrl`。给人的部署记录只记程序版本、回环地址与通过/失败。
2. 专用窗口打开获准的页面后，采集器点「刷新」，选页，开启采集。在虚构或获准页面右键保存一个控件并核对 selector 的实际匹配数。HTTP 通过但采集失败时，检查地址、选中的页签和 WebSocket 握手，把浏览器连通与页面登录分开排查。
3. 准备失败看便携包根目录 `Chrome-Setup-Result.json`。检查 Google 下载域名能否访问、目录写权限及磁盘空间。官方包下载和解压期间会同时占用 ZIP 与展开文件，建议预留 1 GB。已有完整目录时程序复用。已有不完整目录时先保留并移到另一处，再重新准备。

需要手工起专用实例时，用 PowerShell 传入实际路径。浏览器启动后，采集器填写相同端口。

```powershell
$chromeProgram = 'D:\CDP-Picker\optional-dependencies\chrome-win64\chrome.exe'
$chromeProfile = 'D:\CDP-Picker\CDP-Chrome-Profile'
& $chromeProgram --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 "--user-data-dir=$chromeProfile" --no-first-run --no-default-browser-check
```
