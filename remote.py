"""Script envoyé sur stdin SSH; aucune installation persistante sur le VPS."""
import os, re, json, subprocess, tempfile, shutil, sys
from pathlib import Path

def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def credentials(c):
    if c.get('mysql_auto'):
        if os.getuid()!=0: raise RuntimeError('Le mode MySQL automatique nécessite root')
        if Path('/root/.my.cnf').is_file(): return credentials({'mysql_cnf':'/root/.my.cnf'})
        return None,False
    if c.get('mysql_cnf'):
        p = Path(c['mysql_cnf'])
        if not p.is_file() or p.stat().st_mode & 0o077:
            raise RuntimeError('Fichier de connexion MySQL introuvable')
        return str(p), False
    if c.get('mysql_user'):
        fd, name = tempfile.mkstemp(prefix='backupcenter-db-')
        os.fchmod(fd, 0o600)
        def quote(v):
            return '"' + str(v).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r','\\r') + '"'
        with os.fdopen(fd, 'w') as f:
            f.write('[client]\nuser=' + quote(c['mysql_user']) + '\npassword=' + quote(c.get('mysql_password','')) + '\nhost=' + quote(c.get('mysql_host') or 'localhost') + '\n')
        return name, True
    return None, False

def database_candidates(root):
    names=set()
    files=['.env','public/.env','wp-config.php','public/wp-config.php','htdocs/.env','htdocs/wp-config.php',
           'config.php','config.accounts.php','database.php','db.php','config/database.php','config/db.php',
           'config/local.php','includes/config.php','includes/db.php','app/Config/Database.php']
    valid=r'[A-Za-z0-9_][A-Za-z0-9_$-]{0,63}'
    # Conserver les chaînes PHP littérales et retirer uniquement les commentaires.
    tokens=re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|/\*[\s\S]*?\*/|//[^\n]*|\#[^\n]*")
    for relative in files:
        p=Path(root)/relative
        try:
            if not p.is_file() or p.stat().st_size>1024*1024: continue
            body=p.read_text(errors='replace')
        except OSError: continue
        if p.suffix=='.php':
            body=tokens.sub(lambda m:m.group(0) if m.group(0).startswith(("'",'"')) else ' ',body)
            patterns=[
                r'''(?i)define\s*\(\s*['"](?:DB_NAME|DB_DATABASE|DATABASE_NAME)['"]\s*,\s*['"]('''+valid+r''')['"]''',
                r'''(?i)['"](?:database|dbname|db_name|DB_DATABASE|DB_NAME)['"]\s*=>\s*['"]('''+valid+r''')['"]''',
                r'''(?i)(?:\$(?:database|dbname|db_name|DB_DATABASE|DB_NAME)|\b(?:public|protected|private)\s+\$database)\s*=\s*['"]('''+valid+r''')['"]''',
                r'''(?i)['"]mysql:[^'"\r\n]*?\bdbname\s*=\s*('''+valid+r''')(?=[;'"\s])'''
            ]
            for pattern in patterns: names.update(re.findall(pattern,body))
        else:
            pattern=r'(?mi)^\s*(?:database\.default\.database|DB_DATABASE|DB_NAME)\s*=\s*[\'\"]?('+valid+r')(?=[\'\"\s#]|$)'
            names.update(re.findall(pattern,body))
    return sorted(names)

def discover_sites(home=Path('/home'),vhosts=Path('/etc/nginx/sites-enabled')):
    # Lecture seule : ne charge ni le code PHP ni les secrets dans les résultats.
    sites={}; warnings=[]
    def add(path,domain=None,source='dossiers CloudPanel'):
        try:
            path=Path(path).resolve()
            if not path.is_dir(): return
            parts=path.parts; homeparts=home.resolve().parts
            # /home/user/htdocs/domain/public -> sauvegarder le projet entier.
            offset=len(homeparts)
            if parts[:offset]==homeparts and len(parts)>=offset+3 and parts[offset+1]=='htdocs':
                path=Path(*parts[:offset+3])
            if path==Path('/') or path==home.resolve(): return
            item=sites.setdefault(str(path),{'name':domain or path.name,'path':str(path),'domains':[],'sources':[]})
            if domain and domain not in item['domains']: item['domains'].append(domain)
            if source not in item['sources']: item['sources'].append(source)
        except OSError: return
    try:
        for user in sorted(home.iterdir()):
            if user.name.startswith('.'): continue
            folder=user/'htdocs'
            try:
                if folder.is_dir():
                    for site in sorted(folder.iterdir()):
                        if not site.name.startswith('.'): add(site)
            except PermissionError: warnings.append('Dossier inaccessible : '+str(folder))
    except OSError: warnings.append('Arborescence /home inaccessible ou absente.')
    try:
        for file in sorted(vhosts.iterdir()):
            try:
                if not file.is_file() or file.stat().st_size>2*1024*1024: continue
                body=re.sub(r'(?m)#.*$','',file.read_text(errors='replace'))
                domains=[]
                for line in re.findall(r'\bserver_name\s+([^;]+);',body):
                    domains.extend(x for x in line.split() if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9-]+',x))
                for root in re.findall(r'\broot\s+([^;]+);',body):
                    root=root.strip().strip('"\'')
                    if not root.startswith('/') or '$' in root: continue
                    if domains:
                        for domain in domains: add(root,domain,'vhost NGINX')
                    else: add(root,source='vhost NGINX')
            except OSError: warnings.append('Vhost inaccessible : '+file.name)
    except OSError: warnings.append('Vhosts NGINX inaccessibles; détection limitée aux dossiers.')
    for item in sites.values():
        item['domains']=sorted(set(item['domains']))
        item['sources']=sorted(item['sources'])
        # Préférer le nom du dossier CloudPanel au premier alias www.
        item['name']=Path(item['path']).name if Path(item['path']).name in item['domains'] else (next((d for d in item['domains'] if not d.startswith('www.')),None) or item['name'])
        item['databases']=database_candidates(item['path'])
    return sorted(sites.values(),key=lambda x:x['name']),warnings

def database_inventory(c):
    cnf,cleanup=credentials(c)
    try:
        exe=shutil.which('mysql') or shutil.which('mariadb')
        if not exe: raise RuntimeError('Client MySQL absent')
        args=[exe]
        if cnf: args.append('--defaults-extra-file='+cnf)
        result=run(args+['--connect-timeout=10','--batch','--skip-column-names','-e','SHOW DATABASES'],capture_output=True,text=True,timeout=30)
        return [x for x in result.stdout.splitlines() if x not in ['information_schema','performance_schema','mysql','sys']]
    finally:
        if cleanup: os.unlink(cnf)

def main(c):
    action = c['action']
    if action == 'test':
        print(json.dumps({'hostname':os.uname().nodename,'python':sys.version.split()[0], 'tar':bool(shutil.which('tar')), 'mysql': bool(shutil.which('mysqldump') or shutil.which('mariadb-dump')), 'uid':os.getuid()}))
        return
    if action == 'inventory':
        sites,warnings=discover_sites()
        try: mysql={'ok':True,'databases':database_inventory(c),'message':'Accès MySQL vérifié'}
        except Exception: mysql={'ok':False,'databases':[],'message':'Accès MySQL non vérifié. Configurez un fichier MySQL distant protégé ou un compte MySQL dans les réglages du serveur.'}
        print(json.dumps({'sites':sites,'warnings':warnings,'mysql':mysql}))
        return
    if action == 'discover':
        root=Path(c['path'])
        if not root.is_dir(): raise RuntimeError('Dossier du site introuvable')
        names=database_candidates(root)
        accessible=database_inventory(c) if any(c.get(k) for k in ['mysql_cnf','mysql_user','mysql_auto']) else []
        print(json.dumps({'candidates':names,'accessible':accessible,'note':'Les bases accessibles ne sont pas nécessairement toutes associées à ce site. Confirmez la sélection.'}))
        return
    if action == 'files':
        path = c['path']
        if not os.path.isdir(path): raise RuntimeError('Dossier source introuvable')
        args=['tar','--acls','--xattrs','--numeric-owner','-czf','-']
        if c.get('scope') == 'server':
            if os.getuid()!=0: raise RuntimeError('La sauvegarde système nécessite un accès administrateur dédié')
            args += ['--one-file-system','--exclude=./proc','--exclude=./sys','--exclude=./dev','--exclude=./run','--exclude=./tmp','--exclude=./mnt','--exclude=./media','--exclude=./var/lib/mysql','--exclude=./var/lib/docker','--exclude=./swapfile']
        args += ['-C',path,'.']
        run(args, stdout=sys.stdout.buffer)
        return
    if action == 'database':
        exe=shutil.which('mysqldump') or shutil.which('mariadb-dump')
        if not exe: raise RuntimeError('mysqldump/mariadb-dump absent')
        cnf, cleanup=credentials(c)
        try:
            args=[exe]
            if cnf: args += ['--defaults-extra-file='+cnf]
            args += ['--single-transaction','--quick','--routines','--events','--triggers','--hex-blob']
            args += ['--all-databases'] if c.get('all_databases') else ['--databases', c['database']]
            run(args,stdout=sys.stdout.buffer)
        finally:
            if cleanup: os.unlink(cnf)
        return
    if action == 'stage':
        # Restauration isolée exclusivement. Ne touche jamais au site ni à MySQL actifs.
        dest=Path(c['destination'])
        if dest.exists(): raise RuntimeError('La destination existe déjà; choisissez un nouveau dossier')
        if not dest.parent.is_dir(): raise RuntimeError('Le dossier parent doit déjà exister')
        os.mkdir(dest,0o700)
        try:
            with open(dest/'backup.zip','xb') as f: shutil.copyfileobj(sys.stdin.buffer,f)
        except Exception:
            raise RuntimeError('Transfert de restauration incomplet; vérifier le dossier isolé')
        print(json.dumps({'destination':str(dest),'status':'archive transférée; extraction et import SQL non exécutés'}))
        return
    raise RuntimeError('Action inconnue')

if __name__=='__main__':
    try: main(CONFIG)
    except Exception:
        # Jamais de sortie de commande pouvant contenir un secret MySQL.
        print('Opération distante refusée ou échouée. Vérifiez les droits, chemins et outils requis.',file=sys.stderr)
        sys.exit(1)
