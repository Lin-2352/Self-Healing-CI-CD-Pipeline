// The Qwen endpoint gets a new address on every Kaggle boot. The launcher posts it to "Set Model URL";
// this node reads it, and falls back to the QWEN_BASE_URL variable when nothing has been stored.
const s = $getWorkflowStaticData('global');
const base = String(s.qwenUrl || $vars.QWEN_BASE_URL || '').trim().replace(/\/+$/, '');
if (!base) throw new Error('No model URL: run the launcher or set the QWEN_BASE_URL variable');
return [{ json: { ...$input.first().json, model_base_url: base }, pairedItem: { item: 0 } }];
