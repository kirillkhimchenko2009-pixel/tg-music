/* DYNAMIC ARTWORK THEMING §13 (по мотивам Snae/Finamp — только концепции).
   Палитра из обложки одной выборкой canvas (0 зависимостей):
   - dominant — основной кластер (взвешен по площади + насыщенности);
   - accent — самый насыщенный различный кластер;
   - deep — затемнённый dominant для фона;
   - glow/glow2 — готовые rgba для свечений плеера.
   CORS: чужие обложки (ytimg) с crossOrigin='anonymous' обычно читаются;
   tainted canvas / ошибка → null → интерфейс остаётся в базовой теме.
   Меняем минимум CSS-параметров: только свечения .np (уже заведённые
   переменные --g/--g2), контраст текста не трогаем. */

function rgba(r, g, b, a) {
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${a})`;
}

function hex(r, g, b) {
  const h = v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

export function extractPalette(src) {
  return new Promise(resolve => {
    if (!src) return resolve(null);
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      try {
        const S = 24;
        const cv = document.createElement('canvas');
        cv.width = S; cv.height = S;
        const ctx = cv.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, S, S);
        const d = ctx.getImageData(0, 0, S, S).data;
        const buckets = new Map();
        for (let i = 0; i < d.length; i += 4) {
          if (d[i + 3] < 140) continue;
          const r = d[i], g = d[i + 1], b = d[i + 2];
          const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
          const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
          if (lum < 0.10 || lum > 0.94) continue;
          const sat = mx ? (mx - mn) / mx : 0;
          const key = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5);
          const bk = buckets.get(key) || { n: 0, r: 0, g: 0, b: 0, s: 0 };
          bk.n++; bk.r += r; bk.g += g; bk.b += b; bk.s += sat;
          buckets.set(key, bk);
        }
        if (!buckets.size) return resolve(null);
        const scored = [...buckets.values()].map(bk => ({
          bk,
          avg: [bk.r / bk.n, bk.g / bk.n, bk.b / bk.n],
          sat: bk.s / bk.n,
          score: bk.n * (0.4 + bk.s / bk.n),
        })).sort((a, b) => b.score - a.score);
        const dom = scored[0];
        /* accent: заметно другой оттенок (перцептивная дистанция),
           взвешен насыщенностью и «массой» кластера */
        let acc = null, bestS = -1;
        for (const c of scored) {
          const dist = Math.abs(c.avg[0] - dom.avg[0])
            + Math.abs(c.avg[1] - dom.avg[1])
            + Math.abs(c.avg[2] - dom.avg[2]);
          if (dist < 70) continue;
          const sc = c.sat * Math.min(1, c.bk.n / 8);
          if (sc > bestS) { bestS = sc; acc = c; }
        }
        const [dr, dg, db] = dom.avg;
        const ar = acc ? acc.avg : dom.avg;
        resolve({
          dominant: [dr, dg, db],
          accent: ar,
          deep: [dr * 0.42, dg * 0.42, db * 0.42],
          hexDominant: hex(dr, dg, db),
          hexAccent: hex(ar[0], ar[1], ar[2]),
          glow: rgba(dr, dg, db, 0.55),
          glow2: rgba(ar[0], ar[1], ar[2], 0.30),
        });
      } catch { resolve(null); }      // tainted canvas и др. — тихий фолбэк
    };
    img.onerror = () => resolve(null);
  });
}
