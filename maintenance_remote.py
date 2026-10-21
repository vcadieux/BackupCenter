"""Fixed APT operations, invoked over pinned SSH. No interactive shell."""
import os,re,json,subprocess,sys,hashlib,stat
from pathlib import Path

# Executed by systemd independently of the SSH connection.
PROGRAM=r'''
import os,json,subprocess,hashlib,sys,fcntl,re
from pathlib import Path
root=Path(sys.argv[1]);mode=sys.argv[2];expected=sys.argv[3]
def save(data):
 p=root/'result.tmp';p.write_text(json.dumps(data));p.replace(root/'result.json')
def phase(name): (root/'phase').write_text(name)
def plan():
 r=subprocess.run(['apt-get','-s','--no-remove','upgrade'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=dict(os.environ,LC_ALL='C'))
 if r.returncode:raise RuntimeError('simulation')
 lines=[x for x in r.stdout.splitlines() if x.startswith(('Inst ','Remv '))]
 if any(x.startswith('Remv ') for x in lines):raise RuntimeError('removal')
 packages=[x.split()[1] for x in lines]
 return packages,hashlib.sha256('\n'.join(lines).encode()).hexdigest()
try:
 os.umask(0o077)
 lock=open('/var/lib/backupcenter-maintenance/operation.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if mode=='check':
  phase('refresh')
  r=subprocess.run(['apt-get','-o','APT::Update::Error-Mode=any','-o','DPkg::Lock::Timeout=60','update'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  if r.returncode:raise RuntimeError('refresh')
 phase('plan');packages,digest=plan()
 if mode=='upgrade':
  if digest!=expected:raise RuntimeError('changed')
  phase('upgrade')
  r=subprocess.run(['apt-get','-y','--no-remove','-o','DPkg::Lock::Timeout=60','-o','Dpkg::Options::=--force-confold','upgrade'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=dict(os.environ,DEBIAN_FRONTEND='noninteractive',NEEDRESTART_MODE='a',LC_ALL='C'))
  if r.returncode:raise RuntimeError('upgrade')
 save(dict(status='success',packages=packages,digest=digest,reboot=Path('/var/run/reboot-required').exists()))
except Exception as e:
 save(dict(status='failed',reason=str(e) if isinstance(e,RuntimeError) else 'technical',reboot=Path('/var/run/reboot-required').exists()))
finally:
 if 'lock' in globals():lock.close()
'''

def main(c):
 if os.geteuid()!=0 or not Path('/etc/debian_version').exists() or not Path('/run/systemd/system').is_dir():
  return dict(status='failed',reason='unsupported')
 id=c.get('operation_id','')
 if not re.fullmatch('[a-f0-9]{24}',id):raise ValueError('id')
 base=Path('/var/lib/backupcenter-maintenance')
 if base.is_symlink():raise ValueError('path')
 if base.exists() and (base.stat().st_uid!=0 or base.stat().st_mode&0o077):raise ValueError('permissions')
 action=c['action'];root=base/id;unit='backupcenter-apt-'+id
 if action=='maintenance_start':
  mode=c.get('mode');digest=c.get('digest','')
  if mode not in ['check','upgrade'] or (mode=='upgrade' and not re.fullmatch('[a-f0-9]{64}',digest)):raise ValueError('mode')
  base.mkdir(mode=0o700,exist_ok=True)
  # Never relaunch an existing operation, including after SSH uncertainty.
  if not root.exists():
   root.mkdir(mode=0o700)
   r=subprocess.run(['systemd-run','--quiet','--unit='+unit,'--property=Type=exec','--property=StandardOutput=null','--property=StandardError=null',sys.executable,'-c',PROGRAM,str(root),mode,digest],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   if r.returncode:return dict(status='failed',reason='launch')
 if root.is_symlink():raise ValueError('path')
 result=root/'result.json'
 if result.exists():
  if result.is_symlink() or result.stat().st_size>1024*1024:raise ValueError('result')
  return json.loads(result.read_text())
 state=subprocess.run(['systemctl','show','--property=ActiveState','--value',unit],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
 if state.stdout.strip() not in ['active','activating','reloading']:
  if result.exists():return json.loads(result.read_text())
  return dict(status='failed',reason='interrupted')
 phase=root/'phase'
 return dict(status='running',phase=phase.read_text()[:30] if phase.exists() else 'starting')

if __name__=='__main__':
 try:print(json.dumps(main(CONFIG)))
 except Exception:sys.exit(1)
