# CDP 控件采集器 v0.1.0 源码快照

此标签对应已确认可操作的 Windows 面板版 EXE。下载与使用见 [Release](https://github.com/Mikky574/cdp-control-picker/releases/tag/v0.1.0) 和 [使用说明](使用说明.txt)。

本源码保留 `tools/cdp-control-picker/` 与 `skills/cdp-page-contract/` 的旧目录布局，确保应用和构建所用的相对路径不变。使用带 Tk 的 Python，在工具目录准备 requirements.txt 后运行 app.py；构建入口为同目录 build.py。用户运行发布 EXE 无需 Python。

原采集器、测试、构建脚本与页面技能逐字节来自旧仓库提交 `307baaed7ace7fdb2cc1b6d86e3173904187c00e`，来源校验见 [SOURCE_ORIGIN.json](SOURCE_ORIGIN.json)。此快照不含浏览器资料、凭据、个人工作区或构建环境。

当前版按选中的单张控件卡交接，尚未实现自动投递到 ZCode 可见对话。开发中的悬浮窗和同页多控件合并位于本仓库 main，不包含在本版本中。
