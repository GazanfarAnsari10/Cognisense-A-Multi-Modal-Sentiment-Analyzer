(() => {
  const $ = selector => document.querySelector(selector);
  const message = $('#global-message');
  const state = { plans: [], pages: { users: 1, projects: 1, keys: 1, usage: 1, audit: 1 } };
  const adminUserId = Number(document.body.dataset.adminUserId);
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.location.replace('/developer/login?next=%2Fadmin');
      throw new Error('Your session expired. Sign in again.');
    }
    if (response.status === 403) throw new Error(payload.error?.message || 'Administrator access is required.');
    if (!response.ok) throw new Error(payload.error?.message || 'The request could not be completed.');
    return payload;
  }

  async function csrf() {
    const payload = await fetchJson('/api/v1/auth/csrf');
    return payload.data.csrf_token;
  }

  async function mutate(url, method, body) {
    const token = await csrf();
    return fetchJson(url, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: JSON.stringify(body) });
  }

  function showMessage(text, error = false) {
    message.textContent = text;
    message.style.color = error ? 'var(--bad)' : 'var(--good)';
  }

  function table(headers, rows) {
    if (!rows.length) return '<div class="empty-state"><strong>No records</strong>No matching records were found.</div>';
    return `<table><thead><tr>${headers.map(item => `<th>${escapeHtml(item)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
  }

  function paginationControls(resource, pagination) {
    if (!pagination || pagination.pages <= 1) return '';
    return `<div class="admin-pagination"><span>Page ${pagination.page} of ${pagination.pages} · ${pagination.total} records</span><div><button class="small-button" data-page-resource="${resource}" data-page="${pagination.page - 1}" ${pagination.page <= 1 ? 'disabled' : ''}>Previous</button><button class="small-button" data-page-resource="${resource}" data-page="${pagination.page + 1}" ${pagination.page >= pagination.pages ? 'disabled' : ''}>Next</button></div></div>`;
  }

  function metric(label, value, note) {
    return `<article class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`;
  }

  async function loadOverview() {
    const data = (await fetchJson('/api/v1/admin/overview')).data;
    const metrics = data.metrics;
    $('#admin-metrics').innerHTML = [
      metric('Users', metrics.users, `${metrics.active_users} active`),
      metric('Projects', metrics.projects, `${metrics.active_projects} active`),
      metric('API keys', metrics.api_keys, `${metrics.active_api_keys} active`),
      metric('Requests, 24 hours', metrics.requests, 'Recorded usage rows'),
      metric('Successful', metrics.successful, 'HTTP 2xx and 3xx'),
      metric('Errors', metrics.errors, 'HTTP 4xx and 5xx, excluding 429'),
      metric('Rate-limit rejections', metrics.rate_limited, 'HTTP 429'),
      metric('Since', new Date(data.since).toLocaleDateString(), 'Rolling 24-hour window')
    ].join('');
    const [usage, health] = await Promise.all([
      fetchJson('/api/v1/admin/usage?group=status&per_page=100'),
      fetchJson('/api/v1/admin/health')
    ]);
    renderHealth(health.data);
    const statuses = usage.data.by_status;
    $('#overview-status').innerHTML = table(['HTTP status', 'Requests'], statuses.map(item => `<tr><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.requests)}</td></tr>`));
  }

  async function loadHealth() {
    renderHealth((await fetchJson('/api/v1/admin/health')).data);
  }

  function renderHealth(health) {
    const item = (label, status) => `<article class="health-item"><span>${escapeHtml(label)}</span><strong class="health-status health-${escapeHtml(status)}">${escapeHtml(status.replace('_', ' '))}</strong></article>`;
    $('#admin-health').innerHTML = item('Database', health.database) + item('Gateway', health.gateway);
  }

  async function loadUsers() {
    const response = (await fetchJson(`/api/v1/admin/users?page=${state.pages.users}&per_page=50`)).data;
    const data = response.users;
    $('#users-table').innerHTML = table(['User', 'Email', 'Status', 'Projects', 'Created', 'Actions'], data.map(user => {
      const action = user.status === 'active' ? 'disabled' : 'active';
      const statusButton = user.id === adminUserId && action === 'disabled'
        ? '<span class="admin-status">Current admin</span>'
        : `<button class="small-button ${action === 'disabled' ? 'danger' : ''} admin-row-button" data-user-status="${user.id}" data-status="${action}">${action === 'disabled' ? 'Deactivate' : 'Reactivate'}</button>`;
      return `<tr><td>${escapeHtml(user.name)} <span class="admin-status">${escapeHtml(user.role)}</span></td><td>${escapeHtml(user.email)}</td><td><span class="status-pill ${user.status === 'active' ? '' : 'archived'}">${escapeHtml(user.status)}</span></td><td>${escapeHtml(user.project_count)}</td><td>${escapeHtml(new Date(user.created_at).toLocaleDateString())}</td><td><div class="row-actions"><button class="small-button admin-row-button" data-user-detail="${user.id}">Inspect</button>${statusButton}</div></td></tr>`;
    })) + paginationControls('users', response.pagination);
  }

  async function showUser(userId) {
    const user = (await fetchJson(`/api/v1/admin/users/${encodeURIComponent(userId)}`)).data.user;
    const detail = $('#user-detail');
    detail.hidden = false;
    detail.innerHTML = `<h3>${escapeHtml(user.name)}</h3><p>${escapeHtml(user.email)} · ${escapeHtml(user.status)} · ${escapeHtml(user.role)}</p>${table(['Project', 'ID', 'Status', 'Plan'], user.projects.map(project => `<tr><td>${escapeHtml(project.name)}</td><td>${escapeHtml(project.project_id)}</td><td>${escapeHtml(project.status)}</td><td>${escapeHtml(project.plan || 'Default')}</td></tr>`))}`;
  }

  async function loadProjects() {
    const [projectsPayload, plansPayload] = await Promise.all([
      fetchJson(`/api/v1/admin/projects?page=${state.pages.projects}&per_page=50`), fetchJson('/api/v1/admin/plans')
    ]);
    state.plans = plansPayload.data.plans;
    const projects = projectsPayload.data.projects;
    $('#projects-table').innerHTML = table(['Project', 'Owner', 'Status', 'Plan', 'Requests / 24h', 'Actions'], projects.map(project => {
      const options = `<option value="">Default plan</option>${state.plans.map(plan => `<option value="${plan.id}" ${String(plan.id) === String(project.plan_id) ? 'selected' : ''}>${escapeHtml(plan.name)}</option>`).join('')}`;
      return `<tr><td>${escapeHtml(project.name)}<div class="project-id">${escapeHtml(project.project_id)}</div></td><td>${escapeHtml(project.owner_email)}</td><td>${escapeHtml(project.status)}</td><td><select class="admin-inline-select" data-project-plan="${project.id}" ${project.status !== 'active' ? 'disabled' : ''}>${options}</select></td><td>${escapeHtml(project.requests_24h)}</td><td><div class="row-actions">${project.status === 'active' ? `<button class="small-button danger" data-project-archive="${project.id}">Archive</button>` : ''}${project.status !== 'deleted' ? `<button class="small-button danger" data-project-delete="${project.id}">Delete</button>` : '—'}</div></td></tr>`;
    })) + paginationControls('projects', projectsPayload.data.pagination);
  }

  function resetPlanForm() {
    $('#plan-form').reset();
    $('#plan-id').value = '';
    $('#plan-save').textContent = 'Create plan';
  }

  async function loadPlans() {
    const plans = (await fetchJson('/api/v1/admin/plans')).data.plans;
    state.plans = plans;
    $('#plans-table').innerHTML = table(['Plan', 'Requests / 60s', 'Daily quota', 'Active keys', 'Active projects', 'Default', 'Customer selectable', 'Actions'], plans.map(plan => `<tr><td>${escapeHtml(plan.name)}<div class="project-id">${escapeHtml(plan.description || '')}</div></td><td>${escapeHtml(plan.request_limit_per_minute || 'Unlimited')}</td><td>${escapeHtml(plan.quota_limit_per_day || 'Unlimited')}</td><td>${escapeHtml(plan.active_api_key_limit || 'Unlimited')}</td><td>${escapeHtml(plan.max_active_projects || 'Unlimited')}</td><td>${plan.is_default ? 'Yes' : 'No'}</td><td>${plan.is_selectable ? 'Yes' : 'No'}</td><td><button class="small-button" data-plan-edit="${plan.id}">Edit</button></td></tr>`));
  }

  async function loadKeys() {
    const response = (await fetchJson(`/api/v1/admin/keys?page=${state.pages.keys}&per_page=50`)).data;
    const keys = response.keys;
    $('#keys-table').innerHTML = table(['Key prefix', 'Owner', 'Project', 'Status', 'Created / expiry', 'Usage', 'Actions'], keys.map(key => {
      const expired = key.expires_at && new Date(key.expires_at) <= new Date();
      const active = key.status === 'active' && !key.revoked_at && !expired;
      return `<tr><td>${escapeHtml(key.prefix)}••••</td><td>${escapeHtml(key.owner_email)}</td><td>${escapeHtml(key.project_name)}</td><td>${escapeHtml(expired ? 'expired' : key.status)}</td><td>${escapeHtml(new Date(key.created_at).toLocaleDateString())} / ${escapeHtml(key.expires_at ? new Date(key.expires_at).toLocaleDateString() : 'Never')}</td><td>${escapeHtml(key.usage_count)}</td><td>${active ? `<button class="small-button danger" data-key-revoke="${key.key_id}">Revoke</button>` : '—'}</td></tr>`;
    })) + paginationControls('keys', response.pagination);
  }

  async function loadUsage() {
    const days = Number($('#usage-days').value);
    const end = new Date();
    const start = new Date(end.getTime() - days * 86400000);
    const params = new URLSearchParams({ group: $('#usage-group').value, from: start.toISOString(), to: end.toISOString(), page: String(state.pages.usage), per_page: '100' });
    const data = (await fetchJson(`/api/v1/admin/usage?${params}`)).data;
    const metrics = data.metrics;
    $('#usage-metrics').innerHTML = [
      metric('Requests', metrics.requests, 'Selected date range'),
      metric('Successful', metrics.successful, 'HTTP 2xx and 3xx'),
      metric('Failures', metrics.errors, 'HTTP 4xx and 5xx, excluding 429'),
      metric('Rate-limited', metrics.rate_limited, 'HTTP 429')
    ].join('');
    $('#usage-trends').innerHTML = table(['Day', 'Requests', 'Successful', 'Failures', 'HTTP 429'], data.trends.map(item => `<tr><td>${escapeHtml(item.day)}</td><td>${escapeHtml(item.requests)}</td><td>${escapeHtml(item.successful)}</td><td>${escapeHtml(item.errors)}</td><td>${escapeHtml(item.rate_limited)}</td></tr>`));
    $('#usage-table').innerHTML = `<p class="muted-text">${escapeHtml(new Date(data.from).toLocaleString())} to ${escapeHtml(new Date(data.to).toLocaleString())}</p>${table(['Group', 'Requests'], data.groups.map(group => `<tr><td>${escapeHtml(group.label)}</td><td>${escapeHtml(group.requests)}</td></tr>`))}${paginationControls('usage', data.pagination)}<h2>HTTP status breakdown</h2>${table(['HTTP status', 'Requests'], data.by_status.map(item => `<tr><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.requests)}</td></tr>`))}`;
  }

  async function loadAudit() {
    const end = new Date();
    const start = new Date(end.getTime() - Number($('#audit-days').value) * 86400000);
    const params = new URLSearchParams({ from: start.toISOString(), to: end.toISOString(), page: String(state.pages.audit), per_page: '50' });
    const data = (await fetchJson(`/api/v1/admin/audit?${params}`)).data;
    $('#audit-table').innerHTML = `<p class="muted-text">${escapeHtml(new Date(data.from).toLocaleString())} to ${escapeHtml(new Date(data.to).toLocaleString())}</p>${table(['Time', 'Actor', 'Action', 'Target', 'Outcome'], data.records.map(item => `<tr><td>${escapeHtml(new Date(item.occurred_at).toLocaleString())}</td><td>${escapeHtml(item.actor)}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.target_type)} ${escapeHtml(item.target_identifier)}</td><td>${escapeHtml(item.outcome)}</td></tr>`))}${paginationControls('audit', data.pagination)}`;
  }

  const loaders = { overview: loadOverview, health: loadHealth, users: loadUsers, projects: loadProjects, plans: loadPlans, keys: loadKeys, usage: loadUsage, audit: loadAudit };
  async function loadView(view) {
    try { await loaders[view](); }
    catch (error) { showMessage(error.message, true); }
  }

  function setView(view) {
    document.querySelectorAll('.view-panel').forEach(panel => { panel.hidden = panel.id !== `${view}-view`; });
    document.querySelectorAll('.dash-nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === view));
    $('#view-title').textContent = ({ users: 'User Management', projects: 'Project Management', plans: 'Plans & Limits', keys: 'API Key Oversight' })[view] || view.charAt(0).toUpperCase() + view.slice(1);
    loadView(view);
  }

  document.querySelectorAll('.dash-nav-item[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  $('#sidebar-collapse').addEventListener('click', event => {
    const collapsed = $('#dashboard-shell').classList.toggle('sidebar-collapsed');
    event.currentTarget.setAttribute('aria-expanded', String(!collapsed));
    event.currentTarget.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
  });
  $('#usage-load').addEventListener('click', loadUsage);
  $('#audit-load').addEventListener('click', loadAudit);
  $('#plan-reset').addEventListener('click', resetPlanForm);
  $('#plan-form').addEventListener('submit', async event => {
    event.preventDefault();
    const id = $('#plan-id').value;
    const body = { name: $('#plan-name').value.trim(), description: $('#plan-description').value.trim() || null,
      request_limit_per_minute: Number($('#plan-rpm').value), quota_limit_per_day: Number($('#plan-quota').value),
      active_api_key_limit: Number($('#plan-keys').value), max_active_projects: Number($('#plan-projects').value),
      is_default: $('#plan-default').checked,
      is_selectable: $('#plan-selectable').checked };
    try {
      await mutate(id ? `/api/v1/admin/plans/${id}` : '/api/v1/admin/plans', id ? 'PATCH' : 'POST', body);
      showMessage(id ? 'Plan updated. Limits apply to the next request.' : 'Plan created.');
      resetPlanForm(); await loadPlans();
    } catch (error) { showMessage(error.message, true); }
  });

  document.addEventListener('click', async event => {
    const target = event.target;
    try {
      if (target.closest('[data-refresh]')) return loadView(target.closest('[data-refresh]').dataset.refresh);
      if (target.closest('[data-page-resource]')) {
        const button = target.closest('[data-page-resource]');
        state.pages[button.dataset.pageResource] = Number(button.dataset.page);
        return loadView(button.dataset.pageResource);
      }
      if (target.closest('[data-user-detail]')) return showUser(target.closest('[data-user-detail]').dataset.userDetail);
      if (target.closest('[data-user-status]')) {
        const button = target.closest('[data-user-status]');
        if (!window.confirm(`${button.dataset.status === 'disabled' ? 'Deactivate' : 'Reactivate'} this account?`)) return;
        await mutate(`/api/v1/admin/users/${button.dataset.userStatus}/status`, 'PATCH', { status: button.dataset.status });
        showMessage('Account status updated.'); await loadUsers();
      } else if (target.closest('[data-project-archive]')) {
        const id = target.closest('[data-project-archive]').dataset.projectArchive;
        if (!window.confirm('Archive this project? Its API keys will stop working.')) return;
        await mutate(`/api/v1/admin/projects/${id}`, 'PATCH', { status: 'archived' });
        showMessage('Project archived.'); await loadProjects();
      } else if (target.closest('[data-project-delete]')) {
        const id = target.closest('[data-project-delete]').dataset.projectDelete;
        if (!window.confirm('Permanently remove this project from normal access? Keys will be revoked, and usage history will be retained. This cannot be undone.')) return;
        await mutate(`/api/v1/admin/projects/${id}`, 'DELETE', {});
        showMessage('Project deleted and API keys revoked. Usage history was retained.'); await loadProjects();
      } else if (target.closest('[data-plan-edit]')) {
        const plan = state.plans.find(item => String(item.id) === target.closest('[data-plan-edit]').dataset.planEdit);
        if (!plan) return;
        $('#plan-id').value = plan.id; $('#plan-name').value = plan.name; $('#plan-description').value = plan.description || '';
        $('#plan-rpm').value = plan.request_limit_per_minute; $('#plan-quota').value = plan.quota_limit_per_day;
        $('#plan-keys').value = plan.active_api_key_limit; $('#plan-projects').value = plan.max_active_projects;
        $('#plan-default').checked = plan.is_default;
        $('#plan-selectable').checked = plan.is_selectable; $('#plan-save').textContent = 'Save plan';
        $('#plan-name').focus();
      } else if (target.closest('[data-key-revoke]')) {
        const id = target.closest('[data-key-revoke]').dataset.keyRevoke;
        if (!window.confirm('Revoke this API key? This cannot be undone.')) return;
        await mutate(`/api/v1/admin/keys/${id}/revoke`, 'POST', {});
        showMessage('API key revoked.'); await loadKeys();
      }
    } catch (error) { showMessage(error.message, true); }
  });

  document.addEventListener('change', async event => {
    const select = event.target.closest('[data-project-plan]');
    if (!select) return;
    try {
      const planId = select.value ? Number(select.value) : null;
      await mutate(`/api/v1/admin/projects/${select.dataset.projectPlan}`, 'PATCH', { plan_id: planId });
      showMessage('Project plan assignment updated.');
    } catch (error) { showMessage(error.message, true); }
  });

  async function logout() {
    try { const token = await csrf(); await fetchJson('/api/v1/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: '{}' }); }
    catch (error) { if (!error.message.includes('expired')) showMessage(error.message, true); }
    window.location.replace('/developer/login');
  }
  $('#logout-button').addEventListener('click', logout);
  window.addEventListener('pageshow', async event => {
    if (event.persisted) { try { await fetchJson('/api/v1/admin'); } catch (_) { window.location.replace('/developer/login?next=%2Fadmin'); } }
  });
  loadView('overview');
})();
