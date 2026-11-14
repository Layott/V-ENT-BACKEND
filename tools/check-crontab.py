"""Every job written to run on a schedule has its line in deploy/crontab.vent.

    python tools/check-crontab.py              # "crontab: 0 missing" or the names
    python tools/check-crontab.py --self-test  # proves it both ways on fixtures

Why. On 14 November 2026 InterServer deleted the V-ENT server for an unpaid
invoice. The crontab went with it, and it had never been written down in one
place: eleven lines spread over handovers, inbox rows and docstrings, each added
on the day its feature shipped. The same was true of the mail relay script. A
box that holds state git does not is a box that cannot be rebuilt from git.

What it reads, from this repo:
  * every management command whose docstring says it runs on a schedule
    ("cron", "run nightly", "every minute", "every 15 minutes", "once a day")
  * every deploy/*.sh whose header carries a "Cron:" line
and fails when one of them is not named on an uncommented line of
deploy/crontab.vent.

Exceptions carry their reason here, never in the crontab, so the file stays a
plain crontab that `crontab deploy/crontab.vent` installs as it is.
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SCHEDULED = re.compile(
    r'\bcron\b|\brun (?:it )?nightly\b|\bevery (?:minute|\d+ minutes|few minutes|hour)\b|\bonce a day\b',
    re.IGNORECASE)

# Commands that mention cron without being a cron job, each with why.
NOT_SCHEDULED = {
    'backup_status': 'read only; answers the admin console about the backups cron takes, never scheduled itself',
}


def scheduled_commands(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ('venv', '.venv', 'node_modules', '.git')]
        if not dirpath.replace('\\', '/').endswith('management/commands'):
            continue
        for name in sorted(filenames):
            if not name.endswith('.py') or name == '__init__.py':
                continue
            with open(os.path.join(dirpath, name), encoding='utf-8') as f:
                head = ''.join(f.readlines()[:60])
            if SCHEDULED.search(head):
                found.append(name[:-3])
    return sorted(set(found))


def scheduled_scripts(root):
    deploy = os.path.join(root, 'deploy')
    found = []
    if not os.path.isdir(deploy):
        return found
    for name in sorted(os.listdir(deploy)):
        if not name.endswith('.sh'):
            continue
        with open(os.path.join(deploy, name), encoding='utf-8') as f:
            head = ''.join(f.readlines()[:30])
        if re.search(r'^#\s*Cron(?: \([^)]*\))?:', head, re.MULTILINE):
            found.append(name)
    return found


def crontab_lines(root):
    path = os.path.join(root, 'deploy', 'crontab.vent')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith('#')]


def missing(root):
    lines = crontab_lines(root)
    if lines is None:
        return ['deploy/crontab.vent itself']
    body = '\n'.join(lines)
    gaps = []
    for cmd in scheduled_commands(root):
        if cmd in NOT_SCHEDULED:
            continue
        if not re.search(r'manage\.py\s+' + re.escape(cmd) + r'\b', body):
            gaps.append('manage.py ' + cmd)
    for script in scheduled_scripts(root):
        if not re.search(r'deploy/' + re.escape(script) + r'\b', body):
            gaps.append('deploy/' + script)
    return gaps


def self_test():
    with tempfile.TemporaryDirectory() as t:
        cmds = os.path.join(t, 'app', 'management', 'commands')
        os.makedirs(cmds)
        os.makedirs(os.path.join(t, 'deploy'))
        files = {
            'settle_things.py': '"""Settle things. Run by cron every minute."""\n',
            'expire_things.py': '"""Expire what lapsed.\n\nRun nightly. Without it a purchase never ends."""\n',
            'one_off_import.py': '"""Import a JSON export once, by hand."""\n',
            'backup_status.py': '"""Read only. Both of those live in cron."""\n',
            '__init__.py': '',
        }
        for name, text in files.items():
            with open(os.path.join(cmds, name), 'w', encoding='utf-8') as f:
                f.write(text)
        with open(os.path.join(t, 'deploy', 'backup.sh'), 'w', encoding='utf-8') as f:
            f.write('#!/bin/bash\n# Cron: 0 3 * * * /srv/vent/backend/deploy/backup.sh\n')
        with open(os.path.join(t, 'deploy', 'helper.sh'), 'w', encoding='utf-8') as f:
            f.write('#!/bin/bash\n# run by hand\n')

        cases = []
        cases.append(('no crontab file at all', missing(t), ['deploy/crontab.vent itself']))

        tab = os.path.join(t, 'deploy', 'crontab.vent')
        with open(tab, 'w', encoding='utf-8') as f:
            f.write('# * * * * * cd /srv && ./venv/bin/python manage.py settle_things\n'
                    '0 3 * * * bash /srv/vent/backend/deploy/backup.sh\n')
        cases.append(('a commented line does not count, nightly without the word cron is caught',
                      missing(t), ['manage.py expire_things', 'manage.py settle_things']))

        with open(tab, 'w', encoding='utf-8') as f:
            f.write('* * * * * cd /srv && ./venv/bin/python manage.py settle_things\n'
                    '30 4 * * * cd /srv && ./venv/bin/python manage.py expire_things\n')
        cases.append(('a deploy script with a Cron: header is held too',
                      missing(t), ['deploy/backup.sh']))

        with open(tab, 'w', encoding='utf-8') as f:
            f.write('* * * * * cd /srv && ./venv/bin/python manage.py settle_things\n'
                    '30 4 * * * cd /srv && ./venv/bin/python manage.py expire_things\n'
                    '0 3 * * * bash /srv/vent/backend/deploy/backup.sh\n')
        cases.append(('complete: one-off and read-only commands are left alone', missing(t), []))

        with open(tab, 'w', encoding='utf-8') as f:
            f.write('* * * * * cd /srv && ./venv/bin/python manage.py settle_things_old\n'
                    '30 4 * * * cd /srv && ./venv/bin/python manage.py expire_things\n'
                    '0 3 * * * bash /srv/vent/backend/deploy/backup.sh\n')
        cases.append(('a longer name does not stand in for the real one',
                      missing(t), ['manage.py settle_things']))

    bad = 0
    for label, got, want in cases:
        ok = sorted(got) == sorted(want)
        bad += not ok
        print(('ok: ' if ok else 'FAIL: ') + label + ('' if ok else f' (got {got}, want {want})'))
    if bad:
        print(f'self-test FAILED: {bad} of {len(cases)}')
        return 1
    print(f'self-test passed: {len(cases)} cases')
    return 0


def main():
    if '--self-test' in sys.argv:
        return self_test()
    gaps = missing(ROOT)
    held = len(scheduled_commands(ROOT)) - len(NOT_SCHEDULED) + len(scheduled_scripts(ROOT))
    print(f'crontab: {len(gaps)} missing ({held} scheduled jobs held by deploy/crontab.vent)')
    for g in gaps:
        print(f'  no line in deploy/crontab.vent for {g}')
    return 1 if gaps else 0


if __name__ == '__main__':
    sys.exit(main())
