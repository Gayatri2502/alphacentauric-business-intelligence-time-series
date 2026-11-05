"""Offline backup and verified restore. Stop the dashboard before either operation."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def backup(source, destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_dir():raise ValueError('Data directory does not exist')
    if destination.is_relative_to(source):raise ValueError('Place backup outside the data directory')
    files=[p for p in source.rglob('*') if p.is_file() and not p.is_symlink()]
    hashes={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with destination.open('xb') as stream, zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files:z.write(p,p.relative_to(source).as_posix())
        z.writestr('backup-manifest.json',json.dumps(hashes))
    return len(files)


def restore(archive, destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Restore destination must not exist')
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist()
        if len({x.filename for x in entries})!=len(entries):raise ValueError('Duplicate archive paths')
        if sum(x.file_size for x in entries)>2*1024**3:raise ValueError('Backup exceeds restore size limit')
        hashes=json.loads(z.read('backup-manifest.json'))
        if set(z.namelist())!=set(hashes)|{'backup-manifest.json'}:raise ValueError('Unexpected backup entries')
        for name,digest in hashes.items():
            target=destination/name
            if not target.resolve().is_relative_to(destination) or Path(name).is_absolute():raise ValueError('Unsafe backup path')
            if hashlib.sha256(z.read(name)).hexdigest()!=digest:raise ValueError('Backup integrity mismatch')
        destination.mkdir(parents=True)
        for name in hashes:
            p=destination/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(name))
    return len(hashes)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['backup','restore']);parser.add_argument('source');parser.add_argument('destination')
    args=parser.parse_args()
    print((backup if args.operation=='backup' else restore)(args.source,args.destination),'files verified')
