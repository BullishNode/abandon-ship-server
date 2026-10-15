#!/usr/bin/env python3
"""Stop only this new A Core if its required mount/floors become unsafe."""
from pathlib import Path
import json,os,shutil,subprocess,time
BASE=Path('/home/ubuntu/two-step-signet-core');DATA=Path('/srv/two-step-core')
project='two-step-signet-a-20261014'
problems=[]
if not DATA.is_mount() or DATA.stat().st_dev==Path('/').stat().st_dev:problems.append('dedicated data mount absent')
usage={str(p):shutil.disk_usage(p).free for p in (Path('/'),DATA) if p.exists()}
for path,free in usage.items():
 if free<42*1024**3:problems.append(path+':below42GiBstopthreshold')
report={'utc_epoch':time.time(),'free_bytes':usage,'problems':problems,'core_stop_requested':False}
if problems:
 # Match exact compose namespace before touching a running container.
 p=subprocess.run(['docker','ps','-q','--filter','label=com.docker.compose.project='+project,'--filter','label=com.docker.compose.service=core'],capture_output=True,text=True,check=True)
 ids=p.stdout.split()
 if ids:
  subprocess.run(['docker','stop','--time','60',*ids],check=True);report['core_stop_requested']=True;report['container_ids']=ids
 (BASE/'STORAGE_STOP').touch(mode=0o600,exist_ok=True)
print(json.dumps(report))
