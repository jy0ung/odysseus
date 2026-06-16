// static/js/studioWorkspace.js
//
// Studio workspace MVP: seed a role-based AI team plus editable product docs.

import uiModule from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';

let API_BASE = window.location.origin;
let _modal = null;
let _workspaces = [];
let _presets = [];
let _selectedId = '';
let _busy = false;

const _STUDIO_SVG = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M12 12v3"/><path d="M3 12h18"/></svg>';

function _esc(value) {
  if (uiModule && uiModule.esc) return uiModule.esc(value == null ? '' : String(value));
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _toast(message) {
  if (uiModule && uiModule.showToast) uiModule.showToast(message);
}

function _error(message) {
  if (uiModule && uiModule.showError) uiModule.showError(message);
  else _toast(message);
}

async function _fetchJson(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    data = null;
  }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `Request failed: ${res.status}`;
    throw new Error(detail);
  }
  return data || {};
}

function _selectedWorkspace() {
  return _workspaces.find((workspace) => workspace.id === _selectedId) || _workspaces[0] || null;
}

function _presetOptions() {
  if (!_presets.length) {
    return '<option value="game_dev">Game Development Stack</option>';
  }
  return _presets.map((preset) => (
    `<option value="${_esc(preset.key)}">${_esc(preset.name)}</option>`
  )).join('');
}

function _workspaceRows() {
  if (!_workspaces.length) {
    return '<div class="studio-empty">No studios yet</div>';
  }
  return _workspaces.map((workspace) => {
    const active = workspace.id === (_selectedId || (_workspaces[0] && _workspaces[0].id));
    const artifactCount = Array.isArray(workspace.artifacts) ? workspace.artifacts.length : 0;
    return `
      <button type="button" class="studio-list-item${active ? ' active' : ''}" data-studio-id="${_esc(workspace.id)}">
        <span class="studio-list-title">${_esc(workspace.name)}</span>
        <span class="studio-list-meta">${_esc(workspace.phase || 'intake')} / ${artifactCount} docs</span>
      </button>`;
  }).join('');
}

function _teamCards(workspace) {
  const team = workspace && Array.isArray(workspace.team) ? workspace.team : [];
  if (!team.length) return '<div class="studio-empty">No roles seeded</div>';
  return team.map((agent) => `
    <div class="studio-role-card">
      <div class="studio-avatar">${_esc(agent.avatar || agent.role_name || 'AI').slice(0, 2)}</div>
      <div>
        <div class="studio-role-title">${_esc(agent.role_name)}</div>
        <div class="studio-role-mission">${_esc(agent.mission)}</div>
      </div>
    </div>
  `).join('');
}

function _artifactRows(workspace) {
  const artifacts = workspace && Array.isArray(workspace.artifacts) ? workspace.artifacts : [];
  if (!artifacts.length) return '<div class="studio-empty">No artifacts yet</div>';
  return artifacts.map((artifact) => `
    <div class="studio-artifact-row">
      <div>
        <div class="studio-artifact-title">${_esc(artifact.title)}</div>
        <div class="studio-artifact-meta">${_esc(artifact.kind)} / ${_esc(artifact.status)}</div>
      </div>
      <button type="button" class="studio-icon-btn" title="Open document" aria-label="Open ${_esc(artifact.title)}" data-doc-id="${_esc(artifact.document_id || '')}">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <path d="M9 15h6"/>
        </svg>
      </button>
    </div>
  `).join('');
}

function _detailPane(workspace) {
  if (!workspace) {
    return `
      <section class="studio-detail-empty">
        <h5>No studio selected</h5>
      </section>`;
  }
  return `
    <section class="studio-detail">
      <div class="studio-detail-head">
        <div>
          <h5>${_esc(workspace.name)}</h5>
          <div class="studio-status">${_esc(workspace.status || 'draft')} / ${_esc(workspace.phase || 'intake')}</div>
        </div>
        <span class="studio-focus">${_esc(workspace.current_focus || '')}</span>
      </div>
      <div class="studio-idea">${_esc(workspace.idea)}</div>

      <div class="studio-section-title">Team</div>
      <div class="studio-team-grid">${_teamCards(workspace)}</div>

      <div class="studio-section-title">Artifacts</div>
      <div class="studio-artifact-list">${_artifactRows(workspace)}</div>
    </section>`;
}

function _render() {
  if (!_modal) return;
  const workspace = _selectedWorkspace();
  if (workspace && !_selectedId) _selectedId = workspace.id;
  const body = _modal.querySelector('#studio-workspace-body');
  body.innerHTML = `
    <aside class="studio-sidebar">
      <div class="studio-sidebar-head">
        <h5>Studios</h5>
        <button type="button" class="studio-icon-btn" id="studio-refresh" title="Refresh" aria-label="Refresh studios">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
        </button>
      </div>
      <div class="studio-list">${_workspaceRows()}</div>
    </aside>
    <main class="studio-main">
      <form class="studio-create" id="studio-create-form">
        <div class="studio-create-row">
          <input type="text" class="styled-prompt-input" id="studio-name" maxlength="80" placeholder="Studio name" />
          <select class="styled-prompt-input" id="studio-preset">${_presetOptions()}</select>
        </div>
        <textarea class="styled-prompt-input studio-idea-input" id="studio-idea" rows="4" placeholder="Rough game idea"></textarea>
        <div class="studio-create-actions">
          <button type="submit" class="confirm-btn confirm-btn-primary" ${_busy ? 'disabled' : ''}>${_busy ? 'Creating...' : 'Create studio'}</button>
        </div>
      </form>
      ${_detailPane(workspace)}
    </main>`;

  body.querySelectorAll('.studio-list-item').forEach((button) => {
    button.addEventListener('click', () => {
      _selectedId = button.dataset.studioId || '';
      _render();
    });
  });

  const refresh = body.querySelector('#studio-refresh');
  if (refresh) refresh.addEventListener('click', () => _loadAll(true));

  const form = body.querySelector('#studio-create-form');
  if (form) form.addEventListener('submit', _createStudio);

  body.querySelectorAll('[data-doc-id]').forEach((button) => {
    button.addEventListener('click', () => _openDocument(button.dataset.docId || ''));
  });
}

async function _loadAll(showToast = false) {
  try {
    const [presetData, workspaceData] = await Promise.all([
      _fetchJson('/api/studio-workspaces/presets'),
      _fetchJson('/api/studio-workspaces'),
    ]);
    _presets = Array.isArray(presetData.presets) ? presetData.presets : [];
    _workspaces = Array.isArray(workspaceData.workspaces) ? workspaceData.workspaces : [];
    if (_selectedId && !_workspaces.some((workspace) => workspace.id === _selectedId)) _selectedId = '';
    _render();
    if (showToast) _toast('Studios refreshed');
  } catch (err) {
    _error(err.message || 'Could not load studios');
  }
}

async function _createStudio(event) {
  event.preventDefault();
  if (_busy) return;
  const form = event.currentTarget;
  const name = form.querySelector('#studio-name').value.trim();
  const idea = form.querySelector('#studio-idea').value.trim();
  const preset = form.querySelector('#studio-preset').value || 'game_dev';
  if (!idea) {
    _error('Add a rough idea first');
    return;
  }
  _busy = true;
  _render();
  try {
    const data = await _fetchJson('/api/studio-workspaces', {
      method: 'POST',
      body: JSON.stringify({ name: name || null, idea, preset }),
    });
    const workspace = data.workspace;
    if (workspace) {
      _selectedId = workspace.id;
      await _loadAll(false);
      _toast('Studio created');
    }
  } catch (err) {
    _error(err.message || 'Could not create studio');
  } finally {
    _busy = false;
    _render();
  }
}

async function _openDocument(docId) {
  if (!docId) return;
  try {
    if (window.documentModule && window.documentModule.loadDocument) {
      await window.documentModule.loadDocument(docId);
      if (window.documentModule.openPanel) window.documentModule.openPanel();
    } else {
      window.location.hash = `doc-${docId}`;
    }
  } catch (err) {
    window.location.hash = `doc-${docId}`;
  }
}

function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'studio-workspace-modal';
  _modal.className = 'modal';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content studio-modal-content">
      <div class="modal-header">
        <h4>${_STUDIO_SVG} Studio Workspace</h4>
        <button class="close-btn" id="studio-workspace-close" aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </button>
      </div>
      <div class="modal-body studio-modal-body" id="studio-workspace-body"></div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#studio-workspace-close').addEventListener('click', closeStudioWorkspace);
  _modal.addEventListener('click', (event) => {
    if (event.target === _modal) closeStudioWorkspace();
  });
  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  return _modal;
}

export async function openStudioWorkspace() {
  const modal = _getModal();
  modal.style.display = 'flex';
  _render();
  await _loadAll(false);
}

export function closeStudioWorkspace() {
  if (_modal) _modal.style.display = 'none';
}

export function init(apiBase) {
  API_BASE = apiBase || window.location.origin;
  const overflow = document.getElementById('overflow-studio-workspace-btn');
  if (overflow) overflow.addEventListener('click', openStudioWorkspace);
}

const studioWorkspaceModule = {
  init,
  openStudioWorkspace,
  closeStudioWorkspace,
};

export default studioWorkspaceModule;
window.studioWorkspaceModule = studioWorkspaceModule;
