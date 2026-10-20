import os, json, sqlite3, secrets, subprocess, tempfile, shutil, hashlib, zipfile, time, threading, shlex
from pathlib import Path
from datetime import datetime, timezone
from cryptography.fernet import Fernet

DATA=Path(os.environ.get('BC_DATA','/data'))
ARCHIVES=Path(os.environ.get('BC_ARCHIVES','/archives'))
REMOTE=Path(__file__).with_name('remote.py').read_text()

def now(): return int(time.time())
class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try: return super().__exit__(*args)
        finally: self.close()

def db():
    c=sqlite3.connect(DATA/'center.sqlite',timeout=30,factory=Connection)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init():
    os.umask(0o077)
    DATA.mkdir(parents=True,exist_ok=True); ARCHIVES.mkdir(parents=True,exist_ok=True)
    for name in ['work','ssh']: (DATA/name).mkdir(exist_ok=True)
    for name, value in [('secrets.key',Fernet.generate_key()),('session.key',secrets.token_hex(32).encode()),('setup-code.txt',secrets.token_urlsafe(32).encode())]:
        path=DATA/name
        if not path.exists():
            with path.open('xb') as f: f.write(value)
    if not (DATA/'archive.key').exists():
        subprocess.run(['age-keygen','-o',str(DATA/'archive.key')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS servers(id TEXT PRIMARY KEY,name TEXT NOT NULL,host TEXT NOT NULL,port INTEGER NOT NULL,username TEXT NOT NULL,private_key TEXT NOT NULL,host_keys TEXT NOT NULL DEFAULT '',fingerprints TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sites(id TEXT PRIMARY KEY,server_id TEXT NOT NULL REFERENCES servers(id),name TEXT NOT NULL,path TEXT NOT NULL,scope TEXT NOT NULL,databases TEXT NOT NULL,db_config TEXT NOT NULL,interval_hours INTEGER NOT NULL,retention INTEGER NOT NULL,next_run INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,site_id TEXT REFERENCES sites(id),kind TEXT NOT NULL DEFAULT 'backup',status TEXT NOT NULL,created INTEGER NOT NULL,started INTEGER,finished INTEGER,message TEXT NOT NULL DEFAULT '',archive TEXT,sha256 TEXT,size INTEGER,details TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,created INTEGER NOT NULL,event TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,until INTEGER NOT NULL);
        ''')
        columns={r['name'] for r in c.execute('PRAGMA table_info(servers)')}
        if 'db_config' not in columns: c.execute("ALTER TABLE servers ADD COLUMN db_config TEXT NOT NULL DEFAULT ''")
        if 'version' not in columns: c.execute('ALTER TABLE servers ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
        c.execute('CREATE TABLE IF NOT EXISTS discoveries(server_id TEXT PRIMARY KEY REFERENCES servers(id),captured INTEGER NOT NULL,payload TEXT NOT NULL)')

def server_database_config(server):
    return json.loads(decrypt(server['db_config'])) if server['db_config'] else {'mysql_auto':True}

def site_database_config(site,server):
    config=json.loads(decrypt(site['db_config']))
    return server_database_config(server) if config.get('inherit_server') else config

def encrypt(value): return Fernet((DATA/'secrets.key').read_bytes()).encrypt(value.encode()).decode()
def decrypt(value): return Fernet((DATA/'secrets.key').read_bytes()).decrypt(value.encode()).decode()
def audit(event):
    with db() as c: c.execute('INSERT INTO audit(created,event) VALUES(?,?)',(now(),event))
def setting(key, default=None):
    with db() as c: row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return row['value'] if row else default

def setsetting(key,value):
    with db() as c: c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,value))

def enqueue(site_id,kind='backup',details=None):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM jobs WHERE site_id=? AND status IN ('queued','running')",(site_id,)).fetchone():
            raise ValueError('Une tâche est déjà en attente ou en cours pour cet élément.')
        id=secrets.token_hex(12)
        c.execute('INSERT INTO jobs(id,site_id,kind,status,created,details) VALUES(?,?,?,?,?,?)',(id,site_id,kind,'queued',now(),json.dumps(details or {})))
    return id

class SSH:
    def __init__(self,server): self.server=server
    def __enter__(self):
        if not self.server['host_keys']: raise ValueError('Confirmez d’abord l’empreinte SSH du serveur.')
        self.tmp=tempfile.TemporaryDirectory(dir=DATA/'ssh'); p=Path(self.tmp.name)
        (p/'key').write_text(decrypt(self.server['private_key'])+'\n'); (p/'key').chmod(0o600)
        (p/'known_hosts').write_text(self.server['host_keys'])
        self.args=['ssh','-F','/dev/null','-T','-i',str(p/'key'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(p/'known_hosts'),'-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-p',str(self.server['port']),self.server['username']+'@'+self.server['host']]
        return self
    def __exit__(self,*a): self.tmp.cleanup()
    def call(self,config,out=None,timeout=120):
        script='CONFIG='+repr(config)+'\n'+REMOTE
        result=subprocess.run(self.args+['python3 -'],input=script.encode(),stdout=out or subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout)
        if result.returncode: raise ValueError('Échec SSH : vérifiez la clé, l’empreinte, les permissions, Python 3, tar et les accès MySQL sur le VPS.')
        return json.loads(result.stdout) if out is None else None

def scan_host(server):
    r=subprocess.run(['ssh-keyscan','-T','8','-p',str(server['port']),server['host']],capture_output=True,text=True,timeout=35)
    lines=[line for line in r.stdout.splitlines() if line and not line.startswith('#')]
    if not lines: raise ValueError('Aucune clé hôte reçue. Vérifiez l’adresse, le port et le pare-feu.')
    keys='\n'.join(lines)+'\n'
    r=subprocess.run(['ssh-keygen','-lf','-'],input=keys,capture_output=True,text=True,timeout=5,check=True)
    return keys,r.stdout.strip()

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while chunk:=f.read(1024*1024): h.update(chunk)
    return h.hexdigest()

def recipient():
    return subprocess.check_output(['age-keygen','-y',str(DATA/'archive.key')],stderr=subprocess.DEVNULL,text=True).strip()

def checked_archive(job):
    if job['status']!='success' or not job['archive']: raise ValueError('Archive indisponible.')
    path=ARCHIVES/job['archive']
    if path.parent!=ARCHIVES or not path.is_file(): raise ValueError('Archive introuvable.')
    if sha(path)!=job['sha256']: raise ValueError('Empreinte SHA-256 différente : archive refusée.')
    return path

def unpack_archive(job,dest):
    source=checked_archive(job)
    result=subprocess.run(['age','-d','-i',str(DATA/'archive.key'),'-o',str(dest),str(source)],stderr=subprocess.DEVNULL)
    if result.returncode: raise ValueError('Déchiffrement impossible.')
    with zipfile.ZipFile(dest) as z:
        if z.testzip(): raise ValueError('Archive ZIP endommagée.')

def backup(job,site,server):
    names=json.loads(site['databases']); config=site_database_config(site,server)
    if site['scope']=='server' and not config.get('mysql_cnf') and not config.get('mysql_user') and not config.get('mysql_auto'):
        raise ValueError('Configurez explicitement l’accès MySQL pour la sauvegarde globale.')
    with tempfile.TemporaryDirectory(dir=DATA/'work') as tmp:
        work=Path(tmp)
        with SSH(server) as remote:
            with (work/'files.tar.gz').open('wb') as f: remote.call({'action':'files','path':site['path'],'scope':site['scope']},out=f,timeout=21600)
            targets=['all-databases'] if site['scope']=='server' else names
            for name in targets:
                with (work/(name+'.sql')).open('wb') as f:
                    remote.call(dict(config,action='database',database=name,all_databases=site['scope']=='server'),out=f,timeout=21600)
                if (work/(name+'.sql')).stat().st_size==0: raise ValueError('Export SQL vide.')
        manifest={'format':1,'site':site['name'],'path':site['path'],'scope':site['scope'],'created_utc':datetime.now(timezone.utc).isoformat(),'databases':targets,'files':{},'consistency':'Exports transactionnels MySQL; fichiers copiés en ligne, sans instantané applicatif. Prévoir un arrêt des écritures pour une cohérence stricte fichiers/SQL.'}
        for f in work.iterdir(): manifest['files'][f.name]={'sha256':sha(f),'size':f.stat().st_size}
        (work/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
        zip_path=work/'backup.zip'
        with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
            for f in work.iterdir():
                if f!=zip_path: z.write(f,f.name)
        with zipfile.ZipFile(zip_path) as z:
            if z.testzip(): raise ValueError('Échec de vérification ZIP.')
        name=job['id']+'.zip.age'; target=ARCHIVES/name; partial=ARCHIVES/(name+'.partial')
        try:
            subprocess.run(['age','-r',recipient(),'-o',str(partial),str(zip_path)],check=True,stderr=subprocess.DEVNULL)
            digest=sha(partial); size=partial.stat().st_size
            # Vérifie aussi le déchiffrement complet avant de valider la sauvegarde.
            verify=work/'verify.zip'
            subprocess.run(['age','-d','-i',str(DATA/'archive.key'),'-o',str(verify),str(partial)],check=True,stderr=subprocess.DEVNULL)
            if sha(verify)!=sha(zip_path): raise ValueError('Échec de vérification du chiffrement.')
            os.replace(partial,target)
            with db() as c: c.execute('UPDATE jobs SET archive=?,sha256=?,size=? WHERE id=?',(name,digest,size,job['id']))
        finally: partial.unlink(missing_ok=True)
    return 'Fichiers et exports SQL vérifiés; archive chiffrée et déchiffrement vérifié.'

def stage(job,site,server):
    details=json.loads(job['details'])
    with db() as c: source=c.execute('SELECT * FROM jobs WHERE id=? AND site_id=?',(details['backup_id'],site['id'])).fetchone()
    if not source: raise ValueError('Sauvegarde introuvable.')
    with tempfile.TemporaryDirectory(dir=DATA/'work') as tmp:
        path=Path(tmp)/'restore.zip'; unpack_archive(source,path)
        destination=details['destination']
        # Le script ne contient que la destination validée, jamais les identifiants.
        script='''import os,sys,zipfile,tarfile,json,shutil
from pathlib import Path
p=Path(DEST)
if p.exists() or not p.parent.is_dir(): raise RuntimeError('Destination déjà existante ou parent absent')
p.mkdir(mode=0o700)
with (p/'backup.zip').open('xb') as f: shutil.copyfileobj(sys.stdin.buffer,f)
with zipfile.ZipFile(p/'backup.zip') as z:
 for info in z.infolist():
  if '/' in info.filename or '\\\\' in info.filename or info.filename in ['.','..']: raise RuntimeError('Chemin ZIP refusé')
 z.extractall(p)
with tarfile.open(p/'files.tar.gz') as t:
 t.extractall(p/'files',filter='data')
(p/'backup.zip').unlink()
'''
        with SSH(server) as remote, path.open('rb') as f:
            r=subprocess.run(remote.args+['python3 -c '+shlex.quote('DEST='+repr(destination)+'\n'+script)],stdin=f,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=21600)
            if r.returncode: raise ValueError('Restauration isolée échouée. Vérifiez Python >= 3.12, le parent et les permissions. Un dossier partiel peut subsister.')
    return 'Fichiers extraits dans '+destination+'/files ; exports SQL disponibles à côté. Aucun import MySQL et aucun remplacement du site actif.'

def retention(site):
    with db() as c:
        rows=c.execute("SELECT * FROM jobs WHERE site_id=? AND kind='backup' AND status='success' AND archive IS NOT NULL ORDER BY created DESC",(site['id'],)).fetchall()
        for row in rows[site['retention']:]:
            (ARCHIVES/row['archive']).unlink(missing_ok=True)
            c.execute("UPDATE jobs SET archive=NULL,message=message || ' Archive expirée selon la rétention.' WHERE id=?",(row['id'],))

def worker():
    with db() as c:
        c.execute("UPDATE jobs SET status='failed',finished=?,message='Service redémarré pendant la tâche. Aucun succès présumé; vérifier les éventuels fichiers partiels.' WHERE status='running'",(now(),))
    while True:
        try:
            with db() as c:
                due=c.execute('SELECT * FROM sites WHERE enabled=1 AND next_run<=?',(now(),)).fetchall()
                for site in due:
                    busy=c.execute("SELECT 1 FROM jobs WHERE site_id=? AND status IN ('queued','running')",(site['id'],)).fetchone()
                    if not busy:
                        c.execute('INSERT INTO jobs(id,site_id,kind,status,created) VALUES(?,?,?,?,?)',(secrets.token_hex(12),site['id'],'backup','queued',now()))
                    c.execute('UPDATE sites SET next_run=? WHERE id=?',(now()+site['interval_hours']*3600,site['id']))
                job=c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if job:
                    c.execute("UPDATE jobs SET status='running',started=? WHERE id=?",(now(),job['id']))
                    site=c.execute('SELECT * FROM sites WHERE id=?',(job['site_id'],)).fetchone()
                    server=c.execute('SELECT * FROM servers WHERE id=?',(site['server_id'],)).fetchone()
            if job:
                try:
                    message=backup(job,site,server) if job['kind']=='backup' else stage(job,site,server)
                    with db() as c: c.execute("UPDATE jobs SET status='success',finished=?,message=? WHERE id=?",(now(),message,job['id']))
                    audit('Tâche '+job['id']+' terminée')
                    if job['kind']=='backup': retention(site)
                except Exception as e:
                    # Seules les erreurs applicatives sont exposées; jamais stderr ou secrets.
                    message=str(e) if isinstance(e,ValueError) else 'Échec technique de la tâche; archive non validée. Vérifier espace, droits et accès.'
                    with db() as c: c.execute("UPDATE jobs SET status='failed',finished=?,message=? WHERE id=?",(now(),message,job['id']))
                    audit('Échec tâche '+job['id'])
            else: time.sleep(3)
        except Exception:
            time.sleep(5)
