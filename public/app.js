'use strict';

const m = BackupCenterI18n.markup;
const t = BackupCenterI18n.t,
  html = BackupCenterI18n.html;
let state = {},
  currentView = 'overview',
  pendingTrust = null,
  inventoryResult = null,
  archivePage = 1,
  archiveSite = '',
  archiveData = null,
  archiveRequest = 0;
const $ = s => document.querySelector(s),
  $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
})[c]);
const dates = t => t ? new Date(t * 1000).toLocaleString(BackupCenterI18n.locale, {
  dateStyle: 'medium',
  timeStyle: 'short'
}) : '—';
const bytes = n => {
  if (!n) return '—';
  const i = Math.min(3, Math.floor(Math.log(n) / Math.log(1024)));
  return (n / 1024 ** i).toLocaleString(BackupCenterI18n.locale, {
    maximumFractionDigits: 1
  }) + ' ' + [t('o'), t('Ko'), t('Mo'), t('Go')][i];
};
function toast(message, error = false) {
  const el = $('#toast');
  el.textContent = message;
  const dialog = [...document.querySelectorAll('dialog[open]')].at(-1);
  if (dialog) {
    const head=dialog.querySelector('.modal-head');
    if(head)head.after(el);else dialog.prepend(el);
  } else document.body.append(el);
  el.className = (error ? 'error ' : '') + (dialog ? 'dialog-toast' : '');
  el.setAttribute('role',error ? 'alert' : 'status');
  el.hidden = false;
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => el.hidden = true, 8000);
}
async function api(path, data, raw = false) {
  const options = {
    credentials: 'same-origin',
    headers: {
      'X-BackupCenter-Language': BackupCenterI18n.language
    }
  };
  if (data !== undefined) {
    options.method = 'POST';
    options.headers = {
      'Content-Type': 'application/json',
      'X-CSRF-Token': state.csrf,
      'X-BackupCenter-Language': BackupCenterI18n.language
    };
    options.body = JSON.stringify(data);
  }
  const response = await fetch('/api/' + path, options);
  if (!response.ok) {
    let e;
    try {
      e = await response.json();
    } catch {
      e = {
        error: t('Le service ne répond pas. Réessayez.')
      };
    }
    if (response.status === 401) await refresh();
    throw Error(e.error || t('Opération refusée.'));
  }
  return raw ? response : response.json();
}
const formData = f => Object.fromEntries(new FormData(f).entries());
async function action(fn, button) {
  if (button) button.disabled = true;
  try {
    await fn();
  } catch (e) {
    toast(e.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}
function badge(status) {
  const labels = {
    success: t('Vérifiée'),
    failed: t('Échec'),
    running: t('En cours'),
    queued: t('En attente'),
    none: t('À configurer')
  };
  return html`<span class="badge ${esc(status)}">${labels[status] || esc(status)}</span>`;
}
function siteCard(site) {
  const jobs = state.jobs.filter(j => j.site_id === site.id && j.kind === 'backup'),
    last = jobs[0],
    good = jobs.find(j => j.status === 'success' && j.archive),
    server = state.servers.find(s => s.id === site.server_id);
  return html`<article class="site-card"><div class="card-top"><span class="card-icon">${site.scope === 'server' ? '▰' : '▤'}</span>${last ? badge(last.status) : m('<span class="badge neutral">Aucune copie</span>')}</div><h3>${esc(site.name)}</h3><p class="subtle">${esc(server?.name)} · ${site.scope === 'server' ? t('VPS global') : site.databases.length + t(' base(s) de données')}</p><div class="site-meta"><div><span>Dernière copie vérifiée</span><strong>${good ? esc(dates(good.finished)) : '—'}</strong></div><div><span>Prochaine sauvegarde</span><strong>${site.enabled ? esc(dates(site.next_run)) : t('Manuelle')}</strong></div><div><span>Conservation</span><strong>${site.retention} copies</strong></div></div><div class="card-actions"><button class="text-button" data-archives="${site.id}">Archives ↗</button><button class="secondary" data-edit="${site.id}">Configurer</button><button class="primary" data-backup="${site.id}">Sauvegarder ↗</button></div></article>`;
}
function jobTable(jobs) {
  if (!jobs.length) return m('<div class="empty"><h3>Votre historique commence ici.</h3><p>Les sauvegardes et restaurations apparaîtront après leur lancement.</p></div>');
  return html`<div class="table-wrap"><table><thead><tr><th>Source / Opération</th><th>Date</th><th>État</th><th>Taille</th><th>Actions</th></tr></thead><tbody>${jobs.map(j => html`<tr><td><strong>${esc(j.site_name)}</strong><small>${j.kind === 'backup' ? t('Sauvegarde') : t('Restauration isolée')}${j.message ? ' · ' + esc(j.message) : ''}</small></td><td>${esc(dates(j.created))}</td><td>${badge(j.status)}</td><td>${bytes(j.size)}</td><td><div class="table-actions">${j.status === 'success' && j.archive ? html`<a href="/api/jobs/${j.id}/download">Chiffrée ↧</a><a href="/api/jobs/${j.id}/download?format=zip">ZIP ↧</a><button data-restore="${j.id}">Restaurer</button>` : '—'}</div></td></tr>`).join('')}</tbody></table></div>`;
}
function render() {
  const auth = state.authenticated;
  $('#auth').hidden = auth;
  $('#workspace').hidden = !auth;
  if (!auth) {
    $('#setup-label').hidden = !state.setup;
    $('#setup-label input').required = state.setup;
    $('#totp-login').hidden = state.setup;
    $('#auth-title').textContent = state.setup ? t('Bienvenue chez vous.') : t('Bon retour.');
    $('#auth-desc').textContent = state.setup ? t('Créez votre compte pour démarrer BackupCenter.') : t('Connectez-vous à votre centre de sauvegarde.');
    $('#auth-submit').textContent = state.setup ? t('Créer mon compte →') : t('Se connecter →');
    return;
  }
  const success = state.jobs.filter(j => j.kind === 'backup' && j.status === 'success' && j.archive),
    active = state.jobs.filter(j => ['running', 'queued'].includes(j.status));
  const failed = state.sites.filter(s => state.jobs.find(j => j.site_id === s.id && j.kind === 'backup')?.status === 'failed');
  $$('.schedule-zone').forEach(el => el.textContent = t('Fuseau horaire : ') + (state.schedule_timezone || 'UTC'));
  $('#stats').innerHTML = [[t('Sources configurées'), state.sites.length, state.sites.filter(s => s.enabled).length + t(' planification(s) active(s)')], [t('Archives conservées'), state.archive_summary?.count ?? success.length, t('Archives chiffrées et vérifiées')], [t('Tâches à suivre'), active.length + failed.length, active.length + t(' en cours ou en attente · ') + failed.length + t(' échec(s)')], [t('Espace disponible'), bytes(state.storage.free), t('Sur le stockage des archives')]].map(([label, value, note]) => html`<article class="stat"><small>${label}</small><strong>${value}</strong><div class="stat-note">${note}</div>${label === t('Archives conservées') ? m('<button data-view="archives">Consulter les archives →</button>') : ''}</article>`).join('');
  $('#hero-desc').textContent = state.sites.length ? html`${state.sites.length} source(s) configurée(s). Consultez les dernières copies et surveillez les échéances.` : t('Ajoutez votre premier serveur pour commencer à protéger vos sites.');
  const empty = m('<div class="empty"><span class="card-icon">＋</span><h3>Un premier site à mettre à l’abri ?</h3><p>Connectez votre VPS, puis choisissez ses fichiers et ses bases de données.</p><button class="primary" data-action="add-server">Connecter mon serveur</button></div>');
  $('#site-cards').innerHTML = state.sites.length ? state.sites.slice(0, 4).map(siteCard).join('') : empty;
  $('#all-sites').innerHTML = state.sites.length ? state.sites.map(siteCard).join('') : empty;
  $('#archive-directory').textContent = state.storage.directory || '/mnt/user/backups/backupcenter';
  $('#archive-filter').innerHTML = m('<option value="">Toutes les sources</option>') + state.sites.map(s => html`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
  $('#archive-filter').value = archiveSite;
  $('#recent-jobs').innerHTML = jobTable(state.jobs.slice(0, 5));
  $('#jobs').innerHTML = jobTable(state.jobs);
  $('#servers').innerHTML = state.servers.length ? state.servers.map(s => html`<article class="site-card server-card"><div class="card-top"><span class="card-icon">▰</span><span class="badge ${s.fingerprints ? '' : 'neutral'}">${s.fingerprints ? t('Empreinte enregistrée') : t('À vérifier')}</span></div><h3>${esc(s.name)}</h3><p class="subtle">${esc(s.username)}@${esc(s.host)}:${s.port}</p><div class="server-discovery"><button class="primary" data-inventory="${s.id}">Détecter et choisir les sites →</button></div><div class="server-buttons"><button class="secondary" data-server-edit="${s.id}">Configurer</button><button class="secondary" data-scan="${s.id}">Empreinte</button><button class="secondary" data-test="${s.id}">Tester SSH</button><button class="secondary" data-global="${s.id}">VPS global</button><button class="secondary server-maintenance" data-maintenance="${s.id}">Mises à jour</button></div></article>`).join('') : m('<div class="empty">Aucun serveur connecté.</div>');
  $('#totp-status').textContent = state.totp ? t('Activée. Un code est requis à chaque connexion.') : t('Ajoutez un code temporaire à votre mot de passe pour protéger vos archives.');
  $('#audit').innerHTML = state.audit.map(a => html`<div class="audit-item">${esc(a.event)}<small>${esc(dates(a.created))}</small></div>`).join('') || m('<p>Aucun événement.</p>');
  $('#last-refresh').textContent = t('Actualisé à ') + new Date().toLocaleTimeString(BackupCenterI18n.locale);
}
async function refresh() {
  state = await api('state');
  render();
  if (state.authenticated && currentView === 'archives') await loadArchives();
}
function view(name) {
  currentView = name;
  $$('main>section').forEach(s => s.hidden = s.id !== 'view-' + name);
  $$('nav button').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  $('#page-title').textContent = {
    overview: t('Vue d’ensemble'),
    sources: t('Sites et serveurs'),
    history: t('Historique'),
    archives: 'Archives',
    security: t('Sécurité')
  }[name];
  if (name === 'archives') action(loadArchives);
}
function showSite(site = null, serverId = null, global = false) {
  if (!state.servers.length) {
    toast(t('Ajoutez d’abord votre serveur SSH.'));
    $('#server-dialog').showModal();
    return;
  }
  const f = $('#site-form');
  f.reset();
  $('#discovery-result').hidden = true;
  f.elements.server_id.innerHTML = state.servers.map(s => html`<option value="${s.id}">${esc(s.name)}</option>`).join('');
  f.elements.id.value = '';
  f.elements.version.value = '';
  f.elements.use_server_db.checked = true;
  if (serverId) f.elements.server_id.value = serverId;
  if (site) {
    for (const key of ['id', 'version', 'name', 'server_id', 'scope', 'path', 'interval_hours', 'retention']) f.elements[key].value = site[key];
    f.elements.backup_time.value = site.backup_time || '02:00';
    f.elements.databases.value = site.databases.join(', ');
    f.elements.enabled.checked = !!site.enabled;
    f.elements.files_only.checked = site.scope === 'site' && !site.databases.length;
    f.elements.use_server_db.checked = !!site.use_server_db;
  }
  if (global) {
    f.elements.scope.value = 'server';
    f.elements.path.value = '/';
    f.elements.name.value = (state.servers.find(x => x.id === serverId)?.name || 'VPS') + t(' — global');
    f.elements.interval_hours.value = '720';
    f.elements.retention.value = '6';
  }
  $('#site-db-custom').hidden = f.elements.use_server_db.checked;
  $('#site-modal-title').textContent = site ? t('Configurer la source') : t('Ajouter un site');
  scopeChange(false);
  $('#site-dialog').showModal();
}
function scopeChange(changed) {
  const f = $('#site-form'),
    server = f.elements.scope.value === 'server';
  $('#server-notice').hidden = !server;
  $('#db-section').hidden = server;
  if (changed) {
    f.elements.path.value = server ? '/' : '';
    f.elements.interval_hours.value = server ? '720' : '24';
    f.elements.retention.value = server ? '6' : '14';
  }
}
function sitePayload() {
  const f = $('#site-form'),
    d = formData(f);
  d.databases = d.databases.split(',').map(s => s.trim()).filter(Boolean);
  d.enabled = f.elements.enabled.checked;
  d.files_only = f.elements.files_only.checked;
  d.use_server_db = f.elements.use_server_db.checked;
  return d;
}
document.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.hasAttribute('data-close')) {
    b.closest('dialog').close();
    return;
  }
  if (b.dataset.view) {
    view(b.dataset.view);
    return;
  }
  if (b.dataset.archives) {
    archiveSite = b.dataset.archives;
    archivePage = 1;
    $('#archive-filter').value = archiveSite;
    view('archives');
    return;
  }
  if (b.dataset.action === 'add-server') showServer();
  if (b.dataset.action === 'add-site') showInventory();
  if (b.dataset.serverEdit) showServer(state.servers.find(s => s.id === b.dataset.serverEdit));
  if (b.dataset.inventory) showInventory(b.dataset.inventory, true);
  if (b.dataset.global) showSite(null, b.dataset.global, true);
  if (b.dataset.edit) showSite(state.sites.find(s => s.id === b.dataset.edit));
  if (b.dataset.backup) action(async () => {
    await api('sites/' + b.dataset.backup + '/backup', {});
    toast(t('Sauvegarde ajoutée à la file.'));
    await refresh();
  }, b);
  if (b.dataset.test) action(async () => {
    const r = await api('servers/' + b.dataset.test + '/test', {});
    toast(html`SSH connecté : ${r.hostname} · Python ${r.python} · tar ${r.tar ? t('disponible') : t('absent')} · export MySQL ${r.mysql ? t('disponible') : t('absent')}`);
  }, b);
  if (b.dataset.scan) action(async () => {
    const r = await api('servers/' + b.dataset.scan + '/scan', {});
    pendingTrust = {
      id: b.dataset.scan,
      fingerprints: r.fingerprints
    };
    $('#fingerprints').textContent = r.fingerprints;
    $('#trust-dialog').showModal();
  }, b);
  if (b.dataset.restore) {
    const j = state.jobs.find(j => j.id === b.dataset.restore) || archiveData?.items.find(j => j.id === b.dataset.restore);
    if (!j) {
      toast(t('Actualisez la liste des archives.'), true);
      return;
    }
    $('#restore-form').reset();
    $('#restore-form').elements.id.value = j.id;
    $('#restore-name').textContent = j.site_name;
    $('#restore-dialog').showModal();
  }
});
$('#auth-form').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    await api(state.setup ? 'setup' : 'login', formData(e.target));
    e.target.reset();
    await refresh();
  }, e.submitter);
});
$('#logout').onclick = () => action(async () => {
  await api('logout', {});
  await refresh();
});
$('#refresh').onclick = () => action(refresh, $('#refresh'));
$('#server-form').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    const d = formData(e.target);
    const r = await api('servers' + (d.id ? '/' + d.id : ''), d);
    if (!d.id) serverDraftKey = null;
    e.target.reset();
    $('#server-dialog').close();
    await refresh();
    view('sources');
    toast(d.id ? r.reconfirm_host ? t('Serveur modifié. Confirmez à nouveau son empreinte.') : t('Serveur modifié.') : t('VPS ajouté. Confirmez son empreinte, puis détectez les sites.'));
  }, e.submitter);
});
$('#trust-confirm').onclick = () => action(async () => {
  await api('servers/' + pendingTrust.id + '/trust', {
    fingerprints: pendingTrust.fingerprints
  });
  $('#trust-dialog').close();
  await refresh();
  toast(t('Empreinte enregistrée. Vous pouvez tester la connexion.'));
}, $('#trust-confirm'));
$('#site-form').elements.scope.onchange = () => scopeChange(true);
$('#site-form').elements.use_server_db.onchange = e => $('#site-db-custom').hidden = e.target.checked;
$('#server-form').elements.mysql_mode.onchange = e => $('#server-db-custom').hidden = e.target.value !== 'custom';
$('#site-form').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    const d = sitePayload();
    await api('sites' + (d.id ? '/' + d.id : ''), d);
    e.target.reset();
    $('#site-dialog').close();
    await refresh();
    toast(t('Configuration enregistrée.'));
  }, e.submitter);
});
$('#discover').onclick = () => action(async () => {
  const r = await api('discover', sitePayload());
  const box = $('#discovery-result');
  box.replaceChildren();
  const p = document.createElement('p');
  p.textContent = t('Détectées dans les configurations : ') + (r.candidates.join(', ') || t('aucune')) + t('. Accessibles au compte MySQL : ') + (r.accessible.join(', ') || t('non vérifiées')) + '. ' + r.note;
  box.append(p);
  for (const name of [...new Set([...r.candidates, ...r.accessible])]) {
    const label = document.createElement('label');
    label.className = 'checkbox';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = $('#site-form').elements.databases.value.split(',').map(s => s.trim()).includes(name);
    input.onchange = () => {
      const field = $('#site-form').elements.databases,
        values = new Set(field.value.split(',').map(s => s.trim()).filter(Boolean));
      if (input.checked) values.add(name);else values.delete(name);
      field.value = [...values].join(', ');
    };
    label.append(input, document.createTextNode(name));
    box.append(label);
  }
  box.hidden = false;
}, $('#discover'));
$('#restore-form').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    const d = formData(e.target);
    await api('jobs/' + d.id + '/restore', d);
    $('#restore-dialog').close();
    await refresh();
    view('history');
    toast(t('Restauration isolée ajoutée à la file.'));
  }, e.submitter);
});
$('#totp-start').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    const r = await api('security/totp-start', formData(e.target));
    $('#totp-secret').textContent = r.secret;
    $('#totp-enroll').hidden = false;
    e.target.reset();
  }, e.submitter);
});
$('#totp-confirm').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    await api('security/totp-confirm', formData(e.target));
    $('#totp-secret').textContent = '';
    $('#totp-enroll').hidden = true;
    e.target.reset();
    await refresh();
    toast(t('Double authentification activée.'));
  }, e.submitter);
});
$('#password-form').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    await api('security/password', formData(e.target));
    e.target.reset();
    toast(t('Mot de passe modifié.'));
  }, e.submitter);
});
$('#recovery-form').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    const r = await api('security/recovery', formData(e.target), true),
      blob = await r.blob(),
      url = URL.createObjectURL(blob),
      a = document.createElement('a');
    a.href = url;
    a.download = 'BackupCenter-recovery.key';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    e.target.reset();
    await refresh();
  }, e.submitter);
});
function showServer(server = null) {
  keyRequest++;
  activeServerKey = null;
  $('#ssh-install-block').hidden = true;
  $('#ssh-key-status').textContent = '';
  const f = $('#server-form');
  f.reset();
  $('#server-form button.primary').disabled=false;
  f.elements.private_key.readOnly=false;
  f.elements.id.value = '';
  f.elements.version.value = '';
  if (server) {
    for (const name of ['id', 'version', 'name', 'host', 'port', 'username', 'mysql_mode', 'mysql_cnf', 'mysql_user', 'mysql_host']) f.elements[name].value = server[name] ?? '';
  }
  $('#server-modal-title').textContent = server ? t('Configurer le VPS') : t('Ajouter un VPS');
  $('#server-form button.primary').textContent = server ? t('Enregistrer') : t('Ajouter le VPS');
  $('#server-db-custom').hidden = f.elements.mysql_mode.value !== 'custom';
  $('#server-dialog').showModal();
  $('#ssh-key-description').textContent = server ? t('La clé existante sera conservée si ce champ reste vide. Générer une nouvelle paire ne révoque pas l’ancienne clé sur le VPS.') : t('Une paire dédiée est générée pour cette nouvelle connexion. Copiez la commande publique dans le KVM, puis enregistrez le serveur.');
  if (!server) {
    if (serverDraftKey) applyServerKey(serverDraftKey);else action(generateServerKey, $('#generate-ssh-key'));
  }
}
function showInventory(serverId = null, scan = false) {
  if (!state.servers.length) {
    showServer();
    return;
  }
  const select = $('#inventory-server');
  select.innerHTML = state.servers.map(s => html`<option value="${s.id}">${esc(s.name)} · ${esc(s.host)}</option>`).join('');
  if (serverId) select.value = serverId;
  $('#inventory-form').reset();
  if (serverId) select.value = serverId;
  clearInventory();
  $('#inventory-dialog').showModal();
  if (scan) action(scanInventory, $('#inventory-scan'));
}
function clearInventory() {
  inventoryResult = null;
  $('#inventory-list').replaceChildren();
  $('#inventory-notice').hidden = true;
  $('#inventory-submit').disabled = true;
}
async function scanInventory() {
  const id = $('#inventory-server').value;
  clearInventory();
  $('#inventory-notice').textContent = t('Lecture des sites sur le VPS…');
  $('#inventory-notice').hidden = false;
  try {
    const result = await api('servers/' + id + '/inventory', {});
    inventoryResult = {
      ...result,
      serverId: id
    };
    $('#inventory-notice').textContent = result.mysql.message + ' ' + result.warnings.join(' ') + t(' Les bases accessibles ne sont pas automatiquement associées à un site.');
    $('#inventory-list').innerHTML = html`<datalist id="inventory-dbs">${result.mysql.databases.map(n => html`<option value="${esc(n)}"></option>`).join('')}</datalist>` + (result.sites.length ? result.sites.map(s => html`<article class="inventory-item ${s.configured_id ? 'configured' : ''}" data-token="${s.token}"><label class="checkbox inventory-select-name"><input type="checkbox" class="pick-site" ${s.configured_id ? 'disabled' : ''}>${esc(s.name)} ${s.configured_id ? m('<span class="badge">Déjà configuré</span>') : ''}</label><p class="item-path">${esc(s.path)}</p><p class="item-aliases">${esc(s.domains.join(' · '))} · ${esc(s.sources.join(', '))}</p>${s.configured_id ? '' : html`<label>Bases de données à inclure<input class="pick-databases" value="${esc(s.databases.join(', '))}" list="inventory-dbs" placeholder="base_principale, base_archive"></label><label class="checkbox"><input type="checkbox" class="pick-files-only"> Fichiers seulement : aucune base à sauvegarder pour ce site</label>`}</article>`).join('') : m('<div class="empty">Aucun site détecté. Vérifiez les permissions du compte SSH ou utilisez l’ajout manuel pour une arborescence personnalisée.</div>'));
  } catch (e) {
    $('#inventory-notice').textContent = e.message;
    throw e;
  }
}
function inventorySelection() {
  return $$('#inventory-list .inventory-item').filter(row => row.querySelector('.pick-site').checked).map(row => ({
    token: row.dataset.token,
    databases: row.querySelector('.pick-databases').value.split(',').map(x => x.trim()).filter(Boolean),
    files_only: row.querySelector('.pick-files-only').checked
  }));
}
$('#inventory-scan').onclick = () => action(scanInventory, $('#inventory-scan'));
$('#inventory-server').onchange = clearInventory;
$('#inventory-list').onchange = () => $('#inventory-submit').disabled = !inventorySelection().length;
$('#manual-site').onclick = () => {
  const id = $('#inventory-server').value;
  $('#inventory-dialog').close();
  showSite(null, id);
};
$('#inventory-form').addEventListener('submit', e => {
  e.preventDefault();
  action(async () => {
    if (!inventoryResult || inventoryResult.serverId !== $('#inventory-server').value) throw Error(t('Détectez d’abord les sites de ce VPS.'));
    const d = formData(e.target);
    d.sites = inventorySelection();
    d.enabled = e.target.elements.enabled.checked;
    await api('servers/' + inventoryResult.serverId + '/import', d);
    $('#inventory-dialog').close();
    await refresh();
    view('sources');
    toast(d.sites.length + t(' site(s) ajouté(s). Lancez une première sauvegarde manuelle pour les vérifier.'));
  }, e.submitter);
});
async function loadArchives() {
  const requestId = ++archiveRequest;
  $('#archive-list').setAttribute('aria-busy', 'true');
  try {
    const result = await api('archives?page=' + archivePage + '&site_id=' + encodeURIComponent(archiveSite));
    if (requestId !== archiveRequest) return;
    archiveData = result;
    renderArchives();
  } finally {
    if (requestId === archiveRequest) $('#archive-list').removeAttribute('aria-busy');
  }
}
function renderArchives() {
  if (!archiveData) return;
  $('#archive-directory').textContent = archiveData.directory;
  $('#archive-count').textContent = archiveData.total;
  $('#archive-page').textContent = t('Page ') + archiveData.page + t(' sur ') + archiveData.pages;
  $('#archives-prev').disabled = archiveData.page <= 1;
  $('#archives-next').disabled = archiveData.page >= archiveData.pages;
  $('#archive-list').innerHTML = archiveData.items.length ? html`<div class="table-wrap archive-table"><table><thead><tr><th>Archive / Source</th><th>Sauvegardée le</th><th>Taille</th><th>Récupération</th></tr></thead><tbody>${archiveData.items.map(j => html`<tr><td><strong class="archive-source">${esc(j.site_name)}</strong><small>${esc(j.server_name)} · ZIP chiffré age</small><div class="archive-filename">${esc(j.filename)}</div><div class="archive-path"><details><summary>Emplacement et empreinte</summary><code>${esc(j.path)}</code><small>SHA-256</small><code>${esc(j.sha256)}</code></details></div></td><td>${esc(dates(j.finished || j.created))}</td><td>${bytes(j.size)}</td><td>${j.available ? html`<div class="table-actions"><a href="/api/jobs/${encodeURIComponent(j.id)}/download?format=zip">Télécharger ZIP ↧</a><a href="/api/jobs/${encodeURIComponent(j.id)}/download">Archive chiffrée ↧</a><button data-restore="${esc(j.id)}">Restaurer en dossier isolé</button></div>` : m('<span class="badge failed">Fichier introuvable</span><small>La copie est enregistrée, mais son fichier manque sur Unraid.</small>')}</td></tr>`).join('')}</tbody></table></div>` : html`<div class="empty"><span class="card-icon">▧</span><h3>${archiveSite ? t('Aucune archive pour cette source.') : t('Votre première archive apparaîtra ici.')}</h3><p>Lancez une sauvegarde depuis Sites et serveurs. Une copie vérifiée sera disponible ici avec son emplacement sur Unraid.</p><button class="primary" data-view="sources">Voir les sites →</button></div>`;
}
$('#archives-refresh').onclick = () => action(loadArchives, $('#archives-refresh'));
$('#archive-filter').onchange = e => {
  archiveSite = e.target.value;
  archivePage = 1;
  action(loadArchives);
};
$('#archives-prev').onclick = () => {
  if (archivePage > 1) {
    archivePage--;
    action(loadArchives);
  }
};
$('#archives-next').onclick = () => {
  if (archiveData && archivePage < archiveData.pages) {
    archivePage++;
    action(loadArchives);
  }
};
let serverDraftKey = null,
  activeServerKey = null,
  keyRequest = 0;
function buildSSHInstallCommand(username, publicKey) {
  if (!/^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$/.test(username)) return '';
  if (!/^ssh-ed25519 [A-Za-z0-9+/=]+ backupcenter-[a-f0-9]+$/.test(publicKey)) return '';
  return ["sudo bash <<'BACKUPCENTER_SSH'", 'set -eu', "bc_user='" + username + "'", 'bc_home=$(getent passwd "$bc_user" | cut -d: -f6)', 'test -n "$bc_home" && test -d "$bc_home"', 'bc_group=$(id -gn "$bc_user")', 'install -d -m 700 -o "$bc_user" -g "$bc_group" "$bc_home/.ssh"', 'touch "$bc_home/.ssh/authorized_keys"', 'chown "$bc_user:$bc_group" "$bc_home/.ssh/authorized_keys"', 'chmod 600 "$bc_home/.ssh/authorized_keys"', "bc_key='restrict " + publicKey + "'", 'grep -qxF "$bc_key" "$bc_home/.ssh/authorized_keys" || printf \'%s\\n\' "$bc_key" >> "$bc_home/.ssh/authorized_keys"', 'BACKUPCENTER_SSH'].join('\n');
}
function updateSSHInstallCommand() {
  const command = activeServerKey ? buildSSHInstallCommand($('#server-form').elements.username.value.trim(), activeServerKey.public_key) : '';
  $('#ssh-install-command').value = command;
  $('#ssh-install-block').hidden = !command;
}
function applyServerKey(key) {
  activeServerKey = key;
  $('#server-form').elements.private_key.value = key.private_key;
  updateSSHInstallCommand();
  $('#ssh-key-status').textContent = t('Clé générée. Installez la clé publique avant de tester SSH.');
}
async function generateServerKey() {
  const f = $('#server-form'),
    requestId = keyRequest,
    editing = !!f.elements.id.value;
  const submit = $('#server-form button.primary');
  submit.disabled = true;
  f.elements.private_key.readOnly = true;
  $('#ssh-key-status').textContent = t('Génération en cours…');
  try {
    const key = await api('ssh/keypair', {
      username: f.elements.username.value.trim() || 'root'
    });
    if (!editing) serverDraftKey = key;
    if (requestId === keyRequest) {
      applyServerKey(key);
      if (editing) $('#ssh-key-status').textContent = t('La clé a changé. Installez la nouvelle commande publique sur le VPS avant d’enregistrer.');
    }
  } catch(error) {
    if(requestId===keyRequest)$('#ssh-key-status').textContent='';
    throw error;
  } finally {
    if (requestId === keyRequest) {
      submit.disabled = false;
      f.elements.private_key.readOnly = false;
    }
  }
}
$('#generate-ssh-key').onclick = () => action(generateServerKey, $('#generate-ssh-key'));
$('#server-form').elements.username.addEventListener('input', updateSSHInstallCommand);
$('#server-form').elements.private_key.addEventListener('input', () => {
  if (activeServerKey && $('#server-form').elements.private_key.value.trim() !== activeServerKey.private_key.trim()) {
    activeServerKey = null;
    serverDraftKey = null;
    updateSSHInstallCommand();
    $('#ssh-key-status').textContent = '';
  }
});
$('#copy-ssh-command').onclick = () => action(async () => {
  try {
    await navigator.clipboard.writeText($('#ssh-install-command').value);
    toast(t('Commande copiée. Collez-la dans le KVM de votre VPS.'));
  } catch {
    $('#ssh-install-command').focus();
    $('#ssh-install-command').select();
    toast(t('Copie automatique indisponible. Sélectionnez et copiez la commande.'));
  }
}, $('#copy-ssh-command'));
window.addEventListener('bc-language-blocked', () => toast(t('Fermez le formulaire avant de changer de langue afin de conserver vos saisies.')));
action(refresh);
setInterval(() => {
  if (state.authenticated && !$$('dialog[open]').length) action(refresh);
}, 12000);

let maintenanceServer=null, maintenancePreview=null, maintenanceBusy=false;
async function loadMaintenance() {
  const server=maintenanceServer;if(!server)return;
  const r=await api('servers/'+server.id+'/maintenance');
  if(maintenanceServer!==server || !$('#maintenance-dialog').open)return;
  const active=r.items.find(x=>['queued','running','unknown'].includes(x.status));
  const latest=r.items[0];
  maintenanceBusy=!!active;
  maintenancePreview=!active && r.items.find(x=>x.mode==='check' && x.status==='success' && x.server_version===server.version && x.finished>Date.now()/1000-900 && !r.items.some(u=>u.preview===x.id));
  const labels={queued:t('En attente'),running:t('En cours'),unknown:t('Connexion interrompue : vérification distante en attente. Les sauvegardes restent suspendues.'),success:t('Terminée'),failed:t('Échec. Vérifiez le serveur avant de réessayer.')};
  const phases={starting:t('Démarrage'),refresh:t('Actualisation des index APT'),plan:t('Vérification des paquets'),upgrade:t('Installation des paquets')};
  const reasons={unsupported:t('Root, Debian/Ubuntu et systemd sont requis.'),changed:t('La configuration ou les paquets ont changé. Vérifiez à nouveau.'),refresh:t('Actualisation APT échouée. Vérifiez les dépôts et les verrous sur le VPS.'),launch:t('Démarrage distant impossible.'),upgrade:t('APT a échoué. Une installation partielle est possible; vérifiez le VPS.'),interrupted:t('Opération distante interrompue. Vérifiez le VPS.'),technical:t('Erreur distante. Vérifiez le VPS.')};
  $('#maintenance-status').textContent=latest ? (latest.mode==='check'?t('Vérification'):t('Installation'))+' · '+labels[latest.status]+(latest.result.phase?' · '+(phases[latest.result.phase]||''):'')+(latest.result.reason && reasons[latest.result.reason]?' · '+reasons[latest.result.reason]:'')+(latest.result.reboot?' · '+t('Redémarrage du VPS requis (à effectuer manuellement).'):'') : t('Aucune vérification effectuée.');
  const packages=(maintenancePreview||latest)?.result.packages||[];
  $('#maintenance-packages').innerHTML=packages.length ? html`<details open><summary>${packages.length} ${t('paquet(s) dans le plan de mise à jour')}</summary><pre>${esc(packages.join('\n'))}</pre></details>` : '';
  $('#maintenance-install-fields').hidden=maintenanceBusy || !maintenancePreview || !packages.length;
  $('#maintenance-upgrade').disabled=maintenanceBusy || !maintenancePreview || !packages.length;
  $('#maintenance-form [value="check"]').disabled=maintenanceBusy;
}
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-maintenance]');if(!b)return;
  maintenanceServer=state.servers.find(s=>s.id===b.dataset.maintenance);maintenancePreview=null;
  $('#maintenance-form').reset();$('#maintenance-title').textContent=t('Mises à jour du système')+' · '+maintenanceServer.name;
  $('#maintenance-install-fields').hidden=true;$('#maintenance-status').textContent=t('Chargement…');$('#maintenance-packages').innerHTML='';$('#maintenance-upgrade').disabled=true;
  $('#maintenance-dialog').showModal();action(loadMaintenance);
});
$('#maintenance-form').addEventListener('submit',e=>{
  e.preventDefault();const mode=e.submitter.value;
  action(async()=>{
    if(maintenanceBusy)throw Error(t('Une opération est déjà en cours. Réessayez après sa fin.'));
    if(mode==='upgrade' && (!maintenancePreview || !['oui','yes'].includes(e.target.elements.confirm.value.trim().toLowerCase())))throw Error(t('Écrivez Oui pour confirmer l’installation.'));
    const data=mode==='check'?{}:formData(e.target);data.mode=mode;data.version=maintenanceServer.version;if(mode==='upgrade')data.preview=maintenancePreview?.id;
    $('#maintenance-upgrade').disabled=true;$('#maintenance-form [value="check"]').disabled=true;
    try {await api('servers/'+maintenanceServer.id+'/maintenance',data);}finally{e.target.elements.password.value='';e.target.elements.totp.value='';await loadMaintenance();}
  });
});
setInterval(()=>{if($('#maintenance-dialog').open)action(loadMaintenance);},3000);
$('#maintenance-dialog').addEventListener('close',()=>{$('#maintenance-form').reset();maintenanceServer=null;maintenancePreview=null;});

document.addEventListener('close',event=>{
  if(event.target.tagName==='DIALOG' && event.target.contains($('#toast'))){
    const el=$('#toast');el.hidden=true;document.body.append(el);el.classList.remove('dialog-toast');
  }
},true);
