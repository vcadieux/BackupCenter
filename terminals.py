"""Password-only SSH PTYs. Output is bounded in memory and never audited."""
import os,re,json,secrets,tempfile,subprocess,threading,time,select,signal,sys,base64,fcntl,termios,tty,struct
from pathlib import Path
import engine as E
IDLE=300
LIFETIME=1800
LIMIT=1024*1024
sessions={}
mutex=threading.RLock()
BOOT="import os,fcntl,termios,sys,json;os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0);a=json.loads(sys.argv[1]);os.execvpe(a[0],a,os.environ)"

def init():
 with E.db() as c:c.execute("CREATE TABLE IF NOT EXISTS terminal_sessions(id TEXT PRIMARY KEY,server_id TEXT NOT NULL REFERENCES servers(id),owner TEXT NOT NULL,generation TEXT NOT NULL,status TEXT NOT NULL,created INTEGER NOT NULL)")

def active(c):return c.execute("SELECT 1 FROM terminal_sessions WHERE status='open'").fetchone()

class Session:
 def __init__(self,server,owner,generation,cols=100,rows=28):
  self.id=secrets.token_hex(16);self.server=dict(server);self.owner=owner;self.generation=generation
  self.created=self.touched=time.monotonic();self.closed=False;self.reason='';self.output=bytearray(b'login: ');self.base=0;self.login='';self.login_invalid=False;self.proc=None;self.master=None;self.tmp=None;self.cols=cols;self.rows=rows;self.lock=threading.RLock()
 def append(self,data):
  with self.lock:
   self.output.extend(data)
   if len(self.output)>LIMIT:
    n=len(self.output)-LIMIT;del self.output[:n];self.base+=n
 def spawn(self,username):
  self.tmp=tempfile.TemporaryDirectory(dir=E.DATA/'ssh');known=Path(self.tmp.name)/'known_hosts';known.write_text(self.server['host_keys']);known.chmod(0o600)
  args=['ssh','-F','/dev/null','-tt','-p',str(self.server['port']),'-l',username,
   '-o','BatchMode=no','-o','PubkeyAuthentication=no','-o','PreferredAuthentications=password','-o','PasswordAuthentication=yes','-o','KbdInteractiveAuthentication=no',
   '-o','GSSAPIAuthentication=no','-o','HostbasedAuthentication=no','-o','IdentityAgent=none','-o','ForwardAgent=no','-o','ForwardX11=no',
   '-o','ClearAllForwardings=yes','-o','PermitLocalCommand=no','-o','EscapeChar=none','-o','ControlMaster=no','-o','ControlPath=none',
   '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),'-o','ConnectTimeout=15','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2','-o','NumberOfPasswordPrompts=3',self.server['host']]
  master,slave=os.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',self.rows,self.cols,0,0))
  env=dict(os.environ,TERM='xterm-256color',SSH_ASKPASS_REQUIRE='never');env.pop('SSH_AUTH_SOCK',None);env.pop('SSH_ASKPASS',None);env.pop('DISPLAY',None)
  try:self.proc=subprocess.Popen([sys.executable,'-c',BOOT,json.dumps(args)],stdin=slave,stdout=slave,stderr=slave,env=env,close_fds=True)
  except Exception:os.close(master);raise
  finally:os.close(slave)
  self.master=master;os.set_blocking(master,False);threading.Thread(target=self.collect,daemon=True).start()
 def collect(self):
  try:
   while not self.closed:
    if select.select([self.master],[],[],0.2)[0]:
     try:data=os.read(self.master,65536)
     except OSError:break
     if not data:break
     self.append(data)
    elif self.proc.poll() is not None:break
  finally:self.close('disconnected')
 def input(self,text):
  raw=text.encode('utf-8')
  if len(raw)>16384:raise ValueError('Saisie trop volumineuse.')
  with self.lock:
   if self.closed:raise ValueError('Terminal fermé.')
   self.touched=time.monotonic()
   if self.proc is None:
    for char in text:
     if char in '\r\n':
      if self.login_invalid or not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_-]{0,63}',self.login):self.append(b'\r\nInvalid user.\r\nlogin: ');self.login='';self.login_invalid=False;continue
      username=self.login;self.login='';self.append(b'\r\n');self.spawn(username);break
     elif char in '\x7f\b':
      if self.login:self.login=self.login[:-1];self.append(b'\b \b')
     elif char=='\x03':self.login='';self.login_invalid=False;self.append(b'^C\r\nlogin: ')
     elif char=='\x04':self.close('closed');break
     elif char.isascii() and (char.isalnum() or char in '_-') and len(self.login)<64:self.login+=char;self.append(char.encode())
     else:self.login_invalid=True
    return
   end=time.monotonic()+2
   while raw:
    if time.monotonic()>end:self.close('input-error');raise ValueError('Saisie interrompue. Rouvrez le terminal.')
    if select.select([],[self.master],[],0.1)[1]:
     try:n=os.write(self.master,raw)
     except BlockingIOError:continue
     raw=raw[n:]
 def resize(self,cols,rows):
  cols=int(cols);rows=int(rows)
  if not 20<=cols<=300 or not 5<=rows<=120:raise ValueError('Dimensions du terminal invalides.')
  with self.lock:
   self.cols=cols;self.rows=rows
   if self.master is not None and not self.closed:
    fcntl.ioctl(self.master,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
    if self.proc and self.proc.poll() is None:
     try:os.killpg(self.proc.pid,signal.SIGWINCH)
     except ProcessLookupError:pass
 def read(self,offset):
  with self.lock:
   offset=int(offset);end=self.base+len(self.output)
   if offset<0 or offset>end:raise ValueError('Position de terminal invalide.')
   start=max(offset,self.base);data=bytes(self.output[start-self.base:start-self.base+65536])
   return dict(data=base64.b64encode(data).decode(),offset=start+len(data),dropped=offset<self.base,closed=self.closed,reason=self.reason)
 def close(self,reason='closed'):
  with self.lock:
   if self.closed:return
   self.closed=True;self.reason=reason;self.closed_at=time.monotonic()
   if self.proc and self.proc.poll() is None:
    try:os.killpg(self.proc.pid,signal.SIGTERM)
    except ProcessLookupError:self.proc.terminate()
    try:self.proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
     try:os.killpg(self.proc.pid,signal.SIGKILL)
     except ProcessLookupError:pass
     self.proc.wait(timeout=1)
   if self.master is not None:
    try:os.close(self.master)
    except OSError:pass
   if self.tmp:self.tmp.cleanup()
  with E.db() as c:c.execute("UPDATE terminal_sessions SET status='closed' WHERE id=?",(self.id,))
  E.audit('Terminal fermé : '+self.id+' ('+reason+')')

def create(server,owner,generation,cols,rows):
 if not server['host_keys']:raise ValueError('Confirmez d’abord l’empreinte SSH du serveur.')
 obj=Session(server,owner,generation);obj.resize(cols,rows)
 import maintenance as M
 with mutex,E.db() as c:
  c.execute('BEGIN IMMEDIATE')
  if M.busy(c) or c.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():raise ValueError('Une opération est déjà en cours. Réessayez après sa fin.')
  if c.execute('SELECT version FROM servers WHERE id=?',(server['id'],)).fetchone()[0]!=server['version']:raise ValueError('Serveur modifié ailleurs. Actualisez.')
  c.execute('INSERT INTO terminal_sessions VALUES(?,?,?,?,?,?)',(obj.id,server['id'],owner,generation,'open',E.now()));sessions[obj.id]=obj
 E.audit('Terminal ouvert : '+obj.id+' serveur '+server['id']);return obj

def get(id,owner,generation):
 with mutex:obj=sessions.get(id)
 if not obj or not owner or obj.owner!=owner:raise LookupError()
 if obj.generation!=generation or generation!=E.setting('auth_generation','0'):
  obj.close('revoked');raise LookupError()
 if not obj.closed:
  elapsed=time.monotonic()
  if elapsed-obj.created>=LIFETIME:obj.close('limit')
  elif elapsed-obj.touched>=IDLE:obj.close('idle')
 return obj

def close_owner(owner):
 if not owner:return
 with mutex:items=[s for s in sessions.values() if s.owner==owner]
 for s in items:s.close('logout')

def reap():
 while True:
  with mutex:items=list(sessions.values())
  for obj in items:
   try:
    if not obj.closed:get(obj.id,obj.owner,obj.generation)
    if obj.closed and time.monotonic()-obj.closed_at>60:
     with mutex:sessions.pop(obj.id,None)
   except Exception:pass
  time.sleep(2)

def start():
 with E.db() as c:c.execute("UPDATE terminal_sessions SET status='closed' WHERE status='open'")
 threading.Thread(target=reap,daemon=True).start()
