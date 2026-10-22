"""Persistent maintenance queue; shares the single worker with backups."""
import json,secrets
import engine as E
ACTIVE="('queued','running','unknown')"
def init():
 with E.db() as c:
  c.execute("CREATE TABLE IF NOT EXISTS maintenance(id TEXT PRIMARY KEY,server_id TEXT NOT NULL REFERENCES servers(id),server_version INTEGER NOT NULL,mode TEXT NOT NULL,status TEXT NOT NULL,created INTEGER NOT NULL,finished INTEGER,preview TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '{}')")
def busy(c):
 import terminals as T
 return c.execute('SELECT 1 FROM maintenance WHERE status IN '+ACTIVE).fetchone() or T.active(c)
def create(server,mode,preview=None):
 if mode not in ['check','upgrade']:raise ValueError('Opération invalide.')
 if server['username']!='root' or not server['host_keys']:raise ValueError('Cette opération exige root et une empreinte SSH confirmée.')
 with E.db() as c:
  c.execute('BEGIN IMMEDIATE')
  if busy(c) or c.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():raise ValueError('Une opération est déjà en cours. Réessayez après sa fin.')
  if c.execute('SELECT version FROM servers WHERE id=?',(server['id'],)).fetchone()[0]!=server['version']:raise ValueError('Serveur modifié ailleurs. Actualisez.')
  if mode=='upgrade':
   p=c.execute("SELECT * FROM maintenance WHERE id=? AND server_id=? AND mode='check' AND status='success'",(preview,server['id'])).fetchone()
   if not p or p['server_version']!=server['version'] or p['finished']<E.now()-900:raise ValueError('Vérification expirée. Vérifiez à nouveau les mises à jour.')
   if not json.loads(p['result']).get('packages'):raise ValueError('Aucun paquet à mettre à jour.')
   if c.execute("SELECT 1 FROM maintenance WHERE preview=? AND mode='upgrade'",(preview,)).fetchone():raise ValueError('Cette vérification a déjà été utilisée.')
  id=secrets.token_hex(12)
  c.execute('INSERT INTO maintenance(id,server_id,server_version,mode,status,created,preview) VALUES(?,?,?,?,?,?,?)',(id,server['id'],server['version'],mode,'queued',E.now(),preview or ''))
 E.audit('Maintenance '+mode+' demandée : '+id)
 return id
def tick():
 with E.db() as c:
  c.execute('BEGIN IMMEDIATE')
  job=c.execute('SELECT * FROM maintenance WHERE status IN '+ACTIVE+' ORDER BY created LIMIT 1').fetchone()
  if not job:return False
  if c.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():return True
  server=c.execute('SELECT * FROM servers WHERE id=?',(job['server_id'],)).fetchone()
  if job['status']=='queued':
   if server['version']!=job['server_version']:
    c.execute("UPDATE maintenance SET status='failed',finished=?,result=? WHERE id=?",(E.now(),json.dumps(dict(reason='changed')),job['id']));return True
   c.execute("UPDATE maintenance SET status='running' WHERE id=?",(job['id'],))
  digest=''
  if job['preview']:
   p=c.execute('SELECT result FROM maintenance WHERE id=?',(job['preview'],)).fetchone();digest=json.loads(p[0])['digest']
 try:
  with E.SSH(server) as remote:
   result=remote.call(dict(action='maintenance_start' if job['status']=='queued' else 'maintenance_status',operation_id=job['id'],mode=job['mode'],digest=digest),timeout=45)
  status=result.get('status')
  if status not in ['running','success','failed']:raise ValueError('status')
  with E.db() as c:c.execute('UPDATE maintenance SET status=?,finished=?,result=? WHERE id=?',(status,E.now() if status in ['success','failed'] else None,json.dumps(result),job['id']))
  if status in ['success','failed']:E.audit('Maintenance '+job['id']+' : '+status)
 except Exception:
  # Unknown remote outcome: retain the lock and poll; never retry an upgrade.
  with E.db() as c:c.execute("UPDATE maintenance SET status='unknown',result=? WHERE id=?",(json.dumps(dict(reason='connection')),job['id']))
 return True
