import os, tempfile, unittest, json, io, tarfile, subprocess, secrets
from pathlib import Path
from unittest.mock import patch
work=tempfile.TemporaryDirectory()
os.environ.update(BC_DATA=work.name+'/data',BC_ARCHIVES=work.name+'/archives',BC_SECURE_COOKIE='0')
import app
import engine as E

class CenterTests(unittest.TestCase):
    def setUp(self):
        import terminals as T
        for obj in list(T.sessions.values()):obj.close('test-cleanup')
        T.sessions.clear()
        with E.db() as c:
            for table in ['terminal_sessions','maintenance','jobs','sites','discoveries','servers','settings','attempts','audit']: c.execute('DELETE FROM '+table)
        (E.DATA/'setup-code.txt').write_text('private-install-code')
        self.client=app.app.test_client();app.app.testing=True
        self.state=self.client.get('/api/state',headers={'Host':'localhost'}).json
    def post(self,url,data):
        state=self.client.get('/api/state',headers={'Host':'localhost'}).json
        return self.client.post('/api/'+url,json=data,headers={'Host':'localhost','X-CSRF-Token':state['csrf']})
    def setup_admin(self):
        self.assertEqual(self.post('setup',{'code':'private-install-code','username':'owner','password':'a sufficiently long password'}).status_code,200)
    def server(self):
        key=Path(work.name)/('key-'+secrets.token_hex(3)); subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
        r=self.post('servers',{'name':'Fixture','host':'fixture.example','port':22,'username':'backup','private_key':key.read_text()})
        self.assertEqual(r.status_code,200,r.json)
        return r.json['id']
    def site(self,server):
        return self.post('sites',{'server_id':server,'name':'fixture.example','path':'/srv/fixture','scope':'site','databases':[],'files_only':True,'retention':2,'interval_hours':24,'enabled':False})
    def test_schedule_calendar_and_dst(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        zone=ZoneInfo('America/Toronto')
        stamp=lambda value:int(datetime.fromisoformat(value).replace(tzinfo=zone).timestamp())
        local=lambda value:datetime.fromtimestamp(value,zone).isoformat()
        with patch.dict(os.environ,TZ='America/Toronto'):
            self.assertEqual(local(E.next_schedule(24,after=stamp('2026-10-20T01:00'))),'2026-10-20T02:00:00-04:00')
            self.assertEqual(local(E.next_schedule(24,after=stamp('2026-10-20T02:00'))),'2026-10-21T02:00:00-04:00')
            self.assertEqual(local(E.next_schedule(168,'04:30',after=stamp('2026-10-20T05:00'),previous=stamp('2026-10-20T04:30'))),'2026-10-27T04:30:00-04:00')
            self.assertEqual(local(E.next_schedule(720,after=stamp('2026-10-20T03:00'),previous=stamp('2026-10-20T02:00'))),'2026-11-19T02:00:00-05:00')
            self.assertEqual(local(E.next_schedule(24,after=stamp('2026-03-08T00:00'))),'2026-03-08T03:00:00-04:00')
            self.assertEqual(local(E.next_schedule(24,'01:30',after=stamp('2026-11-01T01:30'))),'2026-11-02T01:30:00-05:00')
            self.assertEqual(local(E.next_schedule(6,'20:00',after=stamp('2026-10-20T01:00'))),'2026-10-20T02:00:00-04:00')
            self.assertEqual(local(E.next_schedule(168,after=stamp('2026-11-10T12:00'),previous=stamp('2026-10-20T02:00'))),'2026-11-17T02:00:00-05:00')
        for clock in ['24:00','2:00','12:60','bad',None]:
            with self.assertRaises(ValueError):E.next_schedule(24,clock)

    def test_schedule_migration_preserves_sources_and_is_idempotent(self):
        self.setup_admin();server=self.server();self.site(server)
        with E.db() as c:
            original=dict(c.execute('SELECT * FROM sites').fetchone())
            c.execute('ALTER TABLE sites DROP COLUMN backup_time')
        E.init()
        with E.db() as c:migrated=dict(c.execute('SELECT * FROM sites').fetchone())
        for key in ['id','server_id','db_config','databases','enabled','retention']:
            self.assertEqual(original[key],migrated[key])
        self.assertEqual(migrated['backup_time'],'02:00')
        self.assertEqual(migrated['version'],original['version']+1)
        self.assertEqual(__import__('datetime').datetime.fromtimestamp(migrated['next_run'],E.schedule_zone()).strftime('%H:%M'),'02:00')
        E.init()
        with E.db() as c:self.assertEqual(migrated,dict(c.execute('SELECT * FROM sites').fetchone()))

    def test_schedule_api_and_preserve_due_time(self):
        self.setup_admin();server=self.server();response=self.site(server)
        self.assertEqual(response.status_code,200,response.json)
        site=self.client.get('/api/state').json['sites'][0]
        self.assertEqual(site['backup_time'],'02:00')
        payload=dict(site,files_only=True,backup_time='04:35')
        self.assertEqual(self.post('sites/'+site['id'],payload).status_code,200)
        updated=self.client.get('/api/state').json['sites'][0]
        self.assertEqual(updated['backup_time'],'04:35')
        with patch.object(E,'now',return_value=E.now()+3600):
            self.assertEqual(self.post('sites/'+site['id'],dict(updated,files_only=True,name='Renamed')).status_code,200)
        self.assertEqual(self.client.get('/api/state').json['sites'][0]['next_run'],updated['next_run'])
        self.assertEqual(self.post('sites',dict(payload,backup_time='25:00')).status_code,400)

    def test_extended_session(self):
     from datetime import timedelta
     from itsdangerous import TimestampSigner,SignatureExpired
     self.assertEqual(app.app.permanent_session_lifetime,timedelta(days=30))
     self.assertTrue(app.app.config['SESSION_REFRESH_EACH_REQUEST'])
     self.setup_admin()
     response=self.client.get('/api/state');cookie=response.headers['Set-Cookie']
     self.assertIn('Expires=',cookie);self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
     token=self.client.get_cookie('backupcenter').value
     serializer=app.app.session_interface.get_signing_serializer(app.app)
     issued=TimestampSigner('fixture').get_timestamp()
     with patch.object(TimestampSigner,'get_timestamp',return_value=issued+29*86400):self.assertTrue(serializer.loads(token,max_age=30*86400)['auth'])
     with patch.object(TimestampSigner,'get_timestamp',return_value=issued+31*86400):
      with self.assertRaises(SignatureExpired):serializer.loads(token,max_age=30*86400)
     self.post('logout',{});self.assertFalse(self.client.get('/api/state').json['authenticated'])

    def maintenance_server(self):
        server=self.server()
        with E.db() as c:c.execute("UPDATE servers SET username='root',host_keys='fixture pinned' WHERE id=?",(server,))
        return server

    def test_maintenance_auth_confirmation_and_backup_exclusion(self):
        import maintenance as M
        self.setup_admin();server=self.maintenance_server();self.site(server)
        data=dict(mode='check',version=1)
        self.assertEqual(self.client.post('/api/servers/'+server+'/maintenance',json=data).status_code,403)
        self.assertEqual(self.post('servers/'+server+'/maintenance',dict(data,version=9)).status_code,409)
        response=self.post('servers/'+server+'/maintenance',data);self.assertEqual(response.status_code,200,response.json)
        with E.db() as c:site=c.execute('SELECT * FROM sites').fetchone()
        with self.assertRaises(ValueError):E.enqueue(site['id'])
        self.assertEqual(self.post('servers/'+server+'/maintenance',data).status_code,400)
        with patch.object(E.SSH,'__exit__',return_value=False),patch.object(E.SSH,'__enter__',return_value=unittest.mock.Mock(call=lambda *a,**k:dict(status='success',packages=['openssl'],digest='a'*64,reboot=False))):M.tick()
        preview=response.json['id']
        upgrade=dict(data,mode='upgrade',preview=preview,confirm='Yes')
        self.assertEqual(self.post('servers/'+server+'/maintenance',upgrade).status_code,403)
        upgrade.update(password='a sufficiently long password',confirm='Fixture')
        self.assertEqual(self.post('servers/'+server+'/maintenance',upgrade).status_code,400)
        upgrade['confirm']=' Oui '
        response=self.post('servers/'+server+'/maintenance',upgrade);self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.post('servers/'+server+'/maintenance',upgrade).status_code,400)
        history=self.client.get('/api/servers/'+server+'/maintenance').json
        self.assertNotIn('password',json.dumps(history))
        self.assertNotIn('private_key',json.dumps(history))

    def test_maintenance_check_without_totp_but_upgrade_requires_it(self):
        self.setup_admin();server=self.maintenance_server()
        secret='JBSWY3DPEHPK3PXP';E.setsetting('totp',E.encrypt(secret))
        response=self.post('servers/'+server+'/maintenance',dict(mode='check',version=1))
        self.assertEqual(response.status_code,200,response.json)
        with E.db() as c:c.execute("UPDATE maintenance SET status='success',finished=?,result=? WHERE id=?",(E.now(),json.dumps(dict(packages=['openssl'],digest='a'*64)),response.json['id']))
        payload=dict(mode='upgrade',version=1,preview=response.json['id'],confirm='Yes',password='a sufficiently long password')
        self.assertEqual(self.post('servers/'+server+'/maintenance',payload).status_code,403)
        with patch.object(app,'totp',return_value=True):
            self.assertEqual(self.post('servers/'+server+'/maintenance',dict(payload,totp='fixture')).status_code,200)

    def test_maintenance_unknown_outcome_polls_without_relaunch(self):
        import maintenance as M
        self.setup_admin();server=self.maintenance_server()
        row=app.getrow('servers',server);id=M.create(row,'check')
        with patch.object(E.SSH,'__enter__',side_effect=OSError('fixture secret')):self.assertTrue(M.tick())
        with E.db() as c:self.assertEqual(c.execute('SELECT status FROM maintenance WHERE id=?',(id,)).fetchone()[0],'unknown')
        remote=unittest.mock.Mock();remote.call.return_value=dict(status='success',packages=[],digest='b'*64)
        with patch.object(E.SSH,'__exit__',return_value=False),patch.object(E.SSH,'__enter__',return_value=remote):M.tick()
        self.assertEqual(remote.call.call_args.args[0]['action'],'maintenance_status')
        with E.db() as c:self.assertFalse(M.busy(c))

    def test_maintenance_stale_preview_and_backup_blocks(self):
        import maintenance as M
        self.setup_admin();server=self.maintenance_server();row=app.getrow('servers',server)
        id=M.create(row,'check')
        with E.db() as c:c.execute("UPDATE maintenance SET status='success',finished=?,result=? WHERE id=?",(E.now()-901,json.dumps(dict(packages=['x'],digest='a'*64)),id))
        with self.assertRaises(ValueError):M.create(row,'upgrade',id)
        site=self.site(server).json['id'];E.enqueue(site)
        with self.assertRaises(ValueError):M.create(row,'check')

    def test_maintenance_fixed_remote_program_no_real_apt(self):
        import maintenance_remote as R
        root=Path(work.name)/('apt-fixture-'+secrets.token_hex(3));root.mkdir()
        program=R.PROGRAM.replace("'/var/lib/backupcenter-maintenance/operation.lock'",repr(str(root/'lock')))
        calls=[]
        def fake(args,**kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args,0,stdout='Inst openssl [1] (2 distro)\n' if '-s' in args else '')
        import sys
        with patch.object(sys,'argv',['fixture',str(root),'check','']),patch.object(subprocess,'run',side_effect=fake):exec(program,{'__name__':'fixture'})
        result=json.loads((root/'result.json').read_text());self.assertEqual(result['packages'],['openssl']);self.assertEqual(result['status'],'success')
        self.assertIn('update',calls[0]);self.assertNotIn('-y',calls[0])
        calls.clear()
        with patch.object(sys,'argv',['fixture',str(root),'upgrade','wrong']),patch.object(subprocess,'run',side_effect=fake):exec(program,{'__name__':'fixture'})
        self.assertEqual(json.loads((root/'result.json').read_text())['reason'],'changed');self.assertFalse(any('-y' in x for x in calls))
        calls.clear()
        with patch.object(sys,'argv',['fixture',str(root),'upgrade',result['digest']]),patch.object(subprocess,'run',side_effect=fake):exec(program,{'__name__':'fixture'})
        self.assertTrue(any('--no-remove' in x and '-y' in x for x in calls))
        self.assertFalse(any('reboot' in x for x in calls))

    def test_terminal_session_ownership_csrf_locks_and_logout(self):
        import terminals as T
        self.setup_admin();server=self.maintenance_server();site=self.site(server).json['id']
        self.assertEqual(self.client.post('/api/servers/'+server+'/terminal',json={}).status_code,403)
        response=self.post('servers/'+server+'/terminal',dict(version=1));self.assertEqual(response.status_code,200,response.json);id=response.json['id']
        self.assertEqual(self.client.get('/api/terminals/'+id).status_code,200)
        with self.assertRaises(ValueError):E.enqueue(site)
        import maintenance as M
        with self.assertRaises(ValueError):M.create(app.getrow('servers',server),'check')
        with self.assertRaises(LookupError):T.get(id,'another-browser','0')
        self.assertEqual(self.post('terminals/'+id+'/input',dict(cols=9999,rows=20)).status_code,409)
        self.post('logout',{})
        self.assertTrue(T.sessions[id].closed)
        self.assertEqual(self.client.get('/api/terminals/'+id).status_code,401)
        with E.db() as c:self.assertFalse(M.busy(c))

    def test_terminal_expiry_buffer_and_auth_revocation(self):
        import terminals as T
        self.setup_admin();server=self.maintenance_server();row=app.getrow('servers',server)
        obj=T.create(row,'owner','0',100,25)
        obj.append(b'x'*(T.LIMIT+30));result=obj.read(0)
        self.assertTrue(result['dropped']);self.assertLessEqual(len(obj.output),T.LIMIT)
        with self.assertRaises(ValueError):obj.read(-1)
        obj.touched-=T.IDLE+1;self.assertTrue(T.get(obj.id,'owner','0').closed)
        obj=T.create(row,'owner','0',100,25);obj.created-=T.LIFETIME+1
        self.assertTrue(T.get(obj.id,'owner','0').closed)
        obj=T.create(row,'owner','0',100,25);E.setsetting('auth_generation','new')
        with self.assertRaises(LookupError):T.get(obj.id,'owner','0')
        self.assertTrue(obj.closed)

    def test_terminal_password_only_options_and_login_validation(self):
        import terminals as T
        self.setup_admin();server=self.maintenance_server();obj=T.create(app.getrow('servers',server),'owner','0',100,25)
        obj.input('bad-user;bad\r');self.assertIsNone(obj.proc)
        with patch.object(obj,'spawn') as spawn:obj.input('root\r');spawn.assert_called_once_with('root')
        fake=unittest.mock.Mock();fake.pid=999999;fake.poll.return_value=0
        with patch.object(T.subprocess,'Popen',return_value=fake) as popen,patch.object(T.threading.Thread,'start'):
            obj.spawn('root')
        args=json.loads(popen.call_args.args[0][-1])
        self.assertIn('PubkeyAuthentication=no',args);self.assertIn('PreferredAuthentications=password',args);self.assertIn('StrictHostKeyChecking=yes',args)
        self.assertIn('ClearAllForwardings=yes',args);self.assertIn('PermitLocalCommand=no',args);self.assertNotIn('-i',args)
        obj.close();self.assertFalse(Path(popen.call_args.kwargs['env'].get('SSH_AUTH_SOCK','/missing-fixture-path')).exists())

    def test_auth_csrf_setup_and_secret_redaction(self):
        self.assertEqual(self.client.post('/api/setup',json={}).status_code,403)
        self.assertEqual(self.post('servers',{}).status_code,401)
        self.assertEqual(self.post('setup',{'code':'wrong'}).status_code,403)
        self.setup_admin(); self.assertFalse((E.DATA/'setup-code.txt').exists())
        self.assertEqual(self.post('setup',{}).status_code,409)
        self.server(); data=self.client.get('/api/state',headers={'Host':'localhost'}).json
        self.assertNotIn('private_key',data['servers'][0]);self.assertNotIn('host_keys',data['servers'][0])
        response=self.client.get('/'); self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy']); response.close()
        self.post('logout',{});self.assertEqual(self.post('sites',{}).status_code,401)
    def test_unicode_username_and_password_spaces(self):
        credentials={'username':'Propriétaire','password':'  a sufficiently long password  '}
        self.assertEqual(self.post('setup',dict(credentials,code='private-install-code')).status_code,200)
        self.post('logout',{})
        self.assertEqual(self.post('login',credentials).status_code,200)
    def test_login_rate_limit(self):
        self.setup_admin(); self.post('logout',{})
        for _ in range(10): self.assertEqual(self.post('login',{'username':'owner','password':'bad'}).status_code,403)
        self.assertEqual(self.post('login',{'username':'owner','password':'bad'}).status_code,429)
    def test_site_scope_and_concurrency(self):
        self.setup_admin(); server=self.server()
        base={'server_id':server,'name':'fixture','path':'/srv/fixture','scope':'site','databases':[]}
        self.assertEqual(self.post('sites',base).status_code,400)
        r=self.site(server);self.assertEqual(r.status_code,200,r.json); id=r.json['id']
        self.assertEqual(self.post('sites/'+id,dict(base,files_only=True,version=1)).status_code,200)
        self.assertEqual(self.post('sites/'+id,dict(base,files_only=True,version=1)).status_code,409)
        self.assertEqual(self.post('sites',dict(base,path='/srv/../etc',files_only=True)).status_code,400)
        self.assertEqual(self.post('sites',dict(base,scope='server',path='/')).status_code,400)
        self.assertEqual(self.post('sites/'+id+'/backup',{}).status_code,200)
        self.assertEqual(self.post('sites/'+id+'/backup',{}).status_code,409)
    def test_ssh_requires_confirmed_fingerprint(self):
        self.setup_admin(); id=self.server()
        self.assertEqual(self.post('servers/'+id+'/test',{}).status_code,400)
        self.assertEqual(self.post('servers/'+id+'/trust',{'fingerprints':'wrong'}).status_code,400)
        with patch.object(E,'scan_host',return_value=('fixture ssh-ed25519 AAAA\n','SHA256:fixture')):
            self.assertEqual(self.post('servers/'+id+'/scan',{}).status_code,200)
        self.assertEqual(self.post('servers/'+id+'/trust',{'fingerprints':'wrong'}).status_code,400)
        self.assertEqual(self.post('servers/'+id+'/trust',{'fingerprints':'SHA256:fixture'}).status_code,200)
    def test_totp_and_recovery_reauthentication(self):
        self.setup_admin()
        self.assertEqual(self.post('security/recovery',{'password':'bad'}).status_code,403)
        r=self.post('security/totp-start',{'password':'a sufficiently long password'}); self.assertEqual(r.status_code,200)
        self.assertEqual(self.post('security/totp-confirm',{'totp':'wrong'}).status_code,400)
        response=self.post('security/recovery',{'password':'a sufficiently long password'}); self.assertEqual(response.status_code,200); response.close()
    def test_real_archive_encrypt_decrypt_corruption_and_isolated_restore(self):
        self.setup_admin(); server_id=self.server(); site_id=self.site(server_id).json['id']
        jobid=E.enqueue(site_id)
        with E.db() as c:
            job=c.execute('SELECT * FROM jobs WHERE id=?',(jobid,)).fetchone(); site=c.execute('SELECT * FROM sites WHERE id=?',(site_id,)).fetchone();server=c.execute('SELECT * FROM servers WHERE id=?',(server_id,)).fetchone()
        class FakeSSH:
            args=['sh','-c']
            def __init__(self,*a): pass
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def call(self,config,out=None,timeout=0):
                with tarfile.open(fileobj=out,mode='w:gz') as tar:
                    content=b'fixture content'; info=tarfile.TarInfo('index.html');info.size=len(content);tar.addfile(info,io.BytesIO(content))
        with patch.object(E,'SSH',FakeSSH): E.backup(job,site,server)
        with E.db() as c:
            c.execute("UPDATE jobs SET status='success' WHERE id=?",(jobid,));job=c.execute('SELECT * FROM jobs WHERE id=?',(jobid,)).fetchone()
        path=E.checked_archive(job); self.assertTrue(path.name.endswith('.zip.age'))
        export=Path(work.name)/'export.zip';E.unpack_archive(job,export);self.assertTrue(export.exists())
        destination=work.name+'/restore-'+secrets.token_hex(3)
        restore={'details':json.dumps({'backup_id':jobid,'destination':destination})}
        with patch.object(E,'SSH',FakeSSH): E.stage(restore,site,server)
        self.assertEqual((Path(destination)/'files/index.html').read_text(),'fixture content')
        self.assertEqual(self.post('jobs/'+jobid+'/restore',{'destination':'/srv/fixture/test','confirm':'fixture.example'}).status_code,400)
        path.write_bytes(path.read_bytes()+b'corruption')
        with self.assertRaises(ValueError): E.checked_archive(job)

    def test_inventory_import_selection_duplicates_and_atomicity(self):
        self.setup_admin(); sid=self.server()
        data={'sites':[{'name':'one.example','path':'/home/one/htdocs/one.example','domains':['one.example'],'sources':['fixture'],'databases':['one_db']},{'name':'two.example','path':'/home/two/htdocs/two.example','domains':['two.example'],'sources':['fixture'],'databases':[]}],'warnings':[],'mysql':{'ok':True,'databases':['one_db','unrelated_db'],'message':'ok'}}
        class FakeSSH:
            def __init__(self,*a): pass
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def call(self,*a,**k): return json.loads(json.dumps(data))
        with patch.object(E,'SSH',FakeSSH): result=self.post('servers/'+sid+'/inventory',{}).json
        tokens=[x['token'] for x in result['sites']]
        selection={'sites':[{'token':tokens[0],'databases':['one_db']},{'token':tokens[1],'databases':[]}]}
        self.assertEqual(self.post('servers/'+sid+'/import',selection).status_code,400)
        with E.db() as c: self.assertEqual(c.execute('SELECT count(*) FROM sites').fetchone()[0],0)
        selection['sites'][1]['files_only']=True
        response=self.post('servers/'+sid+'/import',selection);self.assertEqual(response.status_code,200,response.json)
        with E.db() as c:
            rows=c.execute('SELECT * FROM sites').fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual(E.site_database_config(rows[0],c.execute('SELECT * FROM servers WHERE id=?',(sid,)).fetchone()),{'mysql_auto':True})
        self.assertEqual(self.post('servers/'+sid+'/import',selection).status_code,400)
        self.assertEqual(self.post('servers/'+sid+'/import',{'sites':[{'token':'invented','files_only':True}]}).status_code,400)
        with patch.object(E,'SSH',FakeSSH): result=self.post('servers/'+sid+'/inventory',{}).json
        self.assertTrue(all(x['configured_id'] for x in result['sites']))

    def test_server_edit_version_host_pin_and_secret_redaction(self):
        self.setup_admin(); sid=self.server()
        with E.db() as c: c.execute("UPDATE servers SET host_keys='fixture',fingerprints='fixture' WHERE id=?",(sid,))
        values={'name':'VPS','host':'fixture.example','port':22,'username':'root','mysql_mode':'custom','mysql_user':'backup','mysql_password':'secret-not-for-state','version':1}
        self.assertEqual(self.post('servers/'+sid,values).status_code,200)
        self.assertEqual(self.post('servers/'+sid,values).status_code,409)
        r=self.client.get('/api/state',headers={'Host':'localhost'})
        self.assertNotIn('secret-not-for-state',r.get_data(as_text=True)); self.assertEqual(r.json['servers'][0]['fingerprints'],'fixture')
        values.update(host='new.example',version=2,mysql_password='')
        self.assertTrue(self.post('servers/'+sid,values).json['reconfirm_host'])
        with E.db() as c:
            server=c.execute('SELECT * FROM servers WHERE id=?',(sid,)).fetchone()
            self.assertEqual(server['host_keys'],'')
            self.assertEqual(E.server_database_config(server)['mysql_password'],'secret-not-for-state')

    def test_remote_discovery_aliases_project_root_and_no_secrets(self):
        import remote
        root=Path(work.name)/('discovery-'+secrets.token_hex(3));home=root/'home';vhosts=root/'vhosts';vhosts.mkdir(parents=True)
        site=home/'one/htdocs/one.example';(site/'public').mkdir(parents=True)
        (site/'.env').write_text('DB_DATABASE=one_db\nDB_NAME=archive_db\nDB_PASSWORD=secret-never-returned\n')
        (vhosts/'one.conf').write_text('server { server_name one.example www.one.example; root '+str(site/'public')+'; }\nserver { server_name one.example; root '+str(site/'public')+'; }')
        result,warnings=remote.discover_sites(home,vhosts)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['path'],str(site))
        self.assertEqual(result[0]['domains'],['one.example','www.one.example'])
        self.assertEqual(result[0]['databases'],['archive_db','one_db'])
        self.assertNotIn('secret-never-returned',json.dumps(result))

    def test_migration_preserves_legacy_server(self):
        import sqlite3
        root=Path(work.name)/('migration-'+secrets.token_hex(3));root.mkdir()
        with sqlite3.connect(root/'center.sqlite') as c:
            c.execute("CREATE TABLE servers(id TEXT PRIMARY KEY,name TEXT NOT NULL,host TEXT NOT NULL,port INTEGER NOT NULL,username TEXT NOT NULL,private_key TEXT NOT NULL,host_keys TEXT NOT NULL DEFAULT '',fingerprints TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL)")
            c.execute("INSERT INTO servers VALUES('legacy','Old','old.example',22,'site-user','encrypted-legacy','pinned','fingerprint',1)")
        c.close()
        with patch.object(E,'DATA',root):
            E.init()
            with E.db() as c: server=c.execute('SELECT * FROM servers').fetchone()
            self.assertEqual(server['private_key'],'encrypted-legacy');self.assertEqual(server['host_keys'],'pinned');self.assertEqual(server['version'],1)

    def test_public_key_error_is_actionable(self):
        self.setup_admin()
        response=self.post('servers',{'name':'x','host':'x.example','username':'root','private_key':'ssh-ed25519 PUBLIC-KEY'})
        self.assertEqual(response.status_code,400);self.assertIn('clé publique',response.json['error'])

    def test_php_configuration_database_detection_without_execution(self):
        import remote
        root=Path(work.name)/('php-config-'+secrets.token_hex(3));root.mkdir()
        marker=root/'must-not-execute'
        (root/'config.php').write_text("<?php\nreturn ['dsn' => 'mysql:host=127.0.0.1;dbname=example_db;charset=utf8mb4', 'user'=>'root', 'password'=>'private-value'];\nfile_put_contents("+repr(str(marker))+", 'bad');\n// 'database'=>'commented_db'\n/* 'dsn'=>'mysql:dbname=also_commented;' */")
        (root/'config.accounts.php').write_text("<?php return ['database' => 'accounts_db'];")
        (root/'database.php').write_text("<?php $db_name='archive_db'; $database = getenv('DB_DATABASE');")
        (root/'db.php').write_text('<?php $dsn="mysql:host=localhost;dbname=$dynamic;charset=utf8";')
        self.assertEqual(remote.database_candidates(root),['accounts_db','archive_db','example_db'])
        self.assertFalse(marker.exists())
        self.assertNotIn('private-value',str(remote.database_candidates(root)))

    def test_archives_pagination_filter_missing_files_and_auth(self):
        self.assertEqual(self.client.get('/api/archives',headers={'Host':'localhost'}).status_code,401)
        self.setup_admin();sid=self.server();first=self.site(sid).json['id'];second=self.site(sid).json['id']
        with E.db() as c:
            for i in range(105):
                id=f'{i:024x}';name=id+'.zip.age'
                c.execute("INSERT INTO jobs(id,site_id,kind,status,created,finished,archive,sha256,size) VALUES(?,?,?,?,?,?,?,?,?)",(id,first if i<104 else second,'backup','success',i+1,i+2,name,'fixture-sha',20))
            # Une copie expirée et une tâche échouée ne sont pas des archives conservées.
            c.execute("INSERT INTO jobs(id,site_id,status,created) VALUES('expired',?,'success',999)",(first,))
            c.execute("INSERT INTO jobs(id,site_id,status,created,archive) VALUES('failed',?,'failed',999,'failed.zip.age')",(first,))
        (E.ARCHIVES/('000000000000000000000068.zip.age')).write_bytes(b'fixture')
        items=[]
        for page in range(1,6):
            r=self.client.get('/api/archives?page='+str(page),headers={'Host':'localhost'})
            self.assertEqual(r.status_code,200);self.assertEqual(r.json['total'],105);items.extend(r.json['items'])
        self.assertEqual(len({x['id'] for x in items}),105)
        self.assertTrue(items[0]['available']);self.assertFalse(items[1]['available'])
        self.assertEqual(items[0]['path'],'/mnt/user/backups/backupcenter/'+items[0]['filename'])
        r=self.client.get('/api/archives?site_id='+second,headers={'Host':'localhost'});self.assertEqual(r.json['total'],1)
        state=self.client.get('/api/state',headers={'Host':'localhost'}).json
        self.assertEqual(state['archive_summary']['count'],105)
        self.assertEqual(len(state['jobs']),100)
        self.assertEqual(self.client.get('/api/archives?page=-1',headers={'Host':'localhost'}).status_code,400)

    def test_ssh_key_generation_permissions_cleanup_and_rate_limit(self):
        self.assertEqual(self.post('ssh/keypair',{'username':'root'}).status_code,401)
        self.setup_admin()
        before={p.name for p in (E.DATA/'ssh').iterdir()}
        r=self.post('ssh/keypair',{'username':'root'});self.assertEqual(r.status_code,200)
        self.assertTrue(r.json['private_key'].startswith('-----BEGIN OPENSSH PRIVATE KEY-----'))
        self.assertTrue(r.json['public_key'].startswith('ssh-ed25519 '))
        with tempfile.TemporaryDirectory() as temp:
            key=Path(temp)/'key';key.write_text(r.json['private_key']);key.chmod(0o600)
            public=subprocess.check_output(['ssh-keygen','-y','-P','','-f',str(key)],text=True).strip().split()[:2]
            self.assertEqual(public,r.json['public_key'].split()[:2])
        self.assertEqual(before,{p.name for p in (E.DATA/'ssh').iterdir()})
        state=self.client.get('/api/state',headers={'Host':'localhost'}).get_data(as_text=True)
        self.assertNotIn(r.json['private_key'],state)
        self.assertEqual(self.post('ssh/keypair',{'username':'root;touch /tmp/bad'}).status_code,400)
        for _ in range(5):self.assertEqual(self.post('ssh/keypair',{'username':'root'}).status_code,200)
        self.assertEqual(self.post('ssh/keypair',{'username':'root'}).status_code,429)

    def test_api_english_messages_and_user_data_preserved(self):
        r=self.client.get('/api/archives',headers={'Host':'localhost','X-BackupCenter-Language':'en'})
        self.assertEqual(r.status_code,401);self.assertEqual(r.json['error'],'Sign-in required.')
        self.setup_admin();sid=self.server()
        with E.db() as c:c.execute("UPDATE servers SET name='Sécurité' WHERE id=?",(sid,))
        r=self.client.get('/api/state',headers={'Host':'localhost','X-BackupCenter-Language':'en'})
        self.assertEqual(r.json['servers'][0]['name'],'Sécurité')
        headers={'Host':'localhost','X-CSRF-Token':r.json['csrf'],'X-BackupCenter-Language':'en'}
        r=self.client.post('/api/ssh/keypair',json={'username':'invalid;'},headers=headers)
        self.assertEqual(r.json['error'],'Invalid SSH user.')

if __name__=='__main__': unittest.main(verbosity=2)
