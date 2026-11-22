import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyDevice } from './device.js';

test('uses narrow viewports as a phone hint only when touch is available', () => {
  const desktopUA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36';
  assert.equal(classifyDevice({ userAgent: desktopUA, width: 390, touch: true }), 'phone');
  assert.equal(classifyDevice({ userAgent: desktopUA, width: 390 }), 'desktop');
  assert.equal(classifyDevice({ userAgent: desktopUA, width: 900, touch: true }), 'desktop');
});

test('recognizes phones and tablets from user agents without viewport dimensions', () => {
  assert.equal(classifyDevice({ userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36' }), 'phone');
  assert.equal(classifyDevice({ userAgent: 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15' }), 'tablet');
});

test('recognizes touch-capable iPad desktop mode, including wide viewports', () => {
  const iPadDesktopUA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/17 Safari/605.1.15';
  assert.equal(classifyDevice({ userAgent: iPadDesktopUA, width: 1024, touchPoints: 5 }), 'tablet');
});

test('keeps Android TVs and regular desktop browsers distinct', () => {
  assert.equal(classifyDevice({ userAgent: 'Mozilla/5.0 (SMART-TV; Linux; Tizen 6.5) AppleWebKit/537.36' }), 'tv');
  assert.equal(classifyDevice({ userAgent: 'Mozilla/5.0 (Linux; Android 11; Android TV) AppleWebKit/537.36' }), 'tv');
  assert.equal(classifyDevice({ userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36' }), 'desktop');
});
