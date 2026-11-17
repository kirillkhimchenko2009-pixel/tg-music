/* Worker-хешер §6: считает SHA-256 файла чанками (RAM O(1) по чанку),
   главный поток не блокируется. Обмен: {id, blob} → {id, hash, size} /
   {id, error}. Модульный воркер собирается Vite из import.meta.url. */
import { SHA256 } from './sha256.js';

self.onmessage = async (e) => {
  const { id, blob } = e.data || {};
  try {
    const h = new SHA256();
    const CHUNK = 4 * 1024 * 1024;
    for (let off = 0; off < blob.size; off += CHUNK) {
      const part = blob.slice(off, Math.min(blob.size, off + CHUNK));
      h.update(new Uint8Array(await part.arrayBuffer()));
    }
    self.postMessage({ id, hash: h.hexdigest(), size: blob.size });
  } catch (err) {
    self.postMessage({ id, error: String((err && err.message) || err) });
  }
};
