(() => {
  const $ = selector => document.querySelector(selector);
  const message = $('#global-message');
  const state = { projects: [], selectedProject: '', dashboard: null, planPage: 1 };

  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.location.replace('/developer/login?next=%2Fdeveloper%2Fdashboard');
      throw new Error('Your session expired. Sign in again.');
    }
    if (!response.ok) throw new Error(payload.error?.message || 'The request could not be completed.');
    return payload;
  }

  async function csrf() {
    const payload = await fetchJson('/api/v1/auth/csrf');
    return payload.data.csrf_token;
  }

  async function mutate(url, method, body) {
    const token = await csrf();
    return fetchJson(url, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  }

  function showMessage(text, isError = false) {
    message.textContent = text;
    message.style.color = isError ? 'var(--bad)' : 'var(--good)';
  }

  function setView(view) {
    if (view !== 'keys') {
      $('#one-time-secret').hidden = true;
      $('#one-time-secret').replaceChildren();
    }
    document.querySelectorAll('.view-panel').forEach(panel => { panel.hidden = panel.id !== `${view}-view`; });
    document.querySelectorAll('.dash-nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === view));
    $('#view-title').textContent = view === 'keys' ? 'API keys' : view.charAt(0).toUpperCase() + view.slice(1);
    if (view === 'keys') loadKeys();
  }

  function metric(label, value, note) {
    return `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`;
  }

  function activityTable(records) {
    if (!records.length) return '<div class="empty-state"><strong>No requests recorded yet</strong>Requests will appear here after your API keys are used.</div>';
    return `<table><thead><tr><th>Project</th><th>Endpoint</th><th>Status</th><th>Duration</th><th>Time</th></tr></thead><tbody>${records.map(item => `<tr><td>${escapeHtml(item.project)}</td><td>${escapeHtml(item.endpoint)}</td><td class="${item.status >= 200 && item.status < 400 ? 'status-ok' : 'status-error'}">${escapeHtml(item.status)}</td><td>${escapeHtml(item.duration_ms)} ms</td><td>${escapeHtml(new Date(item.timestamp).toLocaleString())}</td></tr>`).join('')}</tbody></table>`;
  }

  function renderProjects() {
    const active = state.projects.filter(project => project.status === 'active');
    const rows = state.projects.map(project => `<article class="project-row">
      <div><div class="project-name">${escapeHtml(project.name)}</div><div class="project-id">${escapeHtml(project.project_id)}</div></div>
      <span class="status-pill ${project.status === 'active' ? '' : 'archived'}">${escapeHtml(project.status)}</span>
      <span class="muted-text">${escapeHtml(project.created_at ? new Date(project.created_at).toLocaleDateString() : '')}</span>
      <div class="row-actions">${project.status === 'active' ? `<button class="small-button" data-rename="${escapeHtml(project.project_id)}">Rename</button><button class="small-button" data-keys="${escapeHtml(project.project_id)}">Keys &amp; usage</button><button class="small-button danger" data-archive="${escapeHtml(project.project_id)}">Archive</button>` : ''}</div>
    </article>`).join('');
    $('#project-list').innerHTML = rows || '<div class="empty-state"><strong>No projects yet</strong>Create a project to issue your first API key.</div>';
    $('#project-overview').innerHTML = active.length ? active.slice(0, 4).map(project => {
      const info = state.dashboard.projects.find(item => item.project_id === project.project_id);
      return `<article class="project-row"><div><div class="project-name">${escapeHtml(project.name)}</div><div class="project-id">${escapeHtml(project.project_id)}</div></div><span class="status-pill">Active</span><span class="muted-text">${info ? `${info.requests_24h} requests / 24h` : 'Usage unavailable'}</span><div class="row-actions"><button class="small-button" data-keys="${escapeHtml(project.project_id)}">Manage keys</button></div></article>`;
    }).join('') : '<div class="empty-state"><strong>Your workspace is ready</strong>Create a project to start integrating Cognisense.</div>';
    $('#key-project').innerHTML = active.length ? active.map(project => `<option value="${escapeHtml(project.project_id)}">${escapeHtml(project.name)}</option>`).join('') : '<option value="">Create an active project first</option>';
    if (state.selectedProject && active.some(project => project.project_id === state.selectedProject)) $('#key-project').value = state.selectedProject;
    else state.selectedProject = $('#key-project').value;
    $('#create-key').disabled = active.length === 0;
  }

  function renderUsage() {
    const projects = state.dashboard.projects;
    $('#usage-list').innerHTML = projects.length ? projects.map(project => {
      const limited = project.quota_limit !== null;
      const ratio = limited ? Math.min(100, project.quota_used / project.quota_limit * 100) : 0;
      const quotaText = limited ? `${project.quota_used} / ${project.quota_limit} requests` : `${project.quota_used} requests; no daily quota configured`;
      const planLimit = project.plan_rate_limit ? ` · Plan ${escapeHtml(project.plan_rate_count)} / ${escapeHtml(project.plan_rate_limit)} per 60s` : '';
      return `<article class="usage-row"><div><div class="project-name">${escapeHtml(project.name)}</div><div class="muted-text">${escapeHtml(project.plan || 'Default configuration')}</div></div><div><strong>${escapeHtml(quotaText)}</strong>${limited ? `<div class="usage-meter" role="meter" aria-label="Daily quota used" aria-valuenow="${Math.min(project.quota_used, project.quota_limit)}" aria-valuemin="0" aria-valuemax="${project.quota_limit}"><span style="width:${ratio}%"></span></div>` : ''}</div><div class="muted-text">Rate: ${escapeHtml(project.rate_count)} / ${escapeHtml(project.rate_limit)} per ${escapeHtml(project.rate_window_seconds)}s${planLimit} · ${escapeHtml(project.limit_responses_24h)} HTTP 429 limit responses / 24h</div></article>`;
    }).join('') : '<div class="empty-state"><strong>No usage data yet</strong>Usage is unavailable until you create a project and make requests.</div>';
  }

  function planLimit(value, unit) {
    return value > 0 ? `${escapeHtml(value)} ${unit}` : `No plan-specific ${unit} limit`;
  }

  async function loadPlans() {
    try {
      const data = (await fetchJson(`/api/v1/projects/plans?page=${state.planPage}&per_page=20`)).data;
      $('#available-plans').innerHTML = data.plans.length ? data.plans.map(plan => `<article class="plan-option">
        <h3>${escapeHtml(plan.name)}</h3><p>${escapeHtml(plan.description || 'No description provided.')}</p>
        <dl><div><dt>Requests per minute</dt><dd>${planLimit(plan.request_limit_per_minute, 'requests/minute')}</dd></div>
        <div><dt>Daily quota</dt><dd>${planLimit(plan.quota_limit_per_day, 'requests/day')}</dd></div>
        <div><dt>Active API keys</dt><dd>${planLimit(plan.active_api_key_limit, 'keys')}</dd></div></dl>
      </article>`).join('') : '<div class="empty-state"><strong>No plans are available</strong>Ask your administrator to publish a plan for customer selection.</div>';
      $('#plan-assignments').innerHTML = data.projects.length ? `<table><thead><tr><th>Project</th><th>Current plan</th><th>Select plan</th></tr></thead><tbody>${data.projects.map(project => {
        const current = project.current_plan;
        const options = data.plans.map(plan => `<option value="${plan.id}" ${current && current.id === plan.id ? 'selected' : ''}>${escapeHtml(plan.name)}</option>`).join('');
        const hiddenCurrent = current && !data.plans.some(plan => plan.id === current.id)
          ? `<option value="" selected disabled>${escapeHtml(current.name)} (not available for new assignments)</option>` : '';
        const placeholder = current ? '' : '<option value="" selected disabled>Select a plan</option>';
        return `<tr><td>${escapeHtml(project.name)}<div class="project-id">${escapeHtml(project.project_id)} · ${escapeHtml(project.status)}</div></td>
          <td>${escapeHtml(current ? `${current.name} (${project.plan_assignment})` : 'Platform limits only')}</td>
          <td><select class="admin-inline-select" data-user-plan="${escapeHtml(project.project_id)}" ${project.status !== 'active' ? 'disabled' : ''}>${placeholder}${hiddenCurrent}${options}</select></td></tr>`;
      }).join('')}</tbody></table>${data.pagination.pages > 1 ? `<div class="plan-pagination"><span>Page ${data.pagination.page} of ${data.pagination.pages} · ${data.pagination.total} active projects</span><button class="small-button" type="button" data-plan-page="${data.pagination.page - 1}" ${data.pagination.page <= 1 ? 'disabled' : ''}>Previous</button><button class="small-button" type="button" data-plan-page="${data.pagination.page + 1}" ${data.pagination.page >= data.pagination.pages ? 'disabled' : ''}>Next</button></div>` : ''}` : '<div class="empty-state"><strong>No active projects</strong>Create an active project before selecting its plan.</div>';
    } catch (error) {
      $('#available-plans').innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
      $('#plan-assignments').replaceChildren();
    }
  }

  async function loadKeys() {
    const projectId = $('#key-project').value;
    state.selectedProject = projectId;
    if (!projectId) {
      $('#key-list').innerHTML = '<div class="empty-state"><strong>No active project selected</strong>Create an active project to manage API keys.</div>';
      return;
    }
    $('#key-list').innerHTML = '<div class="muted-text">Loading API keys…</div>';
    try {
      const payload = await fetchJson(`/api/v1/projects/${encodeURIComponent(projectId)}/keys`);
      const keys = payload.data.keys;
      $('#key-list').innerHTML = keys.length ? keys.map(key => {
        const status = key.is_revoked ? 'revoked' : key.expires_at && new Date(key.expires_at) <= new Date() ? 'expired' : key.status;
        return `<article class="key-row"><div><div class="project-name">${escapeHtml(key.prefix)}••••</div><div class="project-id">Created ${escapeHtml(new Date(key.created_at).toLocaleString())}</div></div><span class="status-pill ${status === 'active' ? '' : status}">${escapeHtml(status)}</span><span class="muted-text">${key.expires_at ? `Expires ${escapeHtml(new Date(key.expires_at).toLocaleDateString())}` : 'No expiry'}${key.revoked_at ? ` · Revoked ${escapeHtml(new Date(key.revoked_at).toLocaleDateString())}` : ''}</span><div class="row-actions">${status === 'active' ? `<button class="small-button danger" data-revoke="${escapeHtml(key.key_id)}">Revoke</button>` : ''}</div></article>`;
      }).join('') : '<div class="empty-state"><strong>No API keys yet</strong>Create a key and store its secret securely. It is shown only once.</div>';
    } catch (error) {
      $('#key-list').innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadDashboard() {
    try {
      const payload = await fetchJson('/api/v1/developer/dashboard');
      state.dashboard = payload.data;
      state.projects = payload.data.projects.map(project => ({
        project_id: project.project_id, name: project.name, status: project.status, created_at: project.created_at
      }));
      const summary = payload.data.summary;
      $('#metrics').innerHTML = [
        metric('Total projects', summary.total_projects, 'Active and archived'),
        metric('Active API keys', summary.active_keys, 'Across your projects'),
        metric('Requests, 24 hours', summary.requests_24h, `${summary.limit_responses_24h} HTTP 429 limit responses`),
        metric('Successful / failed', `${summary.successful_24h} / ${summary.failed_24h}`, 'Recorded response status')
      ].join('');
      renderProjects();
      renderUsage();
      $('#recent-overview').innerHTML = activityTable(payload.data.recent.slice(0, 8));
      $('#recent-usage').innerHTML = activityTable(payload.data.recent);
      if (!payload.data.available) showMessage('Usage records are currently unavailable.', true);
    } catch (error) {
      $('#metrics').innerHTML = `<div class="error-state" style="grid-column:1/-1">${escapeHtml(error.message)}</div>`;
      $('#project-overview').innerHTML = '<div class="error-state">Dashboard data is unavailable.</div>';
      showMessage(error.message, true);
    }
  }

  document.querySelectorAll('.dash-nav-item').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', () => setView(button.dataset.go)));
  $('#sidebar-collapse').addEventListener('click', event => {
    const collapsed = $('#dashboard-shell').classList.toggle('sidebar-collapsed');
    const control = event.currentTarget;
    control.setAttribute('aria-expanded', String(!collapsed));
    control.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
    control.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
  });
  $('#key-project').addEventListener('change', loadKeys);
  $('#plans-refresh').addEventListener('click', loadPlans);

  $('#project-form').addEventListener('submit', async event => {
    event.preventDefault();
    const name = $('#project-name').value.trim();
    if (!name || name.length > 100) return showMessage('Project names must be 1 to 100 characters.', true);
    try {
      await mutate('/api/v1/projects', 'POST', { name });
      $('#project-name').value = '';
      showMessage('Project created.');
      await loadDashboard();
    } catch (error) { showMessage(error.message, true); }
  });

  document.addEventListener('click', async event => {
    const planPage = event.target.closest('[data-plan-page]');
    if (planPage) {
      state.planPage = Number(planPage.dataset.planPage);
      await loadPlans();
      return;
    }
    const rename = event.target.closest('[data-rename]');
    const archive = event.target.closest('[data-archive]');
    const keys = event.target.closest('[data-keys]');
    const revoke = event.target.closest('[data-revoke]');
    try {
      if (rename) {
        const project = state.projects.find(item => item.project_id === rename.dataset.rename);
        const name = window.prompt('New project name', project?.name || '');
        if (name === null) return;
        await mutate(`/api/v1/projects/${encodeURIComponent(rename.dataset.rename)}`, 'PATCH', { name: name.trim() });
        showMessage('Project renamed.');
        await loadDashboard();
      } else if (archive) {
        if (!window.confirm('Archive this project? Its API keys will stop working.')) return;
        await mutate(`/api/v1/projects/${encodeURIComponent(archive.dataset.archive)}`, 'DELETE');
        showMessage('Project archived.');
        await loadDashboard();
      } else if (keys) {
        state.selectedProject = keys.dataset.keys;
        setView('keys');
        $('#key-project').value = state.selectedProject;
        await loadKeys();
      } else if (revoke) {
        if (!window.confirm('Revoke this API key? This cannot be undone.')) return;
        await mutate(`/api/v1/projects/${encodeURIComponent($('#key-project').value)}/keys/${encodeURIComponent(revoke.dataset.revoke)}/revoke`, 'POST', {});
        showMessage('API key revoked.');
        await loadKeys();
        await loadDashboard();
      }
    } catch (error) { showMessage(error.message, true); }
  });

  document.addEventListener('change', async event => {
    const select = event.target.closest('[data-user-plan]');
    if (!select || !select.value) return;
    try {
      await mutate(`/api/v1/projects/${encodeURIComponent(select.dataset.userPlan)}/plan`, 'PATCH', { plan_id: Number(select.value) });
      showMessage('Project plan updated. Gateway limits apply to the next request.');
      await loadPlans();
    } catch (error) {
      showMessage(error.message, true);
      await loadPlans();
    }
  });

  $('#create-key').addEventListener('click', async () => {
    const projectId = $('#key-project').value;
    if (!projectId) return showMessage('Create an active project first.', true);
    const expires = $('#key-expiry').value;
    try {
      const payload = await mutate(`/api/v1/projects/${encodeURIComponent(projectId)}/keys`, 'POST', expires ? { expires_in_days: Number(expires) } : {});
      const secret = payload.data.secret;
      $('#one-time-secret').hidden = false;
      $('#one-time-secret').innerHTML = `<strong>Copy this key now. It cannot be displayed again.</strong><code>${escapeHtml(secret)}</code><button class="small-button" type="button" id="copy-secret">Copy key</button><p>Keep this secret in a server-side secret manager. Never place it in browser storage, source control, or public frontend code.</p>`;
      $('#copy-secret').addEventListener('click', async () => {
        await navigator.clipboard.writeText(secret);
        $('#copy-secret').textContent = 'Copied';
      });
      showMessage('API key created. Copy the secret before leaving this page.');
      await loadKeys();
      await loadDashboard();
    } catch (error) { showMessage(error.message, true); }
  });

  async function logout() {
    try { await mutate('/api/v1/auth/logout', 'POST', {}); }
    catch (error) { if (!error.message.includes('expired')) showMessage(error.message, true); }
    window.location.replace('/developer/login');
  }
  $('#logout-button').addEventListener('click', logout);
  $('#account-logout').addEventListener('click', logout);

  window.addEventListener('pageshow', async event => {
    if (event.persisted) {
      try { await fetchJson('/api/v1/auth/me'); }
      catch (_) { window.location.replace('/developer/login'); }
    }
  });
  window.addEventListener('pagehide', () => {
    $('#one-time-secret').hidden = true;
    $('#one-time-secret').replaceChildren();
  });

  loadDashboard();
})();