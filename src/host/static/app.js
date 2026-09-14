'use strict';
const $ = id => document.getElementById(id);
let csrf = '', configuration = null, currentStatus = null, authenticated = false, polling = false;
let toastTimer;
let fanBusy = false, fanPolling = false, fanRevision = 0, fanState = null, fanSubmitError = '';
function createFanForm(ch) {
  const form = document.createElement('form'); form.className = 'fan-adjust'; form.dataset.channel = ch;
  form.innerHTML = '<div class="section-heading"><h3></h3><strong data-field="rpm">-- RPM</strong></div><p data-field="actual" class="footnote"></p><div class="form-grid"><div><label data-for="mode">模式</label><select data-field="mode"><option value="auto">自动</option><option value="manual">手动</option></select></div><div><label data-for="value">风扇速度 · %</label><input data-field="value" type="number" min="0" max="100" step="1" value="" placeholder="自动调节" required disabled></div></div><p data-field="note" class="footnote"></p><button class="secondary" type="submit">应用设置</button>';
  for (const element of form.querySelectorAll('[data-field]')) element.id = ch+'-'+element.dataset.field;
  for (const label of form.querySelectorAll('[data-for]')) label.htmlFor = ch+'-'+label.dataset.for;
  bindFanForm(form); return form;
}
function renderFanControl(data) {
  fanState = data;
  $('fan-adjust-panel').hidden = !data;
  if (!data) return;
  $('fan-adjust-error').textContent = fanBusy ? '正在应用并读回验证…' : fanSubmitError || data.error || data.note || '';
  $('fan-adjust-forms').hidden = !data.available;
  const items = (data.channels || []).filter(i => /^(pwm[23]|hwmon-[a-f0-9]{16}-pwm[1-9][0-9]?)$/.test(i.channel));
  const ids = new Set(items.map(i => i.channel));
  for (const form of Array.from($('fan-adjust-forms').children)) if (!ids.has(form.dataset.channel)) form.remove();
  for (const item of items) {
    const ch = item.channel;
    let form = Array.from($('fan-adjust-forms').children).find(f => f.dataset.channel === ch);
    if (!form) { form = createFanForm(ch); $('fan-adjust-forms').append(form); }
    form.querySelector('h3').textContent = item.label || (item.fan || ch)+' · '+ch;
    $(ch+'-rpm').textContent = (item.rpm == null ? '--' : item.rpm) + ' RPM';
    $(ch+'-actual').textContent = (item.mode === 'auto' ? '当前自动模式' : item.mode === 'curve' ? '温度曲线 · '+(item.speed_percent ?? '--')+'%' : item.mode === 'manual' && item.speed_percent != null ? '当前手动 · '+item.speed_percent+'%' : '状态不可用') + (item.alarm ? ' · 风扇报警' : '') + ((item.mode === 'curve' ? item.curve_temperature : item.temperature_c) == null ? '' : ' · 温度 '+(item.mode === 'curve' ? item.curve_temperature : item.temperature_c)+' °C');
    $(ch+'-note').textContent = item.curve_error || item.note || '';
    if (data.curve_presets && !$(ch+'-mode').querySelector('[value=curve]')) $(ch+'-mode').add(new Option('温度曲线','curve'));
    $(ch+'-mode').querySelector('[value="auto"]').disabled = item.can_auto === false;
    if (!form.dataset.edited) { $(ch+'-mode').value = item.mode || 'manual'; $(ch+'-value').value = item.mode === 'manual' ? (item.speed_percent ?? '') : ''; }
    $(ch+'-mode').disabled = !item.can_set || fanBusy;
    $(ch+'-value').disabled = !item.can_set || fanBusy || $(ch+'-mode').value !== 'manual';
    form.querySelector('button[type=submit]').disabled = !item.can_set || fanBusy;
    renderCurveEditor(form,item,data);
  }
}
async function pollFans() {
  if (!authenticated || fanPolling || fanBusy) return;
  fanPolling = true; const revision = fanRevision;
  try { const result = await api('fans'); if (!fanBusy && revision === fanRevision) renderFanControl(result); } catch(e) { $('fan-adjust-error').textContent = e.message; }
  finally { fanPolling = false; }
}
function bindFanForm(form) {
  const ch = form.dataset.channel;
  form.addEventListener('input', e => {
    form.dataset.edited = '1'; const mode = $(ch+'-mode'), value = $(ch+'-value');
    if (e.target === mode) value.value = mode.value === 'manual' ? (value.value || '50') : '';
    value.disabled = mode.value !== 'manual';
    if (e.target === mode) renderFanControl(fanState);
  });
  form.addEventListener('submit', async e => {
    e.preventDefault(); if (fanBusy) return;
    const mode = $(ch+'-mode').value, raw = $(ch+'-value').value, speed_percent = mode === 'manual' ? Number(raw) : null;
    if (mode === 'manual' && (raw.trim() === '' || !Number.isInteger(speed_percent) || speed_percent < 0 || speed_percent > 100)) { toast('风扇速度必须是 0–100 的整数百分比。'); return; }
    fanBusy = true; fanRevision++; fanSubmitError = ''; renderFanControl(fanState);
    try { const result = mode === 'curve' ? await api('fan-curve', {channel:ch,curve:form._curve}) : await api('fans', {channel:ch,mode,speed_percent}); delete form.dataset.edited; renderFanControl(result); toast('设置已应用，请观察实际转速和温度。'); }
    catch(err) { fanSubmitError = err.message; }
    finally { fanBusy = false; renderFanControl(fanState); await pollFans(); }
  });
}
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4500); }
function showLogin() { authenticated = false; $('app-view').hidden = true; $('login-view').hidden = false; }
async function api(path, data) {
  const response = await fetch('/api/' + path, {method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', headers: data === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(8000)});
  const result = await response.json();
  if (!response.ok) { if (response.status === 401 && path !== 'login') showLogin(); throw new Error(result.error || '请求失败，请重试。'); }
  return result;
}
function selectTab(name) {
  ['overview', 'settings', 'account'].forEach(id => $(id).hidden = id !== name);
  document.querySelectorAll('.nav').forEach(n => n.classList.toggle('active', n.dataset.tab === name));
  $('page-title').textContent = {overview: '运行概览', settings: '屏幕与采集', account: '账户设置'}[name];
}
function setOptions(id, values, selected, emptyText) {
  const select = $(id); select.replaceChildren();
  const entries = [{value: '', label: emptyText}, ...values];
  if (selected && !entries.some(o => o.value === selected)) entries.push({value: selected, label: selected + '（当前未检测到）'});
  for (const item of entries) { const option = document.createElement('option'); option.value = item.value; option.textContent = item.label; select.append(option); }
  select.value = selected;
}
function updateCpuOptions(sample, selected, force = false) {
  if (!sample) return;
  const signature = JSON.stringify((sample.cpu_temperature_candidates || []).map(t => [t.id, t.driver, t.label]));
  if (!force && $('cpu-sensor').dataset.channels === signature) return;
  setOptions('cpu-sensor', [{value:'auto', label:'自动（Intel 封装；AMD Tdie / Tctl）'}, ...(sample.cpu_temperature_candidates || []).map(t => ({value:t.id, label:t.driver + ' / ' + t.label + ' · ' + temperature(t.value_c)}))], selected, '不显示 CPU 温度');
  $('cpu-sensor').dataset.channels = signature;
}
async function refreshDevices(preserve = false) {
  const cfg = preserve ? readForm() : configuration;
  const status = await api('status'); currentStatus = status;
  const devices = await api('devices');
  const sample = status.sample || {};
  setOptions('interface', Object.keys(sample.network || {}).sort().map(n => ({value: n, label: n})), cfg.interface, '请选择网卡');
  setOptions('gpu-device', [{value: 'auto', label: '自动识别（随主机硬件适配）'}, ...(sample.gpu || []).map(g => ({value: g.device, label: (g.name || g.vendor || 'GPU') + ' · ' + g.device}))], cfg.gpu_device, '不显示 GPU');
  const disks = (sample.disks || []).map(d => ({value: d.id, label: d.device + ' · ' + d.model}));
  updateCpuOptions(sample, cfg.cpu_sensor ?? 'auto', true);
  setOptions('sys-sensor', [{value:'auto', label:'自动（仅使用明确标为 SYS 的通道）'}, ...(sample.board_temperatures || []).map(t => ({value:t.id, label:t.driver + ' / ' + t.label + ' · ' + temperature(t.temperature_c)}))], cfg.sys_sensor ?? 'auto', '不显示 SYS');
  setOptions('disk1', disks, cfg.disks[0], '不显示 SSD 1'); setOptions('disk2', disks, cfg.disks[1], '不显示 SSD 2');
  setOptions('usb-port', [{value: 'auto', label: '自动识别（仅连接一块 ESP32-S3）'}, ...devices.usb.map(d => ({value: d.path, label: d.label}))], cfg.usb_port, '请选择 USB 设备');
  updateStatus(status);
}
function modeFields() {
  const usb = $('transport').value === 'usb';
  $('usb-fields').hidden = !usb;
  document.querySelectorAll('.udp-field').forEach(n => n.hidden = usb);
}
async function loadConfiguration() {
  configuration = await api('config');
  for (const [id, key] of [['display-ip', 'display_ip'], ['port', 'port'], ['token', 'token'], ['interval', 'interval'], ['transport', 'transport']]) $(id).value = configuration[key];
  $('enabled').checked = configuration.enabled;
  modeFields(); await refreshDevices();
}
function readForm() { return {display_ip: $('display-ip').value.trim(), port: Number($('port').value), token: $('token').value.trim(), interface: $('interface').value, interval: Number($('interval').value), gpu_device: $('gpu-device').value, disks: [$('disk1').value, $('disk2').value], enabled: $('enabled').checked, transport: $('transport').value, usb_port: $('usb-port').value || 'auto', sys_sensor: $('sys-sensor').value, cpu_sensor: $('cpu-sensor').value}; }
const number = (v, digits = 0) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : '--';
const temperature = v => number(v, 1) + (typeof v === 'number' ? ' °C' : '');
function rate(v) { if (typeof v !== 'number') return '--'; let unit = 'B/s'; for (const u of ['KiB/s','MiB/s','GiB/s']) { if (v < 1024) break; v /= 1024; unit = u; } return number(v, v < 100 ? 1 : 0) + ' ' + unit; }
function updateFans(sample, stale) {
  const fans = sample?.fans || [];
  $('fan-count').textContent = stale ? '等待采样' : fans.length + ' 个转速通道';
  $('fan-list').replaceChildren();
  for (const fan of fans) {
    const card = document.createElement('article'), title = document.createElement('h3');
    const speed = document.createElement('strong'), detail = document.createElement('p');
    card.className = 'fan-card'; title.textContent = fan.label;
    speed.textContent = number(stale ? null : fan.rpm) + ' RPM';
    const state = stale ? '等待新数据' : {ok: fan.rpm === 0 ? '读数为零' : '转速已读取', fault: '传感器故障', disabled: '传感器已禁用', unreadable: '读数不可用', not_exposed: '未提供转速反馈'}[fan.status] || '状态未知';
    detail.textContent = fan.driver + ' · ' + fan.hwmon + ' / ' + fan.channel + ' · ' + state + (!stale && fan.alarm === 1 ? ' · 转速报警' : '');
    card.append(title, speed, detail); $('fan-list').append(card);
  }
  $('fan-note').textContent = stale ? '等待风扇采样。' : !fans.length ? '当前内核未暴露风扇转速接口，不能据此判断机器没有风扇。' : '按传感器标签显示；未标注用途的通道不会猜测为 CPU 风扇。0 RPM 表示读数为零，不一定是故障。';
}
const diskStates = {ok:'已读取', sleeping:'休眠 · 不唤醒', stale:'数据过期', pending:'等待 SMART 采集', unavailable:'读取失败', unsupported:'未提供温度', power_unknown:'无法确认电源状态', timeout:'读取超时', tool_missing:'缺少 smartmontools'};
function updateThermals(sample, system, stale) {
  $('sys-value').textContent = temperature(stale ? null : system.temperature_c);
  $('sys-source').textContent = system.id ? system.driver + ' / ' + system.label : system.status === 'disabled' ? '已关闭' : '请在屏幕与采集中选择 SYS 来源';
  $('board-list').replaceChildren();
  for (const t of sample.board_temperatures || []) {
    const p = document.createElement('p');
    p.textContent = t.driver + ' / ' + t.label + '：' + temperature(stale ? null : t.temperature_c) + (t.status !== 'ok' ? ' · 无效或不可用' : '') + (t.alarm ? ' · 传感器报警' : '');
    $('board-list').append(p);
  }
  $('storage-count').textContent = (sample.disks || []).length + ' 块硬盘';
  $('storage-list').replaceChildren();
  for (const d of sample.disks || []) {
    const card = document.createElement('article'), title = document.createElement('h3'), value = document.createElement('strong'), detail = document.createElement('p');
    card.className = 'fan-card'; title.textContent = d.device + ' · ' + d.model;
    value.textContent = temperature(stale ? null : d.temperature_c);
    detail.textContent = d.kind + ' · ' + (stale ? '等待新数据' : diskStates[d.status] || '不可用') + (d.age_seconds != null ? ' · ' + d.age_seconds + ' 秒前' : '');
    card.append(title, value, detail); $('storage-list').append(card);
  }
}
function updateStatus(data) {
  if (configuration) updateCpuOptions(data.sample, $('cpu-sensor').options.length ? $('cpu-sensor').value : (configuration.cpu_sensor ?? 'auto'));
  currentStatus = data;
  const stale = data.sample_age === null || data.sample_age > Math.max(10, data.interval * 3);
  const m = stale ? {} : data.metrics || {};
  updateFans(data.sample, stale);
  const hw = data.hardware_setup;
  $('hardware-setup').hidden = !hw;
  if (hw) {
    $('hardware-state').textContent = {ready:'已就绪',existing_only:'仅现有驱动',unsupported:'待适配',needs_attention:'需要处理',cancelled:'已停止'}[hw.state] || '正在准备';
    $('hardware-message').textContent = hw.message;
    $('hardware-kernel').textContent = hw.kernel ? '内核 ' + hw.kernel : '';
  }
  updateThermals(data.sample || {}, data.system_temperature || {}, stale);
  $('sidebar-host').textContent = data.hostname;
  $('version').textContent = 'Dream · v' + data.version;
  $('poll-state').textContent = stale ? '等待采集' : '实时采集中';
  $('poll-state').className = 'pill' + (stale ? ' warning' : '');
  $('connection-error').hidden = !data.sampling_error;
  $('connection-error').textContent = data.sampling_error || '';
  $('display-error').hidden = !data.display_error;
  $('display-error').textContent = data.display_error ? '屏幕连接：' + data.display_error + ' 网页采集继续运行。' : '';
  $('send-state').textContent = !data.enabled ? '仅网页监控' : data.display_error ? '屏幕未连接 · 自动重试' : data.transport === 'usb' ? (data.receiver_confirmed ? 'USB · 屏幕已确认' : 'USB · 等待确认') : 'Wi-Fi · 正在发送';
  $('send-state').className = 'pill' + (!data.enabled ? ' neutral' : data.display_error ? ' warning' : '');
  $('target').textContent = data.transport === 'usb' ? 'USB-C 直连 · 无需配对' : data.display_ip || '尚未设置屏幕地址';
  $('transport-note').textContent = data.transport === 'usb' ? '只有屏幕返回对应数据包的确认后，才计入发送数量。' : 'UDP 发送无回执；这里的发送状态不代表屏幕已经收到数据。';
  $('gpu-kind').textContent = data.selected_gpu?.vendor || '显卡';
  $('sensor-note').textContent = (data.sensor_notes || []).join('；');
  $('sent-count').textContent = data.sent.toLocaleString();
  $('toggle-send').textContent = data.enabled ? '暂停屏幕发送' : '连接屏幕';
  for (const k of ['cpu', 'gpu', 'mem']) { $(k).textContent = number(m[k]); $(k + '-bar').value = m[k] || 0; }
  for (const k of ['ct', 'gt', 'd1', 'd2']) $(k).textContent = temperature(m[k]);
  for (const k of ['rx', 'tx']) $(k).textContent = rate(m[k]);
  $('interface-label').textContent = data.interface || '未选择网卡';
  const mem = data.sample?.memory;
  $('memory-detail').textContent = !stale && mem?.total_bytes && mem.available_bytes != null ? '已用 ' + number((mem.total_bytes - mem.available_bytes) / 2**30, 1) + ' / ' + number(mem.total_bytes / 2**30, 1) + ' GiB' : '等待采集';
  $('disk1-model').textContent = configuration?.disks[0] || '未选择'; $('disk2-model').textContent = configuration?.disks[1] || '未选择';
  $('sample-time').textContent = data.sample_age === null ? '等待采样' : '采样于 ' + data.sample_age + ' 秒前';
  $('uptime').textContent = '服务已运行 ' + Math.floor(data.uptime_seconds / 3600) + ' 小时 ' + Math.floor(data.uptime_seconds % 3600 / 60) + ' 分钟';
  $('events').replaceChildren();
  for (const item of data.events.slice(0, 4)) { const li = document.createElement('li'), time = document.createElement('time'), message = document.createElement('span'); time.textContent = new Date(item.time * 1000).toLocaleTimeString('zh-CN', {hour12: false}); message.textContent = item.message; li.append(time, message); $('events').append(li); }
}
async function openApp() { authenticated = true; $('login-view').hidden = true; $('app-view').hidden = false; await loadConfiguration(); }
async function sessionState() { const s = await api('session'); csrf = s.csrf; if (s.authenticated) await openApp(); else showLogin(); }
async function poll() {
  if (!authenticated || polling) return;
  polling = true;
  try { updateStatus(await api('status')); }
  catch (e) { $('poll-state').textContent = '连接中断'; $('poll-state').className = 'pill warning'; $('connection-error').hidden = false; $('connection-error').textContent = '无法连接主机，显示的是上次数据。' + e.message; }
  finally { polling = false; }
}
document.querySelectorAll('[data-tab]').forEach(n => n.addEventListener('click', () => selectTab(n.dataset.tab)));
$('login-form').addEventListener('submit', async e => { e.preventDefault(); const button = e.submitter; button.disabled = true; $('login-error').textContent = ''; try { const s = await api('session'); csrf = s.csrf; const result = await api('login', {password: $('login-password').value}); csrf = result.csrf; $('login-password').value = ''; await openApp(); } catch (err) { $('login-error').textContent = err.message; } finally { button.disabled = false; } });
$('logout').addEventListener('click', async () => { try { await api('logout', {}); configuration = null; $('token').value = ''; showLogin(); } catch(e) { toast(e.message); } });
$('account-logout').addEventListener('click', () => $('logout').click());
$('transport').addEventListener('change', modeFields);
$('show-token').addEventListener('click', () => { const reveal = $('token').type === 'password'; $('token').type = reveal ? 'text' : 'password'; $('show-token').textContent = reveal ? '隐藏' : '显示'; });
$('refresh-devices').addEventListener('click', async () => { try { await refreshDevices(true); toast('设备列表已刷新'); } catch(e) { toast(e.message); } });
$('config-form').addEventListener('submit', async e => { e.preventDefault(); const button = e.submitter; button.disabled = true; $('config-error').textContent = ''; try { await api('config', readForm()); configuration = await api('config'); toast('配置已保存'); await poll(); } catch(err) { $('config-error').textContent = err.message; } finally { button.disabled = false; } });
$('toggle-send').addEventListener('click', async () => { $('toggle-send').disabled = true; try { const enabled = !currentStatus.enabled; await api('enabled', {enabled}); configuration.enabled = enabled; $('enabled').checked = enabled; await poll(); toast(enabled ? '已启用发送' : '已暂停发送'); } catch(e) { toast(e.message); selectTab('settings'); } finally { $('toggle-send').disabled = false; } });
$('import-config').addEventListener('change', async e => { const file = e.target.files[0]; if (!file) return; try { if (file.size > 32768) throw new Error('文件过大（上限 32 KB）。'); const cfg = JSON.parse(await file.text()); if (!cfg || Array.isArray(cfg) || typeof cfg !== 'object') throw new Error('配置格式错误。'); const fields = ['display_ip','port','token','interface','interval','gpu_device','disks','enabled','transport','usb_port','sys_sensor','cpu_sensor']; if (Object.keys(cfg).some(k => !fields.includes(k))) throw new Error('文件包含未知配置字段。'); if (typeof cfg.token !== 'string' || !/^[0-9a-f]{32}$/.test(cfg.token) || !Array.isArray(cfg.disks) || cfg.disks.length !== 2 || cfg.disks.some(v => typeof v !== 'string')) throw new Error('配对码或 SSD 配置格式错误。'); const merged = {...configuration, ...cfg, enabled: false, transport: cfg.transport || 'udp'}; for (const [id,key] of [['display-ip','display_ip'],['port','port'],['token','token'],['interval','interval'],['transport','transport']]) $(id).value = merged[key] === 'CHANGE_ME' ? '' : merged[key]; $('enabled').checked = false; for (const [id,value] of [['interface',merged.interface],['gpu-device',merged.gpu_device],['disk1',merged.disks[0]],['disk2',merged.disks[1]],['usb-port',merged.usb_port],['sys-sensor',merged.sys_sensor ?? 'auto'],['cpu-sensor',merged.cpu_sensor ?? 'auto']]) setOptions(id, [], String(value || ''), '未选择'); modeFields(); await refreshDevices(true); toast('旧配置已填入，确认后点击保存。发送默认暂停。'); } catch(err) { toast('导入失败：' + err.message); } finally { e.target.value = ''; } });
$('password-form').addEventListener('submit', async e => { e.preventDefault(); $('password-error').textContent = ''; if ($('new-password').value !== $('confirm-password').value) { $('password-error').textContent = '两次新密码不一致。'; return; } e.submitter.disabled = true; try { await api('password', {current: $('current-password').value, new: $('new-password').value}); e.target.reset(); showLogin(); toast('密码已更新，请重新登录。'); } catch(err) { $('password-error').textContent = err.message; } finally { e.submitter.disabled = false; } });
sessionState().catch(e => $('login-error').textContent = '无法连接管理服务：' + e.message);
setInterval(poll, 2000);
setInterval(pollFans, 3000);
