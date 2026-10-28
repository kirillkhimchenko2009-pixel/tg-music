/* Мини-стаб состояния, доступный и вне React (для tg.js/api.js). */

/** Сырой initData пользователя (string). */
export const initDataRaw = { value: '' };

/** Bearer-токен (гостевая сессия в браузере / на случай 401). */
export const authToken = { value: localStorage.getItem('auth_token') || '' };

export function setAuthToken(t) {
  authToken.value = t || '';
  if (t) localStorage.setItem('auth_token', t);
  else localStorage.removeItem('auth_token');
}
