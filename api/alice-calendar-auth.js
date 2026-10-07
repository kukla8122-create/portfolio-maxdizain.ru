'use strict';

const SUPABASE_URL = 'https://uhyaigqizvwtsbtmvkdr.supabase.co';
const SUPABASE_KEY = 'sb_publishable_fS6uiYMTofcNuYE5DuAfmg_lUaZQc_8';
const BRIDGE_URL = SUPABASE_URL + '/functions/v1/alice-bridge';
const OWNER_ID = '04fce5b0-29b6-4d6c-9ded-a24914832eee';
const GOOGLE_CLIENT_ID = process.env.GOOGLE_CALENDAR_CLIENT_ID || '553790601767-5qb7tlrlc5imgav86i8bkamdu0jul93g.apps.googleusercontent.com';
const ALLOWED_ORIGINS = new Set([
  'https://portfolio-maxdizainru.vercel.app',
  'https://portfolio-maxdizain.ru'
]);

function send(res, status, body) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  return res.status(status).json(body);
}

async function factoryUser(req) {
  const auth = String(req.headers.authorization || '');
  if (!/^Bearer \S+$/.test(auth)) return null;
  try {
    const response = await fetch(SUPABASE_URL + '/auth/v1/user', {
      headers: { apikey: SUPABASE_KEY, Authorization: auth },
      signal: AbortSignal.timeout(4000)
    });
    if (!response.ok) return null;
    const data = await response.json();
    return data?.id === OWNER_ID ? data : null;
  } catch (_) {
    return null;
  }
}

async function bridge(action, payload = {}) {
  const secret = process.env.ALICE_MEMORY_SECRET;
  if (!secret) throw new Error('Alice bridge is not configured');
  const response = await fetch(BRIDGE_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Alice-Secret': secret
    },
    body: JSON.stringify({ action, ...payload }),
    signal: AbortSignal.timeout(8000)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data?.error || 'Bridge request failed');
  return data;
}

module.exports = async function aliceCalendarAuth(req, res) {
  const user = await factoryUser(req);
  if (!user) return send(res, 401, { error: 'Войди в цифровую фабрику.' });

  if (req.method === 'GET') {
    try {
      const status = await bridge('calendar_status');
      return send(res, 200, {
        connected: !!status.connected,
        durable: !!status.durable,
        google_email: status.google_email || ''
      });
    } catch (_) {
      return send(res, 503, { error: 'Не удалось проверить подключение календаря.' });
    }
  }

  if (req.method !== 'POST') {
    res.setHeader('Allow', 'GET, POST');
    return send(res, 405, { error: 'Method not allowed' });
  }

  const origin = String(req.headers.origin || '');
  if (!ALLOWED_ORIGINS.has(origin)) return send(res, 403, { error: 'Forbidden origin' });
  if (String(req.headers['x-requested-with'] || '') !== 'XmlHttpRequest') {
    return send(res, 403, { error: 'Missing request marker' });
  }

  let body = req.body;
  try { if (typeof body === 'string') body = JSON.parse(body); } catch (_) {}
  const code = String(body?.code || '');
  if (!code || code.length > 4096) return send(res, 400, { error: 'Нет кода Google.' });

  const form = new URLSearchParams({
    client_id: GOOGLE_CLIENT_ID,
    code,
    grant_type: 'authorization_code',
    redirect_uri: origin
  });
  if (process.env.GOOGLE_CALENDAR_CLIENT_SECRET) {
    form.set('client_secret', process.env.GOOGLE_CALENDAR_CLIENT_SECRET);
  }

  try {
    const tokenResponse = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
      signal: AbortSignal.timeout(8000)
    });
    const tokens = await tokenResponse.json().catch(() => ({}));
    if (!tokenResponse.ok || typeof tokens.access_token !== 'string') {
      console.warn('Google OAuth code exchange failed', {
        status: tokenResponse.status,
        error: String(tokens?.error || ''),
        description: String(tokens?.error_description || '').slice(0, 160)
      });
      return send(res, 400, {
        error: 'Google не завершил серверное подключение.',
        code: String(tokens?.error || 'oauth_exchange_failed')
      });
    }

    let email = '';
    try {
      const profileResponse = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
        headers: { Authorization: 'Bearer ' + tokens.access_token },
        signal: AbortSignal.timeout(4000)
      });
      if (profileResponse.ok) {
        const profile = await profileResponse.json();
        email = typeof profile?.email === 'string' ? profile.email : '';
      }
    } catch (_) {}

    const accessExpiresAt = new Date(Date.now() + Number(tokens.expires_in || 3600) * 1000).toISOString();
    const saved = await bridge('save_calendar_credentials', {
      refresh_token: typeof tokens.refresh_token === 'string' ? tokens.refresh_token : '',
      access_token: tokens.access_token,
      access_expires_at: accessExpiresAt,
      scope: typeof tokens.scope === 'string' ? tokens.scope : '',
      google_email: email
    });

    return send(res, 200, {
      ok: true,
      connected: true,
      durable: !!saved.has_refresh_token,
      google_email: saved.google_email || email
    });
  } catch (_) {
    return send(res, 503, { error: 'Не удалось связаться с Google. Повтори подключение.' });
  }
};

module.exports.config = { maxDuration: 15 };
