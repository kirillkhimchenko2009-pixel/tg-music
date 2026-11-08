/* Vibe Music — Mini App (vanilla SPA) */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

const tg = window.Telegram?.WebApp || null;
if (tg) { try { tg.expand(); tg.setHeaderColor('#0E0A12'); tg.setBackgroundColor('#0E0A12'); } catch (e) {} }

/* API-база: обычный режим — тот же origin; режим «статика отдельно
   (GitHub Pages / другой хост)» — ?api=https://туннель в адресе страницы */
const API_BASE = (new URLSearchParams(location.search).get('api') || '').replace(/\/$/, '');

const headers = () => {
  const h = { 'Content-Type': 'application/json',
              // обход предупреждений ngrok и pinggy свободных туннелей
              'ngrok-skip-browser-warning': '1',
              'X-Pinggy-No-Screen': '1' };
  if (tg?.initData) h['X-Init-Data'] = tg.initData;
  const tok = localStorage.getItem('auth_token');
  if (tok) h['Authorization'] = 'Bearer ' + tok;
  if (!tg?.initData && !tok && new URLSearchParams(location.search).has('dev')) {
    h['X-User-Id'] = localStorage.getItem('dev_uid') || '1';
  }
  return h;
};

function authOK() { return !!(tg?.initData || localStorage.getItem('auth_token')); }

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(API_BASE + path, { headers: headers(), ...opts });
  } catch (e) {
    // сеть не отвечает вообще: устаревший адрес туннеля или обрыв
    throw new Error('Нет связи: туннель обновился. Закрой экран и в чате снова нажми 📱 «Открыть приложение»');
  }
  if (!res.ok) {
    let msg = res.statusText;
    let status = res.status;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    if (status === 401 && String(msg).includes('initData')) openLogin();
    if (status === 401 && !authOK() && !path.startsWith('/api/auth/')) openLogin();
    throw new Error(msg);
  }
  const ctype = res.headers.get('content-type') || '';
  if (!ctype.includes('json')) {
    // пришёл HTML вместо JSON — устаревший адрес туннеля или сбой прокси
    throw new Error('Сессия приложения устарела: закрой этот экран и в чате нажми ещё раз кнопку 📱 «Открыть приложение»');
  }
  return res.json();
}

/* ---------------- state ---------------- */
const state = {
  tab: 'home',
  query: '',
  playlists: [],
  playlistId: null,
  queue: [],
  idx: -1,
  playing: false,
  searchTimer: null,
};

/* ---------------- player ---------------- */
const audio = $('#audio');
const player = {
  current() { return state.queue[state.idx] || null; },

  async play(track, queueList = null) {
    if (queueList) {
      state.queue = queueList.slice();
      state.idx = Math.max(0, queueList.findIndex(t => t.id === track.id));
    } else {
      const i = state.queue.findIndex(t => t.id === track.id);
      if (i >= 0) state.idx = i;
      else { state.queue.push(track); state.idx = state.queue.length - 1; }
    }
    const cur = state.queue[state.idx];
    if (!cur) return;
    try {
      const u = (p) => (p && (p.startsWith('http') || !API_BASE) ? p : API_BASE + p);
  audio.src = cur.stream_url ? u(cur.stream_url) : API_BASE + `/api/stream/${cur.id}`;
      await audio.play();
    } catch (e) {
      toast('Трек ещё не скачан — нажми ⬇ в списке.');
      return;
    }
    _meta(cur);
  },
  toggle() { if (audio.paused) audio.play(); else audio.pause(); },
  next() { if (state.idx < state.queue.length - 1) { state.idx++; _playCurrent(); } },
  prev() { if (state.idx > 0) { state.idx--; _playCurrent(); } },
  seek(p) { if (audio.duration) audio.currentTime = p * audio.duration; },
};

function _playCurrent() {
  const cur = player.current();
  if (cur) player.play(cur);
}

function _meta(t) {
  $('#pb-title').textContent = t.title || '—';
  $('#pb-artist').textContent = t.artist || '—';
  $('#pb-cover').src = t.cover_url || 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E';
  $('#playerbar').classList.remove('hidden');
  $('#playerbar').classList.add('show');
  document.body.dataset.playing = '1';
  highlight();
}

audio.addEventListener('play', () => { _playingUI(true); });
audio.addEventListener('pause', () => { _playingUI(false); });
audio.addEventListener('ended', () => player.next());
audio.addEventListener('timeupdate', () => {
  const r = $('#pb-range');
  if (audio.duration) {
    r.value = Math.round(audio.currentTime / audio.duration * 1000);
    r.style.setProperty('--p', (audio.currentTime / audio.duration * 100) + '%');
  }
  $('#pb-cur').textContent = fmt(audio.currentTime);
  $('#pb-len').textContent = fmt(audio.duration || 0);
});

function _playingUI(playing) {
  state.playing = playing;
  $('#pb-play').textContent = playing ? '⏸' : '▶';
}

$('#pb-play').onclick = () => player.toggle();
$('#pb-next').onclick = () => player.next();
$('#pb-prev').onclick = () => player.prev();
$('#pb-range').addEventListener('change', e => player.seek(+e.target.value / 1000));

function fmt(s) {
  if (!s || !isFinite(s)) return '0:00';
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}

/* Воспроизведение трека: локальный файл (audio/*) или ссылка из ТГ CDN */
async function playTrack(track, queue) {
  try {
    let src;
    const res = await fetch(`/api/stream/${track.id}`, { headers: headers() });
    const ctype = (res.headers.get('content-type') || '').toLowerCase();
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || 'нет файла');
    }
    if (ctype.startsWith('audio/')) {
      const blob = await res.blob();
      src = URL.createObjectURL(blob);
    } else {
      const j = await res.json();
      if (j && j.url) src = j.url;
      else if (j && j.detail) throw new Error(j.detail);
      else if (j && j.path) throw new Error('нет потока');
      else src = null;
      if (src === null) { toast('Файл ещё не скачан — нажми ⬇'); return; }
    }
    track.stream_url = src;
    await player.play(track, queue);
  } catch (e) {
    toast(e.message === 'не скачан' ? 'Файл ещё не скачан — нажми ⬇' : ('Не удалось: ' + e.message), true);
  }
}

/* ---------------- helpers ---------------- */
function esc(s) {
  return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function toast(text, isErr = false, ms = 2600) {
  const t = $('#toast');
  t.textContent = text;
  if (isErr) t.style.borderColor = 'rgba(255,59,107,.5)'; else t.style.borderColor = '';
  t.classList.remove('hidden');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add('hidden'), ms);
}

function coverOf(t) { return t.cover_url || ''; }

function trackRow(t, list) {
  const playingCls = player.current()?.id === t.id ? 'playing' : '';
  return `
  <div class="track-item ${playingCls}" data-id="${t.id}">
    <img class="t-cover" loading="lazy" src="${esc(coverOf(t))}"
         onerror="this.style.opacity=.25">
    <div class="t-info" data-act="play">
      <div class="t-title" title="${esc(t.title)}">${esc(t.title)}</div>
      <div class="t-artist">${esc(t.artist)} · ${fmt(t.duration)}</div>
    </div>
    <div class="t-acts">
      ${t.quality === 'flac' ? '<span class="badge flac">FLAC</span>' : (t.quality ? `<span class="badge">${esc(t.quality)}</span>` : '')}
      <button class="iconbtn ghost" data-act="tg" ${t.ready ? '' : 'title="Скачается при выборе"'}>⬇</button>
      <button class="iconbtn ghost" data-act="add">➕</button>
    </div>
  </div>`;
}

function bindTrackItems(container, list) {
  $$('.track-item', container).forEach(el => {
    el.addEventListener('click', async (ev) => {
      const act = ev.target.closest('[data-act]')?.dataset.act || 'play';
      const id = +el.dataset.id;
      const track = list.find(t => t.id === id);
      if (!track) return;
      if (act === 'add') { openAddToPlaylist(track); return; }
      if (act === 'tg') { downloadToTG(track, ev.target.closest('[data-act="tg"]')); return; }
      await playTrack(track, list);
    });
  });
}

function highlight() {
  $$('.track-item').forEach(el =>
    el.classList.toggle('playing', +el.dataset.id === player.current()?.id));
}

/* ---------------- actions ---------------- */
const tgInFlight = new Set();

async function downloadToTG(track, btn) {
  const key = String(track.id);
  if (tgInFlight.has(key)) {
    toast('Этот трек уже отправляется в чат…');
    return;
  }
  tgInFlight.add(key);
  const oldHTML = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="sp"></span>';
  }
  try {
    const { job } = await api('/api/download?track_id=' + track.id, { method: 'POST' });
    let st = null;
    for (let i = 0; i < 720; i++) {
      st = await api('/api/jobs/' + job);
      if (st.status === 'done' || st.status === 'error') break;
      await new Promise(r => setTimeout(r, 800));
      if (btn) btn.textContent = Math.round(st.progress || 0) + '%';
    }
    if (st?.status === 'done') {
      toast(st.preview ? 'Полная версия недоступна — отправил фрагмент'
                       : 'Скачано и отправлено в Telegram ✅');
    } else {
      toast('Ошибка: ' + (st?.error || 'неизвестно'), true);
    }
  } catch (e) {
    toast('Ошибка: ' + e.message, true);
  } finally {
    tgInFlight.delete(key);
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = oldHTML || '⬇';
    }
  }
}

async function openAddToPlaylist(track) {
  try {
    const { playlists } = await api('/api/playlists');
    state.playlists = playlists;
    const card = $('#modal-card');
    card.innerHTML = `<h3>В какой плейлист добавить?</h3>
      <div id="pl-choice"></div>
      <div class="row-btns">
        <button class="btn primary small" id="pl-new">✨ Новый плейлист</button>
        <button class="btn small" id="pl-close">Закрыть</button>
      </div>`;
    const box = $('#pl-choice', card);
    if (!playlists.length) box.innerHTML = `<div class="empty"><div class="big">▤</div>Плейлистов пока нет</div>`;
    playlists.forEach(p => {
      const wrap = document.createElement('div');
      wrap.className = 'item';
      wrap.innerHTML = `<span>▤ ${esc(p.title)}</span><span class="muted">${p.count}</span>`;
      wrap.onclick = async () => {
        try {
          await api(`/api/playlists/${p.id}/tracks`, { method: 'POST', body: JSON.stringify({ track_id: track.id }) });
          toast(`Добавлено в «${p.title}»`);
          $('#modal').classList.add('hidden');
        } catch (e) { toast(e.message, true); }
      };
      box.appendChild(wrap);
    });
    $('#pl-new', card).onclick = async () => {
      const title = prompt('Название плейлиста:');
      if (!title) return;
      const pl = await api('/api/playlists', { method: 'POST', body: JSON.stringify({ title }) });
      await api(`/api/playlists/${pl.id}/tracks`, { method: 'POST', body: JSON.stringify({ track_id: track.id }) });
      toast(`Создан плейлист «${title}» и добавлен трек`);
      $('#modal').classList.add('hidden');
      if (state.tab === 'playlists') renderTab('playlists');
    };
    $('#pl-close', card).onclick = () => $('#modal').classList.add('hidden');
    $('#modal').classList.remove('hidden');
  } catch (e) { toast(e.message, true); }
}

/* ---------------- screens ---------------- */
function renderTab(tab) {
  state.tab = tab;
  $$('#tabbar .tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  if (tab === 'home') return renderHome();
  if (tab === 'search') return renderSearch();
  if (tab === 'library') return renderLibrary();
  if (tab === 'playlists') return renderPlaylists();
}

async function renderHome() {
  const el = $('#screen');
  el.innerHTML = `
    <div class="hero">
      <h1>Твоя музыка 🎧</h1>
      <p>Ищи треки, качай в Telegram одним касанием<br>и переноси плейлисты из Яндекс Музыки, Spotify и Звука.</p>
      <div class="row">
        <button class="btn primary" data-go="search">🔍 Найти трек</button>
        <button class="btn" data-go="playlists">▤ Плейлисты</button>
      </div>
    </div>
    <div class="h2 accent-text">Популярное</div>
    <div id="home-lib" class="skeleton"><div class="muted">Загружаю…</div></div>`;
  $$('[data-go]', el).forEach(b => b.onclick = () => renderTab(b.dataset.go));
  try {
    const { tracks } = await api('/api/library?limit=30');
    const box = $('#home-lib', el);
    if (!tracks.length) {
      box.innerHTML = `<div class="empty"><div class="big">🖤</div>Здесь появятся треки, которые ты качал или слушал.<br>Начни с поиска!</div>`;
      return;
    }
    box.innerHTML = tracks.map(t => trackRow(t, tracks)).join('');
    bindTrackItems(box, tracks);
  } catch (e) {
    $('#home-lib', el).innerHTML = `<div class="empty">Проблема: ${esc(e.message)}</div>`;
  }
}

let searchBox = null;
async function renderSearch() {
  const el = $('#screen');
  el.innerHTML = `
    <div class="searchbar">
      <span class="mic">⌕</span>
      <input id="q" placeholder="Название трека или исполнитель…" autocomplete="off">
    </div>
    <div class="chiprow" id="chips"></div>
    <div id="res"></div>`;
  const input = $('#q', el);
  const chips = [`хиты`, `новинки`, `рэп`, `рок`, `поп`, `инди`, ` караоке`];
  $('#chips', el).innerHTML = chips.map(c => `<button class="chip" data-c="${c}">${c.trim()}</button>`).join('');
  $$('#chips .chip', el).forEach(b => b.onclick = () => {
    input.value = b.dataset.c.trim();
    input.dispatchEvent(new Event('input'));
  });
  let lastList = [];
  input.addEventListener('input', () => {
    clearTimeout(state.searchTimer);
    const q = input.value.trim();
    if (!q) { $('#res', el).innerHTML = ''; return; }
    state.searchTimer = setTimeout(async () => {
      const box = $('#res', el);
      box.innerHTML = `<div class="muted" style="padding:8px">Ищу…</div>`;
      try {
        const data = await api('/api/search?q=' + encodeURIComponent(q));
        lastList = [...data.local];
        const seen = new Set(lastList.map(t => t.id));
        lastList.push(...data.web.filter(t => !seen.has(t.id)));
        box.innerHTML = `
          ${data.local.length ? `<div class="section-head"><span class="muted">Из каталога</span></div>
          <div id="r-local">${data.local.map(t => trackRow(t, lastList)).join('')}</div>` : ''}
          ${data.web.length ? `<div class="section-head"><span class="muted">Смотреть в интернете</span></div>
          <div id="r-web">${data.web.filter(t => !seen.has(t.id)).map(t => trackRow(t, lastList)).join('')}</div>` : ''}
        ${!lastList.length ? `<div class="empty"><div class="big">🫥</div>Ничего не нашлось</div>` : ''}`;
        const all = [...(data.local || []), ...(data.web || [])];
        bindTrackItems($('#r-local', box) || box, all);
        bindTrackItems($('#r-web', box) || box, all);
      } catch (e) {
        box.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
      }
    }, 420);
  });
  input.focus();
}

async function renderLibrary() {
  const el = $('#screen');
  el.innerHTML = `<div class="h2">Моя музыка</div><div id="lib"><div class="muted">Загружаю…</div></div>`;
  try {
    const { tracks } = await api('/api/library?limit=200');
    const box = $('#lib', el);
    if (!tracks.length) {
      box.innerHTML = `<div class="empty"><div class="big">🖤</div>Скачай что-нибудь через поиск или из чата бота,<br>и библиотека наполнится.</div>`;
      return;
    }
    box.innerHTML = tracks.map(t => trackRow(t, tracks)).join('');
    bindTrackItems(box, tracks);
  } catch (e) { $('#lib', el).innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function renderPlaylists() {
  const el = $('#screen');
  el.innerHTML = `
    <div class="h2">Плейлисты</div>
    <div id="pls"><div class="muted">Загружаю…</div></div>`;
  const fab = `<button class="fab" id="pl-new" title="Новый плейлист">＋</button>`;
  try {
    const { playlists } = await api('/api/playlists');
    state.playlists = playlists;
    const box = $('#pls', el);
    if (!playlists.length) {
      box.innerHTML = `<div class="empty"><div class="big">▤</div>Плейлистов пока нет.<br>Создай первый — треки можно добавлять из поиска.</div>`;
    } else {
      box.innerHTML = `<div class="pl-grid">` + playlists.map(p => `
        <div class="pl-card" data-id="${p.id}">
          ${p.cover_url ? `<img class="pl-cover" src="${esc(p.cover_url)}"></img>`
                        : `<div class="pl-plc">▤</div>`}
          <div class="pl-name">${esc(p.title)}</div>
          <div class="pl-meta">${p.count} треков</div>
        </div>`).join('') + `</div>`;
      $$('.pl-card', box).forEach(c =>
        c.onclick = () => renderPlaylist(+c.dataset.id));
    }
    el.insertAdjacentHTML('beforeend', fab);
    $('#pl-new', el).onclick = async () => {
      const title = prompt('Название плейлиста:');
      if (!title) return;
      await api('/api/playlists', { method: 'POST', body: JSON.stringify({ title }) });
      renderTab('playlists');
    };
  } catch (e) {
    $('#pls', el).innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function renderPlaylist(id) {
  const el = $('#screen');
  el.innerHTML = `<div class="muted" style="margin-top:14px">← <a href="#" id="back">Плейлисты</a></div>
    <div class="h2" id="pl-title">Плейлист</div>
    <button class="btn primary small" id="pl-playall" style="margin:0 0 12px">▶ Слушать всё</button>
    <button class="btn small ghost" id="pl-del" style="margin-left:8px">🗑 Удалить</button>
    <div id="pl-items"><div class="muted">Загружаю…</div></div>`;
  $('#back', el).onclick = (e) => { e.preventDefault(); renderTab(state.tab === 'home' ? 'home' : 'playlists'); };
  try {
    const data = await api(`/api/playlists/${id}`);
    state.playlistId = id;
    $('#pl-title', el).textContent = 'Плейлист';
    try {
      const { playlists } = await api('/api/playlists');
      state.playlists = playlists;
      const me = playlists.find(p => p.id === id);
      if (me) $('#pl-title', el).textContent = me.title;
    } catch (e) {}
    const box = $('#pl-items', el);
    if (!data.tracks?.length) {
      box.innerHTML = `<div class="empty"><div class="big">🫙</div>Плейлист пуст — добавь треки из поиска</div>`;
    } else {
      box.innerHTML = data.tracks.map(t => trackRow(t, data.tracks)).join('');
      bindTrackItems(box, data.tracks);
      $('#pl-playall', el).onclick = async () => {
        await playTrack(data.tracks[0], data.tracks);
      };
      $('#pl-del', el).onclick = () => confirmDel(id);
    }
  } catch (e) {
    $('#pl-items', el).innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

async function confirmDel(id) {
  if (!confirm('Удалить плейлист?')) return;
  try {
    await api(`/api/playlists/${id}`, { method: 'DELETE' });
    toast('Плейлист удалён');
    renderTab('playlists');
  } catch (e) { toast(e.message, true); }
}

/* ---------------- login (обычный браузер без initData) ---------------- */
function openLogin() {
  const el = $('#screen');
  el.innerHTML = `
    <div class="login">
      <div class="login-ico">🔑</div>
      <h2>Подключить приложение</h2>
      <p class="muted">Ты открыл приложение вне Telegram, поэтому нужна авторизация.</p>
      <a class="btn primary login-tg" target="_blank" rel="noopener"
         href="https://t.me/tG_dols_music_bot?start=app">📱 Открыть из Telegram</a>
      <div class="login-or">— или —</div>
      <label class="muted">Код из команды <code>/code</code></label>
      <input id="lg-code" inputmode="numeric" maxlength="9" autocomplete="off"
             placeholder="1234 5678">
      <button class="btn primary" id="lg-go">Подключить</button>
      <div id="lg-err" class="lg-err hidden"></div>
    </div>`;
  const inp = $('#lg-code', el);
  inp.focus();
  const go = async () => {
    const code = inp.value.trim();
    const err = $('#lg-err', el);
    err.classList.add('hidden');
    try {
      const r = await fetch(API_BASE + '/api/auth/exchange', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Pinggy-No-Screen': '1' },
        body: JSON.stringify({ code }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || 'не удалось');
      localStorage.setItem('auth_token', j.token);
      $('#userbox').textContent = 'браузер';
      toast('Приложение подключено ✓');
      renderTab('home');
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove('hidden');
    }
  };
  $('#lg-go', el).onclick = go;
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
}

/* ---------------- boot ---------------- */
$$('#tabbar .tab').forEach(b => b.onclick = () => renderTab(b.dataset.tab));
$('#modal').addEventListener('click', (e) => {
  if (e.target.id === 'modal') $('#modal').classList.add('hidden');
});

(function boot() {
  const name = tg?.initDataUnsafe?.user?.first_name;
  $('#userbox').textContent = name ? `@${name}` : ' ';
  if (!authOK()) {
    // обычный браузер без initData: сразу экран подключения
    openLogin();
    return;
  }
  renderTab('home');
})();
