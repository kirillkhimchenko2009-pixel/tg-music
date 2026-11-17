/* Оболочка хеш-движка §6: worker с фолбэком на главный поток (чанки 4 МиБ,
   RAM-безопасно в обоих вариантах). Один воркер на приложение, очередь
   запросов по id. */
import { SHA256 } from './sha256.js';

let worker = null;
let broken = false;
const pending = new Map();
let seq = 0;

function ensureWorker() {
  if (worker || broken) return worker;
  try {
    worker = new Worker(new URL('./hash.worker.js', import.meta.url), { type: 'module' });
    worker.onmessage = (e) => {
      const { id, hash, error } = e.data || {};
      const p = pending.get(id);
      if (!p) return;
      pending.delete(id);
      if (error) p.reject(new Error(error)); else p.resolve(hash);
    };
    worker.onerror = () => { broken = true; };
  } catch {
    broken = true;                       // WebView без модульных воркеров
  }
  return worker;
}

/* Главный поток: тот же чанковый проход, SHA256 держит только 64-байтовый
   буфер состояния — содержимое файла в память целиком не грузится. */
export async function hashBlobMain(blob) {
  const h = new SHA256();
  const CHUNK = 4 * 1024 * 1024;
  for (let off = 0; off < blob.size; off += CHUNK) {
    const part = blob.slice(off, Math.min(blob.size, off + CHUNK));
    h.update(new Uint8Array(await part.arrayBuffer()));
  }
  return h.hexdigest();
}

export async function hashBlob(blob) {
  const w = ensureWorker();
  if (w) {
    const id = ++seq;
    try {
      return await new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        w.postMessage({ id, blob });
      });
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      // упавший воркер — досчитаем на главном потоке
    }
  }
  return hashBlobMain(blob);
}
