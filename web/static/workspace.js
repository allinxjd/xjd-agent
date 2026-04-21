/**
 * XJDAgent Workspace — 工作台面板逻辑
 * Canvas / Terminal / Files / Memory / Skills / Inspector
 */

// ── Tab switching ──
function switchTab(tabName) {
  document.querySelectorAll('.ws-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
  document.querySelectorAll('.ws-panel').forEach(p => p.classList.toggle('active', p.id === tabName + 'Panel'));
  // Lazy-load panel data
  if (tabName === 'canvas') loadCanvasHistory();
  else if (tabName === 'memory') loadMemories();
  else if (tabName === 'skills') loadSkills();
  else if (tabName === 'market') loadMarket();
  else if (tabName === 'files') { if (!_pinsLoaded) loadPins(); loadContextPreview(); }
}

// ══════════════════════════════════════════════════════════════
//  Canvas Panel
// ══════════════════════════════════════════════════════════════
const _canvasComponents = [];

function renderCanvasComponent(comp) {
  _canvasComponents.push(comp);
  if (_canvasComponents.length > 100) _canvasComponents.splice(0, _canvasComponents.length - 100);
  const viewport = document.querySelector('#canvasPanel .canvas-viewport');
  const empty = document.querySelector('#canvasPanel .canvas-empty');
  if (empty) empty.style.display = 'none';
  if (!viewport) return;

  const el = document.createElement('div');
  el.className = 'canvas-component';
  const contentStr = (comp.content || '').trimStart();
  const looksLikeHtml = /^<!doctype\s|^<html[\s>]/i.test(contentStr);
  const typeLabel = looksLikeHtml ? 'html' : (comp.type || 'html');
  el.innerHTML = `<div class="canvas-component-header"><span>${typeLabel.toUpperCase()}</span></div><div class="canvas-component-body"></div>`;
  const body = el.querySelector('.canvas-component-body');

  if (comp.type === 'html' || comp.type === 'react' || looksLikeHtml) {
    const iframe = document.createElement('iframe');
    iframe.sandbox = 'allow-scripts';
    body.appendChild(iframe);
    // 注入 A2UI bridge 到 canvas 内容
    const safeId = (comp.artifact_id||'').replace(/[^a-zA-Z0-9_-]/g, '');
    const a2uiBridge = `<script>
window.xjd={sendAction:function(a,p){window.parent.postMessage({type:"a2ui_action",action:a,payload:p||{},artifact_id:"${safeId}"},"*")}};
</script>`;
    const content = comp.content || '';
    iframe.srcdoc = content.replace('</head>', a2uiBridge + '</head>') || (a2uiBridge + content);
    // 自动撑高 iframe
    iframe.onload = () => {
      try {
        const h = iframe.contentDocument.documentElement.scrollHeight;
        if (h > 100) iframe.style.height = h + 'px';
      } catch(e) {}
    };
  } else if (comp.type === 'markdown') {
    body.innerHTML = typeof renderMarkdown === 'function' ? renderMarkdown(comp.content || '') : comp.content;
  } else if (comp.type === 'chart') {
    const canvas = document.createElement('canvas');
    canvas.width = 600; canvas.height = 300;
    body.appendChild(canvas);
    try {
      if (window.Chart) new Chart(canvas, JSON.parse(comp.content));
    } catch(e) { body.textContent = 'Chart error: ' + e.message; }
  } else {
    body.innerHTML = `<pre style="margin:0;white-space:pre-wrap;">${_esc(comp.content || '')}</pre>`;
  }
  viewport.prepend(el);
  viewport.scrollTop = 0;
}

// A2UI: 监听 Canvas iframe postMessage，转发到 WebSocket
window.addEventListener('message', function(e) {
  if (e.data && e.data.type === 'a2ui_action' && typeof ws !== 'undefined' && ws && ws.readyState === 1) {
    ws.send(JSON.stringify(e.data));
  }
});

function clearCanvas() {
  _canvasComponents.length = 0;
  const viewport = document.querySelector('#canvasPanel .canvas-viewport');
  const empty = document.querySelector('#canvasPanel .canvas-empty');
  if (viewport) viewport.innerHTML = '';
  if (empty) empty.style.display = '';
}

let _canvasHistoryLoaded = false;
async function loadCanvasHistory() {
  if (_canvasHistoryLoaded) return;
  _canvasHistoryLoaded = true;
  try {
    const res = await fetch('/api/workspace/canvas/list');
    const data = await res.json();
    if (!data.items || !data.items.length) return;
    for (const item of data.items.slice(-20)) {
      try {
        const r = await fetch('/api/workspace/canvas/' + encodeURIComponent(item.artifact_id));
        const comp = await r.json();
        if (comp && !comp.error) renderCanvasComponent(comp);
      } catch(e) {}
    }
  } catch(e) {}
}

// ══════════════════════════════════════════════════════════════
//  Terminal Panel
// ══════════════════════════════════════════════════════════════
const _termHistory = [];
let _termHistIdx = -1;

function termExec() {
  const input = document.getElementById('termInput');
  const cmd = input.value.trim();
  if (!cmd) return;
  _termHistory.unshift(cmd);
  if (_termHistory.length > 200) _termHistory.length = 200;
  _termHistIdx = -1;
  input.value = '';
  termAppend('$ ' + cmd, 'cmd-line');

  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'terminal_exec', command: cmd }));
  } else {
    termAppend('Not connected', 'cmd-error');
  }
}

function termAppend(text, cls) {
  const out = document.getElementById('termOutput');
  if (!out) return;
  const line = document.createElement('div');
  if (cls) line.className = cls;
  line.textContent = text;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}

function termKeydown(e) {
  if (e.key === 'Enter') { e.preventDefault(); termExec(); }
  else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (_termHistIdx < _termHistory.length - 1) {
      _termHistIdx++;
      e.target.value = _termHistory[_termHistIdx];
    }
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (_termHistIdx > 0) { _termHistIdx--; e.target.value = _termHistory[_termHistIdx]; }
    else { _termHistIdx = -1; e.target.value = ''; }
  }
}

// ══════════════════════════════════════════════════════════════
//  Files Panel
// ══════════════════════════════════════════════════════════════
let _filesLoaded = false;
let _currentFilePath = '.';
let _pinsLoaded = false;

// ── File Browser (reused for pin selection) ──

function _escJs(s) { return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }

async function loadFileTree(path) {
  try {
    const res = await fetch('/api/workspace/files?path=' + encodeURIComponent(path));
    if (!res.ok) { throw new Error(res.status + ' ' + res.statusText); }
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { throw new Error('Invalid response'); }
    if (data.error) { console.warn(data.error); return; }
    const tree = document.getElementById('fileTree');
    if (!tree) return;
    tree.innerHTML = '';
    _filesLoaded = true;
    _currentFilePath = path;

    if (path && path !== '.') {
      const parent = path.includes('/') ? path.substring(0, path.lastIndexOf('/')) || '.' : '.';
      const back = document.createElement('div');
      back.className = 'file-tree-item file-tree-back';
      back.innerHTML = '<span class="icon">\u2B06</span><span class="name">..</span>';
      back.onclick = () => loadFileTree(parent);
      tree.appendChild(back);
    }

    for (const item of (data.entries || [])) {
      const el = document.createElement('div');
      el.className = 'file-tree-item';
      const icon = item.is_dir ? '\uD83D\uDCC1' : '\uD83D\uDCC4';
      const safePath = _escJs(item.path);
      const pinType = item.is_dir ? 'directory' : 'file';
      el.innerHTML = `<span class="icon">${icon}</span><span class="name">${_esc(item.name)}</span><button class="ctx-pin-btn" title="Pin to context" onclick="event.stopPropagation(); addPin('${safePath}', '${pinType}')">&#x1F4CC;</button>`;
      if (item.is_dir) {
        el.onclick = () => loadFileTree(item.path);
      }
      tree.appendChild(el);
    }
  } catch(e) { console.warn('loadFileTree failed', e); }
}

// ── Context Pin System ──

async function loadPins() {
  try {
    const res = await fetch('/api/workspace/context/pins');
    const data = await res.json();
    const pins = data.pins || [];
    _pinsLoaded = true;
    const list = document.getElementById('ctxPinList');
    const count = document.getElementById('ctxPinCount');
    if (count) count.textContent = pins.filter(p => p.active).length;
    if (!list) return;
    if (pins.length === 0) {
      list.innerHTML = '<div class="ctx-empty">No pinned files. Browse below to pin.</div>';
      return;
    }
    list.innerHTML = pins.map(p => {
      const eid = _escJs(p.pin_id);
      return `
      <div class="ctx-pin-item ${p.active ? '' : 'ctx-pin-muted'} ${p.exists ? '' : 'ctx-pin-missing'}">
        <span class="ctx-pin-icon">${p.pin_type === 'directory' ? '\uD83D\uDCC1' : '\uD83D\uDCC4'}</span>
        <span class="ctx-pin-path" title="${_esc(p.path)}">${_esc(p.path)}</span>
        <div class="ctx-pin-actions">
          <button class="ctx-pin-action" title="${p.active ? 'Mute' : 'Unmute'}" onclick="togglePin('${eid}', ${p.active ? 0 : 1})">${p.active ? '\u23F8' : '\u25B6'}</button>
          <button class="ctx-pin-action ctx-pin-remove" title="Remove" onclick="removePin('${eid}')">\u2716</button>
        </div>
      </div>
    `}).join('');
  } catch(e) { console.warn('loadPins failed', e); }
}

async function addPin(path, pinType) {
  try {
    const res = await fetch('/api/workspace/context/pins', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-XJD-Request': '1'},
      body: JSON.stringify({path, pin_type: pinType}),
    });
    const data = await res.json();
    if (data.duplicate) { return; }
    await loadPins();
    await loadContextPreview();
  } catch(e) { console.warn('addPin failed', e); }
}

async function removePin(pinId) {
  try {
    await fetch('/api/workspace/context/pins/' + pinId, {method: 'DELETE', headers: {'X-XJD-Request': '1'}});
    await loadPins();
    await loadContextPreview();
  } catch(e) { console.warn('removePin failed', e); }
}

async function togglePin(pinId, active) {
  try {
    await fetch('/api/workspace/context/pins/' + pinId, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json', 'X-XJD-Request': '1'},
      body: JSON.stringify({active}),
    });
    await loadPins();
    await loadContextPreview();
  } catch(e) { console.warn('togglePin failed', e); }
}

async function loadContextPreview() {
  const el = document.getElementById('ctxPreview');
  if (!el) return;
  try {
    const res = await fetch('/api/workspace/context/preview');
    const data = await res.json();
    if (!data.preview) {
      el.innerHTML = '<div class="ctx-empty">Pin files to see injected context preview.</div>';
      return;
    }
    const charCount = data.char_count || 0;
    const pct = Math.min(100, Math.round(charCount / 50000 * 100));
    el.innerHTML = `<div class="ctx-preview-header"><span>Injected Context</span><span class="ctx-char-count">${charCount.toLocaleString()} chars (${pct}% budget)</span></div><pre class="ctx-preview-content">${_esc(data.preview)}</pre>`;
  } catch(e) { el.innerHTML = `<div class="ctx-empty">Error: ${_esc(e.message)}</div>`; }
}

async function loadActivity() {
  const el = document.getElementById('ctxActivity');
  if (!el) return;
  try {
    const res = await fetch('/api/workspace/context/activity');
    const data = await res.json();
    const acts = data.activities || [];
    if (acts.length === 0) {
      el.innerHTML = '<div class="ctx-empty">No file activity yet.</div>';
      return;
    }
    const actionIcons = {read: '\uD83D\uDC41', write: '\u270F\uFE0F', edit: '\u2702\uFE0F', list: '\uD83D\uDCC2', create: '\u2795', delete: '\u274C'};
    el.innerHTML = acts.map(a => `
      <div class="ctx-activity-item">
        <span class="ctx-activity-icon">${actionIcons[a.action] || '\u2022'}</span>
        <span class="ctx-activity-path">${_esc(a.path)}</span>
        <span class="ctx-activity-time">${_timeAgo(a.timestamp)}</span>
      </div>
    `).join('');
  } catch(e) { el.innerHTML = `<div class="ctx-empty">Error: ${_esc(e.message)}</div>`; }
}

function toggleFileBrowser() {
  const browse = document.getElementById('ctxBrowse');
  const icon = document.getElementById('ctxBrowseIcon');
  if (!browse) return;
  const show = browse.style.display === 'none';
  browse.style.display = show ? 'block' : 'none';
  if (icon) icon.innerHTML = show ? '&#x25BC;' : '&#x25B6;';
  if (show && !_filesLoaded) loadFileTree('.');
}

function switchCtxTab(tab) {
  document.querySelectorAll('.ctx-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.ctx-tab-content').forEach(c => c.style.display = 'none');
  const btn = document.querySelector(`.ctx-tab[onclick*="${tab}"]`);
  if (btn) btn.classList.add('active');
  const el = document.getElementById(tab === 'preview' ? 'ctxPreview' : 'ctxActivity');
  if (el) el.style.display = 'block';
  if (tab === 'preview') loadContextPreview();
  if (tab === 'activity') loadActivity();
}

// ══════════════════════════════════════════════════════════════
//  Memory Panel
// ══════════════════════════════════════════════════════════════
async function loadMemories(query) {
  const list = document.getElementById('memoryList');
  if (!list) return;
  list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-tertiary)">Loading...</div>';
  try {
    const url = query ? '/api/workspace/memory/search?q=' + encodeURIComponent(query) : '/api/workspace/memory/list';
    const res = await fetch(url);
    if (!res.ok) { throw new Error(res.status + ' ' + res.statusText); }
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { throw new Error('Invalid response'); }
    const memories = data.memories || [];
    if (memories.length === 0) {
      list.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-tertiary)">No memories found</div>';
      return;
    }
    list.innerHTML = memories.map(m => `
      <div class="memory-card" data-id="${m.id}">
        <div class="memory-card-header">
          <span class="memory-card-type">${_esc(m.type || 'general')}</span>
          ${m.match_type ? `<span style="font-size:10px;color:var(--text-tertiary);margin-left:4px">${_esc(m.match_type)}${m.relevance ? ' ' + m.relevance : ''}</span>` : ''}
          <div class="memory-card-actions">
            <button class="memory-edit-btn" onclick="editMemory('${m.id}')" title="Edit">&#9998;</button>
            <button class="memory-delete-btn" onclick="deleteMemory('${m.id}')" title="Delete">\uD83D\uDDD1</button>
          </div>
        </div>
        <div class="memory-card-content">${_esc(m.content || '')}</div>
        ${m.tags && m.tags.length ? `<div class="memory-card-tags">${m.tags.map(t => `<span class="memory-tag">${_esc(t)}</span>`).join('')}</div>` : ''}
      </div>
    `).join('');
  } catch(e) { list.innerHTML = `<div style="text-align:center;padding:20px;color:var(--red)">Error: ${_esc(e.message)}</div>`; }
}

function searchMemory() {
  const q = document.getElementById('memorySearch')?.value?.trim();
  loadMemories(q || undefined);
}

async function deleteMemory(id) {
  try {
    await fetch('/api/workspace/memory/' + id, { method: 'DELETE', headers: { 'X-XJD-Request': '1' } });
    loadMemories();
  } catch(e) { console.warn('deleteMemory failed', e); }
}

function showAddMemory() {
  const form = document.getElementById('addMemoryForm');
  if (!form) { console.error('addMemoryForm not found'); return; }
  document.getElementById('editMemoryId').value = '';
  document.getElementById('newMemoryContent').value = '';
  document.getElementById('newMemoryType').value = 'fact';
  document.getElementById('newMemoryImportance').value = 'medium';
  form.classList.add('visible');
  document.getElementById('newMemoryContent').focus();
}

function hideAddMemory() {
  const form = document.getElementById('addMemoryForm');
  if (form) form.classList.remove('visible');
  document.getElementById('editMemoryId').value = '';
  document.getElementById('newMemoryContent').value = '';
}

async function editMemory(id) {
  try {
    const res = await fetch('/api/workspace/memory/detail?id=' + encodeURIComponent(id));
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    if (data.error) { console.warn(data.error); return; }
    const form = document.getElementById('addMemoryForm');
    if (!form) return;
    document.getElementById('editMemoryId').value = id;
    document.getElementById('newMemoryContent').value = data.content || '';
    document.getElementById('newMemoryType').value = data.type || 'fact';
    document.getElementById('newMemoryImportance').value = data.importance || 'medium';
    form.classList.add('visible');
    document.getElementById('newMemoryContent').focus();
  } catch(e) { console.warn('editMemory failed', e); }
}

async function saveMemory() {
  const content = document.getElementById('newMemoryContent')?.value?.trim();
  if (!content) return;
  const editId = document.getElementById('editMemoryId')?.value;
  const type = document.getElementById('newMemoryType')?.value || 'fact';
  const importance = document.getElementById('newMemoryImportance')?.value || 'medium';
  try {
    let res;
    if (editId) {
      res = await fetch('/api/workspace/memory/' + editId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-XJD-Request': '1' },
        body: JSON.stringify({ content, type, importance }),
      });
    } else {
      res = await fetch('/api/workspace/memory/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-XJD-Request': '1' },
        body: JSON.stringify({ content, type, importance }),
      });
    }
    if (res.ok) {
      hideAddMemory();
      loadMemories();
    } else {
      const err = await res.json().catch(() => ({}));
      console.warn('saveMemory error:', res.status, err);
    }
  } catch(e) { console.warn('saveMemory failed', e); }
}

async function loadMemoryHealth() {
  const list = document.getElementById('memoryList');
  if (!list) return;
  list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-tertiary)">Loading health data...</div>';
  try {
    const res = await fetch('/api/workspace/memory-health');
    if (!res.ok) throw new Error(res.status + ' ' + res.statusText);
    const h = await res.json();
    let html = '<div class="memory-health">';

    // 学习统计
    if (h.learning) {
      const l = h.learning;
      html += `<div class="mh-section"><div class="mh-title">Learning Stats</div>
        <div class="mh-grid">
          <div class="mh-stat"><span class="mh-num">${l.total_turns}</span><span class="mh-label">Turns</span></div>
          <div class="mh-stat"><span class="mh-num">${l.successful_turns}</span><span class="mh-label">Success</span></div>
          <div class="mh-stat"><span class="mh-num">${l.skills_created}</span><span class="mh-label">Skills</span></div>
          <div class="mh-stat"><span class="mh-num">${l.memories_extracted}</span><span class="mh-label">Extracted</span></div>
        </div></div>`;
    }

    // 反馈分布
    if (h.feedback && Object.keys(h.feedback).length) {
      const pos = h.feedback.positive || 0, neg = h.feedback.negative || 0;
      html += `<div class="mh-section"><div class="mh-title">Feedback</div>
        <div class="mh-grid">
          <div class="mh-stat mh-pos"><span class="mh-num">${pos}</span><span class="mh-label">Positive</span></div>
          <div class="mh-stat mh-neg"><span class="mh-num">${neg}</span><span class="mh-label">Negative</span></div>
        </div></div>`;
    }

    // 合并历史
    if (h.consolidations && h.consolidations.length) {
      html += `<div class="mh-section"><div class="mh-title">Consolidations (${h.consolidations.length})</div>`;
      h.consolidations.forEach(c => {
        html += `<div class="mh-item">${c.source_count} memories merged &rarr; ${c.result_id.slice(0,8)} <span class="mh-time">${_timeAgo(c.created_at)}</span></div>`;
      });
      html += '</div>';
    }

    // 反思洞察
    if (h.reflections && h.reflections.length) {
      html += `<div class="mh-section"><div class="mh-title">Reflections</div>`;
      h.reflections.forEach(r => {
        html += `<div class="mh-item"><span class="memory-card-type">${_esc(r.type)}</span> ${_esc(r.content)}</div>`;
      });
      html += '</div>';
    }

    // META 记忆
    if (h.meta_memories && h.meta_memories.length) {
      html += `<div class="mh-section"><div class="mh-title">Meta Memories</div>`;
      h.meta_memories.forEach(m => {
        html += `<div class="mh-item">${_esc(m.content)}</div>`;
      });
      html += '</div>';
    }

    // Top/Bottom 有用性
    if (h.top_useful && h.top_useful.length) {
      html += `<div class="mh-section"><div class="mh-title">Most Useful</div>`;
      h.top_useful.forEach(m => {
        html += `<div class="mh-item">${_esc(m.content)} <span class="mh-score">${(m.score*100).toFixed(0)}%</span></div>`;
      });
      html += '</div>';
    }
    if (h.bottom_useful && h.bottom_useful.length) {
      html += `<div class="mh-section"><div class="mh-title">Least Useful</div>`;
      h.bottom_useful.forEach(m => {
        html += `<div class="mh-item">${_esc(m.content)} <span class="mh-score">${(m.score*100).toFixed(0)}%</span></div>`;
      });
      html += '</div>';
    }

    html += '</div>';
    list.innerHTML = html;
  } catch(e) { list.innerHTML = `<div style="text-align:center;padding:20px;color:var(--red)">Error: ${_esc(e.message)}</div>`; }
}

function _timeAgo(ts) {
  if (!ts) return '';
  const diff = (Date.now()/1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}

// ══════════════════════════════════════════════════════════════
//  Skills Panel
// ══════════════════════════════════════════════════════════════
let _skillsCache = [];
let _expandedSkillId = null;

async function loadSkills() {
  const grid = document.getElementById('skillsGrid');
  if (!grid) return;
  grid.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-tertiary);grid-column:1/-1">Loading...</div>';
  try {
    const cat = document.getElementById('skillsFilter')?.value || '';
    const url = '/api/admin/skills' + (cat ? '?category=' + cat : '');
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.status + ' ' + res.statusText);
    const data = await res.json();
    _skillsCache = data.skills || [];
    renderSkillCards();
  } catch(e) {
    grid.innerHTML = `<div style="text-align:center;padding:20px;color:var(--red);grid-column:1/-1">Error: ${_esc(e.message)}</div>`;
  }
}

function filterSkillCards() {
  renderSkillCards();
}

function renderSkillCards() {
  const grid = document.getElementById('skillsGrid');
  if (!grid) return;
  const q = (document.getElementById('skillsSearch')?.value || '').toLowerCase();
  const skills = _skillsCache.filter(s => !q || s.name.toLowerCase().includes(q) || (s.description||'').toLowerCase().includes(q));
  if (skills.length === 0) {
    grid.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-tertiary);grid-column:1/-1">No skills found</div>';
    return;
  }
  grid.innerHTML = skills.map(s => {
    const rate = s.success_rate != null ? (s.success_rate * 100).toFixed(0) + '%' : '-';
    const badgeCls = s.success_rate >= 0.8 ? 'badge' : 'badge warn';
    const expanded = _expandedSkillId === s.skill_id;
    const statusCls = s.status === 'draft' ? 'badge warn' : s.status === 'deprecated' ? 'badge danger' : 'badge';
    const statusLabel = s.status || 'active';
    const sourceLabel = s.source || '';
    const priceLabel = s.price > 0 ? '\u00a5' + s.price : '';
    let detail = '';
    if (expanded) {
      const tags = (s.tags||[]).map(t => `<span>${_esc(t)}</span>`).join('');
      const examples = (s.examples||[]).map(e => `<span>${_esc(e)}</span>`).join(', ');
      const steps = (s.steps||[]).map((st,i) => {
        const tool = st.tool ? `<span class="step-tool">[${_esc(st.tool)}]</span>` : '';
        return `<li><span class="step-num">${i+1}</span>${_esc(st.description||'')} ${tool}</li>`;
      }).join('');
      detail = `<div class="skill-detail">
        <div class="skill-detail-section"><label>Trigger</label>${_esc(s.trigger||'\u2014')}</div>
        ${tags ? `<div class="skill-detail-section"><label>Tags</label><div class="skill-detail-tags">${tags}</div></div>` : ''}
        ${examples ? `<div class="skill-detail-section"><label>Examples</label>${examples}</div>` : ''}
        <div class="skill-detail-section"><label>Steps (${(s.steps||[]).length})</label><ul class="skill-detail-steps">${steps||'<li>No steps</li>'}</ul></div>
        <div class="skill-detail-section"><label>Stats</label>v${s.version||1} \u00b7 ${s.use_count||0} uses \u00b7 ${rate} success</div>
        <div class="skill-test-area">
          <input type="text" id="testInput_${s.skill_id}" placeholder="Test trigger phrase...">
          <button class="btn-secondary" onclick="testSkill('${s.skill_id}')">Test</button>
        </div>
        <div id="testResult_${s.skill_id}"></div>
        <div class="skill-detail-actions">
          <button onclick="editSkill('${s.skill_id}')">Edit</button>
          <button onclick="publishSkill('${s.skill_id}')">Publish</button>
          <button onclick="viewVersions('${s.skill_id}')">Versions</button>
          <button class="danger" onclick="deleteSkill('${s.skill_id}','${_esc(s.name)}')">Delete</button>
        </div>
      </div>`;
    }
    return `<div class="skill-card${expanded?' expanded':''}" onclick="toggleSkillDetail('${s.skill_id}',event)">
      <div class="skill-card-name">${_esc(s.name)}</div>
      <div class="skill-card-desc">${_esc(s.description || '')}</div>
      <div class="skill-card-meta">
        <span class="${statusCls}">${statusLabel}</span>
        ${sourceLabel ? `<span class="badge" style="background:var(--accent-dim);color:var(--accent)">${sourceLabel}</span>` : ''}
        ${priceLabel ? `<span class="badge" style="background:rgba(245,158,11,0.15);color:var(--orange)">${priceLabel}</span>` : ''}
        <span class="${badgeCls}">${rate}</span>
        <span>${s.use_count || 0} uses</span>
        <span>v${s.version || '1.0.0'}</span>
      </div>${detail}
    </div>`;
  }).join('');
}

function toggleSkillDetail(id, ev) {
  if (ev.target.closest('button,input')) return;
  _expandedSkillId = _expandedSkillId === id ? null : id;
  renderSkillCards();
}

// ── Modal ──
function openSkillModal(skill) {
  document.getElementById('skillModalTitle').textContent = skill ? 'Edit Skill' : 'New Skill';
  document.getElementById('skillEditId').value = skill ? skill.skill_id : '';
  document.getElementById('skillName').value = skill ? skill.name : '';
  document.getElementById('skillDesc').value = skill ? skill.description : '';
  document.getElementById('skillTrigger').value = skill ? skill.trigger : '';
  document.getElementById('skillCategory').value = skill ? skill.category : 'general';
  document.getElementById('skillTags').value = skill ? (skill.tags||[]).join(', ') : '';
  document.getElementById('skillExamples').value = skill ? (skill.examples||[]).join('\n') : '';
  const editor = document.getElementById('skillStepsEditor');
  editor.innerHTML = '';
  (skill ? skill.steps||[] : []).forEach(st => addSkillStep(st));
  document.getElementById('skillModalOverlay').classList.add('open');
}

function closeSkillModal() {
  document.getElementById('skillModalOverlay').classList.remove('open');
}

function addSkillStep(step) {
  const editor = document.getElementById('skillStepsEditor');
  const div = document.createElement('div');
  div.className = 'skill-step-item';
  div.innerHTML = `<input type="text" class="step-desc" placeholder="Step description" value="${_esc((step&&step.description)||'')}">
    <input type="text" class="step-tool" placeholder="Tool (optional)" value="${_esc((step&&step.tool)||'')}">
    <button class="step-remove" onclick="this.parentElement.remove()">&times;</button>`;
  editor.appendChild(div);
}

async function saveSkill() {
  const id = document.getElementById('skillEditId').value;
  const body = {
    name: document.getElementById('skillName').value.trim(),
    description: document.getElementById('skillDesc').value.trim(),
    trigger: document.getElementById('skillTrigger').value.trim(),
    category: document.getElementById('skillCategory').value,
    tags: document.getElementById('skillTags').value.split(',').map(t=>t.trim()).filter(Boolean),
    examples: document.getElementById('skillExamples').value.split('\n').map(e=>e.trim()).filter(Boolean),
    steps: [...document.querySelectorAll('.skill-step-item')].map(el => {
      const o = { description: el.querySelector('.step-desc').value.trim() };
      const tool = el.querySelector('.step-tool').value.trim();
      if (tool) o.tool = tool;
      return o;
    }),
  };
  if (!body.name) { alert('Name is required'); return; }
  try {
    const url = id ? `/api/admin/skills/${id}` : '/api/admin/skills';
    const method = id ? 'PUT' : 'POST';
    const res = await fetch(url, { method, headers:{'Content-Type':'application/json', 'X-XJD-Request': '1'}, body: JSON.stringify(body) });
    if (!res.ok) { const e = await res.json(); throw new Error(e.error||res.statusText); }
    closeSkillModal();
    await loadSkills();
  } catch(e) { alert('Save failed: ' + e.message); }
}

function editSkill(id) {
  const skill = _skillsCache.find(s => s.skill_id === id);
  if (skill) openSkillModal(skill);
}

async function deleteSkill(id, name) {
  if (!confirm(`Delete skill "${name}"?`)) return;
  try {
    const res = await fetch(`/api/admin/skills/${id}`, { method: 'DELETE', headers: {'X-XJD-Request': '1'} });
    if (!res.ok) { const e = await res.json(); throw new Error(e.error||res.statusText); }
    _expandedSkillId = null;
    await loadSkills();
  } catch(e) { alert('Delete failed: ' + e.message); }
}

async function testSkill(id) {
  const input = document.getElementById('testInput_' + id)?.value?.trim();
  const resultDiv = document.getElementById('testResult_' + id);
  if (!input) { resultDiv.innerHTML = ''; return; }
  resultDiv.innerHTML = '<div style="color:var(--text-tertiary)">Testing...</div>';
  try {
    const res = await fetch(`/api/admin/skills/${id}/test`, {
      method: 'POST', headers:{'Content-Type':'application/json', 'X-XJD-Request': '1'},
      body: JSON.stringify({ test_input: input }),
    });
    const data = await res.json();
    if (data.matched) {
      resultDiv.innerHTML = `<div class="skill-test-result match">Matched this skill</div>`;
    } else if (data.matched_skill) {
      resultDiv.innerHTML = `<div class="skill-test-result no-match">Matched different skill: ${_esc(data.matched_skill.name)}</div>`;
    } else {
      resultDiv.innerHTML = `<div class="skill-test-result no-match">No skill matched</div>`;
    }
  } catch(e) { resultDiv.innerHTML = `<div class="skill-test-result no-match">Error: ${_esc(e.message)}</div>`; }
}

// ── Hub Modal ──
function openHubModal() {
  document.getElementById('hubModalOverlay').classList.add('open');
  document.getElementById('hubResults').innerHTML = '<div style="color:var(--text-tertiary);text-align:center;padding:20px">Search for skills in XjdHub</div>';
}
function closeHubModal() {
  document.getElementById('hubModalOverlay').classList.remove('open');
}

async function hubSearch() {
  const q = document.getElementById('hubSearchInput')?.value?.trim() || '';
  const container = document.getElementById('hubResults');
  container.innerHTML = '<div style="color:var(--text-tertiary);text-align:center;padding:20px">Searching...</div>';
  try {
    const token = localStorage.getItem('xjd_token');
    const headers = {'X-XJD-Request':'1'};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const res = await fetch('/api/admin/hub/search?q=' + encodeURIComponent(q), {headers});
    const data = await res.json();
    const results = data.results || [];
    if (!results.length) {
      container.innerHTML = '<div style="color:var(--text-tertiary);text-align:center;padding:20px">No results</div>';
      return;
    }
    container.innerHTML = results.map(r => {
      const price = r.price > 0 ? '\u00a5' + r.price : 'Free';
      const tags = (r.tags||[]).map(t => `<span style="background:var(--accent-dim);color:var(--accent);padding:1px 6px;border-radius:4px;font-size:11px">${_esc(t)}</span>`).join(' ');
      return `<div style="padding:12px;border-bottom:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><strong style="color:var(--text-primary)">${_esc(r.name)}</strong> <span style="color:var(--text-tertiary);font-size:12px">v${r.version}</span></div>
          <button class="btn-primary" style="padding:4px 12px;font-size:12px" onclick="hubInstall('${_esc(r.name)}')">Install</button>
        </div>
        <div style="color:var(--text-secondary);font-size:13px;margin:4px 0">${_esc(r.description)}</div>
        <div style="display:flex;gap:8px;align-items:center;font-size:12px;color:var(--text-tertiary)">
          <span>${_esc(r.author)}</span> <span>${price}</span> <span>${r.downloads} downloads</span> ${tags}
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    container.innerHTML = `<div style="color:var(--red);text-align:center;padding:20px">Error: ${_esc(e.message)}</div>`;
  }
}

async function hubInstall(name) {
  if (!confirm('Install skill "' + name + '"?')) return;
  try {
    const token = localStorage.getItem('xjd_token');
    const headers = {'Content-Type':'application/json','X-XJD-Request':'1'};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const res = await fetch('/api/admin/hub/install', {method:'POST', headers, body: JSON.stringify({name})});
    const data = await res.json();
    if (data.status === 'ok') {
      alert('Installed: ' + (data.skill_id || name));
      closeHubModal();
      loadSkills();
    } else {
      alert('Install failed: ' + (data.error || 'unknown'));
    }
  } catch(e) { alert('Error: ' + e.message); }
}

async function publishSkill(id) {
  if (!confirm('Publish skill to XjdHub?')) return;
  try {
    const token = localStorage.getItem('xjd_token');
    const headers = {'Content-Type':'application/json','X-XJD-Request':'1'};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const res = await fetch('/api/admin/hub/publish', {method:'POST', headers, body: JSON.stringify({skill_id: id})});
    const data = await res.json();
    if (data.status === 'ok') {
      alert('Published successfully');
      loadSkills();
    } else {
      alert('Publish failed: ' + (data.error || 'unknown'));
    }
  } catch(e) { alert('Error: ' + e.message); }
}

async function viewVersions(id) {
  try {
    const token = localStorage.getItem('xjd_token');
    const headers = {'X-XJD-Request':'1'};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const res = await fetch(`/api/admin/skills/${id}/versions`, {headers});
    const data = await res.json();
    const versions = data.versions || [];
    if (!versions.length) { alert('No version history'); return; }
    const list = versions.map(v => `v${v.version} (${new Date(v.updated_at*1000).toLocaleDateString()}) - ${v.changelog||'no changelog'}`).join('\n');
    const ver = prompt('Version history:\n\n' + list + '\n\nEnter version to rollback (or cancel):');
    if (ver) {
      const rres = await fetch(`/api/admin/skills/${id}/rollback`, {
        method:'POST', headers:{'Content-Type':'application/json','X-XJD-Request':'1',...(token?{'Authorization':'Bearer '+token}:{})},
        body: JSON.stringify({version: ver})
      });
      const rdata = await rres.json();
      if (rdata.status === 'ok') { alert('Rolled back to v' + ver); loadSkills(); }
      else alert('Rollback failed: ' + (rdata.error || 'unknown'));
    }
  } catch(e) { alert('Error: ' + e.message); }
}

// ══════════════════════════════════════════════════════════════
//  Inspector Panel
// ══════════════════════════════════════════════════════════════
let _inspectorSubscribed = false;
const _inspectorEvents = [];
let _inspectorFilter = 'all';
let _inspectorSearch = '';
let _inspectorAutoScroll = true;
const _inspectorStats = { llm: 0, tools: 0, tokens: 0, duration: 0 };

const _badgeClass = {
  llm_request: 'llm', llm_response: 'llm',
  tool_call: 'tool', tool_result: 'tool',
  message_in: 'msg', message_out: 'msg',
  cron_start: 'cron', cron_complete: 'cron',
  thinking: 'llm', error: 'error',
};

const _badgeLabel = {
  llm_request: 'LLM REQ', llm_response: 'LLM RES',
  tool_call: 'TOOL', tool_result: 'RESULT',
  message_in: 'MSG IN', message_out: 'MSG OUT',
  cron_start: 'CRON', cron_complete: 'CRON OK',
  thinking: 'THINK', error: 'ERROR',
};

const _inspectorFilterMap = {
  all: null,
  llm: ['llm_request', 'llm_response'],
  tools: ['tool_call', 'tool_result'],
  messages: ['message_in', 'message_out'],
  cron: ['cron_start', 'cron_complete'],
  errors: ['error'],
};

function subscribeInspector() {
  if (_inspectorSubscribed || !ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({ type: 'inspector_subscribe' }));
  _inspectorSubscribed = true;
  _loadInspectorHistory();
}

function addInspectorEvent(evt) {
  _inspectorEvents.push(evt);
  if (_inspectorEvents.length > 2000) _inspectorEvents.splice(0, _inspectorEvents.length - 1500);
  _updateInspectorStats(evt);
  _clearEmptyState();
  if (!_matchesFilter(evt) || !_matchesSearch(evt)) return;
  _renderInspectorEvent(evt);
}

function _clearEmptyState() {
  const empty = document.querySelector('.inspector-empty');
  if (empty) empty.remove();
}

function _matchesFilter(evt) {
  if (_inspectorFilter === 'all') return true;
  const types = _inspectorFilterMap[_inspectorFilter];
  return types && types.includes(evt.event_type);
}

function _matchesSearch(evt) {
  if (!_inspectorSearch) return true;
  const q = _inspectorSearch.toLowerCase();
  return (evt.title || '').toLowerCase().includes(q) ||
         (evt.detail || '').toLowerCase().includes(q) ||
         (evt.event_type || '').toLowerCase().includes(q);
}

function _renderInspectorEvent(evt) {
  const timeline = document.getElementById('inspectorTimeline');
  if (!timeline) return;
  const el = document.createElement('div');
  el.className = 'inspector-event';
  el.dataset.type = evt.event_type;
  const time = new Date(evt.timestamp * 1000).toLocaleTimeString('en-US', {hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const badge = _badgeClass[evt.event_type] || 'msg';
  const label = _badgeLabel[evt.event_type] || evt.event_type.toUpperCase();
  const detail = _esc(evt.detail || '');
  const needExpand = detail.length > 80;
  let durHtml = '';
  if (evt.duration_ms) {
    const cls = evt.duration_ms > 5000 ? 'very-slow' : evt.duration_ms > 2000 ? 'slow' : '';
    durHtml = `<span class="inspector-event-duration ${cls}">${evt.duration_ms}ms</span>`;
  }
  el.innerHTML = `
    <div class="inspector-event-body">
      <div class="inspector-event-header">
        <span class="inspector-event-badge ${badge}">${label}</span>
        <span class="inspector-event-time">${time}</span>
        ${durHtml}
      </div>
      <div class="inspector-event-title">${_esc(evt.title || evt.event_type)}</div>
      ${detail ? `<div class="inspector-event-detail">${detail}</div>` : ''}
      ${needExpand ? '<button class="inspector-event-toggle" onclick="this.previousElementSibling.classList.toggle(\'expanded\');this.textContent=this.textContent===\'expand\'?\'collapse\':\'expand\'">expand</button>' : ''}
    </div>
  `;
  timeline.appendChild(el);
  if (_inspectorAutoScroll) timeline.scrollTop = timeline.scrollHeight;
}

function _updateInspectorStats(evt) {
  if (evt.event_type === 'llm_response') {
    _inspectorStats.llm++;
    const m = (evt.detail || '').match(/tokens=(\d+)\+(\d+)/);
    if (m) _inspectorStats.tokens += parseInt(m[1]) + parseInt(m[2]);
    if (evt.duration_ms) _inspectorStats.duration += evt.duration_ms;
  } else if (evt.event_type === 'tool_call') {
    _inspectorStats.tools++;
  }
  _renderInspectorStats();
}

function _renderInspectorStats() {
  const fmt = n => n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
  const el = id => document.getElementById(id);
  if (el('statLLM')) el('statLLM').textContent = _inspectorStats.llm;
  if (el('statTools')) el('statTools').textContent = _inspectorStats.tools;
  if (el('statTokens')) el('statTokens').textContent = fmt(_inspectorStats.tokens);
  if (el('statTime')) el('statTime').textContent = (_inspectorStats.duration / 1000).toFixed(1) + 's';
}

function filterInspector(type) {
  _inspectorFilter = type;
  document.querySelectorAll('.inspector-filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === type));
  _rerenderTimeline();
}

function searchInspector(q) {
  _inspectorSearch = q;
  _rerenderTimeline();
}

function _rerenderTimeline() {
  const timeline = document.getElementById('inspectorTimeline');
  if (!timeline) return;
  timeline.innerHTML = '';
  const filtered = _inspectorEvents.filter(e => _matchesFilter(e) && _matchesSearch(e));
  if (filtered.length === 0) {
    timeline.innerHTML = '<div class="inspector-empty"><div class="inspector-empty-icon">&#9678;</div><div>No matching events</div></div>';
    return;
  }
  filtered.forEach(e => _renderInspectorEvent(e));
}

function toggleAutoScroll() {
  _inspectorAutoScroll = !_inspectorAutoScroll;
  const btn = document.getElementById('inspectorAutoScroll');
  if (btn) btn.classList.toggle('active', _inspectorAutoScroll);
}

function clearInspector() {
  _inspectorEvents.length = 0;
  Object.keys(_inspectorStats).forEach(k => _inspectorStats[k] = 0);
  _renderInspectorStats();
  const timeline = document.getElementById('inspectorTimeline');
  if (timeline) timeline.innerHTML = '<div class="inspector-empty"><div class="inspector-empty-icon">&#9678;</div><div>Waiting for events...</div></div>';
}

async function _loadInspectorHistory() {
  try {
    const resp = await fetch('/api/admin/inspector/events?limit=50');
    if (!resp.ok) return;
    const data = await resp.json();
    (data.events || []).forEach(evt => addInspectorEvent(evt));
  } catch (e) {}
}

// ── Utility ──
function _esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ══════════════════════════════════════════════════════════════
//  Market Panel
// ══════════════════════════════════════════════════════════════
let _marketLoaded = false;
let _marketCat = '';

async function loadMarket() {
  if (_marketLoaded) return;
  _marketLoaded = true;
  await Promise.all([loadMarketCategories(), marketSearch(), loadHubBalance()]);
}

async function loadMarketCategories() {
  try {
    const token = localStorage.getItem('xjd_token');
    const headers = {'X-XJD-Request':'1'};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const res = await fetch('/api/admin/hub/categories', {headers});
    const data = await res.json();
    const sidebar = document.getElementById('marketSidebar');
    let html = '<div class="market-cat active" data-cat="" onclick="marketFilter(\'\')">All</div>';
    (data.categories || []).forEach(c => {
      html += `<div class="market-cat" data-cat="${_esc(c.category)}" onclick="marketFilter('${_esc(c.category)}')">${_esc(c.category)} <span class="market-cat-count">${c.count}</span></div>`;
    });
    sidebar.innerHTML = html;
  } catch(e) {}
}

async function marketSearch() {
  const q = (document.getElementById('marketSearchInput')?.value || '').trim();
  const sort = document.getElementById('marketSort')?.value || 'downloads';
  const grid = document.getElementById('marketGrid');
  const detail = document.getElementById('marketDetail');
  detail.style.display = 'none';
  grid.innerHTML = '<div style="color:var(--text-tertiary);text-align:center;padding:40px">Loading...</div>';
  try {
    const token = localStorage.getItem('xjd_token');
    const headers = {'X-XJD-Request':'1'};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const params = new URLSearchParams({q, sort, per_page: '50'});
    if (_marketCat) params.set('category', _marketCat);
    const res = await fetch('/api/admin/hub/search?' + params, {headers});
    const data = await res.json();
    renderMarketGrid(data.results || []);
  } catch(e) {
    grid.innerHTML = `<div style="color:var(--red);text-align:center;padding:40px">Error: ${_esc(e.message)}</div>`;
  }
}
function renderMarketGrid(skills) {
  const grid = document.getElementById('marketGrid');
  if (!skills.length) {
    grid.innerHTML = '<div style="color:var(--text-tertiary);text-align:center;padding:40px">No skills found</div>';
    return;
  }
  grid.innerHTML = skills.map(s => {
    const tags = (s.tags||[]).slice(0,3).map(t => `<span class="market-tag">${_esc(t)}</span>`).join('');
    const installed = s.installed ? '<span class="market-installed">Installed</span>' : '';
    const price = s.price > 0 ? `¥${s.price}` : 'Free';
    return `<div class="market-card" onclick="marketShowDetail('${_esc(s.slug)}')">
      <div class="market-card-header">
        <span class="market-card-name">${_esc(s.name)}</span>
        <span class="market-card-ver">v${_esc(s.version)}</span>
      </div>
      <div class="market-card-desc">${_esc(s.description)}</div>
      <div class="market-card-tags">${tags}</div>
      <div class="market-card-footer">
        <span class="market-card-stat">${s.downloads} downloads</span>
        <span class="market-card-stat">${price}</span>
        ${installed}
      </div>
    </div>`;
  }).join('');
}

function marketFilter(cat) {
  _marketCat = cat;
  document.querySelectorAll('.market-cat').forEach(el => el.classList.toggle('active', el.dataset.cat === cat));
  _marketLoaded = false;
  marketSearch();
  _marketLoaded = true;
}

async function marketShowDetail(slug) {
  const detail = document.getElementById('marketDetail');
  detail.style.display = 'block';
  detail.innerHTML = '<div style="padding:20px;color:var(--text-tertiary)">Loading...</div>';
  try {
    const token = localStorage.getItem('xjd_token');
    const headers = {'X-XJD-Request':'1'};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const res = await fetch('/api/admin/hub/skill/' + encodeURIComponent(slug), {headers});
    const data = await res.json();
    const s = data.skill;
    if (!s) { detail.innerHTML = '<div style="padding:20px;color:var(--red)">Not found</div>'; return; }
    const tags = (s.tags||[]).map(t => `<span class="market-tag">${_esc(t)}</span>`).join(' ');
    const tools = (s.tools||[]).map(t => `<code class="market-tool">${_esc(t)}</code>`).join(' ');
    const installed = s.installed ? '<span class="market-installed">Installed</span>' : '';
    const installBtn = s.installed
      ? '<button class="btn-primary" disabled>Installed</button>'
      : `<button class="btn-primary" onclick="marketInstall('${_esc(s.slug||s.name)}')">Install</button>`;
    const tryBtn = `<button class="btn-secondary" onclick="marketTryInChat('${_esc(s.name)}')">Try in Chat</button>`;
    detail.innerHTML = `<div class="market-detail-inner">
      <div class="market-detail-close" onclick="document.getElementById('marketDetail').style.display='none'">&times;</div>
      <h3>${_esc(s.name)} <span style="color:var(--text-tertiary);font-size:13px">v${_esc(s.version)}</span> ${installed}</h3>
      <p style="color:var(--text-secondary)">${_esc(s.description)}</p>
      <div style="margin:8px 0">${tags}</div>
      ${tools ? '<div style="margin:8px 0"><strong>Tools:</strong> ' + tools + '</div>' : ''}
      <div style="margin:8px 0;color:var(--text-tertiary);font-size:13px">
        Category: ${_esc(s.category)} | Downloads: ${s.downloads} | Rating: ${(s.rating_avg||0).toFixed(1)}
      </div>
      <div style="display:flex;gap:8px;margin-top:12px">${installBtn} ${tryBtn}</div>
    </div>`;
  } catch(e) {
    detail.innerHTML = `<div style="padding:20px;color:var(--red)">Error: ${_esc(e.message)}</div>`;
  }
}

async function marketInstall(name) {
  if (!confirm('Install skill "' + name + '"?')) return;
  try {
    const token = localStorage.getItem('xjd_token');
    const headers = {'Content-Type':'application/json','X-XJD-Request':'1'};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const res = await fetch('/api/admin/hub/install', {method:'POST', headers, body: JSON.stringify({name})});
    const data = await res.json();
    if (data.status === 'ok') {
      alert('Installed: ' + (data.skill_id || name));
      _marketLoaded = false;
      loadMarket();
    } else {
      alert('Install failed: ' + (data.error || 'unknown'));
    }
  } catch(e) { alert('Error: ' + e.message); }
}

function marketTryInChat(name) {
  switchTab('chat');
  const input = document.getElementById('chatInput') || document.querySelector('textarea');
  if (input) { input.value = name; input.focus(); }
}

// ══════════════════════════════════════════════════════════════
//  Recharge / Hub Account
// ══════════════════════════════════════════════════════════════
let _rechargePollingTimer = null;
let _selectedPackage = null;

async function loadHubBalance() {
  try {
    const res = await fetch('/api/admin/hub/account/balance');
    const data = await res.json();
    if (data.balance !== undefined) {
      const el = document.getElementById('hubBalanceDisplay');
      if (el) { el.textContent = data.balance; el.style.display = 'inline'; }
    }
  } catch(e) {}
}

function openRechargeModal() {
  document.getElementById('rechargeModal').style.display = 'flex';
  document.getElementById('rechargeStep1').style.display = 'block';
  document.getElementById('rechargeStep2').style.display = 'none';
  document.getElementById('rechargeStep3').style.display = 'none';
  _selectedPackage = null;
  loadRechargeContent();
}

function closeRechargeModal() {
  document.getElementById('rechargeModal').style.display = 'none';
  if (_rechargePollingTimer) { clearInterval(_rechargePollingTimer); _rechargePollingTimer = null; }
  loadHubBalance();
}

async function loadRechargeContent() {
  const authArea = document.getElementById('rechargeAuthArea');
  const pkgArea = document.getElementById('rechargePackages');

  const balRes = await fetch('/api/admin/hub/account/balance').then(r=>r.json()).catch(()=>({}));
  if (balRes.balance !== undefined) {
    authArea.innerHTML = `<div class="recharge-auth-status">Logged in as ${_esc(balRes.username||'user')} | Balance: ${balRes.balance} credits</div>`;
    loadPackages(pkgArea);
  } else {
    authArea.innerHTML = `
      <div class="recharge-auth-form">
        <input id="hubUsername" placeholder="Username" autocomplete="username">
        <input id="hubEmail" placeholder="Email (for register)" type="email" autocomplete="email">
        <input id="hubPassword" placeholder="Password" type="password" autocomplete="current-password">
        <div class="auth-row">
          <button class="btn-primary" onclick="hubLogin()">Login</button>
          <button class="btn-secondary" onclick="hubRegister()">Register</button>
        </div>
      </div>`;
    pkgArea.innerHTML = '<div style="color:var(--text-tertiary);text-align:center;padding:20px">Login to view packages</div>';
  }
}

async function hubLogin() {
  const u = document.getElementById('hubUsername')?.value?.trim();
  const p = document.getElementById('hubPassword')?.value;
  if (!u || !p) return alert('Please enter username and password');
  try {
    const res = await fetch('/api/admin/hub/account/login', {
      method:'POST', headers:{'Content-Type':'application/json','X-XJD-Request':'1'},
      body: JSON.stringify({username:u, password:p})
    });
    const data = await res.json();
    if (data.token) { loadRechargeContent(); loadHubBalance(); }
    else alert(data.error || 'Login failed');
  } catch(e) { alert('Error: ' + e.message); }
}

async function hubRegister() {
  const u = document.getElementById('hubUsername')?.value?.trim();
  const e = document.getElementById('hubEmail')?.value?.trim();
  const p = document.getElementById('hubPassword')?.value;
  if (!u || !e || !p) return alert('Please fill all fields');
  try {
    const res = await fetch('/api/admin/hub/account/register', {
      method:'POST', headers:{'Content-Type':'application/json','X-XJD-Request':'1'},
      body: JSON.stringify({username:u, email:e, password:p})
    });
    const data = await res.json();
    if (data.token) { loadRechargeContent(); loadHubBalance(); }
    else alert(data.error || 'Register failed');
  } catch(er) { alert('Error: ' + er.message); }
}

async function loadPackages(container) {
  try {
    const res = await fetch('/api/admin/hub/recharge/packages');
    const data = await res.json();
    const pkgs = data.packages || [];
    if (!pkgs.length) { container.innerHTML = '<div style="color:var(--text-tertiary);text-align:center">No packages available</div>'; return; }
    container.innerHTML = pkgs.map((p,i) => {
      const bonus = p.credits - p.amount_yuan * 10;
      const bonusHtml = bonus > 0 ? `<span class="recharge-pkg-bonus">+${bonus} bonus</span>` : '';
      return `<div class="recharge-pkg" data-idx="${i}" onclick="selectPackage(${i})">
        <div class="recharge-pkg-left">
          <span class="recharge-pkg-credits">${p.credits} credits</span>
          ${bonusHtml}
        </div>
        <span class="recharge-pkg-price">&yen;${p.amount_yuan}</span>
      </div>`;
    }).join('') + '<button class="recharge-pay-btn" id="rechargePayBtn" onclick="rechargeCreate()" disabled>Select a package</button>';
    window._rechargePackages = pkgs;
  } catch(e) { container.innerHTML = '<div style="color:var(--red)">Failed to load packages</div>'; }
}

function selectPackage(idx) {
  _selectedPackage = idx;
  document.querySelectorAll('.recharge-pkg').forEach((el,i) => el.classList.toggle('selected', i===idx));
  const btn = document.getElementById('rechargePayBtn');
  if (btn) { btn.disabled = false; btn.textContent = `Pay ¥${window._rechargePackages[idx].amount_yuan}`; }
}

async function rechargeCreate() {
  if (_selectedPackage === null) return;
  const pkg = window._rechargePackages[_selectedPackage];
  const btn = document.getElementById('rechargePayBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Creating order...'; }
  try {
    const res = await fetch('/api/admin/hub/recharge/create', {
      method:'POST', headers:{'Content-Type':'application/json','X-XJD-Request':'1'},
      body: JSON.stringify({amount: pkg.amount_yuan, pay_type:'native'})
    });
    const data = await res.json();
    if (data.error) { alert(data.error); if(btn){btn.disabled=false;btn.textContent=`Pay ¥${pkg.amount_yuan}`;} return; }
    showQrCode(data, pkg);
  } catch(e) { alert('Error: ' + e.message); if(btn){btn.disabled=false;btn.textContent=`Pay ¥${pkg.amount_yuan}`;} }
}

function showQrCode(orderData, pkg) {
  document.getElementById('rechargeStep1').style.display = 'none';
  document.getElementById('rechargeStep2').style.display = 'block';
  document.getElementById('rechargeOrderInfo').textContent = `¥${pkg.amount_yuan} → ${pkg.credits} credits`;
  const qrDiv = document.getElementById('rechargeQrCode');
  if (orderData.code_url) {
    qrDiv.innerHTML = `<img src="https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(orderData.code_url)}" alt="QR">`;
  } else {
    qrDiv.innerHTML = '<div style="color:var(--red);padding:20px">No QR code returned</div>';
  }
  startPolling(orderData.order_no, pkg);
}

function startPolling(orderNo, pkg) {
  const pollEl = document.getElementById('rechargePolling');
  let count = 0;
  if (_rechargePollingTimer) clearInterval(_rechargePollingTimer);
  _rechargePollingTimer = setInterval(async () => {
    count++;
    if (count > 180) { clearInterval(_rechargePollingTimer); pollEl.textContent = 'Payment timeout. Please try again.'; return; }
    try {
      const res = await fetch(`/api/admin/hub/recharge/status/${orderNo}`);
      const data = await res.json();
      if (data.status === 'paid') {
        clearInterval(_rechargePollingTimer);
        _rechargePollingTimer = null;
        document.getElementById('rechargeStep2').style.display = 'none';
        document.getElementById('rechargeStep3').style.display = 'block';
        document.getElementById('rechargeSuccessInfo').textContent = `+${pkg.credits} credits added to your account`;
        loadHubBalance();
      }
    } catch(e) {}
  }, 2000);
}
