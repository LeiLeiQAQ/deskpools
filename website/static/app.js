const text = (value, fallback = '') => value == null ? fallback : String(value);

function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(date);
}

function tag(label) {
  const node = document.createElement('span');
  node.className = 'release-tag';
  node.textContent = label;
  return node;
}

function renderLatest(release) {
  const target = document.querySelector('#latest-release');
  const version = document.querySelector('#hero-version');
  const heroDownload = document.querySelector('#hero-download');
  if (!release || !release.version) {
    target.innerHTML = '<div class="loading-block">暂时没有发布版本。</div>';
    version.textContent = '版本信息暂不可用';
    return;
  }
  version.textContent = `Windows 最新版 ${release.version} · ${formatDate(release.published_at)}`;
  const downloadPath = `/download?version=${encodeURIComponent(release.version)}`;
  const cloudDownload = release.download_url.startsWith('https://pan.quark.cn/');
  heroDownload.href = `${downloadPath}&source=hero`;
  heroDownload.textContent = '下载 Windows 版 ↗';
  target.replaceChildren();
  const top = document.createElement('div'); top.className = 'latest-top';
  const title = document.createElement('div');
  const heading = document.createElement('h3'); heading.textContent = text(release.title, `桌面图标台球 ${release.version}`);
  const meta = document.createElement('span'); meta.className = 'release-version'; meta.textContent = `v${release.version}`;
  title.append(heading, meta);
  const date = document.createElement('span'); date.className = 'release-date'; date.textContent = formatDate(release.published_at);
  top.append(title, date);
  const notes = document.createElement('p'); notes.className = 'release-notes'; notes.textContent = text(release.notes, '本次发布没有填写更新说明。');
  const tags = document.createElement('div'); tags.className = 'release-tags';
  (release.features || []).forEach(item => tags.append(tag(item)));
  target.append(top, notes, tags);
  if (release.download_url) {
    const link = document.createElement('a'); link.className = 'release-download';
    link.href = `${downloadPath}&source=release`; link.textContent = cloudDownload ? 'Windows 版 · 夸克网盘下载 ↗' : '下载 Windows 版 ↗';
    link.rel = 'nofollow';
    if (/^https?:/.test(release.download_url)) link.rel = 'nofollow noreferrer';
    target.append(link);
  }
  if (/^[a-f0-9]{64}$/.test(release.sha256 || '')) {
    const integrity = document.createElement('details'); integrity.className = 'release-integrity';
    const summary = document.createElement('summary'); summary.textContent = '下载文件校验（SHA-256）';
    const hash = document.createElement('code'); hash.textContent = release.sha256;
    integrity.append(summary, hash);
    if (release.size > 0) {
      const size = document.createElement('p'); size.textContent = `Windows x64 · ${(release.size / 1048576).toFixed(1)} MB`;
      integrity.append(size);
    }
    target.append(integrity);
  }
}

function renderHistory(releases, latest) {
  const list = document.querySelector('#release-list');
  const count = document.querySelector('#release-count');
  count.textContent = `${releases.length} 个版本`;
  list.replaceChildren();
  releases.filter(release => release.version !== latest?.version).forEach(release => {
    const article = document.createElement('article'); article.className = 'release-item';
    const top = document.createElement('div'); top.className = 'release-item-top';
    const heading = document.createElement('h3'); heading.textContent = `v${text(release.version)}`;
    const time = document.createElement('time'); time.textContent = formatDate(release.published_at);
    top.append(heading, time);
    const notes = document.createElement('p'); notes.textContent = text(release.notes, '');
    article.append(top, notes);
    (release.features || []).slice(0, 3).forEach(item => article.append(tag(item)));
    list.append(article);
  });
  if (!list.children.length) list.innerHTML = '<div class="loading-block">暂无历史版本。</div>';
}

function renderFlags(flags) {
  const target = document.querySelector('#feature-flags');
  const enabled = Object.entries(flags || {}).filter(([, value]) => value === true).map(([name]) => name);
  if (!enabled.length) return;
  target.hidden = false;
  target.textContent = `当前官网功能状态：${enabled.join(' · ')}`;
}

async function loadSite() {
  try {
    const response = await fetch('/api/releases?limit=20', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderLatest(data.latest);
    renderHistory(data.releases || [], data.latest);
    renderFlags(data.features || {});
  } catch (error) {
    document.querySelector('#latest-release').innerHTML = '<div class="loading-block">版本服务暂时不可用，请稍后刷新。</div>';
    document.querySelector('#hero-version').textContent = '版本服务暂时不可用';
    console.error(error);
  }
}

let rankingRequest = 0;
async function loadRankings() {
  const request = ++rankingRequest;
  const status = document.querySelector('#public-ranking-status');
  const target = document.querySelector('#public-rankings');
  try {
    const board = document.querySelector('#public-board').value;
    const response = await fetch(`/v1/leaderboard?limit=10&board=${encodeURIComponent(board)}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (request !== rankingRequest) return;
    target.replaceChildren();
    const participants = data.generated_players
      ? `${data.real_players} 位玩家 · ${data.generated_players} 个电脑对手`
      : `${data.total_players} 位玩家参与`;
    status.textContent = data.total_players ? `${participants} · 更新于 ${new Date(data.generated_at).toLocaleTimeString('zh-CN')}` : '此榜单还没有成绩。开启游戏中的全球排名，成为第一位上榜玩家。';
    data.entries.forEach(entry => {
      const row = document.createElement('div'); row.className = 'public-rank-row';
      const rank = document.createElement('strong'); rank.textContent = String(entry.rank).padStart(2, '0');
      const player = document.createElement('span'); player.className = 'ranking-player';
      const computer = entry.source === 'generated';
      const ip = document.createElement('span'); ip.textContent = computer ? entry.display_name : (entry.ip_address || '历史 IP 未记录');
      const system = document.createElement('small'); system.textContent = computer ? `${entry.skill_label} · 电脑对手` : (entry.os_name || '历史系统未记录');
      player.append(ip, system);
      const score = document.createElement('b'); score.textContent = board === 'sprint' ? `${entry.pocketed} 球 · ${entry.shots} 杆` : `${(entry.avg_ms / 1000).toFixed(2)} 秒/颗`;
      row.append(rank, player, score); target.append(row);
    });
  } catch (error) {
    if (request !== rankingRequest) return;
    target.replaceChildren(); status.textContent = '榜单读取失败，请点击刷新榜单重试。';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadSite(); loadRankings();
  document.querySelector('#refresh-public-rankings').addEventListener('click', loadRankings);
  document.querySelector('#public-board').addEventListener('change', loadRankings);
});
