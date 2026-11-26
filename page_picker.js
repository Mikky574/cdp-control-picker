/* Main-frame-only CDP control picker. No business click/fill is performed. */
(() => {
  'use strict';
  if (window !== window.top) return;
  const VERSION = '1.0.1';
  if (window.__cdpControlPicker && window.__cdpControlPicker.state) {
    try {
      if (window.__cdpControlPicker.state().version === VERSION) {
        if (window.__CDP_PICKER_AUTO_ENABLE__ === true) window.__cdpControlPicker.enable();
        return;
      }
      window.__cdpControlPicker.disable();
    } catch (_) { /* Replace only this tool's prior instance. */ }
  }

  const ACTIONABLE = 'button,a[href],input,textarea,select,[role="button"],[role="link"],[role="textbox"],[role="checkbox"],[role="radio"],[role="combobox"],[role="switch"],[contenteditable="true"]';
  const ATTRS = ['id', 'type', 'name', 'role', 'aria-label', 'aria-labelledby', 'data-testid', 'data-test-id', 'data-test', 'data-qa', 'data-action', 'data-field', 'data-col'];
  const BUSINESS_ATTRS = ['data-order-id', 'data-record-id', 'data-row-id', 'data-item-id', 'data-key'];
  const SAFE_ATTRS = [...ATTRS, ...BUSINESS_ATTRS];
  const registry = new Map();
  const listeners = [];
  let enabled = false;
  let host = null;
  let shadow = null;
  let ui = null;
  let selected = null;
  let highlighted = null;
  let message = '';
  let toastTimer = null;
  let highlightTimer = null;
  let readyPending = false;
  let updatePending = false;

  function compact(value, max = 160) {
    return String(value || '').replace(/\s+/g, ' ').trim().slice(0, max);
  }
  function token() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    return `picker-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }
  function cssString(value) {
    return '"' + String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/[\n\r\f]/g, char => `\\${char.charCodeAt(0).toString(16)} `) + '"';
  }
  function attrSelector(name, value) { return `[${name}=${cssString(value)}]`; }
  function safeAttributes(element) {
    const result = {};
    for (const name of SAFE_ATTRS) {
      const value = element.getAttribute(name);
      if (value && value.length <= 160) result[name] = value;
    }
    return result;
  }
  function isFormValue(element) {
    return element.matches('input,textarea,select,[contenteditable]:not([contenteditable="false"])') || element.isContentEditable;
  }
  function safeText(element) {
    if (!element || isFormValue(element)) return '';
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let result = '';
    let node;
    let scanned = 0;
    while ((node = walker.nextNode()) && scanned++ < 80 && result.length < 240) {
      let parent = node.parentElement;
      let blocked = false;
      while (parent && parent !== element.parentElement) {
        if (parent.matches('script,style,noscript,input,textarea,select,[contenteditable]:not([contenteditable="false"])') || parent.isContentEditable || parent === host) { blocked = true; break; }
        parent = parent.parentElement;
      }
      if (!blocked) result += ' ' + node.textContent;
    }
    return compact(result);
  }
  function isSensitive(element) {
    if (!element.matches('input,textarea')) return false;
    const type = (element.getAttribute('type') || '').toLowerCase();
    const autocomplete = (element.getAttribute('autocomplete') || '').toLowerCase();
    const fieldName = element.getAttribute('name') || '';
    return type === 'password' || type === 'hidden' || /(?:current-password|new-password|one-time-code)/.test(autocomplete) || /(?:password|passwd|secret|credential|access[_-]?token)/i.test(fieldName);
  }
  function labelHint(element) {
    const explicit = compact(element.getAttribute('aria-label'));
    if (explicit) return explicit;
    const ids = (element.getAttribute('aria-labelledby') || '').trim().split(/\s+/).filter(Boolean).slice(0, 4);
    const labelled = compact(ids.map(id => safeText(document.getElementById(id))).filter(Boolean).join(' '));
    if (labelled) return labelled;
    if (element.labels && element.labels.length) return compact([...element.labels].slice(0, 3).map(safeText).join(' '));
    return '';
  }
  function domRole(element) {
    if (element.getAttribute('role')) return compact(element.getAttribute('role'), 50);
    const tag = element.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && element.hasAttribute('href')) return 'link';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return element.multiple ? 'listbox' : 'combobox';
    if (tag === 'input') {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      return ({ button: 'button', submit: 'button', reset: 'button', checkbox: 'checkbox', radio: 'radio', range: 'slider', number: 'spinbutton', search: 'searchbox' })[type] || 'textbox';
    }
    return element.isContentEditable ? 'textbox' : '';
  }
  function summary(element) {
    return { tagName: element.tagName.toLowerCase(), attributes: safeAttributes(element), role: domRole(element), roleSource: 'dom-heuristic', labelHint: labelHint(element), text: safeText(element) };
  }
  function ownEvent(event) {
    return Boolean(host && (event.composedPath().includes(host) || event.target === host));
  }
  function localCount(selector) {
    try { return [...document.querySelectorAll(selector)].filter(element => element !== host).length; }
    catch (_) { return 0; }
  }
  function selectorCandidates(element) {
    const tag = element.tagName.toLowerCase();
    const bases = [];
    const addBase = (value, priority, reason) => bases.push({ value, priority, reason });
    const id = element.getAttribute('id');
    if (id && id.length <= 160) addBase(window.CSS && CSS.escape ? `#${CSS.escape(id)}` : attrSelector('id', id), 100, 'id');
    for (const [name, priority] of [['data-testid', 98], ['data-test-id', 97], ['data-test', 96], ['data-qa', 95], ['data-action', 76], ['name', 72], ['aria-label', 70], ['data-field', 68], ['data-col', 65]]) {
      const value = element.getAttribute(name);
      if (value && value.length <= 160) addBase(`${tag}${attrSelector(name, value)}`, priority, name);
    }
    addBase(tag, 10, 'tag-only');
    const scoped = [];
    let ancestor = element.parentElement;
    for (let depth = 0; ancestor && depth < 7; depth++, ancestor = ancestor.parentElement) {
      if (ancestor === document.body || ancestor === document.documentElement) break;
      const scopes = [];
      for (const name of BUSINESS_ATTRS) {
        const value = ancestor.getAttribute(name);
        if (value && value.length <= 160) scopes.push({ value: `${ancestor.tagName.toLowerCase()}${attrSelector(name, value)}`, business: true });
      }
      for (const name of ['data-testid', 'id']) {
        const value = ancestor.getAttribute(name);
        if (value && value.length <= 160) scopes.push({ value: `${ancestor.tagName.toLowerCase()}${attrSelector(name, value)}`, business: false });
      }
      for (const scope of scopes) {
        for (const base of bases) scoped.push({ value: `${scope.value} ${base.value}`, priority: base.priority + (scope.business ? 100 : 25) - depth, reason: scope.business ? 'business-scope' : 'region-scope' });
      }
    }
    const deduped = new Map();
    for (const candidate of [...scoped, ...bases]) {
      if (!deduped.has(candidate.value)) deduped.set(candidate.value, { strategy: 'css', value: candidate.value, matchCount: localCount(candidate.value), reason: candidate.reason, _priority: candidate.priority });
    }
    return [...deduped.values()]
      .sort((a, b) => Number(b.matchCount === 1) - Number(a.matchCount === 1) || b._priority - a._priority || a.value.length - b.value.length)
      .slice(0, 12).map(({ _priority, ...candidate }) => candidate);
  }
  function ancestorEvidence(element) {
    const result = [];
    let parent = element.parentElement;
    for (let depth = 0; parent && depth < 7 && result.length < 3; parent = parent.parentElement, depth++) {
      const attributes = safeAttributes(parent);
      if (BUSINESS_ATTRS.some(name => attributes[name]) || attributes['data-testid']) result.push({ tagName: parent.tagName.toLowerCase(), attributes });
    }
    return result;
  }
  function pageIdentity() {
    const current = new URL(window.location.href);
    return { url: current.protocol === 'http:' || current.protocol === 'https:' ? current.origin + current.pathname : current.protocol + current.pathname, origin: current.origin, path: current.pathname, title: compact(document.title, 200), frameChain: [] };
  }
  function bounds(element) {
    const rect = element.getBoundingClientRect();
    return { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) };
  }
  function remember(element) {
    const elementToken = token();
    registry.set(elementToken, typeof WeakRef === 'function' ? new WeakRef(element) : element);
    if (registry.size > 256) registry.delete(registry.keys().next().value);
    return elementToken;
  }
  function resolveElement(elementToken) {
    const saved = registry.get(elementToken);
    const element = saved && typeof saved.deref === 'function' ? saved.deref() : saved;
    return element && element.isConnected && element.ownerDocument === document ? element : null;
  }

  function createUI() {
    if (host && host.isConnected) return true;
    if (!document.documentElement) {
      if (!readyPending) { readyPending = true; document.addEventListener('DOMContentLoaded', onReady, { once: true }); }
      return false;
    }
    host = document.createElement('div');
    host.id = '__cdp_control_picker_root__';
    host.setAttribute('data-cdp-picker-ui', 'true');
    shadow = host.attachShadow({ mode: 'closed' });
    shadow.innerHTML = `<style>
      :host{all:initial!important;position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;z-index:2147483647!important;pointer-events:none!important;font-family:"Microsoft YaHei","Segoe UI",sans-serif!important;color:#142139!important;font-size:14px!important;line-height:1.55!important}
      *{box-sizing:border-box} [hidden]{display:none!important} button,input,textarea,select{font:inherit}button{cursor:pointer}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #317be8;outline-offset:2px}
      .banner{position:fixed;top:12px;left:12px;max-width:calc(100vw - 24px);padding:10px 13px;background:#142139;color:#fff;border-radius:9px;box-shadow:0 4px 24px #14213922;pointer-events:auto;display:flex;align-items:center;gap:14px;font-size:13px}.banner small{display:block;font-size:11px;color:#c7d8f0}.banner button{background:#fff;color:#142139;border:0;border-radius:5px;padding:5px 10px;white-space:nowrap}
      .outline{position:fixed;border:3px solid #0b7c70;background:#0b7c7010;box-shadow:0 0 0 1px #fff;pointer-events:none;border-radius:3px}
      .panel{position:fixed;width:390px;max-width:calc(100vw - 24px);max-height:calc(100vh - 24px);overflow:auto;padding:18px;background:#fff;border:1px solid #d2ddea;border-radius:12px;box-shadow:0 12px 44px #14213933;pointer-events:auto}.panel h2{font-size:17px;margin:0 0 4px}.preview{font-size:12px;color:#5a6b83;overflow-wrap:anywhere;margin:0 0 13px}.panel label{font-size:13px;display:block;margin:11px 0 4px;font-weight:600}.panel input,.panel textarea,.panel select{display:block;width:100%;border:1px solid #bdcce0;border-radius:6px;background:#fff;color:#142139;padding:8px 10px;font-size:14px;min-height:38px}.panel textarea{resize:vertical;min-height:68px}.hint{font-size:12px;color:#5a6b83;margin-top:8px}.error{color:#b42318;font-size:13px;margin-top:9px}.actions{display:flex;justify-content:flex-end;gap:9px;margin-top:15px}.actions button{border:1px solid #c7d4e6;border-radius:6px;background:#fff;color:#142139;padding:7px 15px;min-height:36px}.actions button.primary{background:#175bd7;color:#fff;border-color:#175bd7}.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);max-width:calc(100vw - 28px);padding:11px 16px;background:#142139;color:#fff;border-radius:8px;box-shadow:0 4px 24px #14213933;pointer-events:none;overflow-wrap:anywhere;font-size:13px}
      @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}} @media print{:host{display:none!important}}
    </style><div class="outline" hidden aria-hidden="true"></div>
    <div class="banner" hidden><div><strong>控件采集模式</strong><small>右键选择后填写用途 · 仅普通主页面，暂不支持 iframe / Shadow DOM</small></div><button type="button" data-ui="stop">停止</button></div>
    <section class="panel" role="dialog" aria-label="记录控件用途" hidden><h2>记录这个控件</h2><p class="preview"></p><form novalidate>
      <label for="picker-name">控件名称</label><input id="picker-name" name="name" maxlength="120" autocomplete="off">
      <label for="picker-description">这个位置是做什么的？ <span aria-hidden="true">*</span></label><textarea id="picker-description" name="description" maxlength="1500" required placeholder="例如：打开当前记录的只读详情"></textarea>
      <label for="picker-result">预期结果</label><input id="picker-result" name="result" maxlength="500" autocomplete="off" placeholder="例如：详情中显示相同的记录编号">
      <p class="hint"></p><div class="error" role="alert" hidden></div><div class="actions"><button type="button" data-ui="cancel">取消</button><button class="primary" type="submit">保存控件卡</button></div>
    </form></section><div class="toast" role="status" aria-live="polite" hidden></div>`;
    const find = selector => shadow.querySelector(selector);
    ui = { outline: find('.outline'), banner: find('.banner'), panel: find('.panel'), preview: find('.preview'), form: find('form'), name: find('#picker-name'), description: find('#picker-description'), result: find('#picker-result'), hint: find('.hint'), error: find('.error'), toast: find('.toast') };
    find('[data-ui="stop"]').addEventListener('click', disable);
    find('[data-ui="cancel"]').addEventListener('click', closeForm);
    ui.form.addEventListener('submit', saveForm);
    for (const name of ['click', 'dblclick', 'input', 'change', 'submit', 'pointerdown', 'pointerup', 'mousedown', 'mouseup', 'contextmenu']) shadow.addEventListener(name, event => event.stopPropagation());
    document.documentElement.appendChild(host);
    ui.banner.hidden = !enabled;
    return true;
  }
  function onReady() { readyPending = false; if (enabled) createUI(); }
  function notify(text, duration = 5000) {
    message = text;
    if (!createUI()) return;
    clearTimeout(toastTimer);
    ui.toast.textContent = text;
    ui.toast.hidden = false;
    toastTimer = setTimeout(() => { if (ui) ui.toast.hidden = true; }, duration);
  }
  function paint(element) {
    if (!ui || !element || !element.isConnected) { if (ui) ui.outline.hidden = true; return; }
    const rect = element.getBoundingClientRect();
    ui.outline.hidden = rect.width <= 0 || rect.height <= 0 || rect.bottom < 0 || rect.right < 0 || rect.top > innerHeight || rect.left > innerWidth;
    Object.assign(ui.outline.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` });
  }
  function positionPanel() {
    if (!selected || !ui || ui.panel.hidden) return;
    if (!selected.element.isConnected) {
      ui.error.textContent = '页面已重绘，原控件已失效。请取消后重新选择。';
      ui.error.hidden = false;
      paint(null);
      return;
    }
    const rect = selected.element.getBoundingClientRect();
    const panel = ui.panel.getBoundingClientRect();
    const left = Math.max(12, Math.min(rect.left, innerWidth - panel.width - 12));
    let top = rect.bottom + 8;
    if (top + panel.height > innerHeight - 12) top = rect.top - panel.height - 8;
    top = Math.max(12, Math.min(top, innerHeight - panel.height - 12));
    Object.assign(ui.panel.style, { left: `${left}px`, top: `${top}px` });
  }
  function updatePosition() {
    if (updatePending) return;
    updatePending = true;
    requestAnimationFrame(() => { updatePending = false; paint(selected ? selected.element : highlighted); positionPanel(); });
  }
  function closeForm() {
    selected = null;
    if (ui) { ui.panel.hidden = true; ui.outline.hidden = true; }
    highlighted = null;
  }
  function showForm(element, clicked) {
    if (!createUI()) return;
    clearTimeout(highlightTimer);
    const candidates = selectorCandidates(element);
    const elementSummary = summary(element);
    selected = { element, clicked, elementSummary, candidates, elementToken: remember(element) };
    highlighted = element;
    ui.preview.textContent = `${elementSummary.tagName} · ${elementSummary.labelHint || elementSummary.text || '无可用名称；请补充用途'}`;
    ui.name.value = compact(elementSummary.labelHint || elementSummary.text || elementSummary.tagName, 120);
    ui.description.value = '';
    ui.result.value = '';
    ui.error.hidden = true;
    ui.error.textContent = '';
    const unique = candidates.find(candidate => candidate.matchCount === 1);
    ui.hint.textContent = unique ? '已找到当前页面内的唯一定位；仍会保存为待验证草稿。此处不会执行控件动作。' : '当前定位仍可能匹配多个位置；可保存草稿，使用前需补充范围并重新验证。';
    ui.panel.hidden = false;
    paint(element);
    positionPanel();
    requestAnimationFrame(() => { if (ui && selected) ui.description.focus({ preventScroll: true }); });
  }
  function saveForm(event) {
    event.preventDefault();
    event.stopPropagation();
    if (!selected || !selected.element.isConnected) { ui.error.textContent = '原控件已失效，请取消后重新选择。'; ui.error.hidden = false; return; }
    const description = ui.description.value.trim();
    if (!description) { ui.error.textContent = '请先说明这个控件的功能或用途。'; ui.error.hidden = false; ui.description.focus(); return; }
    const element = selected.element;
    if (isSensitive(element)) { ui.error.textContent = '不采集密码或凭据输入控件。'; ui.error.hidden = false; return; }
    const candidates = selectorCandidates(element);
    const card = {
      schemaVersion: 1,
      captureId: `cap-${token()}`,
      createdAt: new Date().toISOString(),
      page: pageIdentity(),
      control: {
        ...summary(element),
        id: element.getAttribute('id') || '',
        type: element.getAttribute('type') || '',
        name: compact(ui.name.value, 120) || selected.elementSummary.tagName,
        domNameHint: labelHint(element) || safeText(element),
        nameSource: 'user-or-dom-hint-not-accessibility-tree',
        selectors: candidates,
        locatorCandidates: candidates,
        ancestors: ancestorEvidence(element),
        closestActionableAncestor: element.matches(ACTIONABLE) ? summary(element) : null,
        clickedTagName: selected.clicked.tagName.toLowerCase(),
        usedActionableAncestor: selected.clicked !== element,
        bounds: bounds(element)
      },
      annotation: { name: compact(ui.name.value, 120) || selected.elementSummary.tagName, description: description.slice(0, 1500), expectedResult: ui.result.value.trim().slice(0, 500) },
      validation: { status: 'draft', runtimeValidation: 'unvalidated', capturedAt: new Date().toISOString(), uniqueCandidateCount: candidates.filter(candidate => candidate.matchCount === 1).length, note: '采集唯一性不等于刷新后复用或业务动作验证；未执行点击或填写。' },
      ephemeral: { elementToken: selected.elementToken }
    };
    if (typeof window.__cdpControlPickerSave !== 'function') { ui.error.textContent = '桌面保存通道未连接；请检查控制面板后重试。'; ui.error.hidden = false; return; }
    try {
      window.__cdpControlPickerSave(JSON.stringify(card));
      closeForm();
      notify('控件卡已发送到桌面，等待保存确认。');
    } catch (_) { ui.error.textContent = '发送失败，内容仍保留。请检查控制面板后重试。'; ui.error.hidden = false; }
  }
  function blockRightMouse(event) {
    if (!enabled || ownEvent(event) || event.button !== 2) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }
  function onContextMenu(event) {
    if (!enabled || ownEvent(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const path = event.composedPath();
    const clicked = path.find(item => item instanceof Element);
    if (!clicked) return;
    if (path.some(item => item instanceof ShadowRoot) || clicked.getRootNode() !== document || clicked.shadowRoot || clicked.tagName.includes('-')) {
      notify('此目标涉及 Shadow DOM / Web 组件边界，首版请改选普通主页面控件。'); return;
    }
    if (clicked.closest('iframe,frame,canvas,object,embed')) { notify('首版暂不采集 iframe、canvas 或嵌入内容内部的控件。'); return; }
    const element = clicked.closest(ACTIONABLE) || clicked;
    if (isSensitive(element)) { closeForm(); notify('不采集密码、验证码或凭据输入控件，也不会读取输入值。'); return; }
    showForm(element, clicked);
  }
  function onKeyDown(event) {
    if (enabled && event.key === 'Escape' && selected) { event.preventDefault(); event.stopImmediatePropagation(); closeForm(); }
  }
  function listen(target, name, handler, options) {
    target.addEventListener(name, handler, options);
    listeners.push(() => target.removeEventListener(name, handler, options));
  }
  function enable() {
    if (enabled) { createUI(); return state(); }
    enabled = true;
    for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'auxclick']) listen(window, name, blockRightMouse, { capture: true, passive: false });
    listen(window, 'contextmenu', onContextMenu, { capture: true, passive: false });
    listen(window, 'keydown', onKeyDown, true);
    listen(window, 'scroll', updatePosition, { capture: true, passive: true });
    listen(window, 'resize', updatePosition, { passive: true });
    createUI();
    message = '采集已开启。右键选控件，不会自动执行控件动作。';
    return state();
  }
  function disable() {
    enabled = false;
    closeForm();
    for (const remove of listeners.splice(0)) remove();
    if (readyPending) { document.removeEventListener('DOMContentLoaded', onReady); readyPending = false; }
    clearTimeout(toastTimer);
    clearTimeout(highlightTimer);
    if (host) host.remove();
    host = null; shadow = null; ui = null;
    message = '采集已关闭，已移除本工具的浮层和监听。';
    return state();
  }
  function highlight(card) {
    const identity = card && card.page;
    if (identity && (identity.origin && identity.origin !== location.origin || identity.path && identity.path !== location.pathname)) return { ok: false, reason: 'wrong-page', message: '当前页面与卡片的 origin/path 不一致。' };
    const control = card && card.control || {};
    const candidates = control.selectors || control.locatorCandidates || card && card.selectorCandidates || [];
    let bestCount = 0;
    for (const candidate of candidates) {
      if (typeof candidate !== 'string' && candidate.strategy && candidate.strategy !== 'css') continue;
      const value = typeof candidate === 'string' ? candidate : candidate.value || candidate.selector;
      if (!value || typeof value !== 'string') continue;
      let matches;
      try { matches = [...document.querySelectorAll(value)].filter(element => element !== host && !isSensitive(element)); } catch (_) { continue; }
      bestCount = Math.max(bestCount, matches.length);
      if (matches.length !== 1) continue;
      const element = matches[0];
      if (control.tagName && element.tagName.toLowerCase() !== control.tagName.toLowerCase()) continue;
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') continue;
      if (!createUI()) return { ok: false, reason: 'page-not-ready' };
      clearTimeout(highlightTimer);
      highlighted = element;
      paint(element);
      highlightTimer = setTimeout(() => { if (!selected && ui) { ui.outline.hidden = true; highlighted = null; if (!enabled) { host.remove(); host = null; shadow = null; ui = null; } } }, 5000);
      return { ok: true, matchCount: 1, selector: value, bounds: bounds(element), validation: 'highlight-only-not-business-validation' };
    }
    return { ok: false, matchCount: bestCount, reason: bestCount > 1 ? 'multiple-matches' : 'no-unique-visible-match', message: '未找到符合类型的唯一可见控件；未选择 first/nth。' };
  }
  function state() {
    return { version: VERSION, enabled, formOpen: Boolean(selected && ui && !ui.panel.hidden), support: 'ordinary-main-frame-only', limitations: ['iframe', 'shadow-dom', 'canvas', 'earlier-page-right-mouse-listeners'], message };
  }
  const api = { enable, disable, highlight, state };
  Object.defineProperty(api, '_resolveElement', { value: resolveElement, enumerable: false });
  Object.defineProperty(window, '__cdpControlPicker', { value: api, configurable: true });
  if (window.__CDP_PICKER_AUTO_ENABLE__ === true) enable();
})();
