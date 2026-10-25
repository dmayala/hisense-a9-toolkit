"""Exact temporary Glimpse cache files; never enable apps or grant permissions."""
import re
import shlex

COMPONENT = 'org.lineageos.glimpse/org.lineageos.glimpse.ViewActivity'
CACHE = '/data/user/0/org.lineageos.glimpse/cache'
ARGV = ['am', 'start', '-W', '-n', COMPONENT, '-a', 'android.intent.action.VIEW',
        '-t', 'image/png', '-d', '{uri}']


def paths(token):
    if not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('invalid source token')
    return {base + '/ghost-matched-%s-%s.png' % (token, sid)
            for base in ('/data/local/tmp', CACHE) for sid in ('dark', 'white')}


def root(text):
    q = shlex.quote(text)
    # The mainline socket already runs as root; stock's adb shell needs su.
    return 'if [ "$(id -u)" = 0 ]; then sh -c %s; else su -c %s; fi' % (q, q)


def scoped(path, text):
    return root(text) if path.startswith(CACHE + '/') else text


def absent(path, absent='ABSENT', present='EXISTS'):
    q = shlex.quote(path)
    return scoped(path, 'if [ -e %s ] || [ -L %s ]; then echo %s; else echo %s; fi' % (q, q, present, absent))


def remove(path):
    return scoped(path, 'rm -f -- ' + shlex.quote(path))


def stage(source, target):
    # Existing app cache only. Never create a directory, overwrite an existing
    # path, modify package state, copy unrelated data or relabel a directory.
    parent = shlex.quote(CACHE)
    src, dst = shlex.quote(source), shlex.quote(target)
    return root('set -eu; test -d %s; test ! -L %s; test ! -e %s; test ! -L %s; '
                'owner=$(stat -c %%u:%%g %s); context=$(stat -c %%C %s); '
                'case "$owner" in *[!0-9:]*|"") exit 31;; esac; '
                'case "$context" in u:object_r:app_data_file:s0*) ;; *) exit 32;; esac; '
                'set -C; cat %s > %s; chown "$owner" %s; chmod 0600 %s; chcon "$context" %s; '
                'test "$(stat -c %%u:%%g %s)" = "$owner"; test "$(stat -c %%C %s)" = "$context"; '
                'sha256sum %s' % (parent, parent, dst, dst, parent, parent, src, dst, dst, dst, dst, dst, dst, dst))
