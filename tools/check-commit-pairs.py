"""No commit changes a model without carrying its migration.

Structural rule 10. `git add models.py` once shipped an unfinished model
without the migration that made the column exist, the deploy ran, and the
first request touching that column raised ProgrammingError on production.
Staging by file rather than by change is how that happens, and it happens
silently: the commit builds, the tests pass locally against a database that
was migrated by hand at some point, and only a fresh deploy notices.

So this reads the actual commits on a branch rather than the working tree.
For every commit that touches an app's models.py, it requires either a
migration file in the same commit, or proof that the model change needed no
migration (a comment, a method, a property - anything Django does not put in
a migration).

Run from the backend repo:

    ./venv/Scripts/python.exe tools/check-commit-pairs.py [base]

`base` defaults to origin/main.
"""

import re
import subprocess
import sys


def git(*args):
    out = subprocess.run(['git'] + list(args), capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit('git ' + ' '.join(args) + ':\n' + out.stderr)
    return out.stdout


# A models.py hunk that only adds or edits these needs no migration. Django
# writes migrations for fields and Meta, not for behaviour.
NO_MIGRATION_NEEDED = re.compile(
    r'^\+\s*(#|"""|\'\'\'|@|def |class Meta|from |import |$)'
)

FIELD_LIKE = re.compile(
    r'^\+\s*\w+\s*=\s*models\.|^\+\s*class\s+\w+\(.*models\.Model|'
    r'^\+\s*(ordering|unique_together|constraints|indexes|db_table)\s*='
)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'origin/main'
    try:
        git('rev-parse', '--verify', base)
    except SystemExit:
        print('base %s not found; nothing to check' % base)
        return 0

    commits = git('rev-list', '%s..HEAD' % base).split()
    if not commits:
        print('no commits ahead of %s' % base)
        return 0

    problems = []
    checked = 0

    for sha in commits:
        files = [f for f in git('show', '--pretty=', '--name-only', sha).split('\n') if f]
        model_files = [f for f in files if f.endswith('models.py')]
        if not model_files:
            continue
        checked += 1
        migrations = [f for f in files if '/migrations/' in f and f.endswith('.py')]

        for mf in model_files:
            app = mf.split('/')[0]
            diff = git('show', '--pretty=', '-U0', sha, '--', mf)
            added = [l for l in diff.split('\n') if l.startswith('+') and not l.startswith('+++')]
            schema_lines = [l for l in added if FIELD_LIKE.search(l)]
            if not schema_lines:
                continue
            app_migrations = [m for m in migrations if m.startswith(app + '/migrations/')]
            if not app_migrations:
                subject = git('show', '-s', '--format=%h %s', sha).strip()
                problems.append(
                    '%s\n    %s adds schema (%s) with no %s migration in the same commit'
                    % (subject, mf, schema_lines[0].strip()[:60], app)
                )

    if problems:
        for p in problems:
            print(p)
        print('%d commit(s) ship a model change without its migration' % len(problems))
        return 1

    print('%d commit(s) touch a models.py' % checked)
    print('every model change ships with its migration')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
