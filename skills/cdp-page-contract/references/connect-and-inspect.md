# 建立连接与最小只读样例

这些是给 AI 或配置人员使用的技术参考；人看的 HTML 以图解和截图位置为主。主线是复用**本人已登录的固定专用 Google Chrome**，通过前端点击、输入和读取来使用服务。以下命令不会自动安装 Playwright，也不会自动登录目标网页。

## 1. 固定专用 Chrome 与登录身份

先读取用户或项目已有配置，核对以下绑定：Chrome 程序路径、固定 `user-data-dir`、固定 profile 子目录、固定 CDP 端点、目标站点账号/组织。已有授权端点直接复用，无需重启；不要临时另开浏览器或生成一个新 profile 来凑出可连接端点。

仅首次配置专用实例时，在 PowerShell 中使用一个固定非默认数据目录；Chrome 路径按实际安装位置修改，后续启动仍用同一目录。这里的 `AI-CDP-Demo-Profile` 是示例固定目录名，不是每次替换的临时路径：

```powershell
$chromeExe = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$cdpProfile = Join-Path $env:LOCALAPPDATA 'AI-CDP-Demo-Profile'
& $chromeExe --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 "--user-data-dir=$cdpProfile" --profile-directory=Default
```

Linux 桌面示例：

```bash
google-chrome --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 --user-data-dir="$HOME/.local/share/ai-cdp-demo-profile" \
  --profile-directory=Default
```

Chrome 136 起，调试开关需配合非默认数据目录。本人应在这个专用 Chrome 内先登录目标网站，后续 AI 连接同一 Chrome 的同一普通 profile；日常浏览器中已有的登录不会自动转移过来。可在专用 Chrome 的 `chrome://version` 核对 Profile Path，例如固定数据目录下的 `Default` 子目录。不要让两个独立运行的 Chrome 进程同时共用这个数据目录。

固定数据目录保存 profile 和站点本地数据，包括 Cookie；站点登录状态由浏览器及站点共同管理，不能保证永久有效。Cookie 到期、本人退出或删除站点数据后可能需要重新登录。登录失效时，让本人回到同一个专用 Chrome 重新登录，AI 待页面恢复正确账号/组织后再继续。不要导出 Cookie、token、`storageState` 或把其复制到另一个浏览器；本路线不以接口调用代替网页前端。

端口占用时先辨认使用者，不能直接杀进程。确认监听实际只在 loopback；远程连接通过授权隧道传输。CDP 端口可控制浏览器，应只交给受信任客户端，不能以开放公网端口替代认证。

在本机检查：

```powershell
Invoke-RestMethod http://127.0.0.1:9222/json/version
Invoke-RestMethod http://127.0.0.1:9222/json/list
```

返回 browser/version 与 targets 只证明端点连通，不能证明登录账号正确。本人在固定专用 Chrome 内完成登录并打开目标页面后，AI 仍需核实具体页签和页面可见的账号/组织身份。若页面没有可读取的身份标志，沿用本人对该专用浏览器当前账号的明确确认，不编造“已经验证账号”。不要把 `webSocketDebuggerUrl` 当普通业务网页 URL。

## 2. Playwright 查询当前页面

在独立工作目录准备 Node.js 项目：

```bash
npm init -y
npm install playwright
```

连接已安装、正在运行的固定 Google Chrome 不需要执行 `playwright install`。将下面代码保存为 `inspect-order.mjs`，把固定端点和目标 URL 改为项目已核实的配置后运行 `node inspect-order.mjs`。运行前须已按上一节确认 profile 与当前账号/组织；本样例展示定位和读取，没有虚构账号选择器，也不声称自动验证未知网站的账号。

此样例只读取该 Chrome 既有普通 context 中已呈现的订单，不调用 `launch()`、`newContext()` 或创建匿名会话；不打开新页签、不填框、不点击。如果目标行未显示，将报告失败。实际 AI 任务可按页面约定在同一前端中输入查询条件、点击查询/只读详情并读回结果。示例主机是占位地址。

```javascript
import { chromium } from 'playwright';
import process from 'node:process';

const target = new URL('https://orders.example.com/admin/orders');
// 固定配置：必须指向本人已登录、已核实 profile/账号的专用 Google Chrome。
const cdpEndpoint = 'http://127.0.0.1:9222';

async function inspect() {
  const browser = await chromium.connectOverCDP(cdpEndpoint, {
    timeout: 10000
  });
  // connectOverCDP 的首个 context 是现有浏览器的默认 context；不新建 context。
  const context = browser.contexts()[0];
  if (!context) throw new Error('既有普通 profile 的 context 不存在');
  const pages = context.pages();
  const matches = pages.filter(page => {
    try {
      const url = new URL(page.url());
      return url.origin === target.origin && url.pathname === target.pathname;
    } catch {
      return false;
    }
  });
  if (matches.length !== 1) {
    throw new Error(`目标页签应唯一，实际匹配 ${matches.length} 个`);
  }
  const page = matches[0];
  const root = page.locator('[data-testid="orders-page"]');
  if (await root.count() !== 1) throw new Error('订单页根区域不唯一或不存在');
  await root.waitFor({ state: 'visible', timeout: 10000 });

  const table = root.locator('[data-testid="orders-table"]');
  if (await table.count() !== 1) throw new Error('订单表格不唯一或不存在');
  const row = table.locator('tr[data-order-id="A-1042"]');
  if (await row.count() !== 1) throw new Error('目标订单行不唯一或尚未显示');
  if (!await row.isVisible()) throw new Error('目标订单行不可见');
  const orderId = row.locator('[data-col="order-id"]');
  if (await orderId.count() !== 1 || (await orderId.innerText()).trim() !== 'A-1042') {
    throw new Error('订单号单元格未通过精确身份校验');
  }
  const details = row.locator('[data-action="view-order"]');
  if (await details.count() !== 1) throw new Error('详情入口不唯一或不存在');
  if (!await details.isVisible()) throw new Error('详情入口不可见');
  return {
    page: page.url(),
    orderId: 'A-1042',
    rowText: await row.innerText(),
    detailsLabel: await details.innerText(),
    detailsEnabled: await details.isEnabled(),
    action: 'read-only; no click performed'
  };
}

try {
  const result = await inspect();
  await new Promise(resolve => process.stdout.write(JSON.stringify(result, null, 2) + '\n', resolve));
  // 独立客户端进程退出，关闭自己的连接；不发送浏览器关闭命令。
  process.exit(0);
} catch (error) {
  await new Promise(resolve => process.stderr.write(String(error.message) + '\n', resolve));
  process.exit(1);
}
```

精确 URL 的 query、hash、租户身份若参与业务路由，要加入匹配条件。样例没有声称适配任意页面；实际 DOM 必须含上述属性与订单号列，否则应先改约定。

样例进程退出只断开自身连接，固定 Chrome 及其本地 profile 继续保留。不要在每次任务结束时清理 profile、清除站点数据或关闭用户页签。

## 3. 向人解释 selector

假设网页含以下节点：

```html
<main data-testid="orders-page">
  <label for="order-id">订单号</label>
  <input id="order-id" />
  <button data-testid="search-orders">查询</button>
  <table data-testid="orders-table">
    <tbody>
      <tr data-order-id="A-1042">
        <td data-col="order-id">A-1042</td><td>待发货</td>
        <td><button data-action="view-order">详情</button></td>
      </tr>
    </tbody>
  </table>
</main>
```

- `#order-id`：id 为 order-id 的元素。其业务含义来自 label 和页面约定。
- `[data-testid="orders-table"]`：属性值精确匹配的订单表格。
- `tr[data-order-id="A-1042"] [data-action="view-order"]`：这个订单行里面的详情入口；不会随着排序换成另一订单。
- `page.getByRole('button', { name: '详情', exact: true })`：按按钮角色和可访问名称定位，属于 Playwright API。整页可能有很多“详情”，应先限定订单行。
- iframe 例：`page.frameLocator('iframe[title="订单系统"]').getByRole('button', { name: '查询', exact: true })`。这是定位方法组合；CSS selector 不能直接跨 frame。

页面中 `data-*` 属性必须已存在，或由应用开发者在源码中添加。告诉 AI 使用一个不存在的 `data-testid` 并不会让它在页面出现；任意写入 DOM 伪造标记也不能证明找对了业务元素。

## 官方资料

- [Chrome 调试端口变更](https://developer.chrome.com/blog/remote-debugging-port)
- [Chromium 用户数据目录与 Profile Path](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md)
- [Chrome Cookie 与登录状态](https://support.google.com/chrome/answer/95647?hl=en)
- [Chrome Cookie 的有效期与会话属性](https://developer.chrome.com/docs/extensions/reference/api/cookies#type-Cookie)（仅用于说明持久性边界，本技能不使用 Cookie API）
- [Chrome 端口转发与启动示例](https://developer.chrome.com/docs/devtools/remote-debugging/local-server/)
- [Playwright connectOverCDP](https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp)
- [Playwright locators](https://playwright.dev/docs/locators)
- [FrameLocator](https://playwright.dev/docs/api/class-framelocator)
- [Browser.close 的连接/启动语义](https://playwright.dev/docs/api/class-browser#browser-close)
- [CDP DOM.querySelector](https://chromedevtools.github.io/devtools-protocol/tot/DOM/#method-querySelector)
