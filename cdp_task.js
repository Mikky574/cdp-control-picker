function runCdpTask(task) {
  'use strict';
  const execute = () => {
  let attempted = false;
  const blocked = (code, message, extra = {}) => ({status: 'blocked', actionAttempted: attempted, error: {code, message}, ...extra});
  const compact = (text, limit = 250) => String(text || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const sensitive = element => {
    const type = (element.getAttribute('type') || '').toLowerCase();
    const hints = ['id', 'name', 'autocomplete', 'aria-label', 'placeholder'].map(key => element.getAttribute(key) || '').join(' ');
    const form = element.closest('form');
    return ['password', 'hidden'].includes(type) || Boolean(form && form.querySelector('input[type="password"]')) || /password|passwd|secret|credential|api.?key|access.?token|refresh.?token|authorization|cookie|csrf|xsrf|one-time-code|密码|口令|验证码|私钥/i.test(hints);
  };
  const role = element => element.getAttribute('role') || ({button: 'button', a: element.hasAttribute('href') ? 'link' : '', input: ['checkbox', 'radio'].includes(element.type) ? element.type : ['button', 'reset', 'submit'].includes(element.type) ? 'button' : 'textbox', textarea: 'textbox', select: 'combobox'}[element.tagName.toLowerCase()] || '');
  const name = element => {
    const labelled = (element.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map(id => document.getElementById(id)).filter(Boolean).map(label => label.textContent).join(' ');
    const labels = element.labels ? [...element.labels].map(label => label.textContent).join(' ') : '';
    return compact(element.getAttribute('aria-label') || labelled || labels || element.getAttribute('title') || (!element.matches('input,textarea,select,[contenteditable]') ? element.textContent : ''));
  };
  const visible = element => {
    const bounds = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return bounds.width > 0 && bounds.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  };
  const enabled = element => !element.matches(':disabled') && element.getAttribute('aria-disabled') !== 'true' && !element.readOnly;
  const evidence = element => ({tagName: element.tagName.toLowerCase(), role: role(element), roleSource: 'dom-role-hint', name: name(element), nameSource: 'dom-label-hint', visible: visible(element), enabled: enabled(element), ...(element.matches('input,textarea,select') && !sensitive(element) ? {hasValue: Boolean(element.value)} : {})});
  function locate(item) {
    const control = item.capture.control || {};
    const raw = Array.isArray(control.locatorCandidates) && control.locatorCandidates.length ? control.locatorCandidates : control.selectors || [];
    const candidates = Array.isArray(raw) ? raw.slice(0, 20) : [];
    const observations = [];
    for (const candidate of candidates) {
      if (!candidate || typeof candidate !== 'object') continue;
      const strategy = candidate.strategy || candidate.type || 'css';
      const value = candidate.value || candidate.selector;
      let matches;
      const locator = strategy === 'role' ? {strategy, role: candidate.role, name: candidate.name} : {strategy, selector: value};
      try {
        if (strategy === 'css' && typeof value === 'string' && value.length <= 1500) matches = [...document.querySelectorAll(value)];
        else if (strategy === 'id' && typeof value === 'string' && value.length <= 1500) { const element = document.getElementById(value); matches = element ? [element] : []; }
        else if (strategy === 'role' && typeof candidate.role === 'string' && typeof candidate.name === 'string') matches = [...document.querySelectorAll('button,a,input,textarea,select,[role]')].filter(element => role(element) === candidate.role && name(element) === candidate.name);
        else { observations.push({locator, status: 'invalid-locator'}); continue; }
      } catch (_) { observations.push({locator, status: 'invalid-locator'}); continue; }
      observations.push({locator, matchCount: matches.length});
      if (matches.length !== 1) continue;
      const element = matches[0];
      if (control.tagName && element.tagName.toLowerCase() !== String(control.tagName).toLowerCase()) {
        observations[observations.length - 1].status = 'tag-mismatch'; continue;
      }
      if (sensitive(element)) return {captureId: item.captureId, status: 'sensitive', locator, matchCount: 1, candidateEvidence: observations};
      return {captureId: item.captureId, status: 'located', locator, matchCount: 1, ...evidence(element), candidateEvidence: observations, element};
    }
    const status = observations.some(item => item.matchCount > 1) ? 'ambiguous' : observations.some(item => typeof item.matchCount === 'number') ? 'not-found' : 'invalid-locator';
    return {captureId: item.captureId, status, matchCount: observations.length ? observations[observations.length - 1].matchCount : 0, candidateEvidence: observations};
  }
  const publicControl = located => { const {element, ...result} = located; return result; };
  try {
    if (location.href !== task.expectedLiveUrl) return blocked('page-changed', '检查后页面路由已经变化，未操作控件。');
    if (task.operation === 'inspect') return {status: 'completed', actionAttempted: false, controls: task.captures.map(item => publicControl(locate(item)))};
    if (task.operation === 'read-page') {
      const chunks = []; let total = 0; let truncated = false;
      const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const parent = walker.currentNode.parentElement;
        if (!parent || parent.closest('script,style,noscript,template,input,textarea,select,option,[contenteditable],#__cdp_control_picker_root__,[hidden]') || !visible(parent)) continue;
        const value = compact(walker.currentNode.textContent, 3001);
        if (!value) continue;
        if (total + value.length + 1 > 3000) { chunks.push(value.slice(0, Math.max(0, 3000 - total - 1))); truncated = true; break; }
        chunks.push(value); total += value.length + 1;
      }
      return {status: 'completed', actionAttempted: false, evidence: {text: chunks.join('\n').slice(0, 3000), truncated}};
    }
    const item = task.captures.find(item => item.captureId === task.captureId);
    if (!item) return blocked('capture-not-found', '控件不在本包范围内。');
    const found = locate(item);
    if (found.status !== 'located') return blocked('control-not-located', '控件未通过唯一定位或敏感字段检查。', {controls: [publicControl(found)]});
    if (!found.visible || (task.action !== 'read' && !found.enabled)) return blocked('control-unavailable', '控件不可见或当前不可操作。', {controls: [publicControl(found)]});
    const element = found.element;
    const action = task.action;
    const allowed = (item.capture.annotation || {}).allowedActions || [];
    if (!allowed.includes(action)) return blocked('action-not-allowed', '控件标注没有允许此动作。');
    const result = {action, captureId: item.captureId, actionDispatched: false, control: publicControl(found)};
    if (action === 'read') {
      result.text = element.matches('input,textarea,select,[contenteditable]') ? '' : compact(element.innerText || element.textContent, 1500);
      return {status: 'completed', actionAttempted: false, evidence: result};
    }
    if (action === 'fill') {
      if (!element.matches('input,textarea') || typeof task.value !== 'string') return blocked('invalid-fill', '只支持已明确参数的普通输入框。');
      const descriptor = Object.getOwnPropertyDescriptor(element.tagName.toLowerCase() === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value');
      if (!descriptor || !descriptor.set) return blocked('unsupported-fill', '页面输入框缺少标准值设置接口。');
      result.hadValueBefore = Boolean(element.value);
      result.overwroteDifferentValue = Boolean(element.value) && element.value !== task.value;
      attempted = true; descriptor.set.call(element, task.value);
      element.dispatchEvent(new Event('input', {bubbles: true})); element.dispatchEvent(new Event('change', {bubbles: true}));
      result.valueMatchesRequested = element.value === task.value;
    } else if (action === 'select') {
      if (!(element instanceof HTMLSelectElement) || typeof task.value !== 'string' || ![...element.options].some(option => option.value === task.value && !option.disabled)) return blocked('invalid-select', '所选项不存在、不可用或参数不明确。');
      attempted = true; Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(element, task.value);
      element.dispatchEvent(new Event('input', {bubbles: true})); element.dispatchEvent(new Event('change', {bubbles: true}));
      result.selectionMatchesRequested = element.value === task.value;
    } else if (action === 'check') {
      if (!element.matches('input[type="checkbox"],input[type="radio"]') || typeof task.value !== 'boolean' || (element.type === 'radio' && task.value === false && element.checked)) return blocked('invalid-check', '勾选需要可用的原生选项和明确状态。');
      if (element.checked !== task.value) { attempted = true; element.click(); }
      result.checkedMatchesRequested = element.checked === task.value;
    } else if (action === 'click') {
      if (typeof element.click !== 'function') return blocked('unsupported-click', '控件不支持标准前端点击。');
      attempted = true; element.click();
    } else return blocked('unsupported-action', '不支持此动作。');
    result.actionDispatched = attempted;
    return {status: 'completed', actionAttempted: attempted, evidence: result, businessSuccessVerified: false};
  } catch (_) {
    return {status: 'failed', actionAttempted: attempted, error: {code: 'page-operation-failed', message: '页面检查或操作中断；未自动重试，不能据此判断业务成功。'}};
  }
  };
  const answer = execute();
  answer._pageLocation = {url: location.href, title: document.title};
  return answer;
}
