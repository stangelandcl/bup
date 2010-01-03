#!/usr/bin/env python
import sys, time, re, struct
import hashsplit, git, options
from helpers import *

optspec = """
bup split [-tcb] [-n name] [--bench] [filenames...]
--
r,remote=  remote repository path
b,blobs    output a series of blob ids
t,tree     output a tree id
c,commit   output a commit id
n,name=    name of backup set to update (if any)
v,verbose  increase log output (can be used more than once)
bench      print benchmark timings to stderr
"""
o = options.Options('bup split', optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

git.check_repo_or_die()
if not (opt.blobs or opt.tree or opt.commit or opt.name):
    log("bup split: use one or more of -b, -t, -c, -n\n")
    o.usage()

hashsplit.split_verbosely = opt.verbose
if opt.verbose >= 2:
    git.verbose = opt.verbose - 1
    opt.bench = 1

start_time = time.time()

def server_connect(remote):
    rs = remote.split(':', 1)
    if len(rs) == 1:
        (host, dir) = ('NONE', remote)
        p = subprocess.Popen(['bup', 'server'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    else:
        (host, dir) = rs
        p = subprocess.Popen(['ssh', host, '--', 'bup', 'server'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    conn = Conn(p.stdout, p.stdin)
    dir = re.sub(r'[\r\n]', ' ', dir)
    conn.write('set-dir %s\n' % dir)
    conn.check_ok()
    
    conn.write('list-indexes\n')
    cachedir = git.repo('index-cache/%s' % re.sub(r'[^@:\w]', '_',
                                                  "%s:%s" % (host, dir)))
    packdir = git.repo('objects/pack')
    mkdirp(cachedir)
    all = {}
    needed = {}
    for line in linereader(conn):
        if not line:
            break
        all[line] = 1
        assert(line.find('/') < 0)
        if (not os.path.exists(os.path.join(cachedir, line)) and
            not os.path.exists(os.path.join(packdir, line))):
                needed[line] = 1
    conn.check_ok()
                
    for f in os.listdir(cachedir):
        if f.endswith('.idx') and not f in all:
            log('pruning old index: %r\n' % f)
            os.unlink(os.path.join(cachedir, f))
            
    # FIXME this should be pipelined: request multiple indexes at a time, or
    # we waste lots of network turnarounds.
    for name in needed.keys():
        log('requesting %r\n' % name)
        conn.write('send-index %s\n' % name)
        n = struct.unpack('!I', conn.read(4))[0]
        assert(n)
        log('   expect %d bytes\n' % n)
        fn = os.path.join(cachedir, name)
        f = open(fn + '.tmp', 'w')
        for b in chunkyreader(conn, n):
            f.write(b)
        conn.check_ok()
        f.close()
        os.rename(fn + '.tmp', fn)
    return (p, conn, cachedir)

if opt.remote:
    (p, conn, cachedir) = server_connect(opt.remote)
    conn.write('receive-objects\n')
    w = git.PackWriter_Remote(conn, objcache = git.MultiPackIndex(cachedir))
else:
    w = git.PackWriter()
    
(shalist,tree) = hashsplit.split_to_tree(w, hashsplit.autofiles(extra))

if opt.verbose:
    log('\n')
if opt.blobs:
    for (mode,name,bin) in shalist:
        print bin.encode('hex')
if opt.tree:
    print tree.encode('hex')
if opt.commit or opt.name:
    msg = 'bup split\n\nGenerated by command:\n%r' % sys.argv
    ref = opt.name and ('refs/heads/%s' % opt.name) or None
    commit = w.new_commit(ref, tree, msg)
    if opt.commit:
        print commit.encode('hex')

if opt.remote:
    w.close()
    p.stdin.write('quit\n')
    p.wait()

secs = time.time() - start_time
size = hashsplit.total_split
if opt.bench:
    log('\nbup: %.2fkbytes in %.2f secs = %.2f kbytes/sec\n'
        % (size/1024., secs, size/1024./secs))
