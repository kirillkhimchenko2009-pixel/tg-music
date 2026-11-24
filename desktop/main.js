/* Vibe Music — десктоп-окно мини-аппа (Electron).
   Отдельное окно ПК без браузерной обёртки; закрытие → в трей,
   музыка продолжает играть. URL сервера — Render (или локальный для теста). */
const { app, BrowserWindow, Tray, Menu, shell, nativeImage, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const TB_ICONS = require('./build/tb_icons.js');

const APP_URL = process.env.VIBE_URL || 'https://tg-music-0en5.onrender.com/v2/';
const ICON = path.join(__dirname, 'build', 'icon.ico');
const BOUNDS_FILE = () => path.join(app.getPath('userData'), 'window-bounds.json');

let win = null;
let tray = null;
let quitting = false;

function loadBounds() {
  try { return JSON.parse(fs.readFileSync(BOUNDS_FILE(), 'utf8')); }
  catch { return { width: 1120, height: 760 }; }
}

function saveBounds() {
  if (!win || win.isMinimized() || win.isMaximized()) return;
  try { fs.writeFileSync(BOUNDS_FILE(), JSON.stringify(win.getBounds())); } catch { /* ок */ }
}

function createWindow() {
  const bounds = loadBounds();
  win = new BrowserWindow({
    ...bounds,
    minWidth: 380,
    minHeight: 560,
    backgroundColor: '#0A0C18',
    autoHideMenuBar: true,
    title: 'Vibe Music',
    icon: ICON,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  win.webContents.on('did-finish-load', () => console.log('[vibe] loaded:', win.webContents.getURL()));
  win.loadURL(APP_URL);
  win.once('ready-to-show', () => win.show());

  /* сохраняем геометрию окна (не чаще раза в секунду) */
  let boundsTimer = null;
  const scheduleBounds = () => {
    clearTimeout(boundsTimer);
    boundsTimer = setTimeout(saveBounds, 700);
  };
  win.on('resize', scheduleBounds);
  win.on('move', scheduleBounds);

  /* закрытие → прячем в трей: музыка продолжает играть */
  win.on('close', (e) => {
    if (!quitting) {
      e.preventDefault();
      win.hide();
    }
  });

  /* внешние ссылки (target=_blank) — в системный браузер */
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(APP_URL)) { shell.openExternal(url); return { action: 'deny' }; }
    return { action: 'allow' };
  });

  win.webContents.on('did-fail-load', (e, code, desc, url, isMain) => {
    if (isMain && code !== -3) {           // -3 = отменённая навигация
      dialog.showMessageBox(win, {
        type: 'warning',
        title: 'Vibe Music',
        message: 'Не удалось загрузить Vibe Music',
        detail: `Сервер недоступен (${desc}). Проверь интернет — окно попробует снова при нажатии F5 (Ctrl+R).`,
        buttons: ['Понятно'],
      });
    }
  });
}

/* ---------- мини-плеер: трей-меню + thumbar (Windows) ---------- */
function press(label) {
  if (!win || win.isDestroyed()) return;
  const js = "[...document.querySelectorAll('.pbar button')]" +
    ".find(b => b.getAttribute('aria-label') === " + JSON.stringify(label) + ")?.click();";
  win.webContents.executeJavaScript(js).catch(() => {});
}

function queryState() {
  if (!win || win.isDestroyed()) return Promise.resolve(null);
  return win.webContents.executeJavaScript(
    "(() => { const v = document.querySelector('video[playsinline]');" +
    " const t = document.querySelector('.pbar strong, .pbar [class*=title]');" +
    " return { playing: !!v && !v.paused && !v.ended, title: (t && t.textContent || '').slice(0, 60) }; })()"
  ).catch(() => null);
}

function updateMediaUI(st) {
  if (!st) return;
  try {
    tray.setToolTip('Vibe Music — ' + (st.playing ? 'играет: ' + st.title : 'пауза' + (st.title ? ': ' + st.title : '')));
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: (st.playing ? '⏸ ' : '▶ ') + (st.title || 'Играть/пауза'), click: () => press('Играть/пауза') },
      { label: '⏮  Предыдущий', click: () => press('Предыдущий') },
      { label: '⏭  Следующий', click: () => press('Следующий') },
      { type: 'separator' },
      { label: 'Открыть Vibe Music', click: showWindow },
      { label: 'Выход', click: () => { quitting = true; app.quit(); } },
    ]));
  } catch { /* трей не критичен */ }
  try {
    if (process.platform === 'win32' && win && !win.isDestroyed()) {
      const img = (n) => nativeImage.createFromPath(path.join(__dirname, 'build', 'tb_' + n + '.png'));
      win.setThumbarButtons([
        { icon: img('prev'), click: () => press('Предыдущий') },
        { icon: img(st.playing ? 'pause' : 'play'), click: () => press('Играть/пауза') },
        { icon: img('next'), click: () => press('Следующий') },
      ]);
    }
  } catch { /* thumbar не критичен */ }
}

function showWindow() {
  if (!win) createWindow();
  else {
    win.show();
    win.focus();
  }
}

function createTray() {
  const img = nativeImage.createFromPath(ICON);
  tray = new Tray(img.resize({ width: 16, height: 16 }));
  tray.setToolTip('Vibe Music — играет в фоне');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Открыть Vibe Music', click: showWindow },
    { type: 'separator' },
    { label: 'Выход', click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on('double-click', showWindow);
  /* состояние плеера → меню/tooltip/thumbar, раз в 2 сек */
  setInterval(async () => {
    if (!win || win.isDestroyed()) return;
    updateMediaUI(await queryState());
  }, 2000);
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', showWindow);

  app.whenReady().then(() => {
    createWindow();
    createTray();
  });

  app.on('before-quit', () => { quitting = true; });

  app.on('window-all-closed', () => {
    /* окно прячется в трей, приложение не умирает (музыка играет) */
  });
}
