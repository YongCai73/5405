"""Read-only text connector. All model reads resolve SQLite retained versions."""
import os
import stat
from pathlib import Path, PurePosixPath

EXT={'.txt','.md','.rst','.py','.js','.ts','.tsx','.jsx','.c','.h','.cpp','.java','.go','.rs','.sql','.toml','.yaml','.yml','.css','.html','.sh'}
DENY={'.git','.ssh','.aws','.azure','.venv','venv','node_modules','__pycache__','data','.cache'}

def linklike(p):
    st=p.lstat()
    return p.is_symlink() or bool(getattr(st,'st_file_attributes',0)&0x400)

def approved_root(root):
    p=Path(root).absolute()
    for part in (p,*p.parents):
        if linklike(part): raise ValueError('symlink/junction root forbidden')
    if not p.is_dir():raise ValueError('folder required')
    return p.resolve(strict=True)

def safe_relative(path):
    if '\\' in path or ':' in path:raise ValueError('invalid project path')
    p=PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts or not p.parts:raise ValueError('path escape')
    return p.as_posix()

def allowed(p):
    name=p.name.lower()
    return p.suffix.lower() in EXT and not name.startswith('.') and not any(x in name for x in ('secret','credential','password','token','private','id_rsa','id_ed25519','api_key','api-key','apikey')) and name not in {'config.json','auth.json','key.json'} and not name.endswith(('.pem','.key','.p12'))

def scan(root):
    root=approved_root(root)
    result=[];total=0;visited=0
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d.lower() not in DENY and not linklike(Path(base)/d))
        for name in sorted(files):
            visited+=1
            if visited>5000:raise ValueError('folder scan limit (5000 entries)')
            p=Path(base)/name
            if not allowed(p) or linklike(p):continue
            if not p.resolve().is_relative_to(root) or not stat.S_ISREG(p.stat().st_mode) or p.stat().st_size>131072:continue
            # Recheck canonical path immediately before open. Trusted local OS/process threat model.
            if linklike(p) or not p.resolve().is_relative_to(root):continue
            raw=p.read_bytes()
            if len(raw)>131072 or b'\0' in raw:continue
            try:text=raw.decode('utf-8')
            except UnicodeError:continue
            total+=len(raw)
            if total>4*1024*1024 or len(result)>=1000:raise ValueError('manifest size limit')
            result.append((p.relative_to(root).as_posix(),text))
    return result
