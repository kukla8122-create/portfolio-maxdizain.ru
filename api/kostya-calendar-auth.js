'use strict';

// Personal Google Calendar authorization for the Kostya voice assistant.
// Secrets and OAuth tokens stay on the server; never return them to the browser.
const crypto = require('node:crypto');
const BASE_URL = 'https://portfolio-maxdizainru.vercel.app';
const CALLBACK = BASE_URL + '/api/kostya-calendar-auth';
const SUPABASE_BRIDGE = 'https://uhyaigqizvwtsbtmvkdr.supabase.co/functions/v1/kostya-calendar-bridge';
const COOKIE_NAME = '__Host-kostya_calendar_oauth';
const SCOPE = 'openid email https://www.googleapis.com/auth/calendar.events';

function page(res, title, message, status = 200) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  return res.status(status).send(`<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title><body style="font:18px system-ui;margin:8vh auto;max-width:580px;padding:20px;line-height:1.5"><h1>${title}</h1><p>${message}</p></body></html>`);
}
function random(bytes = 32) { return crypto.randomBytes(bytes).toString('base64url'); }
function getCookie(req, name) {
  const header = String(req.headers.cookie || '');
  const pair = header.split(';').map(x => x.trim()).find(x => x.startsWith(name + '='));
  return pair ? pair.slice(name.length + 1) : '';
}
function cookie(res, value, maxAge) {
  res.setHeader('Set-Cookie', COOKIE_NAME + '=' + value + '; Path=/; Max-Age=' + maxAge + '; HttpOnly; Secure; SameSite=Lax');
}
function clientConfigured() {
  return !!(process.env.GOOGLE_CALENDAR_CLIENT_ID && process.env.GOOGLE_CALENDAR_CLIENT_SECRET &&
    process.env.KOSTYA_CALENDAR_OWNER_EMAIL && process.env.ALICE_MEMORY_SECRET);
}
async function googleJSON(url, options, stage = 'google') {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(10000) });
  const json = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(stage + ':' + response.status + ':' + String(json?.error || 'unknown').slice(0, 45));
  return json;
}
module.exports = async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return page(res, 'Метод не поддерживается', 'Открой ссылку подключения через браузер.', 405);
  }
  res.setHeader('Cache-Control', 'no-store');
  if (!clientConfigured()) {
    return page(res, 'Костя: требуется настройка', 'На сервере ещё не добавлен секрет OAuth-клиента Google Calendar. Сначала завершите настройку сервера.', 503);
  }
  const clientId = process.env.GOOGLE_CALENDAR_CLIENT_ID.trim();
  const clientSecret = process.env.GOOGLE_CALENDAR_CLIENT_SECRET.trim();
  const action = String(req.query.action || '');
  const code = String(req.query.code || '');
  const state = String(req.query.state || '');

  if (action === 'check' && !code) {
    try {
      const check = await fetch(SUPABASE_BRIDGE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Alice-Secret': process.env.ALICE_MEMORY_SECRET },
        body: JSON.stringify({ action: 'calendar_status' }), signal: AbortSignal.timeout(8000)
      });
      const answer = await check.json().catch(() => ({}));
      if (check.ok && answer.ok) return page(res, 'Сервер Кости готов', 'Соединение с сервером памяти работает. Можно повторить авторизацию Google.');
      return page(res, 'Сервер Кости: ошибка', 'Проверка подключения к памяти: HTTP ' + check.status + ', код ' + String(answer.reason || answer.error || 'неизвестно').replace(/[^a-z0-9_]/gi, '').slice(0, 30) + '.', 200);
    } catch (e) {
      console.error('Kostya calendar bridge health:', String(e.message || 'failed').slice(0, 100));
      return page(res, 'Сервер Кости недоступен', 'Не удалось подключиться к серверу памяти (сеть или тайм-аут).', 200);
    }
  }
  if (action === 'connect' && !code) {
    const nonce = random(32);
    const verifier = random(48);
    const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
    cookie(res, nonce + '.' + verifier, 600);
    const auth = new URL('https://accounts.google.com/o/oauth2/v2/auth');
    const params = {
      client_id: clientId, redirect_uri: CALLBACK, response_type: 'code',
      scope: SCOPE, access_type: 'offline', prompt: 'consent',
      state: nonce, code_challenge: challenge, code_challenge_method: 'S256'
    };
    for (const [k, v] of Object.entries(params)) auth.searchParams.set(k, v);
    return res.redirect(302, auth.toString());
  }
  // Never accept an unsolicited callback or an OAuth error without clearing state.
  const signed = getCookie(req, COOKIE_NAME);
  const [expectedState, verifier] = signed.split('.');
  cookie(res, '', 0);
  if (req.query.error) return page(res, 'Доступ не предоставлен', 'Google не выдал разрешение. Ты можешь повторить подключение.');
  if (!code || !state || !expectedState || !verifier ||
      state.length !== expectedState.length ||
      !crypto.timingSafeEqual(Buffer.from(state), Buffer.from(expectedState))) {
    return page(res, 'Неверный запрос', 'Начни подключение заново по специальной ссылке.', 400);
  }
  try {
    const form = new URLSearchParams({
      code, client_id: clientId, client_secret: clientSecret,
      redirect_uri: CALLBACK, grant_type: 'authorization_code', code_verifier: verifier
    });
    const tokens = await googleJSON('https://oauth2.googleapis.com/token', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: form
    }, 'token');
    if (!tokens.access_token || !tokens.refresh_token) {
      return page(res, 'Требуется повторное разрешение', 'Google не выдал постоянный доступ. Повтори подключение и разреши доступ к календарю.', 400);
    }
    const profile = await googleJSON('https://openidconnect.googleapis.com/v1/userinfo', {
      headers: { Authorization: 'Bearer ' + tokens.access_token }
    }, 'profile');
    const approvedEmail = String(process.env.KOSTYA_CALENDAR_OWNER_EMAIL).trim().toLowerCase();
    if (!profile.email_verified || String(profile.email || '').toLowerCase() !== approvedEmail) {
      return page(res, 'Выбран не тот аккаунт', 'Выбери Google-аккаунт, который подключён к твоему календарю.', 403);
    }
    const response = await fetch(SUPABASE_BRIDGE, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Alice-Secret': process.env.ALICE_MEMORY_SECRET
      },
      body: JSON.stringify({
        action: 'save_calendar_credentials',
        client_id: clientId,
        client_secret: process.env.GOOGLE_CALENDAR_CLIENT_SECRET,
        refresh_token: tokens.refresh_token,
        access_token: tokens.access_token,
        access_expires_at: new Date(Date.now() + Number(tokens.expires_in || 3600) * 1000).toISOString(),
        scope: tokens.scope || SCOPE,
        google_email: profile.email
      }),
      signal: AbortSignal.timeout(10000)
    });
    const saved = await response.json().catch(() => ({}));
    if (!response.ok || !saved.ok) throw new Error('storage:' + response.status + ':' + String(saved.error || 'unknown').slice(0, 60));
    return page(res, 'Календарь подключён', 'Google дал доступ Косте. Теперь можно проверять голосовые напоминания.');
  } catch (e) {
    console.error('Kostya calendar OAuth:', String(e.message || 'failed'));
    const reason = String(e.message || 'unknown');
    const safeReason = /^token:400:invalid_client/.test(reason) ? 'Google отклонил секрет OAuth-клиента. Проверь переменную GOOGLE_CALENDAR_CLIENT_SECRET.' :
      /^token:400:invalid_grant/.test(reason) ? 'Google отклонил код авторизации. Начни подключение заново.' :
      /^token:/.test(reason) ? 'Google не смог подтвердить авторизацию (этап token).' :
      /^profile:/.test(reason) ? 'Не удалось проверить Google-аккаунт (этап profile).' :
      /^storage:403:/.test(reason) ? 'Сервер памяти запретил сохранение (403). Требуется исправление связки.' :
      /^storage:/.test(reason) ? 'Сервер памяти не сохранил доступ (' + reason.replace(/[^a-z0-9:_-]/gi, '').slice(0, 80) + ').' :
      'Сервер не ответил вовремя. Попробуй снова позже.';
    return page(res, 'Подключение не завершено', safeReason, 502);
  }
};