/* Keep campaign attribution across pages in this browser tab; never store contact data. */
(function () {
  'use strict';
  var fields = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];
  var key = 'maximum-campaign-v1';
  function clean(value) {
    return String(value || '').replace(/[<>"'\r\n]/g, '').slice(0, 100);
  }
  var params = new URLSearchParams(location.search);
  var tagged = fields.some(function (field) { return params.has(field); });
  var campaign = {};
  if (!tagged) {
    try { campaign = JSON.parse(sessionStorage.getItem(key) || '{}'); } catch (_) {}
  }
  if (!campaign || typeof campaign !== 'object' || Array.isArray(campaign)) campaign = {};
  var safe = {};
  fields.forEach(function (field) { safe[field] = clean(tagged ? params.get(field) : campaign[field]); });
  try { sessionStorage.setItem(key, JSON.stringify(safe)); } catch (_) {}
  window.maximumAttribution = { get: function () { return Object.assign({}, safe); } };
}());
