/* Экран Vibe Pro — подписка за Telegram-звёзды (XTR).
   Планы → /api/billing/plans, статус → /api/billing/status,
   покупка → /api/billing/checkout → WebApp.openInvoice(link). */
import { useEffect, useState } from 'react';
import { api, isGuest } from '../api.js';
import { haptic } from '../tg.js';
import { toast } from '../motion/utils/toast.js';
import { IcoStar } from '../ui.jsx';

const PLAN_TITLES = { month: 'Месяц', half: 'Полгода', year: 'Год' };
const PLAN_NOTE = {
  month: 'попробовать',
  half: 'выгода ~15%',
  year: 'лучшая цена',
};

const PERKS = [
  ['⬇️', 'Безлимитные скачивания треков в чат'],
  ['🎧', 'PRO-обложка плеера: аниме-арт локскрина'],
  ['⚡️', 'Приоритетная волна и очередь скачиваний'],
  ['💜', 'Поддержка развития Vibe'],
];

function openInvoice(url, onDone) {
  const wa = window.Telegram?.WebApp;
  if (!wa || typeof wa.openInvoice !== 'function') {
    toast('Открой Vibe из Telegram, чтобы оплатить звёздами', { kind: 'info' });
    return;
  }
  wa.openInvoice(url, (status) => {
    if (status === 'paid') onDone?.();
  });
}

export default function Pro() {
  const [plans, setPlans] = useState([]);
  const [status, setStatus] = useState(null);   // {pro, plan, expires_at}
  const [busy, setBusy] = useState('');         // plan id при покупке
  const [err, setErr] = useState('');

  const load = async () => {
    try {
      const [p, s] = await Promise.all([
        api('/api/billing/plans'),
        api('/api/billing/status').catch(() => null),
      ]);
      setPlans(p.plans || []);
      if (s) setStatus(s);
    } catch (e) {
      setErr((e && e.message) || 'не удалось загрузить планы');
    }
  };

  useEffect(() => { load(); }, []);

  const buy = async (planId) => {
    if (isGuest()) {
      toast('Оплата — в Telegram: открой Vibe из бота', { kind: 'info' });
      return;
    }
    haptic('medium');
    setBusy(planId);
    try {
      const { invoice_link } = await api('/api/billing/checkout', {
        method: 'POST',
        body: JSON.stringify({ plan: planId }),
      });
      openInvoice(invoice_link, async () => {
        haptic('success');
        toast('🌟 Vibe Pro активирована! Спасибо 💜', { kind: 'success' });
        setStatus(await api('/api/billing/status').catch(() => status));
      });
    } catch (e) {
      toast((e && e.message) || 'не удалось создать счёт', { kind: 'error' });
    } finally {
      setBusy('');
    }
  };

  const fmtDate = (iso) => (iso ? String(iso).slice(0, 10).split('-').reverse().join('.') : '');

  return (
    <div className="pro-screen">
      <section className="pro-hero">
        <span className="pro-spark">★</span>
        <h1>Vibe Pro</h1>
        <p>
          {status?.pro
            ? <>Подписка активна · до <b>{fmtDate(status.expires_at)}</b></>
            : 'Больше музыки, больше свободы — за звёзды'}
        </p>
      </section>

      <ul className="pro-perks">
        {PERKS.map(([ico, text]) => (
          <li key={text}><span className="pro-perk-ico">{ico}</span>{text}</li>
        ))}
      </ul>

      {err && <p className="pro-err" role="alert">{err}</p>}

      <div className="pro-plans">
        {plans.map(pl => (
          <button key={pl.id} className={'pro-plan' + (busy === pl.id ? ' busy' : '')}
                  onClick={() => buy(pl.id)}>
            <span className="pro-plan-t">{PLAN_TITLES[pl.id] || pl.title}</span>
            <span className="pro-plan-stars"><IcoStar width="1em" height="1em" /> {pl.stars}</span>
            <span className="pro-plan-n">{PLAN_NOTE[pl.id] || ''}</span>
            <span className="pro-plan-c">{busy === pl.id ? 'Открываю счёт…' : 'Оплатить звёздами'}</span>
          </button>
        ))}
      </div>

      {isGuest() && (
        <p className="pro-guest-note">
          Это гостевая сессия браузера. Оплата звёздами работает внутри Telegram:
          открой Vibe из бота (кнопка «Приложение 🎧»).
        </p>
      )}

      <p className="pro-legal">
        Оплата проходит через Telegram (валюта XTR — звёзды).
        Подписка привязывается к твоему аккаунту Telegram и продлевается
        от даты окончания, а не от даты оплаты.
      </p>
    </div>
  );
}
