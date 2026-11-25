/* Экран Vibe Pro — подписка за Telegram-звёзды (XTR).
   Два тира (Lite / Pro), у каждого три срока (месяц / полгода / год).
   Планы → /api/billing/plans, статус → /api/billing/status,
   покупка → /api/billing/checkout → WebApp.openInvoice(link). */
import { useEffect, useState } from 'react';
import { api, isGuest } from '../api.js';
import { haptic } from '../tg.js';
import { toast } from '../motion/utils/toast.js';
import { IcoStar } from '../ui.jsx';

const openInvoice = (url, onDone) => {
  const wa = window.Telegram?.WebApp;
  if (!wa || typeof wa.openInvoice !== 'function') {
    toast('Открой Vibe из Telegram, чтобы оплатить звёздами', { kind: 'info' });
    return;
  }
  wa.openInvoice(url, (st) => { if (st === 'paid') onDone?.(); });
};

const fmtDate = (iso) => (iso ? String(iso).slice(0, 10).split('-').reverse().join('.') : '');

export default function Pro() {
  const [tiers, setTiers] = useState([]);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [promo, setPromo] = useState('');
  const [promoBusy, setPromoBusy] = useState(false);
  const [promoMsg, setPromoMsg] = useState('');
  const [promoOk, setPromoOk] = useState(false);
  const [stats, setStats] = useState(null);
  const [referral, setReferral] = useState(null);
  const [build, setBuild] = useState(null);

  const load = async () => {
    try {
      const [p, s] = await Promise.all([
        api('/api/billing/plans'),
        api('/api/billing/status').catch(() => null),
      ]);
      setTiers(p.tiers || []);
      if (s) { setStatus(s); if (s.admin) loadStats(); }
      api('/api/billing/referral').then((r) => setReferral(r)).catch(() => {});
    } catch (e) {
      setErr((e && e.message) || 'не удалось загрузить планы');
    }
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    fetch('/api/ping').then((r) => r.json()).then((d) => setBuild(d?.build || null))
      .catch(() => {});
  }, []);

  const loadStats = async () => {
    try { setStats(await api('/api/billing/admin/stats')); } catch { setStats(null); }
  };

  const applyPromo = async () => {
    if (!promo.trim() || promoBusy) return;
    haptic('medium');
    setPromoBusy(true); setPromoMsg('');
    try {
      const res = await api('/api/billing/promo/redeem', {
        method: 'POST',
        body: JSON.stringify({ code: promo.trim() }),
      });
      setPromoOk(true);
      setPromoMsg(res?.message || 'Промокод активирован');
      setPromo('');
      haptic('success');
      setStatus(await api('/api/billing/status').catch(() => status));
    } catch (e) {
      setPromoOk(false);
      setPromoMsg((e && e.message) || 'не удалось активировать');
    } finally {
      setPromoBusy(false);
    }
  };

  const buy = async (tierId, termId) => {
    if (isGuest()) {
      toast('Оплата — в Telegram: открой Vibe из бота', { kind: 'info' });
      return;
    }
    haptic('medium');
    setBusy(tierId + '-' + termId);
    try {
      const { invoice_link } = await api('/api/billing/checkout', {
        method: 'POST',
        body: JSON.stringify({ plan: `${tierId}-${termId}` }),
      });
      openInvoice(invoice_link, async () => {
        haptic('success');
        toast('🌟 Подписка активирована! Спасибо 💜', { kind: 'success' });
        setStatus(await api('/api/billing/status').catch(() => status));
      });
    } catch (e) {
      toast((e && e.message) || 'не удалось создать счёт', { kind: 'error' });
    } finally {
      setBusy('');
    }
  };

  const activeBadge = (tierId) => {
    const t = status?.tiers?.[tierId];
    return t?.active
      ? <span className="pro-badge-on">Активна · до {fmtDate(t.expires_at)}</span>
      : null;
  };

  return (
    <div className="pro-screen">
      <section className="pro-hero">
        <span className="pro-spark">★</span>
        <h1>Подписка Vibe</h1>
        <p>
          {status?.best
            ? <>Твой план: <b>{status.tiers?.[status.best] && (status.best === 'pro' ? 'Vibe Pro' : 'Vibe Lite')}</b>
              {status.tiers?.[status.best]?.active && <> · до <b>{fmtDate(status.tiers[status.best].expires_at)}</b></>}</>
            : 'Два тира — за звёзды, без автопродления'}
        </p>
      </section>

      {err && <p className="pro-err" role="alert">{err}</p>}

      {referral?.link && (
        <section className="pro-ref">
          <h2>🎁 Пригласи друга — +3 дня Lite</h2>
          <p>
            Каждый, кто запустит бота по твоей ссылке, продлевает твою
            подписку: <b>+3 дня Vibe Lite</b> за каждого. Приглашено:
            <b> {referral.invited}</b> · заработано: <b>{referral.days} дн.</b>
          </p>
          <div className="pro-ref-row">
            <code className="pro-ref-link">{referral.link}</code>
            <button
              className="pro-ref-copy"
              onClick={async () => {
                try { await navigator.clipboard.writeText(referral.link); } catch { /* ignore */ }
                toast('Ссылка скопирована — отправь другу 💜', { kind: 'success' });
              }}
            >Копировать</button>
          </div>
        </section>
      )}

      <div className="pro-tiers">
        {tiers.map(tier => (
          <section key={tier.id}
                   className={'pro-tier' + (tier.id === 'pro' ? ' best' : '') + (status?.tiers?.[tier.id]?.active ? ' active' : '')}>
            <header className="pro-tier-head">
              <h2>{tier.title}</h2>
              {tier.id === 'pro' && <span className="pro-tag">Всё лучшее</span>}
              {activeBadge(tier.id)}
            </header>
            <ul className="pro-tier-perks">
              {tier.perks.map(perk => <li key={perk}>{perk}</li>)}
            </ul>
            <div className="pro-terms">
              {tier.terms.map(t => (
                <button key={t.id}
                        className={'pro-term' + (busy === tier.id + '-' + t.id ? ' busy' : '')}
                        onClick={() => buy(tier.id, t.id)}>
                  <span className="pro-term-t">{t.title}</span>
                  <span className="pro-term-s"><IcoStar width="1em" height="1em" /> {t.stars}</span>
                  <span className="pro-term-c">{busy === tier.id + '-' + t.id ? 'Счёт…' : 'Купить'}</span>
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>

      {isGuest() && (
        <p className="pro-guest-note">
          Это гостевая сессия браузера. Оплата звёздами работает внутри Telegram:
          открой Vibe из бота (кнопка «Приложение 🎧») или войди по коду.
        </p>
      )}

      <section className="pro-promo">
        <h3>Промокод</h3>
        <div className="pro-promo-row">
          <input className="pro-promo-input" placeholder="Введи промокод"
                 value={promo} maxLength={48} onChange={e => setPromo(e.target.value)} />
          <button className="pro-promo-btn" disabled={promoBusy || !promo.trim()}
                  onClick={applyPromo}>{promoBusy ? '…' : 'Активировать'}</button>
        </div>
        {promoMsg && <p className={'pro-promo-msg' + (promoOk ? ' ok' : '')}>{promoMsg}</p>}
      </section>

      {status?.admin && <AdminStats stats={stats} />}

      <p className="pro-legal">
        Оплата проходит через Telegram (валюта XTR — звёзды). Подписка
        привязана к аккаунту Telegram, продлевается от даты окончания,
        а не от даты оплаты. Без автосписаний.
      </p>
    </div>
  );
}

/* ---------------- админ: статистика продаж (только is_admin) ---------------- */
function AdminStats({ stats }) {
  if (!stats) return null;
  const fmt = (iso) => (iso ? String(iso).slice(0, 10) : '—');
  return (
    <section className="pro-admin">
      <h3>📊 Статистика продаж</h3>
      <div className="pro-admin-cards">
        <div><strong>{stats.revenue_stars} ★</strong><span>выручка</span></div>
        <div><strong>{stats.payments}</strong><span>платежей</span></div>
        <div><strong>{stats.subs_active?.pro ?? 0}</strong><span>Pro активен</span></div>
        <div><strong>{stats.subs_active?.lite ?? 0}</strong><span>Lite активен</span></div>
        <div><strong>{stats.subs_active?.lifetime ?? 0}</strong><span>бессрочных</span></div>
      </div>
      {stats.last_payments?.length > 0 && (
        <table className="pro-admin-table">
          <thead><tr><th>кто</th><th>тир</th><th>срок</th><th>★</th><th>дата</th></tr></thead>
          <tbody>
            {stats.last_payments.map((p, i) => (
              <tr key={i}><td>{p.user_id}</td><td>{p.tier}</td><td>{p.plan}</td>
                <td>{p.stars}</td><td>{fmt(p.at)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {stats.promos?.length > 0 && (
        <table className="pro-admin-table">
          <thead><tr><th>промокод</th><th>тир</th><th>дней</th><th>активаций</th><th>лимит</th></tr></thead>
          <tbody>
            {stats.promos.map((p, i) => (
              <tr key={i}><td>{p.code}</td><td>{p.tier}</td><td>{p.days ?? '∞'}</td>
                <td>{p.used}</td><td>{p.max_uses ?? '∞'}</td></tr>
            ))}
          </tbody>
        </table>
      )}

      {build && (
        <p className="pro-build">vibe build {build}</p>
      )}
    </section>
  );
}
