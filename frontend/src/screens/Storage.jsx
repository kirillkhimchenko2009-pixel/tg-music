/* Экран «Хранилище»: Local-first диагностика (§4/§5/§15/§19).
   Честные данные: стратегия (OPFS/IndexedDB), квота/использование,
   persistent-флаг, живой список загрузок, проверка целостности библиотеки
   (сироты/потерянные файлы). Никаких обещаний «навсегда»: дисклеймер внизу. */
import React, { useEffect, useState, useCallback } from 'react';
import { haptic } from '../tg.js';
import { toast } from '../motion/utils/toast.js';
import { IcoDl, IcoX, IcoCheck } from '../ui.jsx';
import * as storage from '../local/storage.js';
import * as dl from '../local/downloads.js';
import { db } from '../local/db.js';
import { markMissing } from '../local/repo.js';

const fmtMB = b => b >= 1e9 ? (b / 1e9).toFixed(2) + ' ГБ'
  : b >= 1e6 ? (b / 1e6).toFixed(1) + ' МБ' : Math.round(b / 1024) + ' КБ';

export default function Storage() {
  const [info, setInfo] = useState(null);
  const [est, setEst] = useState({});
  const [persisted, setPersisted] = useState(false);
  const [offline, setOffline] = useState([]);
  const [dlList, setDlList] = useState([]);
  const [wifi, setWifi] = useState(false);
  const [health, setHealth] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const st = await storage.storageInfo();
      setInfo(st);
      setEst(await storage.estimate());
      setPersisted(await storage.isPersisted());
      const rows = await db.tracks.where('dl_state').equals('local').toArray();
      setOffline(rows);
      setWifi(await dl.wifiOnly());
    } catch { /* экран не должен падать */ }
  }, []);

  useEffect(() => {
    refresh();
    const un = dl.subscribeDl(list => setDlList(list));
    return un;
  }, [refresh]);

  const toggleWifi = async () => {
    haptic('light');
    const nv = !wifi;
    setWifi(nv);
    await dl.setWifiOnly(nv);
  };

  const requestPersist = async () => {
    haptic('medium');
    const ok = await storage.requestPersist();
    setPersisted(await storage.isPersisted());
    toast(ok ? 'Хранилище закреплено — браузер не очистит его при нехватке места'
             : 'Система пока не закрепила хранилище — примет решение при росте данных',
          { kind: ok ? 'success' : 'info' });
  };

  const clearAll = async () => {
    haptic('medium');
    if (!confirm('Удалить все сохранённые аудиофайлы с этого устройства? '
                 + 'Онлайн-слушание не пострадает, но офлайн придётся качать заново.')) return;
    setBusy(true);
    try {
      await storage.clearAudio();
      await db.storage_manifest.clear();
      await db.tracks.where('dl_state').equals('local').modify({ dl_state: 'remote', hash: null, size: 0 });
      toast('Кеш аудио очищен', { kind: 'success' });
      refresh();
    } catch (e) {
      toast(e.message || 'Не удалось очистить', { kind: 'error' });
    }
    setBusy(false);
  };

  const runHealth = async () => {
    haptic('light');
    setBusy(true);
    try {
      const keys = await storage.listAudioKeys();
      const manifest = await db.storage_manifest.toArray();
      const manHashes = new Set(manifest.map(m => m.hash));
      const orphans = keys ? keys.filter(k => !manHashes.has(k)) : null;
      const missing = [];
      for (const m of manifest) {
        if (!(await storage.hasAudio(m.hash))) missing.push(m);
      }
      setHealth({ orphans, missing, scanned: true });
    } catch (e) {
      toast(e.message || 'Проверка не удалась', { kind: 'error' });
    }
    setBusy(false);
  };

  const fixOrphans = async () => {
    if (!health?.orphans?.length) return;
    haptic('light');
    setBusy(true);
    try {
      for (const k of health.orphans) await storage.deleteAudio(k);
      toast(`Сироты убраны: ${health.orphans.length}`, { kind: 'success' });
      setHealth(null);
      refresh();
    } finally { setBusy(false); }
  };

  const fixMissing = async () => {
    if (!health?.missing?.length) return;
    haptic('light');
    setBusy(true);
    try {
      for (const m of health.missing) {
        await markMissing(m.track_id);
        await db.storage_manifest.delete(m.hash);
      }
      toast(`Потерянные помечены: ${health.missing.length} — можно перекачать из меню трека`,
            { kind: 'success' });
      setHealth(null);
      refresh();
    } finally { setBusy(false); }
  };

  const offlineSize = offline.reduce((s, t) => s + (t.size || 0), 0);
  const used = est.usage || 0, quota = est.quota || 0;
  const pct = quota ? Math.min(100, Math.round(used / quota * 100)) : 0;

  return (
    <div className="stg">
      <div className="sechead"><h2>Хранилище</h2></div>
      <p className="stg-sub">Музыка живёт на этом устройстве: скачанное играет офлайн,
        без сервера и даже без сети.</p>

      {/* --- стратегия и квота --- */}
      <div className="stg-card">
        <div className="stg-row">
          <span className="stg-k">Файловая система</span>
          <span className={'stg-badge' + (info?.strategy === 'opfs' ? ' ok' : '')}>
            {info ? (info.strategy === 'opfs' ? 'OPFS · быстрый диск'
                    : info.opfs ? 'OPFS ограничен → IndexedDB' : 'IndexedDB') : '…'}
          </span>
        </div>
        <div className="stg-row">
          <span className="stg-k">Занято браузером</span>
          <span className="stg-v">{used ? fmtMB(used) : '—'}{quota ? ` из ${fmtMB(quota)}` : ''}</span>
        </div>
        {quota > 0 && (
          <div className="stg-bar"><i style={{ width: pct + '%' }} /></div>
        )}
        <div className="stg-row">
          <span className="stg-k">Защита от автоочистки</span>
          <button className="stg-link" onClick={requestPersist} disabled={busy}>
            {persisted ? <><IcoCheck /> закреплено</> : 'Закрепить хранилище'}
          </button>
        </div>
      </div>

      {/* --- офлайн-библиотека --- */}
      <div className="stg-card">
        <div className="stg-row">
          <span className="stg-k">Сохранено офлайн</span>
          <span className="stg-v">{offline.length} трек(ов) · {fmtMB(offlineSize)}</span>
        </div>
        <div className="stg-acts">
          <button className="stg-btn" onClick={runHealth} disabled={busy}>Проверить целостность</button>
          <button className="stg-btn danger" onClick={clearAll} disabled={busy}>Очистить весь кеш</button>
        </div>
        {health?.scanned && (
          <div className="stg-health">
            {health.orphans === null && <div className="stg-hnote">Файлы на диске не
              перечислить в этом WebView — проверены только записи базы.</div>}
            {!!health.orphans?.length && (
              <div className="stg-hrow">
                <span>Сироты без записи в базе: {health.orphans.length}</span>
                <button className="stg-btn" onClick={fixOrphans} disabled={busy}>Убрать</button>
              </div>
            )}
            {!!health.missing.length && (
              <div className="stg-hrow">
                <span>Записи без файла: {health.missing.length}</span>
                <button className="stg-btn" onClick={fixMissing} disabled={busy}>Пометить потерянными</button>
              </div>
            )}
            {!health.orphans?.length && !health.missing.length && (
              <div className="stg-hnote ok">Всё сходится: файлы и база совпадают.</div>
            )}
          </div>
        )}
      </div>

      {/* --- загрузки --- */}
      <div className="stg-card">
        <button className={'stg-row as-btn' + (wifi ? ' on' : '')} onClick={toggleWifi}>
          <span className="stg-k"><IcoDl /> Скачивать только по Wi-Fi</span>
          <span className={'stg-switch' + (wifi ? ' on' : '')} aria-hidden="true" />
        </button>
        <p className="stg-note">Определяется, когда устройство отдаёт тип сети; на части
          iPhone WebView не сообщает — тогда правило не срабатывает.</p>

        {dlList.length > 0 && (
          <div className="stg-dls">
            {dlList.map(j => (
              <div key={j.trackId} className="stg-dl">
                <div className="stg-dl-name">
                  {j.name || ('Трек #' + j.trackId)}
                  {j.state === 'hash' && ' · хеширую…'}
                </div>
                <div className="stg-dl-bar">
                  <i style={{ width: dlPct(j) + '%' }} className={'st-' + j.state} />
                </div>
                <div className="stg-dl-meta">
                  <span>{dlLabel(j)}</span>
                  <span className="stg-dl-acts">
                    {(j.state === 'down' || j.state === 'queued') && (
                      <button className="stg-x" aria-label="Отменить" title="Отменить"
                              onClick={() => { haptic('light'); dl.cancel(j.trackId); }}><IcoX /></button>
                    )}
                    {j.state === 'error' && (
                      <button className="stg-link" onClick={() => { haptic('light'); dl.retry(j.trackId); }}>
                        Повторить
                      </button>
                    )}
                  </span>
                </div>
                {j.error && <div className="stg-dl-err">{j.error}</div>}
              </div>
            ))}
          </div>
        )}
        {!dlList.length && <div className="stg-hnote">Активных загрузок нет.
          «Сохранить офлайн» — в меню «…» у любого трека.</div>}
      </div>

      {/* --- честный дисклеймер §19 --- */}
      <div className="stg-foot">
        Файлы привязаны к хранилищу этого WebView: Telegram может очистить его при
        нехватке места, а переустановка/смена домена — сбросить. Закрепление и бэкап
        снижают риск, но «навсегда» не гарантируется никем.
      </div>
    </div>
  );
}

function dlPct(j) {
  if (j.state === 'done') return 100;
  if (!j.total) return 0;
  return Math.max(2, Math.min(100, Math.round(j.received / j.total * 100)));
}

function dlLabel(j) {
  if (j.state === 'done') return 'сохранено';
  if (j.state === 'error') return 'ошибка';
  if (j.state === 'paused') return 'приостановлено';
  if (j.state === 'hash') return 'проверка целостности…';
  if (j.total) return `${fmtMB(j.received)} / ${fmtMB(j.total)}`;
  return fmtMB(j.received || 0);
}
