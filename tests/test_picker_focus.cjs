// Real Chromium regression. Run with Playwright installed:
// node --test tests/test_picker_focus.cjs
// PICKER_TEST_BROWSER optionally selects the packaged Chromium executable.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {chromium} = require('playwright');

const source = fs.readFileSync(path.join(__dirname, '..', 'page_picker.js'), 'utf8');

for (const nativeDialog of [false, true]) {
  test(nativeDialog ? 'picker over a native site dialog' : 'picker over a scripted site focus trap', async () => {
    const browser = await chromium.launch({executablePath: process.env.PICKER_TEST_BROWSER || undefined});
    try {
      const page = await browser.newPage({viewport: {width: 800, height: 640}});
      const saved = [];
      await page.exposeFunction('__cdpControlPickerSave', data => saved.push(JSON.parse(data)));
      const tag = nativeDialog ? 'dialog' : 'div';
      await page.setContent(`<${tag} id="site-dialog" style="margin:80px auto">
        <label for="site-search">Search or jump to</label>
        <input id="site-search" type="search" value="existing site query">
      </${tag}>`);
      await page.evaluate(native => {
        const site = document.querySelector('#site-dialog');
        const search = document.querySelector('#site-search');
        if (native) site.showModal();
        search.focus();
        document.addEventListener('focusin', event => {
          if (!site.contains(event.target)) search.focus();
        }, true);
        window.siteKeys = 0;
        document.addEventListener('keydown', () => window.siteKeys++);
        const attach = Element.prototype.attachShadow;
        Element.prototype.attachShadow = function (options) {
          const root = attach.call(this, options);
          if (this.id === '__cdp_control_picker_root__') window.testPickerRoot = root;
          return root;
        };
      }, nativeDialog);
      await page.evaluate(source);
      await page.evaluate(() => window.__cdpControlPicker.enable());
      const search = page.locator('#site-search');
      await search.click({button: 'right'});
      await page.waitForFunction(() => window.__cdpControlPicker.state().formOpen);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
      const activePickerField = () => page.evaluate(() => window.testPickerRoot.activeElement?.id ?? null);
      assert.equal(await activePickerField(), 'picker-description');
      await page.keyboard.type('Purpose belongs to the picker');
      assert.equal(await search.inputValue(), 'existing site query');
      assert.equal(await page.evaluate(() => window.testPickerRoot.querySelector('#picker-description').value),
        'Purpose belongs to the picker');
      assert.equal(await page.evaluate(() => window.siteKeys), 0);

      await page.keyboard.press('Tab');
      assert.equal(await activePickerField(), 'picker-result');
      await page.keyboard.press('Shift+Tab');
      assert.equal(await activePickerField(), 'picker-description');
      await page.evaluate(() => {
        const field = window.testPickerRoot.querySelector('#picker-description');
        field.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, composed: true, isComposing: true}));
      });
      assert.equal(await page.evaluate(() => window.__cdpControlPicker.state().formOpen), true);

      const save = await page.evaluate(() =>
        window.testPickerRoot.querySelector('button[type="submit"]').getBoundingClientRect().toJSON());
      await page.mouse.click(save.x + save.width / 2, save.y + save.height / 2);
      await page.waitForFunction(() => !window.__cdpControlPicker.state().formOpen);
      assert.equal(saved.length, 1);
      assert.equal(saved[0].annotation.description, 'Purpose belongs to the picker');
      assert.ok(saved[0].control.selectors.some(selector =>
        selector.value.endsWith('#site-search') && selector.matchCount === 1));
      assert.equal(await search.evaluate(element => document.activeElement === element), true);
      assert.equal(await search.inputValue(), 'existing site query');

      await search.click({button: 'right'});
      await page.keyboard.press('Escape');
      assert.equal(await page.evaluate(() => window.__cdpControlPicker.state().formOpen), false);
      assert.equal(await search.evaluate(element => document.activeElement === element), true);
      if (nativeDialog) assert.equal(await page.locator('#site-dialog').evaluate(dialog => dialog.open), true);
    } finally {
      await browser.close();
    }
  });
}
