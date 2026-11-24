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

  const load = async () => {
    try {
      const [p, s] = await Promise.all([
        api('/api/billing/plans'),
        api('/api/billing/status').catch(() => null),
      ]);
      setTiers(p.tiers || []);
      if (s) setStatus(s);
    } catch (e) {
      setErr((e && e.message) || 'не удалось загрузить планы');
    }
  };

  useEffect(() => { load(); }, []);

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

      <p className="pro-legal">
        Оплата проходит через Telegram (валюта XTR — звёзды). Подписка
        привязана к аккаунту Telegram, продлевается от даты окончания,
        а не от даты оплаты. Без автосписаний.
      </p>
    </div>
  );
}
