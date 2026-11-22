/* Presentation/device class for recommendation context, not capabilities/security. */
export function classifyDevice({ userAgent = '', width = 0, touch = false, touchPoints = 0 } = {}) {
  if (/SmartTV|SMART-TV|HbbTV|WebOS|Tizen|Android TV|GoogleTV/i.test(userAgent)) return 'tv';
  if (/Android/i.test(userAgent) && /TV/i.test(userAgent)) return 'tv';
  if (/iPad|Tablet/i.test(userAgent) || (/Macintosh/i.test(userAgent) && (touch || touchPoints > 0))) return 'tablet';
  if (/iPhone|iPod|Android.*Mobile|Windows Phone/i.test(userAgent)) return 'phone';
  if (width > 0 && width < 600 && (touch || touchPoints > 0)) return 'phone';
  if (/Android/i.test(userAgent)) return 'tablet';
  return 'desktop';
}
