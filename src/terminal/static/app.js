'use strict';
const base = '/app/dream-terminal';
const $ = id => document.getElementById(id);
let token = '', term, fit, connected = false, offset = 0, seq = 0, generation = 0;
let inputQueue = Promise.resolve(), pendingBytes = 0, inputFailed = false, ctrl = false, fontSize = 15;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function api(action, body = {}) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`${base}/api/${action}`, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Dream-Terminal': '1', ...(token ? {'X-Dream-Session': token} : {})},
      body: JSON.stringify(body), signal: controller.signal, cache: 'no-store'});
    // Some reverse proxies change the response Content-Type. Validate JSON
    // itself; never interpret an HTML error page as an expired application token.
    const raw = await response.text();
    let result;
    try {result = JSON.parse(raw);} catch (_) {result = null;}
    if (!result || typeof result !== 'object' || Array.isArray(result)) {
      const code = response.status;
      let message;
      if (response.redirected) message = '接口被重定向到其他页面，请检查飞牛网关登录状态和应用入口。';
      else if (code === 401 || code === 403) message = '访问被拒绝，请检查飞牛登录状态和应用访问权限。';
      else if (code === 404) message = '找不到终端接口，请检查飞牛统一网关的应用路径。';
      else if (code >= 500) message = '终端服务或飞牛网关返回服务器错误，刷新登录不会修复此问题。';
      else message = '接口返回了非 JSON 内容，暂时无法判断原因。';
      const err = new Error(`${message}（HTTP ${code}，${action}）`);
      err.status = code; throw err;
    }
    if (!response.ok) {const err = new Error(result.error || '请求失败。'); err.status = response.status; throw err;}
    if (action === 'login' && (typeof result.token !== 'string' || result.token.length < 32)) {
      throw new Error('解锁接口返回内容不完整，请检查飞牛网关转发。');
    }
    if (action === 'connect' && result.ok !== true) {
      throw new Error(`连接接口返回内容异常，请检查飞牛网关转发。（HTTP ${response.status}，connect）`);
    }
    return result;
  } finally {clearTimeout(timer);}
}
function status(message, live = false) {$('state').textContent = message; $('state').classList.toggle('live', live);}
function showWelcome() {
  connected = false; generation++;
  $('workspace').hidden = true; $('welcome').hidden = false; document.body.classList.remove('in-terminal');
  $('unlock-form').hidden = !!token; $('connect-form').hidden = !token;
  status(token ? '已解锁' : '尚未连接');
}
function showSessions(result) {
  $('old-sessions').hidden = !result.own_connections;
  $('sessions-note').textContent = `本账号还有 ${result.own_connections || 0} 个 SSH 连接；刷新页面不会立刻关闭远端连接。`;
}
$('unlock-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('error').textContent = '';
  try {const result = await api('login', {password: $('password').value}); token = result.token;
    $('password').value = ''; $('username').value = result.username; showSessions(result); showWelcome(); $('username').focus();
  } catch (error) {$('error').textContent = error.message;} finally {button.disabled = false;}
});
$('close-others').onclick = async () => {
  if (!confirm('关闭本账号已有的终端连接？这些页面将断开，SSH 前台任务可能结束。')) return;
  const button = $('close-others'); button.disabled = true;
  try {const result = await api('close_others'); showSessions(result); $('error').textContent = '';}
  catch (error) {$('error').textContent = error.message;}
  finally {button.disabled = false;}
};
function resize() {
  if (!term || $('workspace').hidden) return;
  fit.fit();
  const cols = Math.max(20, Math.min(300, term.cols)), rows = Math.max(5, Math.min(150, term.rows));
  if (connected) api('resize', {cols, rows}).catch(() => {});
}
function createTerminal() {
  if (term) term.dispose();
  term = new Terminal({fontSize, fontFamily: '"Cascadia Mono", "DejaVu Sans Mono", monospace',
    cursorBlink: true, scrollback: 3000, allowProposedApi: false,
    theme: {background: '#0c151c', foreground: '#d5e7ef', cursor: '#83f2d6', selectionBackground: '#32576b',
      black: '#12222d', red: '#ff9292', green: '#83e7bc', yellow: '#eace89', blue: '#8bbfff', magenta: '#ceafff', cyan: '#79dbe7', white: '#edf4fa'}});
  fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open($('terminal')); fit.fit();
  // No link/clipboard addons: terminal escape output never gets HTML or clipboard privileges.
  term.onData(data => {
    if (ctrl && data.length === 1) {const c = data.toUpperCase().charCodeAt(0); if (c >= 64 && c <= 95) data = String.fromCharCode(c - 64);}
    ctrl = false; $('ctrl').setAttribute('aria-pressed', 'false'); enqueue(data);
  });
}
$('connect-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $('error').textContent = '';
  try {
    $('welcome').hidden = true; $('workspace').hidden = false; document.body.classList.add('in-terminal');
    createTerminal();
    await api('connect', {username: $('username').value.trim(), cols: Math.max(20, Math.min(300, term.cols)), rows: Math.max(5, Math.min(150, term.rows))});
    connected = true; offset = 0; seq = 0; inputFailed = false; pendingBytes = 0; inputQueue = Promise.resolve();
    const current = ++generation; $('connection').textContent = `${$('username').value.trim()} · 本机 SSH`;
    $('notice').textContent = '请在终端内输入 NAS 的 SSH 密码，输入时不会显示字符。';
    status('连接中', true); term.focus(); poll(current);
  } catch (error) {
    if (error.status === 401) token = '';
    showWelcome(); $('error').textContent = error.message;
    if (token) api('sessions').then(showSessions).catch(() => {});
  }
  finally {button.disabled = false;}
});
async function poll(current) {
  let failures = 0;
  while (connected && generation === current) {
    try {
      const result = await api('read', {offset});
      if (generation !== current) return;
      if (result.truncated) term.write('\r\n[离线期间输出过多，已跳过较早内容]\r\n');
      if (result.data) {
        const bytes = Uint8Array.from(atob(result.data), c => c.charCodeAt(0));
        await new Promise(resolve => term.write(bytes, resolve));
      }
      offset = result.offset;
      if (result.ended) {
        connected = false; status('连接已结束');
        $('notice').textContent = 'SSH 已退出。连接被拒绝时，请检查飞牛 SSH 开关与端口；点击“断开”可重新连接。'; return;
      }
      status('终端已打开', true);
      if (failures && !inputFailed) $('notice').textContent = '网络已恢复，继续当前终端。';
      failures = 0;
    } catch (error) {
      if (generation !== current) return;
      if (error.status === 401 || error.status === 403) {
        connected = false; token = ''; status('请重新登录'); $('notice').textContent = error.message; return;
      }
      if (error.status === 400 || error.status === 404) {connected = false; status('连接已结束'); $('notice').textContent = error.message; return;}
      failures++; status('正在重连'); $('notice').textContent = '网络暂时中断，正在恢复当前会话…';
    }
    await sleep(failures ? 2000 : 250);
  }
}
function enqueue(data) {
  if (!connected || inputFailed) return;
  const size = new TextEncoder().encode(data).length;
  if (size > 4096 || pendingBytes + size > 16384) {$('notice').textContent = '输入过长，请分段发送（每次不超过 4096 字节）。'; return;}
  const current = generation; pendingBytes += size;
  inputQueue = inputQueue.then(async () => {
    if (!connected || generation !== current || inputFailed) return;
    const number = ++seq;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {await api('write', {data, seq: number}); return;}
      catch (error) {
        if (generation !== current) return;
        if (error.status || attempt === 2) {
          inputFailed = true; $('notice').textContent = '无法确认输入是否送达，已停止继续发送。请断开后重新连接，避免重复执行命令。'; return;
        }
        await sleep(1000);
      }
    }
  }).finally(() => {if (generation === current) pendingBytes -= size;});
}
async function leave(lock) {
  // Stop queuing input before closing the remote terminal.
  connected = false; generation++;
  try {if (token) await api(lock ? 'logout' : 'disconnect');}
  catch (error) {
    $('notice').textContent = '暂时无法确认远端关闭，闲置超时后会自动清理。';
    if (!lock) {status('关闭未确认'); return;}
  }
  if (lock) token = '';
  showWelcome();
}
$('disconnect').onclick = () => leave(false);
$('lock').onclick = () => leave(true);
$('ctrl').onclick = () => {ctrl = !ctrl; $('ctrl').setAttribute('aria-pressed', String(ctrl)); term?.focus();};
const keys = {esc: '\x1b', tab: '\t', interrupt: '\x03', up: '\x1b[A', down: '\x1b[B', left: '\x1b[D', right: '\x1b[C', enter: '\r'};
document.querySelectorAll('[data-key]').forEach(button => {button.onclick = () => {enqueue(keys[button.dataset.key]); term?.focus();};});
$('smaller').onclick = () => {fontSize = Math.max(12, fontSize - 1); term.options.fontSize = fontSize; resize();};
$('larger').onclick = () => {fontSize = Math.min(24, fontSize + 1); term.options.fontSize = fontSize; resize();};
$('paste').onclick = () => {$('paste-text').value = ''; $('paste-dialog').showModal(); $('paste-text').focus();};
$('paste-send').onclick = () => {
  const text = $('paste-text').value;
  if (new TextEncoder().encode(text).length > 4000) {alert('请分段粘贴，每次最多 4000 字节。'); return;}
  term.paste(text); $('paste-text').value = ''; $('paste-dialog').close(); term.focus();
};
$('copy').onclick = async () => {
  try {const text = term.getSelection(); if (!text) throw new Error('请先选中需要复制的终端内容。');
    await navigator.clipboard.writeText(text); $('notice').textContent = '已复制选中内容。';
  } catch (error) {$('notice').textContent = error.message || '请使用系统复制菜单。';}
};
$('diagnostics').onclick = () => $('commands-dialog').showModal();
const commands = {
  ipv6: 'ip -6 addr show scope global; ip -6 route show default',
  ports: 'ss -lnt',
  sensors: 'for h in /sys/class/hwmon/hwmon*; do echo "=== $h ==="; cat "$h/name" "$h"/temp*_input "$h"/fan*_input 2>/dev/null; done',
  services: 'systemctl status nas-display-fnos.service nas-display-fnos-fan.service --no-pager -l'
};
document.querySelectorAll('[data-command]').forEach(button => {button.onclick = () => {term.paste(commands[button.dataset.command]); $('commands-dialog').close(); term.focus();};});
let resizeTimer;
function scheduleResize() {clearTimeout(resizeTimer); resizeTimer = setTimeout(resize, 180);}
window.addEventListener('resize', scheduleResize);
window.visualViewport?.addEventListener('resize', () => {
  if (document.body.classList.contains('in-terminal')) $('workspace').style.height = Math.max(260, window.visualViewport.height - 125) + 'px';
  scheduleResize();
});
window.addEventListener('pagehide', () => {
  if (token) fetch(`${base}/api/logout`, {method: 'POST', credentials: 'same-origin', keepalive: true,
    headers: {'Content-Type': 'application/json', 'X-Dream-Terminal': '1', 'X-Dream-Session': token}, body: '{}'}).catch(() => {});
});
