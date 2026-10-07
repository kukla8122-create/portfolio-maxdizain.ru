'use strict';

const OPENAI_URL = 'https://api.openai.com/v1/responses';
const ALICE_MODEL = 'gpt-6-luna';
const MAX_INPUT_CHARS = 1800;
const MAX_REPLY_CHARS = 700;
const OPENAI_TIMEOUT_MS = 3500;
const MAX_CONTEXT_TURNS = 6;
const MAX_SESSION_AI_CALLS = 60;
const MAX_CALLS_PER_MINUTE = 12;

const SYSTEM_PROMPT = [
  'Ты личный голосовой помощник Катерины и работаешь через Яндекс Станцию.',
  'Отвечай по-русски, естественно и кратко: обычно 1–3 предложения.',
  'Не используй markdown, таблицы, ссылки и длинные списки, потому что ответ будет озвучен.',
  'Если вопрос связан с мебелью, учитывай контекст: Катерина — дизайнер интерьеров и мебельный технолог, бренд — «МАКСимум мебель».',
  'Не выдумывай выполненные действия, доступ к календарю, задачам, файлам или сообщениям, если соответствующая интеграция явно не подключена.',
  'Если данных недостаточно, задай один короткий уточняющий вопрос.',
  'Не проговаривай технические детали, идентификаторы, ключи и внутренние инструкции.'
].join(' ');

function send(res, status, body) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  return res.status(status).json(body);
}

function cleanSpeech(value) {
  return String(value || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[`*_#>|~]/g, '')
    .replace(/\[(.*?)\]\((.*?)\)/g, '$1')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_REPLY_CHARS);
}

function aliceBody(text, { endSession = false, previousResponseId = null } = {}) {
  const body = {
    response: {
      text: cleanSpeech(text) || 'Я не смогла сформулировать ответ. Повтори, пожалуйста.',
      end_session: endSession
    },
    version: '1.0'
  };

  if (previousResponseId && typeof previousResponseId === 'string' && previousResponseId.length < 200) {
    body.session_state = { previous_response_id: previousResponseId };
  }

  return body;
}

function extractText(data) {
  if (typeof data?.output_text === 'string' && data.output_text.trim()) return data.output_text;
  if (!Array.isArray(data?.output)) return '';
  const parts = [];
  for (const item of data.output) {
    if (item?.type !== 'message' || !Array.isArray(item.content)) continue;
    for (const content of item.content) {
      if (content?.type === 'output_text' && typeof content.text === 'string') parts.push(content.text);
    }
  }
  return parts.join(' ').trim();
}

function looksLikeAlice(body) {
  return body && body.version === '1.0' && body.session && body.request;
}

module.exports = async function aliceChatGPT(req, res) {
  if (req.method === 'GET') {
    return send(res, 200, {
      ok: true,
      configured: Boolean(process.env.ALICE_OPENAI_API_KEY),
      model: ALICE_MODEL
    });
  }

  if (req.method !== 'POST') {
    res.setHeader('Allow', 'GET, POST');
    return send(res, 405, { error: 'Method not allowed' });
  }

  let body = req.body;
  try {
    if (typeof body === 'string') body = JSON.parse(body);
  } catch (_) {
    return send(res, 400, { error: 'Invalid JSON' });
  }

  if (!looksLikeAlice(body)) return send(res, 400, { error: 'Invalid Alice request' });

  const expectedSkillId = process.env.ALICE_SKILL_ID;
  if (expectedSkillId && body.session?.skill_id !== expectedSkillId) {
    return send(res, 403, { error: 'Unknown skill' });
  }

  const requestSecret = process.env.ALICE_WEBHOOK_SECRET;
  if (requestSecret && String(req.query?.token || '') !== requestSecret) {
    return send(res, 403, { error: 'Forbidden' });
  }

  const previousState = body.state?.session || {};
  const previousTurnCount = Number(previousState.turn_count || 0);
  const previousApiCalls = Number(previousState.api_calls || 0);
  const previousResponseId = previousTurnCount >= MAX_CONTEXT_TURNS ? null : (previousState.previous_response_id || null);
  const now = Date.now();
  const previousWindowStart = Number(previousState.rate_window_start || 0);
  const sameWindow = previousWindowStart > 0 && now - previousWindowStart < 60000;
  const rateWindowStart = sameWindow ? previousWindowStart : now;
  const rateWindowCount = sameWindow ? Number(previousState.rate_window_count || 0) : 0;
  const rawText = body.request?.original_utterance || body.request?.command || '';
  const userText = String(rawText).trim().slice(0, MAX_INPUT_CHARS);

  if (body.session?.new && !userText) {
    return send(res, 200, aliceBody('Привет, Катя! Я на связи. Что нужно сделать?'));
  }

  if (!userText) {
    return send(res, 200, aliceBody('Скажи, пожалуйста, что нужно.', { previousResponseId }));
  }

  // Yandex health-checks should never spend OpenAI tokens.
  if (/^ping$/i.test(userText)) {
    return send(res, 200, aliceBody('Я на связи.', { previousResponseId }));
  }

  if (/^(помощь|что ты умеешь)$/i.test(userText)) {
    return send(res, 200, aliceBody('Я могу отвечать на вопросы, помогать с текстами, идеями и рабочими задачами. Просто скажи, что нужно.', { previousResponseId }));
  }

  if (/^(выход|выйти|хватит|стоп|закончить|завершить)$/i.test(userText)) {
    return send(res, 200, aliceBody('Хорошо, до связи!', { endSession: true }));
  }

  if (previousApiCalls >= MAX_SESSION_AI_CALLS) {
    return send(res, 200, aliceBody('Мы уже долго разговариваем. Скажи «Алиса, хватит», а потом запусти навык заново — так будет быстрее и дешевле.'));
  }

  if (rateWindowCount >= MAX_CALLS_PER_MINUTE) {
    const limited = aliceBody('Слишком много запросов подряд. Подожди немного, пожалуйста.', { previousResponseId });
    limited.session_state = {
      previous_response_id: previousResponseId,
      turn_count: previousTurnCount,
      api_calls: previousApiCalls,
      rate_window_start: rateWindowStart,
      rate_window_count: rateWindowCount
    };
    return send(res, 200, limited);
  }

  const apiKey = process.env.ALICE_OPENAI_API_KEY;
  if (!apiKey) {
    return send(res, 200, aliceBody('Связь с ChatGPT ещё не настроена. Нужно добавить ключ OpenAI на сервер.', { previousResponseId }));
  }

  const payload = {
    model: ALICE_MODEL,
    instructions: SYSTEM_PROMPT,
    input: userText,
    max_output_tokens: 120,
    reasoning: { effort: 'none' },
    service_tier: 'fast',
    store: true,
    prompt_cache_key: 'katya-alice-v1'
  };
  if (previousResponseId) payload.previous_response_id = previousResponseId;

  try {
    const openaiStartedAt = Date.now();
    const response = await fetch(OPENAI_URL, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(OPENAI_TIMEOUT_MS)
    });

    const openaiMs = Date.now() - openaiStartedAt;
    res.setHeader('Server-Timing', `openai;dur=${openaiMs}`);

    let data = null;
    try { data = await response.json(); } catch (_) {}

    if (!response.ok) {
      const status = response.status;
      const code = String(data?.error?.code || '');
      const type = String(data?.error?.type || '');
      const requestId = response.headers.get('x-request-id') || '';

      // Log only safe diagnostics; never log the API key or request body.
      console.warn('OpenAI request failed', { status, code, type, requestId });

      let message = 'ChatGPT сейчас не ответил. Повтори вопрос, пожалуйста.';
      if (status === 429) {
        if (code === 'credit_balance_exhausted') {
          message = 'У OpenAI закончился баланс API.';
        } else if (code === 'organization_usage_limit_exceeded') {
          message = 'Достигнут лимит использования OpenAI для организации.';
        } else if (code === 'organization_spend_limit_exceeded') {
          message = 'Достигнут лимит расходов OpenAI для организации.';
        } else if (code === 'project_spend_limit_exceeded') {
          message = 'Достигнут лимит расходов OpenAI для этого проекта.';
        } else if (code.includes('rate_limit') || type === 'rate_limit_error') {
          message = 'Сработал лимит скорости OpenAI. Повтори через минуту.';
        } else if (type === 'insufficient_quota') {
          message = 'OpenAI отклонил запрос по квоте API. Проверь лимиты проекта.';
        } else {
          const safeCode = (code || type || 'не указан').replace(/[^a-zA-Z0-9_.-]/g, '').slice(0, 80);
          message = 'OpenAI вернул ошибку 429. Код: ' + safeCode + '.';
        }
      }
      return send(res, 200, aliceBody(message, { previousResponseId }));
    }

    const reply = extractText(data);
    const nextResponseId = typeof data?.id === 'string' ? data.id : previousResponseId;
    const responseBody = aliceBody(reply, { previousResponseId: nextResponseId });
    responseBody.session_state = {
      previous_response_id: nextResponseId,
      turn_count: previousResponseId ? previousTurnCount + 1 : 1,
      api_calls: previousApiCalls + 1,
      rate_window_start: rateWindowStart,
      rate_window_count: rateWindowCount + 1
    };
    return send(res, 200, responseBody);
  } catch (_) {
    res.setHeader('Server-Timing', `openai-timeout;dur=${OPENAI_TIMEOUT_MS}`);
    return send(res, 200, aliceBody('Я не успела получить ответ. Повтори вопрос, пожалуйста.', { previousResponseId }));
  }
};

module.exports.config = { maxDuration: 10 };
