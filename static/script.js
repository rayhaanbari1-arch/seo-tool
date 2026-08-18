/* SEO Event Tracker — Client Manager + Report Generator */

// ─── State ───────────────────────────────────────────────────────────────────

let clients = [];
let activeClientId = null;
let activeClientData = null; // current editor state (unsaved)

// ─── Init ─────────────────────────────────────────────────────────────────────

let pollInterval = null;

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initFileDrop();
  loadClients();

  document.getElementById('add-client-btn').addEventListener('click', createNewClient);
  document.getElementById('import-csv-btn').addEventListener('click', () => {
    document.getElementById('csv-file-input').click();
  });
  document.getElementById('csv-file-input').addEventListener('change', handleCsvImport);
  document.getElementById('add-group-btn').addEventListener('click', addGroup);
  document.getElementById('save-client-btn').addEventListener('click', saveClient);
  document.getElementById('delete-client-btn').addEventListener('click', deleteClient);
  document.getElementById('generate-form').addEventListener('submit', generateReport);
  document.getElementById('error-retry-btn').addEventListener('click', resetGenerateForm);
  document.getElementById('refresh-reports-btn').addEventListener('click', loadReports);
});

// ─── Tabs ─────────────────────────────────────────────────────────────────────

function initTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => {
        p.classList.remove('active');
        p.classList.add('hidden');
      });
      btn.classList.add('active');
      const panel = document.getElementById('tab-' + tab);
      panel.classList.remove('hidden');
      panel.classList.add('active');

      if (tab === 'generate') refreshGenerateClientList();
      if (tab === 'reports') loadReports();
    });
  });
}

// ─── Client List ─────────────────────────────────────────────────────────────

async function loadClients() {
  const res = await fetch('/api/clients');
  clients = await res.json();
  renderClientList();
}

function renderClientList() {
  const list = document.getElementById('client-list');
  const empty = document.getElementById('client-list-empty');

  // Clear existing items (keep empty message)
  list.querySelectorAll('.client-item').forEach(el => el.remove());

  if (clients.length === 0) {
    empty.style.display = '';
    return;
  }

  empty.style.display = 'none';

  clients.forEach(client => {
    const linkCount = client.groups.reduce((sum, g) => sum + g.links.length, 0);
    const item = document.createElement('div');
    item.className = 'client-item' + (client.id === activeClientId ? ' active' : '');
    item.dataset.id = client.id;
    item.innerHTML = `
      <span class="client-item-name">${escHtml(client.name)}</span>
      <span class="client-item-count">${linkCount} URL${linkCount !== 1 ? 's' : ''}</span>
    `;
    item.addEventListener('click', () => selectClient(client.id));
    list.appendChild(item);
  });
}

function selectClient(id) {
  activeClientId = id;
  const client = clients.find(c => c.id === id);
  if (!client) return;

  // Deep copy for editing
  activeClientData = JSON.parse(JSON.stringify(client));

  // Highlight in sidebar
  document.querySelectorAll('.client-item').forEach(el => {
    el.classList.toggle('active', parseInt(el.dataset.id) === id);
  });

  renderEditor();
  loadGtmStatus(id);
}

// ─── Editor ───────────────────────────────────────────────────────────────────

function renderEditor() {
  const emptyEl = document.getElementById('editor-empty');
  const formEl = document.getElementById('editor-form');

  if (!activeClientData) {
    emptyEl.style.display = '';
    formEl.classList.add('hidden');
    return;
  }

  emptyEl.style.display = 'none';
  formEl.classList.remove('hidden');

  document.getElementById('client-name-input').value = activeClientData.name;
  renderGroups();
}

function renderGroups() {
  const container = document.getElementById('groups-container');
  container.innerHTML = '';

  (activeClientData.groups || []).forEach((group, gi) => {
    container.appendChild(buildGroupBlock(group, gi));
  });
}

function buildGroupBlock(group, gi) {
  const block = document.createElement('div');
  block.className = 'group-block';
  block.dataset.gi = gi;

  block.innerHTML = `
    <div class="group-header">
      <input
        type="text"
        class="group-name-input"
        placeholder="Group name (e.g. Landing Pages)"
        value="${escHtml(group.group_name || '')}"
        data-gi="${gi}"
      >
      <button class="group-delete-btn" data-gi="${gi}" title="Delete group">✕</button>
    </div>
    <div class="link-table">
      <div class="link-table-head">
        <span>Project Name</span>
        <span>URL</span>
        <span>Page Type</span>
        <span></span>
      </div>
      <div class="link-rows" data-gi="${gi}"></div>
      <button class="add-link-btn" data-gi="${gi}">+ Add URL</button>
    </div>
  `;

  // Group name change
  block.querySelector('.group-name-input').addEventListener('input', e => {
    activeClientData.groups[gi].group_name = e.target.value;
  });

  // Delete group
  block.querySelector('.group-delete-btn').addEventListener('click', () => {
    activeClientData.groups.splice(gi, 1);
    renderGroups();
  });

  // Add link
  block.querySelector('.add-link-btn').addEventListener('click', () => {
    if (!activeClientData.groups[gi].links) activeClientData.groups[gi].links = [];
    activeClientData.groups[gi].links.push({ project_name: '', url: '', page_type: 'lp' });
    renderGroups();
  });

  // Render link rows
  const rowsContainer = block.querySelector('.link-rows');
  (group.links || []).forEach((link, li) => {
    rowsContainer.appendChild(buildLinkRow(link, gi, li));
  });

  return block;
}

function buildLinkRow(link, gi, li) {
  const row = document.createElement('div');
  row.className = 'link-row';

  row.innerHTML = `
    <input
      type="text"
      placeholder="TVS Altura"
      value="${escHtml(link.project_name || '')}"
      data-gi="${gi}" data-li="${li}" data-field="project_name"
    >
    <input
      type="url"
      placeholder="https://..."
      value="${escHtml(link.url || '')}"
      data-gi="${gi}" data-li="${li}" data-field="url"
    >
    <select data-gi="${gi}" data-li="${li}" data-field="page_type">
      <option value="lp" ${link.page_type === 'lp' ? 'selected' : ''}>Landing Page</option>
      <option value="project" ${link.page_type === 'project' ? 'selected' : ''}>Project Page</option>
    </select>
    <button class="link-delete-btn" data-gi="${gi}" data-li="${li}" title="Remove">✕</button>
  `;

  // Field changes
  row.querySelectorAll('input, select').forEach(el => {
    el.addEventListener('input', e => {
      const g = parseInt(e.target.dataset.gi);
      const l = parseInt(e.target.dataset.li);
      const field = e.target.dataset.field;
      activeClientData.groups[g].links[l][field] = e.target.value;
    });
  });

  // Delete row
  row.querySelector('.link-delete-btn').addEventListener('click', e => {
    const g = parseInt(e.target.dataset.gi);
    const l = parseInt(e.target.dataset.li);
    activeClientData.groups[g].links.splice(l, 1);
    renderGroups();
  });

  return row;
}

// ─── Add Group ────────────────────────────────────────────────────────────────

function addGroup() {
  if (!activeClientData) return;
  if (!activeClientData.groups) activeClientData.groups = [];
  activeClientData.groups.push({ group_name: '', links: [] });
  renderGroups();

  // Focus the new group name input
  const inputs = document.querySelectorAll('.group-name-input');
  if (inputs.length > 0) {
    inputs[inputs.length - 1].focus();
  }
}

// ─── Create Client ────────────────────────────────────────────────────────────

async function createNewClient() {
  const res = await fetch('/api/clients', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'New Client' })
  });
  const client = await res.json();
  clients.unshift(client);
  renderClientList();
  selectClient(client.id);

  // Focus the name input
  setTimeout(() => {
    const input = document.getElementById('client-name-input');
    input.focus();
    input.select();
  }, 50);
}

// ─── Save Client ──────────────────────────────────────────────────────────────

async function saveClient() {
  if (!activeClientData || !activeClientId) return;

  activeClientData.name = document.getElementById('client-name-input').value.trim() || 'Unnamed Client';

  const btn = document.getElementById('save-client-btn');
  btn.textContent = 'Saving…';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/clients/${activeClientId}/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(activeClientData)
    });
    const updated = await res.json();

    // Update local state
    const idx = clients.findIndex(c => c.id === activeClientId);
    if (idx >= 0) clients[idx] = updated;
    else clients.unshift(updated);

    activeClientData = JSON.parse(JSON.stringify(updated));
    renderClientList();
    renderEditor();

    btn.textContent = 'Saved ✓';
    setTimeout(() => { btn.textContent = 'Save'; btn.disabled = false; }, 1500);
  } catch (e) {
    btn.textContent = 'Save';
    btn.disabled = false;
    alert('Save failed. Please try again.');
  }
}

// ─── Delete Client ────────────────────────────────────────────────────────────

async function deleteClient() {
  if (!activeClientId) return;
  const client = clients.find(c => c.id === activeClientId);
  if (!confirm(`Delete "${client?.name}"? This cannot be undone.`)) return;

  await fetch(`/api/clients/${activeClientId}`, { method: 'DELETE' });
  clients = clients.filter(c => c.id !== activeClientId);
  activeClientId = null;
  activeClientData = null;
  renderClientList();
  renderEditor();
}

// ─── Generate Tab ─────────────────────────────────────────────────────────────

function refreshGenerateClientList() {
  const select = document.getElementById('report-client-select');
  const currentVal = select.value;
  select.innerHTML = '<option value="">— Select a client —</option>';
  clients.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = c.name;
    select.appendChild(opt);
  });
  if (currentVal) select.value = currentVal;
}

// ─── File Drop ────────────────────────────────────────────────────────────────

function initFileDrop() {
  const zone = document.getElementById('file-drop-zone');
  const input = document.getElementById('excel-upload');
  const selected = document.getElementById('file-selected');
  const nameDisplay = document.getElementById('file-name-display');
  const clearBtn = document.getElementById('file-clear-btn');

  ['dragenter', 'dragover'].forEach(evt => {
    zone.addEventListener(evt, e => { e.preventDefault(); zone.classList.add('dragover'); });
  });

  ['dragleave', 'drop'].forEach(evt => {
    zone.addEventListener(evt, () => zone.classList.remove('dragover'));
  });

  zone.addEventListener('drop', e => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
  });

  input.addEventListener('change', () => {
    if (input.files[0]) setFile(input.files[0]);
  });

  clearBtn.addEventListener('click', () => {
    input.value = '';
    selected.classList.add('hidden');
    zone.style.display = '';
    document.getElementById('validation-result').classList.add('hidden');
  });

  function setFile(file) {
    nameDisplay.textContent = file.name;
    selected.classList.remove('hidden');
    zone.style.display = 'none';

    // Auto-validate if client is selected
    const clientId = document.getElementById('report-client-select').value;
    if (clientId) validateExcel(clientId, file);
  }
}

async function validateExcel(clientId, file) {
  const valEl = document.getElementById('validation-result');
  valEl.className = 'validation-result';
  valEl.textContent = 'Checking sheet names…';

  const fd = new FormData();
  fd.append('client_id', clientId);
  fd.append('excel', file);

  try {
    const res = await fetch('/validate', { method: 'POST', body: fd });
    const data = await res.json();

    if (data.error) {
      valEl.classList.add('warn');
      valEl.textContent = 'Validation error: ' + data.error;
      return;
    }

    const lines = [];
    if (data.matched.length > 0) {
      lines.push(`✓ Matched ${data.matched.length} sheet${data.matched.length > 1 ? 's' : ''}: ${data.matched.join(', ')}`);
    }
    if (data.unmatched_sheets.length > 0) {
      lines.push(`⚠ Excel sheets with no matching URL: ${data.unmatched_sheets.join(', ')}`);
    }
    if (data.unmatched_links.length > 0) {
      lines.push(`⚠ Client URLs with no matching sheet: ${data.unmatched_links.join(', ')}`);
    }

    valEl.classList.add(data.unmatched_sheets.length > 0 || data.unmatched_links.length > 0 ? 'warn' : 'ok');
    valEl.textContent = lines.join('\n');
  } catch (e) {
    valEl.classList.add('warn');
    valEl.textContent = 'Could not validate — continuing anyway.';
  }
}

// ─── Generate Report (Background) ─────────────────────────────────────────────

async function generateReport(e) {
  e.preventDefault();

  const form = document.getElementById('generate-form');
  const progress = document.getElementById('progress-panel');
  const errorPanel = document.getElementById('error-panel');

  form.classList.add('hidden');
  progress.classList.remove('hidden');
  errorPanel.classList.add('hidden');
  document.getElementById('progress-msg').textContent = 'Submitting report job…';

  const formData = new FormData(form);

  try {
    const res = await fetch('/generate', {
      method: 'POST',
      body: formData
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `Server error ${res.status}`);
    }

    const job = await res.json();

    document.getElementById('progress-msg').textContent = 'Report queued! Generating in the background…';
    document.querySelector('.progress-sub').textContent =
      'You can close this and check the Reports tab. We\'ll keep working on it.';

    // Switch to reports tab after a short delay
    setTimeout(() => {
      progress.classList.add('hidden');
      form.classList.remove('hidden');
      // Reset form for next use
      document.getElementById('progress-msg').textContent = 'Taking screenshots and building report…';
      document.querySelector('.progress-sub').textContent =
        'This can take 30–90 seconds depending on how many pages are tracked.';

      // Switch to Reports tab
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => {
        p.classList.remove('active');
        p.classList.add('hidden');
      });
      const reportsBtn = document.querySelector('[data-tab="reports"]');
      reportsBtn.classList.add('active');
      const reportsPanel = document.getElementById('tab-reports');
      reportsPanel.classList.remove('hidden');
      reportsPanel.classList.add('active');
      loadReports();
    }, 1500);

  } catch (err) {
    progress.classList.add('hidden');
    errorPanel.classList.remove('hidden');
    document.getElementById('error-msg').textContent = err.message;
  }
}

function resetGenerateForm() {
  document.getElementById('generate-form').classList.remove('hidden');
  document.getElementById('error-panel').classList.add('hidden');
}

// ─── Reports Panel ────────────────────────────────────────────────────────────

async function loadReports() {
  const list = document.getElementById('reports-list');
  const empty = document.getElementById('reports-empty');

  try {
    const res = await fetch('/api/reports');
    const reports = await res.json();

    // Clear existing items
    list.querySelectorAll('.report-item').forEach(el => el.remove());

    if (reports.length === 0) {
      empty.style.display = '';
      stopPolling();
      return;
    }

    empty.style.display = 'none';
    let hasActive = false;

    reports.forEach(report => {
      const item = document.createElement('div');
      item.className = 'report-item';
      item.dataset.id = report.id;

      const createdAt = report.created_at ? new Date(report.created_at).toLocaleString() : '';
      const displayName = report.report_name || `${report.client_name} Report`;

      let actionsHtml = '';
      if (report.status === 'completed') {
        actionsHtml = `
          <button class="btn btn-primary btn-sm" onclick="viewReport(${report.id})">View</button>
          <button class="btn btn-outline btn-sm" onclick="downloadReport(${report.id}, '${escHtml(report.report_name || 'report')}')">Download</button>
          <button class="btn btn-danger btn-sm" onclick="deleteReport(${report.id})">Delete</button>
        `;
      } else if (report.status === 'failed') {
        actionsHtml = `
          <button class="btn btn-danger btn-sm" onclick="deleteReport(${report.id})">Delete</button>
        `;
      } else if (report.status === 'pending' || report.status === 'running') {
        actionsHtml = `
          <button class="btn btn-danger btn-sm" onclick="cancelReport(${report.id}, this)">Cancel</button>
        `;
      } else if (report.status === 'cancelled') {
        actionsHtml = `
          <button class="btn btn-danger btn-sm" onclick="deleteReport(${report.id})">Delete</button>
        `;
      }

      if (report.status === 'pending' || report.status === 'running') {
        hasActive = true;
      }

      item.innerHTML = `
        <div class="report-item-info">
          <div class="report-item-name">${escHtml(displayName)}</div>
          <div class="report-item-meta">${escHtml(createdAt)}${report.status === 'failed' ? ' — ' + escHtml(report.error || 'Unknown error') : ''}</div>
        </div>
        <span class="report-status ${report.status}">${{running:'Generating…',pending:'Queued',cancelled:'Cancelled',completed:'Completed',failed:'Failed'}[report.status] ?? report.status}</span>
        <div class="report-item-actions">${actionsHtml}</div>
      `;

      list.appendChild(item);
    });

    // Poll if there are active jobs
    if (hasActive) {
      startPolling();
    } else {
      stopPolling();
    }

  } catch (e) {
    console.error('Failed to load reports:', e);
  }
}

function startPolling() {
  if (pollInterval) return;
  pollInterval = setInterval(loadReports, 3000);
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

function viewReport(id) {
  window.open(`/api/reports/${id}/view`, '_blank');
}

function downloadReport(id, name) {
  const a = document.createElement('a');
  a.href = `/api/reports/${id}/download`;
  a.download = `${name}.html`;
  a.click();
}

async function deleteReport(id) {
  if (!confirm('Delete this report?')) return;
  await fetch(`/api/reports/${id}`, { method: 'DELETE' });
  loadReports();
}

async function cancelReport(id, btn) {
  btn.disabled = true;
  btn.textContent = 'Cancelling…';
  await fetch(`/api/reports/${id}/cancel`, { method: 'POST' });
  loadReports();
}

async function handleCsvImport(e) {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;

  const btn = document.getElementById('import-csv-btn');
  btn.disabled = true;
  btn.textContent = 'Importing…';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/clients/import-csv', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);

    let msg = `Imported: ${data.created_clients} new client(s), ${data.created_links} link(s).`;
    if (data.errors && data.errors.length) {
      msg += `\n\nWarnings:\n${data.errors.join('\n')}`;
    }
    alert(msg);
    loadClients();
  } catch (err) {
    alert(`CSV import failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Import CSV';
  }
}

function downloadCsvTemplate() {
  const a = document.createElement('a');
  a.href = '/api/clients/csv-template';
  a.download = 'client_import_template.csv';
  a.click();
}

// ─── Validation on client change ──────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('report-client-select').addEventListener('change', () => {
    const clientId = document.getElementById('report-client-select').value;
    const fileInput = document.getElementById('excel-upload');
    if (clientId && fileInput.files[0]) {
      validateExcel(clientId, fileInput.files[0]);
    }
  });
});

// ─── GTM Container Upload ────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('gtm-upload-btn').addEventListener('click', () => {
    document.getElementById('gtm-json-input').click();
  });
  document.getElementById('gtm-json-input').addEventListener('change', handleGtmFileSelect);
  document.getElementById('gtm-remove-btn').addEventListener('click', removeGtm);
});

async function loadGtmStatus(clientId) {
  const badge    = document.getElementById('gtm-badge');
  const preview  = document.getElementById('gtm-mappings-preview');
  const removeBtn = document.getElementById('gtm-remove-btn');

  badge.textContent = '';
  badge.className = 'gtm-badge';
  preview.classList.add('hidden');
  preview.innerHTML = '';
  removeBtn.classList.add('hidden');

  if (!clientId) return;

  try {
    const res = await fetch(`/api/clients/${clientId}/gtm`);
    const data = await res.json();

    if (data.mapping_count > 0) {
      const date = data.uploaded_at ? new Date(data.uploaded_at).toLocaleDateString() : '';
      badge.textContent = `${data.mapping_count} mappings${date ? ' · ' + date : ''}`;
      badge.classList.add('gtm-badge-ok');
      removeBtn.classList.remove('hidden');

      // Show a preview table of the first 10 mappings
      const shown = data.mappings.slice(0, 10);
      const more  = data.mappings.length - shown.length;
      const rows  = shown.map(m => `
        <tr>
          <td><code class="gtm-map-event">${escHtml(m.event_name)}</code></td>
          <td><code class="gtm-map-sel">${escHtml(m.css_selector)}</code></td>
        </tr>
      `).join('');
      preview.innerHTML = `
        <table class="gtm-map-table">
          <thead><tr><th>Event Name</th><th>CSS Selector</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        ${more > 0 ? `<p class="gtm-map-more">+ ${more} more mappings</p>` : ''}
      `;
      preview.classList.remove('hidden');
    } else {
      badge.textContent = 'No GTM container';
      badge.classList.add('gtm-badge-none');
    }
  } catch (e) {
    badge.textContent = 'Could not load GTM status';
    badge.classList.add('gtm-badge-none');
  }
}

async function handleGtmFileSelect() {
  const input    = document.getElementById('gtm-json-input');
  const file     = input.files[0];
  if (!file || !activeClientId) return;

  const badge = document.getElementById('gtm-badge');
  badge.textContent = 'Uploading…';
  badge.className = 'gtm-badge';

  const fd = new FormData();
  fd.append('gtm_json', file);

  try {
    const res = await fetch(`/api/clients/${activeClientId}/gtm`, {
      method: 'POST',
      body: fd
    });
    const data = await res.json();

    if (!res.ok) {
      badge.textContent = data.error || 'Upload failed';
      badge.classList.add('gtm-badge-err');
      return;
    }

    await loadGtmStatus(activeClientId);
  } catch (e) {
    badge.textContent = 'Upload failed';
    badge.classList.add('gtm-badge-err');
  } finally {
    input.value = '';
  }
}

async function removeGtm() {
  if (!activeClientId) return;
  if (!confirm('Remove GTM container mappings for this client?')) return;

  await fetch(`/api/clients/${activeClientId}/gtm`, { method: 'DELETE' });
  await loadGtmStatus(activeClientId);
}

// ─── Utility ──────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
