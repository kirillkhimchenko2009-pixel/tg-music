/* Vibe Music — десктоп-окно мини-аппа (Electron).
   Отдельное окно ПК без браузерной обёртки; закрытие → в трей,
   музыка продолжает играть. URL сервера — Render (или локальный для теста). */
const { app, BrowserWindow, Tray, Menu, shell, nativeImage, dialog } = require('electron');
const path = require('path');
const fs = require('fs');

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
