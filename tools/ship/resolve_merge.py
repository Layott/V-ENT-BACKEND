"""Resolve the conflicts a merge of origin/main brings into a feature branch,
the same way every time. Run inside the repo during the conflicted merge.

  generated dictionaries, hashed public bundles, the debt ledger: main's copy
      (the bundles are regenerated after, the hook rewrites the ledger)
  src/i18n/dictionaries.js: both sides' lines, ours first, no line twice
  security/idor-cases.json: every case from both sides (union_idor.py)
Anything else stops the merge for a person to read.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def git(*args):
    return subprocess.check_output(['git'] + list(args)).decode('utf-8')


conflicted = [p for p in git('diff', '--name-only', '--diff-filter=U').splitlines() if p]
left = []
for path in conflicted:
    norm = path.replace('\\', '/')
    if norm.startswith('src/i18n/generated/') or norm.startswith('public/i18n/') \
            or norm == 'tools/debt-ledger.json':
        if subprocess.call(['git', 'checkout', '--theirs', '--', path]) != 0:
            subprocess.call(['git', 'rm', '-q', '--', path])
        else:
            subprocess.call(['git', 'add', '--', path])
        print('main copy:', path)
    elif norm == 'src/i18n/dictionaries.js':
        raw = open(path, 'rb').read().decode('utf-8')
        crlf = '\r\n' in raw
        out, side, ours, theirs = [], None, [], []
        for line in raw.replace('\r\n', '\n').split('\n'):
            if line.startswith('<<<<<<< '):
                side, ours, theirs = 'ours', [], []
            elif line.startswith('||||||| ') and side == 'ours':
                side = 'base'
            elif line.startswith('=======') and side in ('ours', 'base'):
                side = 'theirs'
            elif line.startswith('>>>>>>> ') and side == 'theirs':
                seen = set()
                for kept in ours + theirs:
                    if kept not in seen:
                        out.append(kept)
                        seen.add(kept)
                side = None
            elif side == 'ours':
                ours.append(line)
            elif side == 'theirs':
                theirs.append(line)
            elif side is None:
                out.append(line)
        text = '\n'.join(out)
        if crlf:
            text = text.replace('\n', '\r\n')
        open(path, 'wb').write(text.encode('utf-8'))
        subprocess.call(['git', 'add', '--', path])
        print('union:', path)
    elif norm == 'security/idor-cases.json':
        subprocess.check_call([sys.executable, os.path.join(HERE, 'union_idor.py')])
        subprocess.call(['git', 'add', '--', path])
        print('union:', path)
    else:
        left.append(path)
if left:
    print('NOT RESOLVED, read by hand:', ' '.join(left))
    sys.exit(1)
