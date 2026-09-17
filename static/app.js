const $ = (id) => document.getElementById(id);
let token = new URLSearchParams(location.hash.slice(1)).get('token') || sessionStorage.getItem('localscribe-access') || '';
if (location.hash) history.replaceState(null, '', location.pathname);
let current = null, dirty = false, imageURL = null, selection = 0, saving = false, uploading = false;
let listItems = [], toastTimer, searchTimer, grouping = false, groupSelection = [];
const states = {queued: 'In queue', running: 'Transcribing', ready: 'Needs review', error: 'Needs attention', cancelled: 'Cancelled'};

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
function changed() { dirty = true; $('save-state').textContent = 'Unsaved changes'; countWords(); }
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
function askConfirm(title, copy, okLabel = 'Delete') {
  const dialog = $('confirm-dialog');
  $('confirm-title').textContent = title; $('confirm-copy').textContent = copy; $('confirm-ok').textContent = okLabel;
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
function selectText(start, end) {
  const editor = $('transcript'); editor.focus(); editor.setSelectionRange(start, end);
  const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 24;
  editor.scrollTop = Math.max(0, editor.value.slice(0, start).split('\n').length * lineHeight - editor.clientHeight / 3);
}
function showRegion(region, button) {
  const highlight = $('region-highlight');
  highlight.hidden = false; highlight.style.top = `${region.top * 100}%`; highlight.style.height = `${(region.bottom - region.top) * 100}%`;
  for (const item of $('region-buttons').children) item.classList.toggle('active', item === button);
  $('image-scroll').scrollTop = Math.max(0, $('page-image').offsetHeight * region.top - 40);
  if (!dirty && current.text === current.raw_text) selectText(region.start, region.end);
  else { const start = $('transcript').value.indexOf(region.text); if (start >= 0) selectText(start, start + region.text.length); }
}
function renderReview(note) {
  const regions = Array.isArray(note.regions) ? note.regions : [];
  $('review-tools').hidden = note.status !== 'ready' || !regions.length;
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
function showVersion(enhanced) {
  if (!current) return;
  $('version-text').hidden = false;
  $('version-text').textContent = enhanced ? current.enhanced_text : current.raw_text;
  $('show-original').classList.toggle('active', !enhanced);
  $('show-enhanced').classList.toggle('active', enhanced);
}
function renderEnhancement(note) {
  const available = !!note.enhanced_text;
  const busy = ['queued','running'].includes(note.enhance_status);
  $('ai-tools').hidden = note.status !== 'ready';
  $('enhance-text').disabled = busy;
  $('enhance-text').textContent = available ? 'Re-read full page again' : 'Re-read full page';
  $('enhance-state').textContent = busy ? (note.enhance_status === 'queued' ? 'Waiting in the local queue…' : 'Re-reading the full page…') : (available ? 'Second reading ready' : '');
  $('version-switch').hidden = !available;
  $('version-text').hidden = !available;
  if (available) { $('version-text').textContent = note.raw_text; $('show-original').classList.add('active'); $('show-enhanced').classList.remove('active'); }
  $('enhance-error').hidden = note.enhance_status !== 'error';
  $('enhance-error').textContent = note.enhance_error || '';
}
function renderNote(note, updateText = true) {
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
    $('title').value = note.title; $('transcript').value = note.text; $('reviewed').checked = !!note.reviewed;
    $('raw').textContent = note.raw_text; dirty = false;
    $('save-state').textContent = note.seconds != null ? `Saved locally · Transcribed in ${Math.round(note.seconds)}s` : 'Saved locally';
    countWords();
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
async function refresh() {
  if (!token) return;
  const response = await api('/api/notes?q=' + encodeURIComponent($('search').value));
  listItems = await response.json(); renderList();
  if (current && !dirty && !saving) {
    const id = current.id, ticket = selection;
    const note = await (await api('/api/notes/' + id)).json();
    if (current?.id === id && ticket === selection && !dirty && !saving) renderNote(note);
  }
}
async function connect() {
  const status = await (await api('/api/status')).json();
  sessionStorage.setItem('localscribe-access', token);
  $('login').hidden = true; $('workspace').hidden = false;
  $('engine-state').textContent = status.engine_ready ? 'Local transcription ready' : 'Local model is starting…';
  $('engine-dot').className = 'dot' + (status.engine_ready ? ' ready' : '');
  await refresh();
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
$('enhance-text').onclick = run(async () => { await api(`/api/notes/${current.id}/enhance`, {method:'POST'}); await refresh(); toast('Local AI correction queued.'); });
$('show-original').onclick = () => showVersion(false);
$('show-enhanced').onclick = () => showVersion(true);
$('use-enhanced').onclick = () => {
  if (!current?.enhanced_text || !confirm('Replace the editable text with the AI-enhanced version? The original OCR remains preserved.')) return;
  $('transcript').value = current.enhanced_text; changed(); toast('Enhanced text copied into the editor. Review it before saving.');
};
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
for (const id of ['title','transcript','reviewed']) $(id).addEventListener('input', changed);
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('keydown', run(async event => { if ((event.ctrlKey || event.metaKey) && event.key === 's') { event.preventDefault(); if (current && !$('save').disabled) await save(); } }));
setInterval(() => { if (token && !$('workspace').hidden) refresh().catch(() => {}); }, 2500);
if (token) run(connect)(); else $('login').hidden = false;
