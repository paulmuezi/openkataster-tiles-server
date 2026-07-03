
from __future__ import annotations
from pathlib import Path
import os, subprocess, sys, time
DATA=Path('/srv/openkataster-tiles/data')
BUILDER=Path('/opt/openkataster-tiles/build_alkis_search_index.py')
PYTHON=sys.executable
states=[]
for feature in sorted(DATA.glob('*.features.sqlite')):
    state=feature.name[:-len('.features.sqlite')]
    link=DATA / f'{state}.search.sqlite'
    if link.exists():
        print(f'skip {state}: search.sqlite exists -> {link.resolve()}')
        continue
    real_feature=Path(os.path.realpath(feature))
    out=real_feature.with_name('search.sqlite')
    states.append((state, feature, real_feature, out, link))
print(f'missing {len(states)} search.sqlite')
for state, feature, real_feature, out, link in states:
    tmp=out.with_name(out.name + '.tmp')
    if tmp.exists():
        tmp.unlink()
    print(f'\n[{time.strftime("%H:%M:%S")}] build {state}')
    print(f'  source {real_feature}')
    print(f'  out    {out}')
    env=os.environ.copy()
    env['ALKIS_SEARCH_INDEX_SOURCE_FEATURES']=str(real_feature)
    env['ALKIS_SEARCH_INDEX_OUT']=str(tmp)
    subprocess.run([PYTHON, str(BUILDER)], check=True, env=env)
    if out.exists():
        backup=out.with_name(out.name + f'.bak-{int(time.time())}')
        out.rename(backup)
        print(f'  backup old {backup}')
    tmp.rename(out)
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink(out, link)
    print(f'  linked {link} -> {out}')
print('\ndone')
