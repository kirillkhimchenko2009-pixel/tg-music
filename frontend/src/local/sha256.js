/* Инкрементальный SHA-256 (FIPS 180-4). Собственная реализация по тексту
   стандарта — чужой код не копировался (EdgeDeck недоступен без лицензии,
   поэтому хеш-движок §6 написан с нуля). Потоковое обновление: update()
   сколько угодно раз, digest() финализирует; память O(1) (64 байта буфера). */
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const rotr = (x, n) => (x >>> n) | (x << (32 - n));

export class SHA256 {
  constructor() { this.reset(); }

  reset() {
    this.h = new Uint32Array([
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    this.buf = new Uint8Array(64);
    this.bufLen = 0;
    this.len = 0;                       // всего байт прошло через update()
  }

  update(data) {
    const d = data instanceof Uint8Array ? data : new Uint8Array(data);
    let i = 0;
    this.len += d.length;
    if (this.bufLen > 0) {
      while (this.bufLen < 64 && i < d.length) this.buf[this.bufLen++] = d[i++];
      if (this.bufLen < 64) return this;
      this._compress(this.buf, 0);
      this.bufLen = 0;
    }
    for (; i + 64 <= d.length; i += 64) this._compress(d, i);
    if (i < d.length) {
      this.buf.set(d.subarray(i));
      this.bufLen = d.length - i;
    }
    return this;
  }

  digest() {
    const bits = this.len * 8;          // ≤ 2^53 — Number безопасен
    const bitsHi = Math.floor(bits / 0x100000000) >>> 0;
    const bitsLo = bits >>> 0;
    /* финальный блок(и): хвостовой буфер + 0x80 + нули + 8-байтовая длина */
    const total = this.bufLen + 9;
    const fin = new Uint8Array(Math.ceil(total / 64) * 64);
    fin.set(this.buf.subarray(0, this.bufLen), 0);
    fin[this.bufLen] = 0x80;
    const dv = new DataView(fin.buffer);
    dv.setUint32(fin.length - 8, bitsHi);
    dv.setUint32(fin.length - 4, bitsLo);
    for (let off = 0; off < fin.length; off += 64) this._compress(fin, off);

    const out = new Uint8Array(32);
    const odv = new DataView(out.buffer);
    for (let j = 0; j < 8; j++) odv.setUint32(j * 4, this.h[j] >>> 0);
    this.reset();
    return out;
  }

  hexdigest() {
    return [...this.digest()].map(b => b.toString(16).padStart(2, '0')).join('');
  }

  _compress(p, off) {
    const w = this._w || (this._w = new Uint32Array(64));
    for (let i = 0; i < 16; i++) {
      const o = off + i * 4;
      w[i] = ((p[o] << 24) | (p[o + 1] << 16) | (p[o + 2] << 8) | p[o + 3]) >>> 0;
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    const h = this.h;
    let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e;
      e = (d + t1) >>> 0;
      d = c; c = b; b = a;
      a = (t1 + t2) >>> 0;
    }
    h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0; h[2] = (h[2] + c) >>> 0;
    h[3] = (h[3] + d) >>> 0; h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0;
    h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0;
  }
}
