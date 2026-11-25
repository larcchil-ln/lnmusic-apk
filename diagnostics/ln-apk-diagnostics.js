(function () {
  if (location.hostname !== 'localhost') return;
  var logs = [];
  try { logs = JSON.parse(localStorage.getItem('ln_apk_diagnostics') || '[]'); } catch (_) {}
  var badge, panel;
  function record(type, message) {
    logs.push('[' + type + '] ' + String(message || 'Unknown').slice(0, 450));
    if (logs.length > 25) logs.shift();
    try { localStorage.setItem('ln_apk_diagnostics', JSON.stringify(logs)); } catch (_) {}
    render();
  }
  function render() {
    if (!document.body) return;
    if (!badge) {
      badge = document.createElement('button'); badge.type = 'button'; badge.textContent = 'APK log';
      badge.style.cssText = 'position:fixed;right:8px;top:8px;z-index:2147483647;padding:7px 10px;border:1px solid #f59e0b;border-radius:9px;background:#241b0a;color:white;font:12px system-ui';
      badge.onclick = function () { panel.style.display = panel.style.display === 'none' ? 'block' : 'none'; };
      document.body.appendChild(badge);
      panel = document.createElement('pre');
      panel.style.cssText = 'display:none;position:fixed;inset:48px 8px 90px;z-index:2147483646;overflow:auto;white-space:pre-wrap;word-break:break-word;padding:12px;border:1px solid #f59e0b;border-radius:10px;background:#09090b;color:white;font:11px monospace';
      document.body.appendChild(panel);
    }
    badge.textContent = 'APK log (' + logs.length + ')';
    panel.textContent = logs.length ? logs.join('\\n\\n') : 'Belum ada error tertangkap.';
  }
  window.addEventListener('error', function (e) {
    var t = e.target;
    if (t && t !== window) record('RESOURCE', (t.src || t.href || t.tagName) + ' gagal dimuat');
    else record('JS', (e.message || 'Script error') + ' @ ' + (e.filename || '') + ':' + (e.lineno || 0));
  }, true);
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason; record('PROMISE', r && (r.stack || r.message) || r);
  });
  var f = window.fetch;
  if (f) window.fetch = function () {
    var x = arguments[0], url = typeof x === 'string' ? x : x && x.url;
    return f.apply(this, arguments).catch(function (e) { record('FETCH', url + ' — ' + (e && e.message || e)); throw e; });
  };
  var xo = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u) { this.__lnUrl = u; return xo.apply(this, arguments); };
  var xs = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    this.addEventListener('error', function () { record('XHR', this.__lnUrl + ' gagal dimuat'); });
    return xs.apply(this, arguments);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render, { once: true }); else render();
})();