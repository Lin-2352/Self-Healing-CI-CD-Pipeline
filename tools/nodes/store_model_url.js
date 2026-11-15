// Called by the launcher after each Kaggle boot: remember the endpoint's new tunnel address.
const body = $input.first().json.body || {};
const url = String(body.url || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(url)) {
  throw new Error('refusing to store a model URL that is not a trycloudflare.com address: ' + url.slice(0, 80));
}
const s = $getWorkflowStaticData('global');
s.qwenUrl = url;
s.qwenUrlSetAt = new Date().toISOString();
return [{ json: { ok: true, url, stored_at: s.qwenUrlSetAt } }];
