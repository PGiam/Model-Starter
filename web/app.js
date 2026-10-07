// Model Starter front end: session list, chat with Claude, file downloads, feedback.
const $ = (id) => document.getElementById(id);
let current = null, cursor = 0, timer = null, activity = null;

async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts});
  if (r.status === 401) { location.href = '/login.html'; throw new Error('login'); }
  if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || r.statusText); }
  return r.json();
}

function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])); }

// Small, safe markdown subset: paragraphs, lists, bold, italics, code, links.
function md(src) {
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  const out = []; let list = null;
  for (const line of src.split('\n')) {
    const ul = line.match(/^\s*[-*]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (ul || ol) {
      const tag = ul ? 'ul' : 'ol';
      if (!list || list.tag !== tag) { if (list) out.push(`</${list.tag}>`); out.push(`<${tag}>`); list = {tag}; }
      out.push(`<li>${inline((ul || ol)[1])}</li>`);
      continue;
    }
    if (list) { out.push(`</${list.tag}>`); list = null; }
    if (line.trim() === '') continue;
    const h = line.match(/^#{1,4}\s+(.*)/);
    out.push(h ? `<p><strong>${inline(h[1])}</strong></p>` : `<p>${inline(line)}</p>`);
  }
  if (list) out.push(`</${list.tag}>`);
  return out.join('');
}

function add(cls, html) {
  const log = $('log');
  if (log.querySelector('.empty')) log.innerHTML = '';
  const d = document.createElement('div'); d.className = cls; d.innerHTML = html;
  log.appendChild(d); log.scrollTop = log.scrollHeight;
  return d;
}

function render(ev) {
  if (ev.type === 'user') { activity = null; add('msg user', esc(ev.text)); }
  else if (ev.type === 'assistant') { activity = null; add('msg assistant', md(ev.text)); }
  else if (ev.type === 'error') { activity = null; add('msg assistant error', esc(ev.text)); }
  else if (ev.type === 'tool') {
    if (!activity) {
      activity = add('activity', '<details><summary>Working: 0 steps</summary><ol></ol></details>');
    }
    const ol = activity.querySelector('ol'); const li = document.createElement('li'); li.textContent = ev.summary; ol.appendChild(li);
    activity.querySelector('summary').textContent = `Working: ${ol.children.length} step${ol.children.length === 1 ? '' : 's'} (latest: ${ev.summary.slice(0, 90)})`;
    $('working').textContent = ev.summary;
  } else if (ev.type === 'file') { loadFiles(); }
}

async function poll() {
  if (!current) return;
  try {
    const d = await api(`/api/sessions/${current}/events?after=${cursor}`);
    d.events.forEach(render); cursor = d.next;
    const busy = d.running;
    $('working').textContent = busy ? ($('working').textContent || 'Claude is working...') : '';
    $('text').disabled = busy; $('sendBtn').disabled = busy;
    if (!busy && activity) activity.querySelector('summary').textContent = activity.querySelector('summary').textContent.replace(/^Working/, 'Done');
    timer = setTimeout(poll, busy ? 2000 : 6000);
  } catch (e) { timer = setTimeout(poll, 8000); }
}

async function open(sid) {
  clearTimeout(timer); current = sid; cursor = 0; activity = null;
  $('log').innerHTML = ''; $('working').textContent = '';
  try { history.replaceState(null, '', `#${sid}`); } catch (e) {}
  document.querySelectorAll('.sess').forEach((a) => a.classList.toggle('active', a.dataset.id === sid));
  await loadFiles(); poll();
}

async function loadSessions() {
  const list = await api('/api/sessions');
  $('sessions').innerHTML = list.map((s) => `<a class="sess" href="#${s.id}" data-id="${s.id}">${esc(s.ticker || '?')} <small>${esc(s.company || '')}</small><small>${esc(s.owner || '')} · ${esc((s.created || '').slice(0, 10))}</small></a>`).join('') || '<small style="color:var(--muted)">No models yet</small>';
  document.querySelectorAll('.sess').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); open(a.dataset.id); }));
}

async function loadFiles() {
  if (!current) return;
  const fs = await api(`/api/sessions/${current}/files`);
  const row = (f) => `<a class="file ${f.model ? 'model' : ''}" href="/api/sessions/${current}/files/${encodeURIComponent(f.name)}">${esc(f.name)} <small>${(f.size / 1024).toFixed(0)} KB</small></a>`;
  const models = fs.filter((f) => f.model).reverse(), docs = fs.filter((f) => !f.model);
  $('models').innerHTML = models.map(row).join('') || '<small style="color:var(--muted)">None yet</small>';
  $('docs').innerHTML = docs.map(row).join('') || '<small style="color:var(--muted)">None yet</small>';
  $('zipWrap').hidden = !docs.length; $('zip').href = `/api/sessions/${current}/documents.zip`;
}

$('newForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const t = $('ticker').value.trim(); if (!t) return;
  const d = await api('/api/sessions', {method: 'POST', body: JSON.stringify({ticker: t})});
  $('ticker').value = ''; await loadSessions(); open(d.id);
});

$('bar').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('text').value.trim(); if (!text || !current) return;
  $('text').value = ''; $('text').disabled = true; $('sendBtn').disabled = true;
  try { await api(`/api/sessions/${current}/messages`, {method: 'POST', body: JSON.stringify({text})}); }
  catch (err) { add('msg assistant error', esc(err.message)); }
  clearTimeout(timer); poll();
});
$('text').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('bar').requestSubmit(); } });

$('fbBtn').addEventListener('click', () => { $('fbModal').classList.add('open'); $('fbMsg').textContent = ''; $('fbText').focus(); });
$('fbCancel').addEventListener('click', () => $('fbModal').classList.remove('open'));
$('fbSend').addEventListener('click', async () => {
  const text = $('fbText').value.trim(); if (text.length < 3) return;
  try {
    await api('/api/feedback', {method: 'POST', body: JSON.stringify({text, session_id: current})});
    $('fbText').value = ''; $('fbMsg').style.color = 'var(--ok)';
    $('fbMsg').textContent = 'Thanks. Claude is drafting the change; an admin will review it.';
    setTimeout(() => $('fbModal').classList.remove('open'), 1800);
  } catch (err) { $('fbMsg').style.color = ''; $('fbMsg').textContent = err.message; }
});
$('logout').addEventListener('click', async () => { await api('/api/logout', {method: 'POST'}); location.href = '/login.html'; });

(async () => {
  const me = await api('/api/me');
  $('who').textContent = me.name; $('adminLink').hidden = !me.admin;
  await loadSessions();
  const h = location.hash.slice(1); if (h) open(h);
})();
