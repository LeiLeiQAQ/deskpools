const $ = selector => document.querySelector(selector);
let token = sessionStorage.getItem('dib-admin-token') || '';
let releases = [];
let offset = 0;
const pageSize = 25;

function showStatus(message, type = 'ok') {
  const node = $('#admin-status');
  node.hidden = false; node.className = `status-message ${type}`; node.textContent = message;
}
function node(tag, content, className = '') {
  const element = document.createElement(tag); element.textContent = content; element.className = className; return element;
}
function action(label, callback) {
  const button = node('button', label, 'small-button'); button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await callback(); } catch (error) { showStatus(error.message, 'error'); }
    finally { button.disabled = false; }
  }); return button;
}
function lockConsole() {
  $('#admin-console').hidden = true; $('#logout').hidden = true; $('#login-form').hidden = false;
}
async function api(path, payload) {
  const options = { headers: { Accept: 'application/json', 'X-Admin-Token': token }, cache: 'no-store' };
  if (payload !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(payload); }
  const response = await fetch(path, options);
  const data = await response.json();
  if (response.status === 401) { sessionStorage.removeItem('dib-admin-token'); lockConsole(); }
  if (!response.ok) throw new Error(response.status === 401 ? '管理员令牌不正确，请重新登录。' : data.error || `请求失败 (${response.status})`);
  return data;
}
function date(value) { return new Date(value).toLocaleString('zh-CN', { hour12: false }); }
function seconds(ms) { return `${(ms / 1000).toFixed(2)} 秒`; }
async function refreshOverview() {
  const data = await api('/api/admin/overview');
  $('#stat-players').textContent = data.ranking.players;
  $('#stat-scores').textContent = data.ranking.scores;
  $('#stat-best').textContent = data.ranking.best_avg_ms == null ? '暂无成绩' : seconds(data.ranking.best_avg_ms);
  $('#stat-version').textContent = data.current_release ? `v${data.current_release.version}` : '未发布';
}
async function refreshDownloads() {
  const data = await api('/api/admin/downloads');
  const number = value => new Intl.NumberFormat('zh-CN').format(value);
  $('#download-total').textContent = number(data.total_clicks);
  $('#download-today').textContent = number(data.today_clicks);
  $('#download-week').textContent = number(data.last_7_days);
  $('#download-month').textContent = number(data.last_30_days);
  $('#download-updated').textContent = `按北京时间统计 · ${data.date} · 刷新于 ${new Date().toLocaleTimeString('zh-CN')}`;
  const versions = $('#download-versions'); versions.replaceChildren();
  data.versions.forEach(item => {
    const row = document.createElement('tr');
    row.append(node('td', `v${item.version}`), node('td', number(item.clicks)), node('td', number(item.today_clicks)));
    versions.append(row);
  });
  const daily = $('#download-daily'); daily.replaceChildren();
  [...data.daily].reverse().forEach(item => {
    const row = document.createElement('tr');
    row.append(node('td', item.date), node('td', number(item.clicks))); daily.append(row);
  });
  const labels = { hero: '官网首页 Windows 按钮', release: '官网版本下载按钮', direct: '直接访问本站下载入口', windows: 'Windows 下载卡片', macos: 'Mac DMG 下载卡片' };
  const sources = $('#download-sources'); sources.replaceChildren();
  data.sources.forEach(item => {
    const row = document.createElement('tr');
    row.append(node('td', labels[item.source] || item.source), node('td', number(item.clicks))); sources.append(row);
  });
  if (!data.sources.length) {
    const row = document.createElement('tr'); const cell = node('td', '暂无下载点击，统计从功能启用时开始。');
    cell.colSpan = 2; row.append(cell); sources.append(row);
  }
}
async function refreshReleases() {
  const data = await api('/api/releases?limit=50'); releases = data.releases;
  const target = $('#admin-releases'); target.replaceChildren();
  releases.forEach(release => {
    const row = node('div', '', 'console-release-row');
    const info = node('div', '', 'console-release-info');
    info.append(node('strong', `v${release.version}${release.current ? ' · 当前版本' : ''}`), node('span', release.title), node('small', date(release.published_at)));
    const buttons = node('div', '', 'row-actions');
    buttons.append(action('编辑', () => editRelease(release)));
    if (!release.current) buttons.append(action('设为当前版本', async () => {
      await api('/api/admin/releases/current', { version: release.version });
      await Promise.all([refreshReleases(), refreshOverview()]); showStatus(`当前下载版本已切换为 v${release.version}`);
    }));
    row.append(info, buttons); target.append(row);
  });
  if (!releases.length) target.append(node('p', '暂无发布版本。', 'field-help'));
  $('#flags-preview').textContent = JSON.stringify(data.features, null, 2);
  $('#flags-json').value = JSON.stringify(data.features, null, 2);
}
function editRelease(release) {
  $('#release-version').value = release.version; $('#release-title').value = release.title;
  $('#release-url').value = release.download_url; $('#release-notes').value = release.notes;
  $('#release-features').value = release.features.join('\n'); $('#release-current').checked = release.current;
  $('#release-file').value = ''; $('#release-form').scrollIntoView({ behavior: 'smooth' });
}
async function refreshFiles() {
  const data = await api('/api/admin/files'); const target = $('#package-files'); target.replaceChildren();
  data.files.forEach(file => {
    const row = node('div', '', 'package-row');
    row.append(node('strong', file.name), node('small', `${(file.size / 1024 / 1024).toFixed(2)} MB`), action('使用此文件', () => { $('#release-url').value = file.download_url; }));
    target.append(row);
  });
  if (!data.files.length) target.append(node('p', '还没有安装包，上传后即可在这里选择。', 'field-help'));
}
async function uploadPackage() {
  const file = $('#release-file').files[0]; if (!file) throw new Error('请先选择 .exe、.msi 或 .zip 安装包。');
  const button = $('#upload-package'); button.disabled = true; $('#publish-release').disabled = true;
  $('#upload-status').textContent = `正在上传 ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)…`;
  try {
    const response = await fetch('/api/admin/uploads', { method: 'POST', headers: { 'X-Admin-Token': token, 'X-Filename': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream' }, body: file });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '安装包上传失败');
    $('#release-url').value = data.download_url;
    $('#upload-status').textContent = `上传完成 · ${(data.size / 1024 / 1024).toFixed(2)} MB · SHA-256: ${data.sha256}`;
    $('#release-file').value = ''; await refreshFiles(); showStatus('安装包上传完成，可以发布版本。');
  } catch (error) { $('#upload-status').textContent = `上传失败：${error.message}`; throw error; }
  finally { button.disabled = false; $('#publish-release').disabled = false; }
}
async function refreshRankings() {
  const data = await api(`/api/admin/ranking?limit=${pageSize}&offset=${offset}`);
  const target = $('#ranking-rows'); target.replaceChildren();
  data.entries.forEach(score => {
    const row = document.createElement('tr');
    const device = node('td', '');
    const computer = score.source === 'generated';
    device.append(node('strong', computer ? score.display_name : (score.ip_address || '历史 IP 未记录')),
      node('small', computer ? `${score.skill_label} · 电脑对手` : (score.os_name || '历史系统未记录')), node('small', date(score.created_at)));
    device.title = score.device_id;
    const actions = node('td', '', 'row-actions');
    actions.append(action(score.hidden ? '恢复成绩' : '隐藏成绩', async () => {
      await api('/api/admin/ranking/score', { score_id: score.id, hidden: !score.hidden }); await Promise.all([refreshRankings(), refreshOverview()]);
    }), action(score.disabled ? '启用设备' : '停用设备', async () => {
      await api('/api/admin/ranking/device', { device_id: score.device_id, disabled: !score.disabled }); await Promise.all([refreshRankings(), refreshOverview()]);
    }));
    row.append(device, node('td', `${seconds(score.avg_ms)}/颗`), node('td', `${seconds(score.total_ms)} / ${score.icon_count} 颗 / ${score.shots} 杆`), node('td', score.disabled ? '设备已停用' : score.hidden ? '已隐藏' : '有效'), actions);
    target.append(row);
  });
  if (!data.entries.length) { const row = document.createElement('tr'); const cell = node('td', '暂无成绩，玩家开启全球排名并完成清台后会显示在这里。'); cell.colSpan = 5; row.append(cell); target.append(row); }
  $('#rank-page').textContent = `共 ${data.total} 条 · 第 ${Math.floor(offset / pageSize) + 1} 页`;
  $('#rank-prev').disabled = offset === 0; $('#rank-next').disabled = offset + pageSize >= data.total;
}
async function unlock() {
  await refreshOverview(); sessionStorage.setItem('dib-admin-token', token);
  $('#login-form').hidden = true; $('#admin-console').hidden = false; $('#logout').hidden = false; $('#admin-token').value = '';
  await Promise.all([refreshReleases(), refreshFiles(), refreshRankings(), refreshDownloads()]);
}
function run(task) { return () => task().catch(error => showStatus(error.message, 'error')); }
document.addEventListener('DOMContentLoaded', () => {
  $('#login-form').addEventListener('submit', async event => {
    event.preventDefault(); token = $('#admin-token').value.trim();
    try { await unlock(); $('#admin-status').hidden = true; } catch (error) { showStatus(error.message, 'error'); }
  });
  $('#logout').addEventListener('click', () => { token = ''; sessionStorage.removeItem('dib-admin-token'); lockConsole(); });
  document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('[data-tab]').forEach(item => { if (item === button) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current'); });
    document.querySelectorAll('.console-section').forEach(section => { section.hidden = section.id !== `tab-${button.dataset.tab}`; });
    if (button.dataset.tab === 'downloads') run(refreshDownloads)();
  }));
  $('#upload-package').addEventListener('click', run(uploadPackage));
  $('#release-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = $('#publish-release'); button.disabled = true;
    try {
      const release = await api('/api/admin/releases', { version: $('#release-version').value.trim(), title: $('#release-title').value.trim(), download_url: $('#release-url').value.trim(), notes: $('#release-notes').value, features: $('#release-features').value.split('\n').map(item => item.trim()).filter(Boolean), current: $('#release-current').checked });
      await Promise.all([refreshReleases(), refreshOverview()]); showStatus(`v${release.version} 已发布，官网已同步更新。`);
    } catch (error) { showStatus(error.message, 'error'); } finally { button.disabled = false; }
  });
  $('#new-release').addEventListener('click', () => { $('#release-form').reset(); $('#upload-status').textContent = '请选择安装包并上传，或输入已有的 HTTPS 下载链接。'; });
  $('#flags-form').addEventListener('submit', async event => {
    event.preventDefault(); try { const flags = JSON.parse($('#flags-json').value); await api('/api/admin/features', flags); await refreshReleases(); showStatus('官网功能状态已保存。'); } catch (error) { showStatus(error.message, 'error'); }
  });
  $('#refresh-releases').addEventListener('click', run(refreshReleases)); $('#refresh-files').addEventListener('click', run(refreshFiles)); $('#refresh-rankings').addEventListener('click', run(refreshRankings));
  $('#refresh-downloads').addEventListener('click', run(refreshDownloads));
  $('#rank-prev').addEventListener('click', run(async () => { offset = Math.max(0, offset - pageSize); await refreshRankings(); }));
  $('#rank-next').addEventListener('click', run(async () => { offset += pageSize; await refreshRankings(); }));
  if (token) unlock().catch(error => showStatus(error.message, 'error'));
});
