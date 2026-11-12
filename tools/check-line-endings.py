"""A staged file whose every line ending changed is a tool's accident, not an edit.

Seen twice. 11 November 2026: `sed -i` on a CRLF file rewrote every line ending
and the diff grew to 38,000 lines for a one-word change (restored by hand).
12 November 2026: `sed -i` again, on vent_event/views.py, caught by eye before
it was staged. Both times the change that was meant was a few lines; what the
tool did was the whole file.

So: for each modified staged file, read the blob in HEAD and the blob in the
index and compare the share of lines ending in CRLF. A share that moves from
nearly all to nearly none (or back) is a flip.

The blobs are read directly on purpose. A path-based `git diff --cached` on
this machine (autocrlf, Git for Windows) reported whole files changed when both
blobs were plain LF, which is how the first version of this catcher read nine
honest edits as flips on 12 November 2026.

    python tools/check-line-endings.py [repo]     the staged files of repo (default: here)
    python tools/check-line-endings.py --self-test

Exit 0 when clean; 1 with each flipped file named.
"""
import os
import subprocess
import sys
import tempfile

# Inside a git hook, GIT_DIR and GIT_INDEX_FILE point at the repository being
# committed, and `git -C` then answers about that one instead of the one asked
# (feedback_hook_git_dir). Every git call here runs without them.
_ENV = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}

# Below this many lines a file is never judged.
MIN_LINES = 40
# Judged flipped when the share of CRLF lines moves by this much or more.
FLIP = 0.9

NL = bytes([10])
CRLF = bytes([13, 10])
NUL = bytes([0])


def _out(repo, *args):
    return subprocess.run(['git', '-C', repo] + list(args), capture_output=True,
                          check=False, env=_ENV).stdout


def _endings(blob):
    """(lines, share of them ending in CRLF) for one stored blob."""
    lines = blob.count(NL)
    return lines, (blob.count(CRLF) / lines) if lines else 0.0


def flipped(repo):
    names = _out(repo, 'diff', '--cached', '--name-only', '--no-renames',
                 '--diff-filter=M').decode('utf-8', 'replace').splitlines()
    found = []
    for path in names:
        old = _out(repo, 'cat-file', 'blob', 'HEAD:%s' % path)
        new = _out(repo, 'cat-file', 'blob', ':%s' % path)
        if NUL in old[:8000] or NUL in new[:8000]:
            continue  # binary
        n_old, crlf_old = _endings(old)
        n_new, crlf_new = _endings(new)
        if min(n_old, n_new) < MIN_LINES:
            continue
        if abs(crlf_old - crlf_new) >= FLIP:
            found.append((path, crlf_old, crlf_new))
    return found


def _git(repo, *args):
    subprocess.run(['git', '-C', repo] + list(args), check=True,
                   capture_output=True, text=True, env=_ENV)


def self_test():
    failures = 0
    crlf = chr(13) + chr(10)
    lf = chr(10)
    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, 'init', '-q')
        _git(tmp, 'config', 'core.autocrlf', 'false')
        _git(tmp, 'config', 'user.email', 'check@vent.test')
        _git(tmp, 'config', 'user.name', 'check')
        body = ''.join('line %d%s' % (i, crlf) for i in range(200))
        path = os.path.join(tmp, 'a.py')
        with open(path, 'w', newline='') as f:
            f.write(body)
        _git(tmp, 'add', 'a.py')
        _git(tmp, 'commit', '-q', '-m', 'one')

        # 1. Every ending flipped: must be caught.
        with open(path, 'w', newline='') as f:
            f.write(body.replace(crlf, lf))
        _git(tmp, 'add', 'a.py')
        if not flipped(tmp):
            print('FAIL: a file with every ending flipped was not caught')
            failures += 1

        # 2. A real edit of every line, endings kept: must pass.
        with open(path, 'w', newline='') as f:
            f.write(''.join('changed %d%s' % (i, crlf) for i in range(200)))
        _git(tmp, 'add', 'a.py')
        if flipped(tmp):
            print('FAIL: a real edit of 200 lines was called a flip')
            failures += 1

        # 3. A small edit with its own odd ending: must pass.
        with open(path, 'w', newline='') as f:
            f.write(body.replace('line 5' + crlf, 'line five' + lf))
        _git(tmp, 'add', 'a.py')
        if flipped(tmp):
            print('FAIL: a one-line edit was called a flip')
            failures += 1
    print('%d of 3 self-test case(s) failed' % failures)
    return 1 if failures else 0


def main():
    if '--self-test' in sys.argv:
        return self_test()
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    repo = args[0] if args else os.getcwd()
    found = flipped(repo)
    for path, before, after in found:
        print('%s: %d%% of lines ended CRLF in HEAD, %d%% staged. '
              'A tool rewrote the endings; restore them before committing.'
              % (path, round(before * 100), round(after * 100)))
    print('%d staged file(s) with every line ending changed' % len(found))
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
