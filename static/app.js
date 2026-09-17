const $ = (id) => document.getElementById(id);
let token = new URLSearchParams(location.hash.slice(1)).get('token') || sessionStorage.getItem('localscribe-access') || '';
if (location.hash) history.replaceState(null, '', location.pathname);
let current = null, dirty = false, imageURL = null, selection = 0, saving = false, uploading = false;
let listItems = [], toastTimer, searchTimer, grouping = false, groupSelection = [];
let aiView = 'edit', correctorReady = false, correctorModel = 'a local language model';
let comparedKey = '', lastEnhanceStatus = new Map();
let uncertainSpans = [], locatedSpans = [], pageLines = [], focused = -1, showUncertain = true;
const states = {queued: 'In queue', running: 'Transcribing', ready: 'Needs review', error: 'Needs attention', cancelled: 'Cancelled'};
const BUSY = ['queued', 'running'];
let pollTimer = null, statusChecked = 0;
function isBusy(note) { return !!note && (BUSY.includes(note.status) || BUSY.includes(note.enhance_status)); }

function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 7000);
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {...options.headers, Authorization: 'Bearer ' + token}});
  if (!response.ok) {
    let message = 'Request failed. Please try again.';
    try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch {}
    if (response.status === 401) { $('login').hidden = false; $('workspace').hidden = true; }
    throw new Error(message);
  }
  return response;
}
function run(action) { return async (...args) => { try { await action(...args); } catch (error) { toast(error.message); } }; }
function changed() { dirty = true; $('save-state').textContent = 'Unsaved changes'; autoGrow(); countWords(); refreshUncertainty(); }
function countWords() { $('word-count').textContent = ($('transcript').value.trim().match(/\S+/g) || []).length + ' words'; }
function mayLeave() { return !dirty || confirm('You have unsaved edits. Leave this note without saving?'); }
function uniqueDocuments() {
  const documents = new Map();
  for (const note of listItems) if (note.document_id && !documents.has(note.document_id)) {
    documents.set(note.document_id, {id: note.document_id, title: note.document_title});
  }
  return [...documents.values()].sort((a, b) => a.title.localeCompare(b.title));
}
function clearCurrent(id) {
  if (current?.id !== id) return;
  current = null; dirty = false; $('editor').hidden = true; $('empty').hidden = false;
  if (imageURL) { URL.revokeObjectURL(imageURL); imageURL = null; }
}
function askConfirm(title, copy, okLabel = 'Delete', danger = true) {
  const dialog = $('confirm-dialog');
  $('confirm-title').textContent = title; $('confirm-copy').textContent = copy; $('confirm-ok').textContent = okLabel;
  $('confirm-ok').className = danger ? 'danger-solid' : 'primary';
  dialog.showModal();
  return new Promise(resolve => {
    const closed = () => resolve(false);
    const finish = value => { dialog.removeEventListener('close', closed); dialog.close(); resolve(value); };
    dialog.addEventListener('close', closed, {once: true});
    $('confirm-form').onsubmit = event => { event.preventDefault(); finish(true); };
    $('confirm-cancel').onclick = () => finish(false);
  });
}
async function cancelNote(note) {
  await api(`/api/notes/${note.id}/cancel`, {method:'POST'}); await refresh(); toast(`Cancelled “${note.title}”.`);
}
async function deleteNote(note) {
  if (!await askConfirm('Delete this note?', `“${note.title}” and its saved transcript will be permanently deleted.`)) return;
  await api(`/api/notes/${note.id}`, {method:'DELETE'}); clearCurrent(note.id); await refresh(); toast('Note deleted from this PC.');
}
async function deleteDocument(documentId, title, pages) {
  if (!await askConfirm('Delete this document?', `“${title}” and all ${pages.length} page${pages.length === 1 ? '' : 's'} will be permanently deleted.`)) return;
  await api(`/api/documents/${documentId}`, {method:'DELETE'});
  if (pages.some(page => page.id === current?.id)) clearCurrent(current.id);
  await refresh(); toast('Document and all its pages deleted from this PC.');
}
function noteButton(note) {
  const selectedIndex = groupSelection.indexOf(note.id);
  const row = document.createElement('div'); row.className = 'note-row';
  const button = document.createElement('button'); button.className = 'note' + (current?.id === note.id && !grouping ? ' active' : '') + (selectedIndex >= 0 ? ' group-selected' : '');
  const title = document.createElement('strong');
  if (grouping) {
    const order = document.createElement('span'); order.className = 'selection-order'; order.textContent = selectedIndex >= 0 ? selectedIndex + 1 : '';
    title.append(order, document.createTextNode(note.document_id ? `${note.document_title} — page ${note.page_number}` : note.title));
  } else title.textContent = note.document_id ? `Page ${note.page_number}` : note.title;
  const meta = document.createElement('small'); meta.textContent = new Date(note.created).toLocaleDateString() + ' · ' + (note.reviewed ? 'Reviewed' : states[note.status]);
  const excerpt = document.createElement('p'); excerpt.textContent = note.excerpt || (note.status === 'error' ? 'Open to see what happened.' : 'Your text is on its way…');
  button.append(title, meta, excerpt);
  button.onclick = grouping ? () => {
    const index = groupSelection.indexOf(note.id);
    if (index >= 0) groupSelection.splice(index, 1); else groupSelection.push(note.id);
    $('group-count').textContent = `${groupSelection.length} selected · click in page order`; renderList();
  } : run(() => selectNote(note.id));
  row.append(button);
  if (!grouping) {
    const actions = document.createElement('div'); actions.className = 'note-side-actions';
    if (note.status === 'queued') {
      const cancel = document.createElement('button'); cancel.textContent = 'Cancel'; cancel.title = 'Cancel queued transcription';
      cancel.onclick = run(() => cancelNote(note)); actions.append(cancel);
    }
    if (note.status !== 'running' && note.enhance_status !== 'running') {
      const remove = document.createElement('button'); remove.textContent = 'Delete'; remove.className = 'delete-link'; remove.title = 'Delete note';
      remove.onclick = run(() => deleteNote(note)); actions.append(remove);
    }
    row.append(actions);
  }
  return row;
}
function renderList() {
  $('note-count').textContent = listItems.length;
  if (grouping) { $('notes').replaceChildren(...listItems.map(noteButton)); return; }
  const nodes = [], handled = new Set();
  for (const note of listItems) {
    if (!note.document_id) { nodes.push(noteButton(note)); continue; }
    if (handled.has(note.document_id)) continue;
    handled.add(note.document_id);
    const pages = listItems.filter(item => item.document_id === note.document_id)
      .sort((left, right) => left.page_number - right.page_number);
    const group = document.createElement('section'); group.className = 'document-group';
    const heading = document.createElement('div'); heading.className = 'document-heading';
    const label = document.createElement('span'); label.textContent = `${note.document_title} · ${pages.length} page${pages.length === 1 ? '' : 's'}`;
    const actions = document.createElement('div'); actions.className = 'document-actions';
    const exportButton = document.createElement('button'); exportButton.textContent = 'Export all';
    exportButton.onclick = run(() => download(`/api/documents/${note.document_id}/export`, `${note.document_title}.txt`));
    const deleteButton = document.createElement('button'); deleteButton.textContent = 'Delete'; deleteButton.className = 'delete-document';
    deleteButton.onclick = run(() => deleteDocument(note.document_id, note.document_title, pages));
    actions.append(exportButton, deleteButton); heading.append(label, actions);
    const pageList = document.createElement('div'); pageList.className = 'document-pages'; pageList.append(...pages.map(noteButton));
    group.append(heading, pageList); nodes.push(group);
  }
  $('notes').replaceChildren(...nodes);
}
function autoGrow() {
  const editor = $('transcript');
  editor.style.height = 'auto';
  editor.style.height = editor.scrollHeight + 'px';
}
function selectText(start, end) {
  const editor = $('transcript'), panel = $('panel-edit');
  editor.focus({preventScroll: true}); editor.setSelectionRange(start, end);
  const style = getComputedStyle(editor);
  const lineHeight = parseFloat(style.lineHeight) || 24;
  const offset = (parseFloat(style.paddingTop) || 0) + editor.value.slice(0, start).split('\n').length * lineHeight;
  panel.scrollTop = Math.max(0, offset - panel.clientHeight / 3);
}
function showRegion(region, button) {
  const highlight = $('region-highlight');
  highlight.hidden = false; highlight.style.top = `${region.top * 100}%`; highlight.style.height = `${(region.bottom - region.top) * 100}%`;
  for (const item of $('region-buttons').children) item.classList.toggle('active', item === button);
  $('image-scroll').scrollTop = Math.max(0, $('page-image').offsetHeight * region.top - 40);
  if (!dirty && current.text === current.raw_text) selectText(region.start, region.end);
  else { const start = $('transcript').value.indexOf(region.text); if (start >= 0) selectText(start, start + region.text.length); }
}
function locateSpans() {
  // Offsets come from the untouched OCR text, so each word is found again by name.
  const value = $('transcript').value, found = [];
  let cursor = 0;
  for (const span of uncertainSpans) {
    const at = value.indexOf(span.text, cursor);
    if (at < 0) continue;
    found.push({...span, at, to: at + span.text.length});
    cursor = at + span.text.length;
  }
  return found;
}
const METRICS = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'fontVariant', 'letterSpacing',
                 'wordSpacing', 'lineHeight', 'textIndent', 'textTransform', 'whiteSpace',
                 'overflowWrap', 'wordBreak', 'tabSize', 'padding', 'borderWidth', 'borderStyle'];
function matchBackdrop() {
  // The shading only lands on the right words while both boxes lay text out identically.
  const editor = $('transcript'), backdrop = $('highlight-backdrop'), style = getComputedStyle(editor);
  for (const property of METRICS) backdrop.style[property] = style[property];
  backdrop.style.borderColor = 'transparent';
  backdrop.style.width = editor.clientWidth + 'px';
}
function paintBackdrop() {
  const backdrop = $('highlight-backdrop'), editor = $('transcript'), value = editor.value;
  if (!showUncertain || !locatedSpans.length) { backdrop.replaceChildren(); return; }
  matchBackdrop();
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  for (const [index, span] of locatedSpans.entries()) {
    fragment.append(document.createTextNode(value.slice(cursor, span.at)));
    const mark = document.createElement('span');
    mark.className = 'uncertain-word' + (span.p < 0.4 ? ' doubtful' : '') + (index === focused ? ' focused' : '');
    mark.textContent = span.text;
    fragment.append(mark);
    cursor = span.to;
  }
  fragment.append(document.createTextNode(value.slice(cursor) + '\n'));
  backdrop.replaceChildren(fragment);
}
function paintBands() {
  const stage = $('uncertain-bands');
  if (!showUncertain || !pageLines.length || !locatedSpans.length) { stage.replaceChildren(); return; }
  const wanted = new Map();
  for (const [index, span] of locatedSpans.entries()) {
    if (span.line == null || !pageLines[span.line]) continue;
    const existing = wanted.get(span.line);
    if (!existing || span.p < existing.p) wanted.set(span.line, {p: span.p, focused: index === focused});
    if (index === focused) wanted.get(span.line).focused = true;
  }
  stage.replaceChildren(...[...wanted].map(([line, detail]) => {
    const band = pageLines[line], box = document.createElement('span');
    box.className = 'uncertain-band' + (detail.p < 0.4 ? ' doubtful' : '') + (detail.focused ? ' focused' : '');
    box.style.top = `${band.top * 100}%`;
    box.style.height = `${Math.max(band.bottom - band.top, 0.008) * 100}%`;
    return box;
  }));
}
function focusUncertain(index, scrollText = true) {
  if (!locatedSpans.length) return;
  focused = (index + locatedSpans.length) % locatedSpans.length;
  const span = locatedSpans[focused];
  if (scrollText) selectText(span.at, span.to);
  const band = span.line != null ? pageLines[span.line] : null;
  if (band) $('image-scroll').scrollTop = Math.max(0, $('page-image').offsetHeight * band.top - $('image-scroll').clientHeight / 3);
  $('region-highlight').hidden = true;
  for (const button of $('uncertain-buttons').children) button.classList.toggle('active', Number(button.dataset.index) === focused);
  paintBackdrop(); paintBands();
}
function refreshUncertainty() {
  locatedSpans = locateSpans();
  if (focused >= locatedSpans.length) focused = -1;
  const doubtful = locatedSpans.filter(span => span.p < 0.4).length;
  $('uncertain-summary').textContent = locatedSpans.length
    ? `${locatedSpans.length} to check · ${doubtful} scored below 40%`
    : 'The model scored every word it read as confident.';
  $('uncertain-buttons').replaceChildren(...locatedSpans.map((span, index) => {
    const button = document.createElement('button');
    button.className = 'uncertain-chip' + (span.p < 0.4 ? ' doubtful' : '') + (index === focused ? ' active' : '');
    button.dataset.index = index;
    button.title = `The model scored this reading ${Math.round(span.p * 100)}%`;
    const word = document.createElement('span'); word.textContent = span.text;
    const score = document.createElement('small'); score.textContent = `${Math.round(span.p * 100)}%`;
    button.append(word, score);
    button.onclick = () => focusUncertain(index);
    return button;
  }));
  for (const id of ['previous-uncertain', 'next-uncertain']) $(id).disabled = !locatedSpans.length;
  paintBackdrop(); paintBands();
}
function renderReview(note) {
  const regions = Array.isArray(note.regions) ? note.regions : [];
  uncertainSpans = Array.isArray(note.uncertain) ? note.uncertain : [];
  pageLines = Array.isArray(note.lines) ? note.lines : [];
  $('review-tools').hidden = note.status !== 'ready' || (!regions.length && !uncertainSpans.length);
  const scored = uncertainSpans.length || pageLines.length;
  $('confidence-block').hidden = !scored;
  $('no-confidence').hidden = !!scored;
  $('section-review').hidden = !regions.length;
  refreshUncertainty();
  $('region-highlight').hidden = true;
  $('region-buttons').replaceChildren(...regions.map(region => {
    const button = document.createElement('button'), excerpt = region.text.replace(/\s+/g, ' ').trim();
    button.textContent = `Section ${region.index}: ${excerpt.slice(0, 42) || 'No text'}`;
    button.title = excerpt; button.onclick = () => showRegion(region, button); return button;
  }));
  const cues = [...new Set((note.raw_text.match(/\b(?:\d[\d.,:/-]*|[A-Z][A-Za-z'-]{2,}|[A-Z]{2,})\b/g) || []))].slice(0, 24);
  $('risk-buttons').replaceChildren(...cues.map(cue => {
    const button = document.createElement('button'); button.textContent = cue;
    button.onclick = () => { const start = $('transcript').value.indexOf(cue); if (start >= 0) selectText(start, start + cue.length); };
    return button;
  }));
  $('risk-buttons').parentElement.hidden = !cues.length;
}
function tokenize(text) { return text.match(/\s+|\S+/g) || []; }
function lcsOps(before, after) {
  const rows = before.length, columns = after.length, width = columns + 1;
  const table = new Uint32Array((rows + 1) * width);
  for (let row = rows - 1; row >= 0; row--)
    for (let column = columns - 1; column >= 0; column--)
      table[row * width + column] = before[row] === after[column]
        ? table[(row + 1) * width + column + 1] + 1
        : Math.max(table[(row + 1) * width + column], table[row * width + column + 1]);
  const ops = [];
  const push = (type, token) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.tokens.push(token); else ops.push({type, tokens: [token]});
  };
  let row = 0, column = 0;
  while (row < rows && column < columns) {
    if (before[row] === after[column]) { push('same', before[row]); row++; column++; }
    else if (table[(row + 1) * width + column] >= table[row * width + column + 1]) push('del', before[row++]);
    else push('ins', after[column++]);
  }
  while (row < rows) push('del', before[row++]);
  while (column < columns) push('ins', after[column++]);
  return ops;
}
function diffTokens(before, after) {
  let start = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) start++;
  let endBefore = before.length, endAfter = after.length;
  while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) { endBefore--; endAfter--; }
  const middleBefore = before.slice(start, endBefore), middleAfter = after.slice(start, endAfter);
  const ops = [];
  if (start) ops.push({type: 'same', tokens: before.slice(0, start)});
  // A full table over two long pages would stall the browser; fall back to one block.
  if (middleBefore.length * middleAfter.length > 1_500_000) {
    if (middleBefore.length) ops.push({type: 'del', tokens: middleBefore});
    if (middleAfter.length) ops.push({type: 'ins', tokens: middleAfter});
  } else ops.push(...lcsOps(middleBefore, middleAfter));
  if (endBefore < before.length) ops.push({type: 'same', tokens: before.slice(endBefore)});
  return ops;
}
function paintDiff(node, ops, changeType, markClass) {
  const fragment = document.createDocumentFragment();
  for (const op of ops) {
    if (op.type !== 'same' && op.type !== changeType) continue;
    const text = op.tokens.join('');
    if (op.type === 'same' || !text.trim()) { fragment.append(document.createTextNode(text)); continue; }
    const mark = document.createElement('mark'); mark.className = markClass; mark.textContent = text;
    fragment.append(mark);
  }
  node.replaceChildren(fragment);
}
function renderComparison() {
  const key = `${current.id}:${current.raw_text.length}:${current.enhanced_text.length}`;
  if (key === comparedKey) return;
  comparedKey = key;
  const ops = diffTokens(tokenize(current.raw_text), tokenize(current.enhanced_text));
  paintDiff($('compare-left'), ops, 'del', 'diff-removed');
  paintDiff($('compare-right'), ops, 'ins', 'diff-added');
  const changes = ops.filter(op => op.type !== 'same' && op.tokens.join('').trim()).length;
  $('change-count').textContent = changes ? `· ${changes} change${changes === 1 ? '' : 's'}` : '· no changes suggested';
}
function setView(view) {
  if (view !== 'edit' && !current?.enhanced_text) view = 'edit';
  aiView = view;
  for (const [name, tab, panel] of [['edit', 'view-edit', 'panel-edit'], ['ai', 'view-ai', 'panel-ai'], ['compare', 'view-compare', 'panel-compare']]) {
    const active = name === view;
    $(tab).classList.toggle('active', active);
    $(tab).setAttribute('aria-selected', String(active));
    $(panel).hidden = !active;
  }
  $('ai-apply').hidden = view === 'edit';
  // scrollHeight is 0 while the panel is hidden, so size the editor once it is shown.
  if (view === 'edit') autoGrow();
  if (view === 'ai') $('corrected-text').textContent = current.enhanced_text;
  if (view === 'compare') renderComparison();
}
function renderEnhancement(note) {
  const usable = note.status === 'ready' && !!note.raw_text;
  const available = !!note.enhanced_text;
  const busy = ['queued','running'].includes(note.enhance_status);
  $('text-toolbar').hidden = !usable;
  $('enhance-text').disabled = busy || !correctorReady;
  $('enhance-text').textContent = available ? '✦  Run AI fix again' : '✦  Fix with local AI';
  for (const id of ['view-ai', 'view-compare']) $(id).disabled = !available;
  let message = '';
  if (!usable) message = '';
  else if (!correctorReady) message = 'The local correction model is not running, so AI correction is unavailable.';
  else if (note.enhance_status === 'queued') message = 'Waiting in the local queue…';
  else if (note.enhance_status === 'running') message = `${correctorModel} is reading the whole transcription…`;
  else if (available) message = `${correctorModel} reviewed the full page transcription. Compare it before using it.`;
  $('ai-status').textContent = message; $('ai-status').hidden = !message;
  $('ai-failure').hidden = note.enhance_status !== 'error';
  $('ai-failure').textContent = note.enhance_error || '';
  const previous = lastEnhanceStatus.get(note.id);
  lastEnhanceStatus.set(note.id, note.enhance_status);
  setView(available && ['queued','running'].includes(previous) && note.enhance_status === 'ready' ? 'compare' : aiView);
}
function renderNote(note, updateText = true) {
  if (current?.id !== note.id) { aiView = 'edit'; comparedKey = ''; focused = -1; }
  current = note; $('empty').hidden = true; $('editor').hidden = false;
  const busy = ['queued','running'].includes(note.status);
  $('processing').hidden = !busy;
  $('processing-title').textContent = note.status === 'queued' ? 'Your page is in the queue…' : 'Reading your handwriting…';
  $('cancel-job').hidden = note.status !== 'queued';
  $('note-error').hidden = !['error','cancelled'].includes(note.status); $('note-error-text').textContent = note.error;
  $('retry').hidden = !!note.text || !['error','cancelled'].includes(note.status);
  $('truncated').hidden = !note.truncated;
  $('note-state').textContent = note.reviewed ? 'Reviewed' : states[note.status];
  $('note-state').className = 'status-pill' + (note.reviewed ? ' reviewed' : '');
  for (const id of ['title','transcript','reviewed','save','copy','export']) $(id).disabled = busy;
  $('delete-note').disabled = note.status === 'running';
  if (updateText) {
    // Rewriting these on every poll would drop the caret out of text the reader is checking.
    if ($('title').value !== note.title) $('title').value = note.title;
    if ($('transcript').value !== note.text) $('transcript').value = note.text;
    $('reviewed').checked = !!note.reviewed;
    $('raw').textContent = note.raw_text; dirty = false;
    $('save-state').textContent = note.seconds != null ? `Saved locally · Transcribed in ${Math.round(note.seconds)}s` : 'Saved locally';
    autoGrow(); countWords();
  }
  renderReview(note); renderEnhancement(note); renderList();
}
async function selectNote(id) {
  if (saving || !mayLeave()) return;
  const ticket = ++selection;
  const note = await (await api('/api/notes/' + id)).json();
  const image = await (await api('/api/notes/' + id + '/image')).blob();
  if (ticket !== selection) return;
  if (imageURL) URL.revokeObjectURL(imageURL);
  imageURL = URL.createObjectURL(image); $('page-image').src = imageURL;
  renderNote(note);
}
function currentStale() {
  const row = current && listItems.find(item => item.id === current.id);
  if (!row) return false;
  return row.status !== current.status || row.enhance_status !== current.enhance_status
    || !!row.reviewed !== !!current.reviewed || row.title !== current.title;
}
async function refresh() {
  if (!token) return;
  if (!correctorReady && Date.now() - statusChecked > 10000) await updateStatus();
  const response = await api('/api/notes?q=' + encodeURIComponent($('search').value));
  listItems = await response.json(); renderList();
  if (current && !dirty && !saving && (isBusy(current) || currentStale())) {
    const id = current.id, ticket = selection;
    const note = await (await api('/api/notes/' + id)).json();
    if (current?.id === id && ticket === selection && !dirty && !saving) renderNote(note);
  }
  schedulePoll();
}
function schedulePoll() {
  clearTimeout(pollTimer);
  // Idle polling competes with the model for the same CPU cores, so back right off.
  const delay = listItems.some(isBusy) || isBusy(current) ? 2000 : 15000;
  pollTimer = setTimeout(async () => {
    if (token && !$('workspace').hidden) await refresh().catch(() => {});
    schedulePoll();
  }, delay);
}
async function updateStatus() {
  statusChecked = Date.now();
  const status = await (await api('/api/status')).json();
  $('engine-state').textContent = status.engine_ready ? 'Local transcription ready' : 'Local model is starting…';
  $('engine-dot').className = 'dot' + (status.engine_ready ? ' ready' : '');
  correctorReady = !!status.corrector_ready;
  if (status.corrector_model) correctorModel = status.corrector_model;
  return status;
}
async function connect() {
  await updateStatus();
  sessionStorage.setItem('localscribe-access', token);
  $('login').hidden = true; $('workspace').hidden = false;
  await refresh();
  schedulePoll();
}
async function save() {
  if (!current || saving) return;
  saving = true;
  for (const control of ['save','title','transcript','reviewed','export','copy']) $(control).disabled = true;
  const id = current.id;
  const values = {title: $('title').value.trim() || 'Untitled note', text: $('transcript').value, reviewed: $('reviewed').checked, revision: current.revision};
  try {
    const note = await (await api('/api/notes/' + id, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(values)})).json();
    if (current?.id === id) {
      const unchanged = $('title').value.trim() === values.title && $('transcript').value === values.text && $('reviewed').checked === values.reviewed;
      if (unchanged) renderNote(note); else { current = note; changed(); }
    }
    toast('Changes saved on this PC.');
  } finally {
    saving = false;
    for (const control of ['save','title','transcript','reviewed','export','copy']) $(control).disabled = false;
  }
  await refresh();
}
async function download(path, filename) {
  const blob = await (await api(path)).blob(), url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('login-form').onsubmit = run(async event => { event.preventDefault(); token = $('access-code').value.trim(); await connect(); });
$('upload').onchange = run(async () => {
  if (uploading) return;
  const files = [...$('upload').files]; $('upload').value = '';
  if (!files.length || !mayLeave()) return;
  uploading = true;
  try {
    for (const file of files) {
      const isPDF = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
      const limit = isPDF ? 50 : 15;
      if (file.size > limit * 1024 * 1024) { toast(`${file.name} is larger than ${limit} MB.`); continue; }
      const data = new FormData(); data.append('file', file); data.append('segment', $('segment-pages').checked);
      const result = await (await api('/api/notes', {method:'POST', body:data})).json();
      const notes = result.notes || [result];
      dirty = false; await selectNote(notes[0].id);
      if (notes.length > 1) toast(`${notes.length} PDF pages added as separate notes.`);
    }
    await refresh();
  } finally { uploading = false; }
});
$('sample').onclick = run(async () => {
  if (!mayLeave()) return;
  $('sample').disabled = true;
  try { const note = await (await api('/api/sample', {method:'POST'})).json(); dirty = false; await selectNote(note.id); await refresh(); }
  finally { $('sample').disabled = false; }
});
$('group-toggle').onclick = () => {
  grouping = true; groupSelection = []; $('group-toggle').hidden = true; $('group-actions').hidden = false;
  $('group-count').textContent = '0 selected · click in page order'; renderList();
};
$('group-cancel').onclick = () => {
  grouping = false; groupSelection = []; $('group-toggle').hidden = false; $('group-actions').hidden = true; renderList();
};
function renderDocumentOrder() {
  $('document-order').replaceChildren(...groupSelection.map((id, index) => {
    const note = listItems.find(item => item.id === id);
    const item = document.createElement('li');
    const number = document.createElement('span'); number.textContent = index + 1;
    const title = document.createElement('strong'); title.textContent = note?.title || 'Missing note';
    const actions = document.createElement('div'); actions.className = 'order-actions';
    for (const [label, offset] of [['↑', -1], ['↓', 1]]) {
      const move = document.createElement('button'); move.type = 'button'; move.textContent = label;
      move.title = offset < 0 ? 'Move page up' : 'Move page down';
      move.disabled = index + offset < 0 || index + offset >= groupSelection.length;
      move.onclick = () => { const next = index + offset; [groupSelection[index], groupSelection[next]] = [groupSelection[next], groupSelection[index]]; renderDocumentOrder(); };
      actions.append(move);
    }
    item.append(number, title, actions); return item;
  }));
}
function updateDocumentDestination() {
  const existing = !!$('document-target').value;
  $('document-name-fields').hidden = existing;
  $('document-name').required = !existing;
  $('document-form').querySelector('button[type="submit"]').textContent = existing ? 'Add pages' : 'Create document';
}
function openDocumentDialog() {
  if (!groupSelection.length) throw new Error('Select at least one note.');
  const target = $('document-target');
  target.replaceChildren(new Option('Create a new document', ''), ...uniqueDocuments().map(document => new Option(document.title, document.id)));
  target.value = ''; $('document-name').value = ''; updateDocumentDestination(); renderDocumentOrder();
  $('document-dialog').showModal(); setTimeout(() => $('document-name').focus(), 0);
}
$('group-save').onclick = run(async () => openDocumentDialog());
$('document-target').onchange = updateDocumentDestination;
$('document-dialog-cancel').onclick = () => $('document-dialog').close();
$('document-form').onsubmit = run(async event => {
  event.preventDefault();
  const documentId = $('document-target').value;
  const existing = uniqueDocuments().find(document => document.id === documentId);
  const title = existing?.title || $('document-name').value.trim();
  if (!title) { $('document-name').focus(); throw new Error('Enter a document name.'); }
  await api('/api/documents', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title, note_ids:groupSelection, document_id:documentId || null})});
  $('document-dialog').close(); grouping = false; groupSelection = []; $('group-toggle').hidden = false; $('group-actions').hidden = true;
  await refresh(); toast(documentId ? 'Pages added to the document.' : 'Document created in the selected page order.');
});
$('cancel-job').onclick = run(async () => cancelNote(current));
$('retry').onclick = run(async () => { await api('/api/notes/' + current.id + '/retry', {method:'POST'}); await refresh(); });
$('enhance-text').onclick = run(async () => {
  await api(`/api/notes/${current.id}/enhance`, {method:'POST'});
  await refresh(); toast('Local AI correction queued. It reads the whole page transcription.');
});
$('view-edit').onclick = () => setView('edit');
$('view-ai').onclick = () => setView('ai');
$('view-compare').onclick = () => setView('compare');
$('use-enhanced').onclick = run(async () => {
  if (!current?.enhanced_text) return;
  if (!await askConfirm('Use the AI-corrected text?', 'It replaces the text in the editor. Your original OCR stays saved and unchanged, and nothing is saved until you click Save changes.', 'Use it', false)) return;
  $('transcript').value = current.enhanced_text; setView('edit'); changed();
  toast('Corrected text placed in the editor. Review it, then save.');
});
$('save').onclick = run(save);
$('copy').onclick = run(async () => {
  if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText($('transcript').value); toast('Text copied.'); }
  else { $('transcript').focus(); $('transcript').select(); toast('Text selected. Use your device’s Copy command.'); }
});
$('export').onclick = run(async () => { if (dirty) await save(); await download('/api/notes/' + current.id + '/export', current.title.replace(/[<>:"/\\|?*]/g,'_') + '.txt'); });
$('delete-note').onclick = run(async () => {
  if (current) await deleteNote(current);
});
$('original').onclick = run(async () => { await download('/api/notes/' + current.id + '/image?original=true', current.original); });
$('search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(run(refresh), 200); };
$('next-uncertain').onclick = () => focusUncertain(focused + 1);
$('previous-uncertain').onclick = () => focusUncertain(focused - 1);
$('show-uncertain').onchange = () => {
  showUncertain = $('show-uncertain').checked;
  $('uncertain-buttons').hidden = !showUncertain;
  paintBackdrop(); paintBands();
};
for (const event of ['click', 'keyup']) $('transcript').addEventListener(event, () => {
  const caret = $('transcript').selectionStart;
  const index = locatedSpans.findIndex(span => caret >= span.at && caret <= span.to);
  if (index >= 0 && index !== focused) focusUncertain(index, false);
});
window.addEventListener('resize', () => { autoGrow(); paintBackdrop(); });
for (const id of ['title','transcript','reviewed']) $(id).addEventListener('input', changed);
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('keydown', run(async event => { if ((event.ctrlKey || event.metaKey) && event.key === 's') { event.preventDefault(); if (current && !$('save').disabled) await save(); } }));
if (token) run(connect)(); else $('login').hidden = false;
