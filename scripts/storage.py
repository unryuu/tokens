"""File-level incremental snapshots using hardlinks between backup files only."""
import json
import os
from pathlib import Path
import uuid


def index(root):
    known = {}
    for manifest_path in sorted((root/'backups').glob('*/manifest.json')):
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for entry in manifest['files']:
            path = manifest_path.parent/entry['path']
            if path.is_file(): known.setdefault((entry['sha256'],entry['bytes']),path)
    return known


def reuse(path, sha, known):
    """Atomic replacement with an identical archived file; never link live sources."""
    key = (sha,path.stat().st_size); old = known.get(key)
    if old is None:
        known[key] = path; return False
    if os.path.samefile(old,path):return True
    temporary = path.with_name(path.name+'.link-'+uuid.uuid4().hex)
    try:
        os.link(old,temporary)
        os.replace(temporary,path)
        return True
    except OSError:
        # Unsupported filesystems retain a normal complete copy.
        if temporary.exists():
            # Keep the newly created link as a private recovery artifact, not a source file.
            os.replace(temporary,path.with_name(path.name+'.unused-link'))
        return False


def compact(root, digest):
    # Validate every file before changing the representation of any old snapshot.
    manifests = sorted((root/'backups').glob('*/manifest.json'))
    files = []
    for p in manifests:
        m=json.loads(p.read_text(encoding='utf-8'))
        for e in m['files']:
            f=p.parent/e['path']
            if digest(f)!=e['sha256']:raise RuntimeError(f'Backup content changed: {f}')
            files.append((f,e))
    known={};saved=0;count=0
    for f,e in files:
        old=known.get((e['sha256'],e['bytes']))
        already=old is not None and os.path.samefile(old,f)
        if reuse(f,e['sha256'],known) and not already:
            saved+=e['bytes'];count+=1
    return {'deduplicated_files':count,'logical_duplicate_bytes_removed':saved}
