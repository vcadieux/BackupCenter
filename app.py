import os, re, json, secrets, hmac, time, base64, hashlib, struct, tempfile, shutil, threading
from pathlib import Path
from datetime import timedelta
from flask import Flask, request, session, jsonify, send_from_directory, send_file, Response, abort
from werkzeug.security import generate_password_hash, check_password_hash
import engine as E
from i18n import translate

E.init()
app=Flask(__name__,static_folder='public',static_url_path='/static')
app.secret_key=(E.DATA/'session.key').read_text()
app.config.update(SESSION_COOKIE_NAME='backupcenter',SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SECURE=os.environ.get('BC_SECURE_COOKIE','1')=='1',SESSION_COOKIE_SAMESITE='Strict',PERMANENT_SESSION_LIFETIME=timedelta(days=30),SESSION_REFRESH_EACH_REQUEST=True,MAX_CONTENT_LENGTH=128*1024)

def fail(message,status=400): return jsonify(error=translate(message)),status

def totp(secret,code):
    if not re.fullmatch(r'[0-9]{6}',str(code)): return False
    key=base64.b32decode(secret+'='*((8-len(secret)%8)%8))
    for offset in [-1,0,1]:
        raw=hmac.new(key,struct.pack('>Q',int(time.time()//30)+offset),hashlib.sha1).digest(); i=raw[-1]&15
        value=str((struct.unpack('>I',raw[i:i+4])[0]&0x7fffffff)%1000000).zfill(6)
        if hmac.compare_digest(value,str(code)): return True
    return False

def limited():
    # Limite globale persistante : ne dépend pas d'un en-tête client falsifiable.
    with E.db() as c:
        row=c.execute("SELECT * FROM attempts WHERE key='login'").fetchone()
        return bool(row and row['count']>=10 and row['until']>E.now())

def attempt(success):
    with E.db() as c:
        if success: c.execute("DELETE FROM attempts WHERE key='login'")
        else:
            row=c.execute("SELECT * FROM attempts WHERE key='login'").fetchone()
            count=row['count']+1 if row and row['until']>E.now() else 1
            c.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',('login',count,E.now()+900))

@app.before_request
def protect():
    if request.host.split(':')[0] not in [os.environ.get('BC_HOST','backup.example.com'),'localhost','127.0.0.1']:
        return fail('Hôte refusé.',400)
    if 'csrf' not in session: session['csrf']=secrets.token_hex(32)
    if request.method not in ['GET','HEAD','OPTIONS']:
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token','').encode(),session['csrf'].encode()): return fail('Session expirée; actualisez la page.',403)
        origin=request.headers.get('Origin')
        if origin and origin!=('https://'+os.environ.get('BC_HOST','backup.example.com')) and not (app.testing or os.environ.get('BC_SECURE_COOKIE')=='0'):
            return fail('Origine refusée.',403)
    if session.get('auth') and session.get('auth_generation','0')!=E.setting('auth_generation','0'):
        session.clear(); session['csrf']=secrets.token_hex(32)
    if request.path.startswith('/api/') and request.path not in ['/api/state','/api/login','/api/setup']:
        if not session.get('auth'): return fail('Connexion requise.',401)

@app.after_request
def headers(r):
    r.headers['Cache-Control']='no-store'
    r.headers['X-Content-Type-Options']='nosniff'
    r.headers['X-Frame-Options']='DENY'
    r.headers['Referrer-Policy']='no-referrer'
    r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return r

@app.errorhandler(400)
def bad(e): return fail('Requête invalide.',400)
@app.errorhandler(413)
def large(e): return fail('Requête trop volumineuse.',413)
@app.errorhandler(500)
def error(e): return fail('Erreur interne. Aucun secret n’est affiché.',500)

def body(): return request.get_json(silent=True) or {}
def text(d,k,limit=200,required=True):
    v=str(d.get(k,'')).strip()
    if len(v)>limit or (required and not v) or '\x00' in v: raise ValueError('Champ invalide : '+k)
    return v

def path_valid(value,allow_root=False):
    if not value.startswith('/') or '\n' in value or '\r' in value or '..' in Path(value).parts or (value=='/' and not allow_root): raise ValueError('Chemin absolu requis, sans traversée de dossiers.')
    return value.rstrip('/') or '/'

def getrow(table,id):
    if table not in ['servers','sites','jobs']: abort(404)
    with E.db() as c: row=c.execute('SELECT * FROM '+table+' WHERE id=?',(id,)).fetchone()
    if not row: abort(404)
    return row

@app.get('/')
def index(): return send_from_directory('public','index.html')

@app.get('/api/state')
def state():
    out={'csrf':session['csrf'],'setup':not bool(E.setting('password')),'authenticated':bool(session.get('auth'))}
    if session.get('auth'):
        with E.db() as c:
            servers=[dict(x) for x in c.execute('SELECT id,name,host,port,username,fingerprints,created,version FROM servers ORDER BY created')]
            sites=[dict(x) for x in c.execute('SELECT id,server_id,name,path,scope,databases,interval_hours,retention,next_run,enabled,version,backup_time FROM sites ORDER BY name')]
            jobs=[dict(x) for x in c.execute('SELECT jobs.*,sites.name AS site_name FROM jobs LEFT JOIN sites ON sites.id=jobs.site_id ORDER BY created DESC LIMIT 100')]
            archive_summary=dict(c.execute("SELECT count(*) AS count,coalesce(sum(size),0) AS bytes FROM jobs WHERE kind='backup' AND status='success' AND archive IS NOT NULL").fetchone())
            audits=[dict(x) for x in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 30')]
        for j in jobs:
            j['details']=json.loads(j['details'])
            j['message']=translate(j['message'])
        for event in audits: event['event']=translate(event['event'])
        for s in sites:
            s['databases']=json.loads(s['databases'])
            s['use_server_db']=bool(json.loads(E.decrypt(getrow('sites',s['id'])['db_config'])).get('inherit_server'))
        for server in servers:
            config=E.server_database_config(getrow('servers',server['id']))
            server['mysql_mode']='auto' if config.get('mysql_auto') else 'custom'
            server['mysql_cnf']=config.get('mysql_cnf','')
            server['mysql_user']=config.get('mysql_user','')
            server['mysql_host']=config.get('mysql_host','')
        usage=shutil.disk_usage(E.ARCHIVES)
        out.update(schedule_timezone=os.environ.get('TZ','UTC'),servers=servers,sites=sites,jobs=jobs,audit=audits,username=E.setting('username'),totp=bool(E.setting('totp')),storage={'free':usage.free,'total':usage.total,'directory':os.environ.get('BC_ARCHIVE_DISPLAY_PATH','/mnt/user/backups/backupcenter')},archive_summary=archive_summary,archive_format=translate('ZIP chiffré age'))
    return jsonify(out)

@app.get('/api/archives')
def archives():
    try:
        page=int(request.args.get('page','1'))
        if not 1<=page<=100000: raise ValueError()
    except ValueError: return fail('Page invalide.')
    site_id=request.args.get('site_id','').strip()
    if site_id: getrow('sites',site_id)
    clause="jobs.kind='backup' AND jobs.status='success' AND jobs.archive IS NOT NULL"
    params=[]
    if site_id: clause+=' AND jobs.site_id=?'; params.append(site_id)
    with E.db() as c:
        total=c.execute('SELECT count(*) FROM jobs WHERE '+clause,params).fetchone()[0]
        rows=c.execute('SELECT jobs.id,jobs.site_id,jobs.created,jobs.finished,jobs.size,jobs.archive,jobs.sha256,sites.name AS site_name,servers.name AS server_name FROM jobs LEFT JOIN sites ON sites.id=jobs.site_id LEFT JOIN servers ON servers.id=sites.server_id WHERE '+clause+' ORDER BY jobs.created DESC,jobs.id DESC LIMIT 25 OFFSET ?',params+[(page-1)*25]).fetchall()
    directory=os.environ.get('BC_ARCHIVE_DISPLAY_PATH','/mnt/user/backups/backupcenter')
    items=[]
    for row in rows:
        item=dict(row); name=item.pop('archive')
        safe=bool(re.fullmatch(r'[A-Za-z0-9_-]+\.zip\.age',name))
        item.update(filename=name if safe else translate('Nom de fichier invalide'),path=str(Path(directory)/name) if safe else '',available=safe and (E.ARCHIVES/name).is_file())
        items.append(item)
    return jsonify(items=items,page=page,total=total,pages=max(1,(total+24)//25),directory=directory)

@app.post('/api/setup')
def setup():
    if E.setting('password'): return fail('Installation déjà terminée.',409)
    if limited(): return fail('Trop de tentatives. Réessayez dans 15 minutes.',429)
    d=body()
    if not hmac.compare_digest(str(d.get('code','')).encode(),(E.DATA/'setup-code.txt').read_text().strip().encode()):
        attempt(False); return fail('Code d’installation incorrect.',403)
    try:
        username=text(d,'username',80); password=str(d.get('password',''))
        if len(password)>256: raise ValueError('Mot de passe trop long.')
        if len(password)<14: raise ValueError('Choisissez un mot de passe d’au moins 14 caractères.')
    except ValueError as e: return fail(str(e))
    with E.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM settings WHERE key='password'").fetchone(): return fail('Installation déjà terminée.',409)
        c.executemany('INSERT INTO settings VALUES(?,?)',[('username',username),('password',generate_password_hash(password))])
    (E.DATA/'setup-code.txt').unlink(missing_ok=True)
    session.clear(); session.update(auth=True,csrf=secrets.token_hex(32),auth_generation=E.setting('auth_generation','0')); session.permanent=True
    attempt(True); E.audit('Compte administrateur créé')
    return jsonify(ok=True)

@app.post('/api/login')
def login():
    if limited(): return fail('Trop de tentatives. Réessayez dans 15 minutes.',429)
    d=body(); password=E.setting('password'); second=E.setting('totp')
    valid=password and hmac.compare_digest(str(d.get('username','')).encode(),E.setting('username','').encode()) and check_password_hash(password,str(d.get('password','')))
    if second: valid=valid and totp(E.decrypt(second),d.get('totp',''))
    attempt(bool(valid))
    if not valid: return fail('Identifiants ou code de sécurité incorrects.',403)
    session.clear(); session.update(auth=True,csrf=secrets.token_hex(32),auth_generation=E.setting('auth_generation','0')); session.permanent=True
    E.audit('Connexion administrateur'); return jsonify(ok=True)

@app.post('/api/logout')
def logout(): session.clear(); return jsonify(ok=True)

def reauth(d):
    if limited(): return False
    valid=check_password_hash(E.setting('password'),str(d.get('password','')))
    secret=E.setting('totp')
    if secret: valid=valid and totp(E.decrypt(secret),d.get('totp',''))
    attempt(valid); return valid

@app.post('/api/security/totp-start')
def totp_start():
    if not reauth(body()): return fail('Authentification incorrecte.',403)
    secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip('='); session['pending_totp']=E.encrypt(secret)
    return jsonify(secret=secret)

@app.post('/api/security/totp-confirm')
def totp_confirm():
    if limited(): return fail('Trop de tentatives. Réessayez dans 15 minutes.',429)
    pending=session.get('pending_totp')
    valid=bool(pending and totp(E.decrypt(pending),body().get('totp','')))
    attempt(valid)
    if not valid: return fail('Code incorrect.')
    generation=secrets.token_hex(16); E.setsetting('auth_generation',generation); session['auth_generation']=generation
    E.setsetting('totp',pending); session.pop('pending_totp',None); E.audit('Double authentification activée'); return jsonify(ok=True)

@app.post('/api/security/password')
def password_change():
    d=body()
    if not reauth(d): return fail('Authentification incorrecte.',403)
    password=str(d.get('new_password',''))
    if len(password)<14 or len(password)>256: return fail('Mot de passe : 14 à 256 caractères.')
    E.setsetting('password',generate_password_hash(password))
    generation=secrets.token_hex(16); E.setsetting('auth_generation',generation); session['auth_generation']=generation
    E.audit('Mot de passe modifié; autres sessions révoquées'); return jsonify(ok=True)

@app.post('/api/security/recovery')
def recovery():
    if not reauth(body()): return fail('Authentification incorrecte.',403)
    E.audit('Clé de récupération des archives téléchargée')
    return send_file(E.DATA/'archive.key',as_attachment=True,download_name='BackupCenter-recovery.key',mimetype='text/plain')

def server_values(d,old=None):
    name=text(d,'name',100); host=text(d,'host',253); username=text(d,'username',64); port=int(d.get('port',22))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*',host) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*',username) or not 1<=port<=65535: raise ValueError('Adresse, utilisateur ou port invalide.')
    key=str(d.get('private_key','')).strip().replace('\r\n','\n').lstrip('\ufeff')
    encrypted_key=old['private_key'] if old else None
    if key:
        if key.startswith(('ssh-','ecdsa-')): raise ValueError('Vous avez collé la clé publique. Collez le contenu du fichier privé, de BEGIN OPENSSH PRIVATE KEY à END OPENSSH PRIVATE KEY.')
        with tempfile.TemporaryDirectory(dir=E.DATA/'ssh') as tmp:
            p=Path(tmp)/'key'; p.write_text(key+'\n'); p.chmod(0o600)
            import subprocess
            r=subprocess.run(['ssh-keygen','-y','-P','','-f',str(p)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
            if r.returncode: raise ValueError('Clé privée SSH invalide ou chiffrée. Collez son contenu complet, pas son chemin ni la clé publique.')
        encrypted_key=E.encrypt(key)
    if not encrypted_key: raise ValueError('Clé privée SSH requise.')
    if d.get('mysql_mode','auto')=='auto': config={'mysql_auto':True}
    elif d.get('mysql_mode')=='custom':
        previous=E.server_database_config(old) if old else {}
        config={k:str(d.get(k,'')).strip() for k in ['mysql_cnf','mysql_user','mysql_host']}
        config['mysql_password']=str(d.get('mysql_password','')) or previous.get('mysql_password','')
        if config['mysql_cnf']: path_valid(config['mysql_cnf'])
        if not config['mysql_cnf'] and not config['mysql_user']: raise ValueError('Indiquez un fichier MySQL distant ou un utilisateur MySQL.')
    else: raise ValueError('Mode MySQL invalide.')
    return name,host,port,username,encrypted_key,E.encrypt(json.dumps(config))

@app.post('/api/ssh/keypair')
def generate_ssh_keypair():
    username=str(body().get('username','root')).strip()
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]{0,63}',username): return fail('Utilisateur SSH invalide.')
    with E.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM attempts WHERE key='keygen'").fetchone()
        count=row['count'] if row and row['until']>E.now() else 0
        if count>=6: return fail('Trop de générations de clés. Réessayez dans une minute.',429)
        c.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)',('keygen',count+1,E.now()+60))
    try:
        import subprocess
        with tempfile.TemporaryDirectory(dir=E.DATA/'ssh') as tmp:
            path=Path(tmp)/'key'
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','backupcenter-'+secrets.token_hex(6),'-f',str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            private_key=path.read_text();public_key=path.with_suffix('.pub').read_text().strip()
        E.audit('Paire de clés SSH générée')
        return jsonify(private_key=private_key,public_key=public_key,username=username)
    except Exception: return fail('Génération de clé impossible. Réessayez.')

@app.post('/api/servers')
def add_server():
    try:
        values=server_values(body()); id=secrets.token_hex(12)
        with E.db() as c: c.execute('INSERT INTO servers(id,name,host,port,username,private_key,db_config,created) VALUES(?,?,?,?,?,?,?,?)',(id,)+values+(E.now(),))
        E.audit('Serveur ajouté : '+values[0]); return jsonify(id=id)
    except (ValueError,TypeError) as e: return fail(str(e))

@app.post('/api/servers/<id>')
def update_server(id):
    d=body()
    try:
        old=getrow('servers',id); values=server_values(d,old)
        changed=(values[1],values[2])!=(old['host'],old['port'])
        with E.db() as c:
            r=c.execute('UPDATE servers SET name=?,host=?,port=?,username=?,private_key=?,db_config=?,host_keys=?,fingerprints=?,version=version+1 WHERE id=? AND version=?',values+('' if changed else old['host_keys'],'' if changed else old['fingerprints'],id,int(d.get('version',0))))
            if not r.rowcount: return fail('Serveur modifié ailleurs. Actualisez.',409)
            c.execute('DELETE FROM discoveries WHERE server_id=?',(id,))
        E.audit('Serveur modifié : '+values[0]); return jsonify(ok=True,reconfirm_host=changed)
    except (ValueError,TypeError) as e: return fail(str(e))

@app.post('/api/servers/<id>/inventory')
def inventory(id):
    server=getrow('servers',id)
    try:
        with E.SSH(server) as remote: result=remote.call(dict(E.server_database_config(server),action='inventory'),timeout=150)
        result['server_version']=server['version']
        with E.db() as c:
            if c.execute('SELECT version FROM servers WHERE id=?',(id,)).fetchone()['version']!=server['version']: return fail('Le serveur a changé pendant la détection. Relancez-la.',409)
            existing=c.execute("SELECT id,name,path FROM sites WHERE server_id=? AND scope='site'",(id,)).fetchall()
            for item in result['sites']:
                item['token']=secrets.token_hex(12)
                match=next((s for s in existing if s['path']==item['path'] or s['name']==item['name']),None)
                item['configured_id']=match['id'] if match else None
            c.execute('INSERT OR REPLACE INTO discoveries VALUES(?,?,?)',(id,E.now(),json.dumps(result)))
        E.audit('Sites détectés pour le serveur '+server['name'])
        for item in result['sites']:item['sources']=[translate(x) for x in item['sources']]
        result['mysql']['message']=translate(result['mysql']['message'])
        result['warnings']=[translate(x) for x in result['warnings']]
        return jsonify(result)
    except Exception as e: return fail(str(e) if isinstance(e,ValueError) else 'Détection impossible. Vérifiez SSH, Python 3 et les droits de lecture du VPS.')

@app.post('/api/servers/<id>/import')
def import_sites(id):
    getrow('servers',id); d=body()
    try:
        selected=d.get('sites',[])
        if not isinstance(selected,list) or not 1<=len(selected)<=100: raise ValueError('Sélectionnez entre 1 et 100 sites.')
        with E.db() as c: snapshot=c.execute('SELECT * FROM discoveries WHERE server_id=?',(id,)).fetchone()
        if not snapshot or snapshot['captured']<E.now()-3600: raise ValueError('Détection expirée. Détectez à nouveau les sites.')
        candidates={x['token']:x for x in json.loads(snapshot['payload'])['sites']}
        prepared=[]; seen=set()
        for chosen in selected:
            token=chosen.get('token') if isinstance(chosen,dict) else None
            if token not in candidates or token in seen: raise ValueError('Sélection invalide ou répétée. Relancez la détection.')
            seen.add(token); item=candidates[token]
            # Les chemins viennent du résultat serveur, jamais d'un chemin fourni par le navigateur.
            values=site_values({'server_id':id,'name':item['name'],'path':item['path'],'scope':'site','databases':chosen.get('databases',[]),'files_only':bool(chosen.get('files_only')),'use_server_db':True,'interval_hours':d.get('interval_hours',24),'backup_time':d.get('backup_time','02:00'),'retention':d.get('retention',14),'enabled':bool(d.get('enabled'))})
            prepared.append((secrets.token_hex(12),values))
        with E.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT version FROM servers WHERE id=?',(id,)).fetchone()['version']!=json.loads(snapshot['payload']).get('server_version'): raise ValueError('Le serveur a changé. Relancez la détection.')
            for source_id,values in prepared:
                if c.execute("SELECT 1 FROM sites WHERE server_id=? AND scope='site' AND (path=? OR name=?)",(id,values[2],values[1])).fetchone(): raise ValueError('Un des sites est déjà configuré. Aucun site ajouté; relancez la détection.')
                c.execute('INSERT INTO sites(id,server_id,name,path,scope,databases,db_config,interval_hours,retention,next_run,enabled,backup_time) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(source_id,)+values)
        E.audit(str(len(prepared))+' site(s) ajouté(s) depuis la détection du serveur '+id)
        return jsonify(ids=[i for i,_ in prepared])
    except (ValueError,TypeError) as e: return fail(str(e))

@app.post('/api/servers/<id>/scan')
def scan(id):
    try:
        server=getrow('servers',id)
        keys,fingerprints=E.scan_host(server)
        session['scan']={'id':id,'version':server['version'],'keys':keys,'fingerprints':fingerprints,'expires':E.now()+600}
        return jsonify(fingerprints=fingerprints)
    except Exception as e: return fail(str(e) if isinstance(e,ValueError) else 'Scan SSH impossible.')

@app.post('/api/servers/<id>/trust')
def trust(id):
    pending=session.get('scan',{})
    if pending.get('id')!=id or pending.get('expires',0)<E.now() or body().get('fingerprints')!=pending.get('fingerprints'): return fail('Relancez la lecture des empreintes.')
    with E.db() as c:
        r=c.execute('UPDATE servers SET host_keys=?,fingerprints=?,version=version+1 WHERE id=? AND version=?',(pending['keys'],pending['fingerprints'],id,pending.get('version',0)))
        if not r.rowcount: return fail('Le serveur a changé. Relancez la lecture des empreintes.',409)
        c.execute('DELETE FROM discoveries WHERE server_id=?',(id,))
    session.pop('scan',None); E.audit('Empreinte SSH confirmée pour '+id); return jsonify(ok=True)

@app.post('/api/servers/<id>/test')
def test_server(id):
    try:
        with E.SSH(getrow('servers',id)) as remote: result=remote.call({'action':'test'})
        return jsonify(result)
    except Exception as e: return fail(str(e) if isinstance(e,ValueError) else 'Test SSH impossible.')

def site_values(d):
    name=text(d,'name',100); server_id=text(d,'server_id'); getrow('servers',server_id)
    scope=d.get('scope','site')
    if scope not in ['site','server']: raise ValueError('Type de sauvegarde invalide.')
    path=path_valid(text(d,'path',1000),scope=='server')
    if scope=='server' and path!='/': raise ValueError('La sauvegarde globale utilise la racine /.')
    names=d.get('databases',[])
    if not isinstance(names,list) or len(names)>200 or any(not re.fullmatch(r'[A-Za-z0-9_$-]{1,64}',str(x)) for x in names): raise ValueError('Noms de bases invalides.')
    if scope=='site' and not names and not d.get('files_only'): raise ValueError('Sélectionnez les bases ou confirmez explicitement « Fichiers seulement ».')
    config={k:str(d.get(k,'')).strip() for k in ['mysql_cnf','mysql_user','mysql_host']}; config['mysql_password']=str(d.get('mysql_password',''))
    if config['mysql_cnf']: path_valid(config['mysql_cnf'])
    if d.get('use_server_db'): config={'inherit_server':True}
    if (names or scope=='server') and not config.get('inherit_server') and not config.get('mysql_cnf') and not config.get('mysql_user'): raise ValueError('Indiquez un fichier MySQL distant protégé ou un compte MySQL.')
    hours=int(d.get('interval_hours',24)); retention=int(d.get('retention',14))
    if not 1<=hours<=8760 or not 1<=retention<=365: raise ValueError('Fréquence ou rétention invalide.')
    clock=d.get('backup_time','02:00')
    return (server_id,name,path,scope,json.dumps(sorted(set(names))),E.encrypt(json.dumps(config)),hours,retention,E.next_schedule(hours,clock),int(bool(d.get('enabled'))),clock)

@app.post('/api/sites')
def add_site():
    try:
        values=site_values(body()); id=secrets.token_hex(12)
        with E.db() as c: c.execute('INSERT INTO sites(id,server_id,name,path,scope,databases,db_config,interval_hours,retention,next_run,enabled,backup_time) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(id,)+values)
        E.audit('Source ajoutée : '+values[1]); return jsonify(id=id)
    except (ValueError,TypeError) as e: return fail(str(e))

@app.post('/api/sites/<id>')
def update_site(id):
    d=body()
    try:
        # Les identifiants ne sont jamais renvoyés au navigateur; conserver si champs secrets vides.
        old=getrow('sites',id); config=json.loads(E.decrypt(old['db_config']))
        if not d.get('mysql_password') and not d.get('clear_db_credentials'):
            for k,v in config.items():
                if not d.get(k): d[k]=v
        d.setdefault('backup_time',old['backup_time'])
        values=site_values(d)
        if values[6]==old['interval_hours'] and values[10]==old['backup_time'] and values[9]==old['enabled']:
            values=values[:8]+(old['next_run'],)+values[9:]
        with E.db() as c:
            r=c.execute('UPDATE sites SET server_id=?,name=?,path=?,scope=?,databases=?,db_config=?,interval_hours=?,retention=?,next_run=?,enabled=?,backup_time=?,version=version+1 WHERE id=? AND version=?',values+(id,int(d.get('version',0))))
            if not r.rowcount: return fail('Configuration modifiée ailleurs. Actualisez.',409)
        E.audit('Source modifiée : '+values[1]); return jsonify(ok=True)
    except (ValueError,TypeError) as e: return fail(str(e))

@app.post('/api/discover')
def discover():
    d=body()
    try:
        config={k:str(d.get(k,'')) for k in ['mysql_cnf','mysql_user','mysql_password','mysql_host']}
        server=getrow('servers',text(d,'server_id'))
        if d.get('use_server_db'): config=E.server_database_config(server)
        config.update(action='discover',path=path_valid(text(d,'path',1000)))
        with E.SSH(server) as remote: result=remote.call(config)
        result['note']=translate(result['note'])
        return jsonify(result)
    except Exception as e: return fail(str(e) if isinstance(e,ValueError) else 'Détection impossible. Vérifiez les permissions.')

@app.post('/api/sites/<id>/backup')
def start_backup(id):
    getrow('sites',id)
    try:
        id=E.enqueue(id); E.audit('Sauvegarde demandée : '+id); return jsonify(id=id)
    except ValueError as e: return fail(str(e),409)

@app.post('/api/jobs/<id>/restore')
def restore(id):
    job=getrow('jobs',id); d=body(); site=getrow('sites',job['site_id'])
    try:
        E.checked_archive(job)
        if d.get('confirm')!=site['name']: raise ValueError('Recopiez le nom exact de la source pour confirmer.')
        dest=path_valid(text(d,'destination',1000))
        if dest in ['/etc','/home','/root','/var','/usr','/opt'] or dest==site['path'] or (site['scope']=='site' and dest.startswith(site['path'].rstrip('/')+'/')): raise ValueError('Choisissez un dossier isolé, hors du site actif.')
        id=E.enqueue(site['id'],'restore',{'backup_id':job['id'],'destination':dest})
        E.audit('Restauration isolée demandée : '+id); return jsonify(id=id)
    except ValueError as e: return fail(str(e))

@app.get('/api/jobs/<id>/download')
def download(id):
    job=getrow('jobs',id)
    try:
        source=E.checked_archive(job)
        if request.args.get('format')!='zip':
            E.audit('Archive chiffrée téléchargée : '+id)
            return send_file(source,as_attachment=True,download_name='BackupCenter-'+id+'.zip.age')
        tmp=tempfile.TemporaryDirectory(dir=E.DATA/'work'); path=Path(tmp.name)/'backup.zip'
        try: E.unpack_archive(job,path)
        except Exception:
            tmp.cleanup(); raise
        E.audit('Export ZIP déchiffré téléchargé : '+id)
        def stream():
            try:
                with path.open('rb') as f:
                    while chunk:=f.read(1024*1024): yield chunk
            finally: tmp.cleanup()
        return Response(stream(),mimetype='application/zip',headers={'Content-Disposition':'attachment; filename="BackupCenter-'+id+'.zip"'})
    except ValueError as e: return fail(str(e))

if __name__=='__main__':
    threading.Thread(target=E.worker,daemon=True).start()
    from waitress import serve
    socket_path=os.environ.get('BC_SOCKET')
    if socket_path:
        Path(socket_path).unlink(missing_ok=True)
        serve(app,unix_socket=socket_path,unix_socket_perms='660',threads=6,clear_untrusted_proxy_headers=True)
    else:
        serve(app,host='127.0.0.1',port=8099,threads=6,clear_untrusted_proxy_headers=True)
