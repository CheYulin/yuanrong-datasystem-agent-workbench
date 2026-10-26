#!/usr/bin/env python3
"""Register only the authenticated KVC control entry; dry-run is the default.

Use --host xqyun-32c32g to inspect the live site through its existing SSH alias.
Review plan_sha256, then pass it with --apply --expect-plan-sha256. Apply keeps
private recovery copies, uses the shared publication lock, and compares all four
source files and the target page_meta row before writing. It never pushes Git.

page_meta.is_restricted controls homepage presentation. Actual authorization is
the existing Flask lab-prefix gate, including UI assets, API and downloads.
"""
import argparse
from datetime import date
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid


PAGE = 'site-api/kvc-lab/ui/'
HOMEPAGE = 'index.html'
METADATA = 'assets/js/document-index-20261011.json'
FILES = (HOMEPAGE, METADATA, HOMEPAGE + '.gz', METADATA + '.gz')
TITLE = 'DataSystem × Mooncake：同机部署与性能实验控制台'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def insert_home(content, entry):
    text = content.decode('utf-8')
    matches = list(re.finditer(r'var P\s*=\s*', text))
    if len(matches) != 1:
        raise ValueError('Expected exactly one homepage P array')
    begin = matches[0].end()
    pages, _ = json.JSONDecoder(object_pairs_hook=unique_object).raw_decode(text[begin:])
    if not isinstance(pages, list) or not all(isinstance(row, dict) for row in pages):
        raise ValueError('Homepage P must be an array of objects')
    aliases = {PAGE, '/' + PAGE, PAGE + 'index.html', '/' + PAGE + 'index.html'}
    existing = [row for row in pages if row.get('p') in aliases]
    if existing:
        if existing != [entry]:
            raise ValueError('Different or duplicated control-page registration already exists')
        return content, len(pages), False
    insertion = '\n' + json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + (',' if pages else '')
    position = begin + 1
    updated = text[:position] + insertion + text[position:]
    checked, _ = json.JSONDecoder(object_pairs_hook=unique_object).raw_decode(updated[begin:])
    if checked != [entry] + pages or updated[:position] + updated[position + len(insertion):] != text:
        raise AssertionError('Unrelated homepage content changed')
    return updated.encode(), len(pages), True


def insert_metadata(content, item):
    text = content.decode('utf-8')
    data = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError('Document metadata must be an object')
    if PAGE in data:
        if data[PAGE] != item:
            raise ValueError('Different control-page document metadata already exists')
        return content, False
    position = text.index('{') + 1
    insertion = '\n' + json.dumps(PAGE) + ':' + json.dumps(item, ensure_ascii=False, separators=(',', ':')) + (',' if data else '')
    updated = text[:position] + insertion + text[position:]
    checked = json.loads(updated, object_pairs_hook=unique_object)
    if checked.pop(PAGE) != item or checked != data or updated[:position] + updated[position + len(insertion):] != text:
        raise AssertionError('Unrelated document metadata changed')
    return updated.encode(), True


def file_snapshot(site):
    result = {}
    for name in FILES:
        path = site / name
        if path.is_symlink() or path.parent.resolve() != path.parent:
            raise ValueError('Registration paths cannot be symlink aliases')
        if path.exists():
            if not path.is_file():
                raise ValueError('Registration source is not a regular file')
            before = path.stat()
            content = path.read_bytes()
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise RuntimeError('Registration source changed while reading')
            result[name] = dict(content=content, sha256=digest(content), mode=stat.S_IMODE(after.st_mode))
        else:
            if not name.endswith('.gz'):
                raise FileNotFoundError('Missing registration source: ' + name)
            result[name] = None
    return result


def db_snapshot(connection):
    connection.row_factory = sqlite3.Row
    schema = [dict(row) for row in connection.execute('PRAGMA table_info(page_meta)')]
    columns = {row['name']: row for row in schema}
    if not {'page_path', 'is_hidden', 'is_restricted'} <= columns.keys() or columns['page_path']['pk'] != 1:
        raise ValueError('Unsupported page_meta schema')
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='page_meta'").fetchone():
        raise ValueError('Refusing page_meta triggers with unknown side effects')
    row = connection.execute('SELECT * FROM page_meta WHERE page_path=?', (PAGE,)).fetchone()
    record = dict(row) if row else None
    return dict(schema=schema, row=record, schema_sha256=digest(canonical(schema)), row_sha256=digest(canonical(record)))


def readonly_db(path):
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        raise ValueError('Database must be an existing regular file without aliases')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15)
    try:
        return db_snapshot(connection)
    finally:
        connection.close()


def public_files(snapshot):
    return {name: {key: value[key] for key in ('sha256', 'mode')} if value else None for name, value in snapshot.items()}


def build_plan(site, database, published_date):
    sources = file_snapshot(site)
    before_db = readonly_db(database)
    entry = dict(t=TITLE, p=PAGE, d=published_date, u=published_date, c='ops', g='tools',
                 e='管理员登录后控制 tiantiyun 同机部署、PUT/GET/EXIST 与资源扩展实验，保留逐步验收和原始结果。',
                 tg=['DataSystem', 'Mooncake', '部署控制', '同机扩展', '受保护入口'])
    metadata = dict(c='ops', g='tools', group='运维工具', d=published_date, u=published_date, title=TITLE, parent=None)
    home, count, home_added = insert_home(sources[HOMEPAGE]['content'], entry)
    meta, metadata_added = insert_metadata(sources[METADATA]['content'], metadata)
    outputs = {HOMEPAGE: home, METADATA: meta}
    for name in (HOMEPAGE, METADATA):
        companion = sources[name + '.gz']
        try:
            matches = companion and gzip.decompress(companion['content']) == outputs[name]
        except (OSError, EOFError):
            matches = False
        outputs[name + '.gz'] = companion['content'] if matches else gzip.compress(outputs[name], mtime=0)
    plan = dict(schema_version=1, page=PAGE, site=str(site), database=str(database), date=published_date,
                entry=entry, metadata=metadata, pages_before=count, pages_after=count + int(home_added),
                homepage_added=home_added, metadata_added=metadata_added,
                files_before=public_files(sources),
                files_after={name: dict(sha256=digest(content), mode=(sources[name] or {}).get('mode', 0o644)) for name, content in outputs.items()},
                database_before=dict(schema_sha256=before_db['schema_sha256'], row_sha256=before_db['row_sha256'],
                                     row_exists=before_db['row'] is not None,
                                     is_restricted=before_db['row']['is_restricted'] if before_db['row'] else None),
                database_after=dict(page_path=PAGE, is_restricted=1),
                database_columns=[row['name'] for row in before_db['schema']],
                access_control='page_meta is homepage UI metadata only; existing Flask lab-prefix authorization remains authoritative')
    current = file_snapshot(site)
    current_db = readonly_db(database)
    if (public_files(current) != public_files(sources)
            or current_db['row_sha256'] != before_db['row_sha256']
            or current_db['schema_sha256'] != before_db['schema_sha256']):
        raise RuntimeError('Baseline changed during plan calculation; rerun against fresh content')
    return plan, sources, outputs, before_db


def atomic(path, content, mode):
    handle, temporary = tempfile.mkstemp(prefix='.kvc-register-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_plan(args, plan, sources, outputs, before_db):
    plan_sha256 = digest(canonical(plan))
    if not args.expect_plan_sha256 or args.expect_plan_sha256 != plan_sha256:
        raise RuntimeError('Fresh plan differs from reviewed --expect-plan-sha256; run dry-run again')
    if public_files(file_snapshot(args.site)) != public_files(sources):
        raise RuntimeError('Source CAS failed before apply')
    connection = sqlite3.connect(args.database.as_uri() + '?mode=rw', uri=True, timeout=15)
    changed = []
    backup = args.backup_dir / (time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex)
    committed = False
    try:
        connection.execute('BEGIN IMMEDIATE')
        current = db_snapshot(connection)
        if current['schema_sha256'] != before_db['schema_sha256'] or current['row_sha256'] != before_db['row_sha256']:
            raise RuntimeError('Target page_meta row CAS failed')
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(backup, 0o700)
        for name, value in sources.items():
            if value is not None:
                target = backup / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                atomic(target, value['content'], 0o600)
        atomic(backup / 'database-row-before.json', canonical(before_db['row']) + b'\n', 0o600)
        atomic(backup / 'plan.json', canonical(plan) + b'\n', 0o600)
        if before_db['row'] is None:
            connection.execute('INSERT INTO page_meta(page_path,is_restricted) VALUES (?,1)', (PAGE,))
        elif before_db['row']['is_restricted'] != 1:
            connection.execute('UPDATE page_meta SET is_restricted=1 WHERE page_path=?', (PAGE,))
        for name in FILES:
            expected = sources[name]
            current = file_snapshot(args.site)
            for other in FILES:
                wanted = digest(outputs[other]) if other in changed else (sources[other] or {}).get('sha256')
                mode = (sources[other] or {}).get('mode', 0o644) if other in changed else (sources[other] or {}).get('mode')
                if (current[other] or {}).get('sha256') != wanted or (current[other] or {}).get('mode') != mode:
                    raise RuntimeError('File CAS failed during apply; rollback will preserve concurrent changes')
            if not expected or expected['content'] != outputs[name]:
                atomic(args.site / name, outputs[name], (expected or {}).get('mode', 0o644))
                changed.append(name)
        if connection.execute('SELECT is_restricted FROM page_meta WHERE page_path=?', (PAGE,)).fetchone()[0] != 1:
            raise RuntimeError('Restriction flag verification failed')
        for name in (HOMEPAGE, METADATA):
            if gzip.decompress((args.site / (name + '.gz')).read_bytes()) != (args.site / name).read_bytes():
                raise RuntimeError('Published gzip is not synchronized')
        connection.commit()
        committed = True
        result = dict(applied=True, changed_files=changed, backup_directory=str(backup), database_restricted=True)
        try:
            atomic(backup / 'complete.json', canonical(dict(complete=True, plan_sha256=plan_sha256, changed_files=changed)) + b'\n', 0o600)
        except OSError:
            result['completion_receipt_written'] = False
        return result
    except BaseException:
        if not committed:
            connection.rollback()
            failures = []
            for name in reversed(changed):
                path = args.site / name
                if not path.is_file() or digest(path.read_bytes()) != digest(outputs[name]):
                    failures.append(name)
                    continue
                original = sources[name]
                if original is None:
                    path.unlink()
                else:
                    atomic(path, original['content'], original['mode'])
            if failures:
                raise RuntimeError('Concurrent file changes preserved; manual recovery required from private backup: ' + str(backup)) from None
        raise
    finally:
        connection.close()


def execute(args):
    args.site = args.site.absolute()
    args.database = (args.database or args.site / 'server/site.db').absolute()
    if args.site.resolve() != args.site or not args.site.is_dir():
        raise ValueError('Site root must be an existing real directory')
    if not args.apply:
        plan, _, _, _ = build_plan(args.site, args.database, args.date)
        return dict(dry_run=True, applied=False, plan_sha256=digest(canonical(plan)), plan=plan)
    if not args.expect_plan_sha256:
        raise ValueError('--apply requires a reviewed --expect-plan-sha256')
    args.backup_dir = args.backup_dir.absolute()
    if (args.backup_dir.resolve() != args.backup_dir or args.backup_dir == args.site
            or args.site in args.backup_dir.parents):
        raise ValueError('Private backups must be outside the public site without aliases')
    handle = os.open(args.lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(handle, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan, sources, outputs, before_db = build_plan(args.site, args.database, args.date)
        result = apply_plan(args, plan, sources, outputs, before_db)
        return dict(dry_run=False, plan_sha256=digest(canonical(plan)), plan=plan, **result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', help='Existing SSH alias; omitted when run on the publication host')
    parser.add_argument('--site', type=Path, default=Path('/var/www/html'))
    parser.add_argument('--database', type=Path)
    parser.add_argument('--date', required=True, type=lambda value: date.fromisoformat(value).isoformat())
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expect-plan-sha256')
    parser.add_argument('--backup-dir', type=Path, default=Path('/var/lib/kvc-lab/registration'))
    parser.add_argument('--lock', type=Path, default=Path('/tmp/kvc-yche-publish.lock'))
    parser.add_argument('--record', type=Path, help='Fresh local evidence file; never replaced')
    args = parser.parse_args()
    if args.record and (args.record.exists() or args.record.is_symlink()):
        raise ValueError('--record must be a fresh local path')
    if args.host:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.host):
            raise ValueError('--host must be a trusted existing SSH alias')
        command = ['python3', '-B', '-', '--site', str(args.site), '--date', args.date,
                   '--backup-dir', str(args.backup_dir), '--lock', str(args.lock)]
        if args.database:
            command += ['--database', str(args.database)]
        if args.apply:
            command += ['--apply']
        if args.expect_plan_sha256:
            command += ['--expect-plan-sha256', args.expect_plan_sha256]
        remote = subprocess.run(['ssh', '-o', 'BatchMode=yes', args.host, shlex.join(command)],
                                input=Path(__file__).read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            result = json.loads(remote.stdout)
        except (ValueError, UnicodeError):
            raise RuntimeError('Remote registration did not return JSON; SSH diagnostics withheld') from None
        if remote.returncode:
            raise RuntimeError(result.get('error', 'Remote registration failed'))
    else:
        result = execute(args)
    if args.record:
        try:
            with args.record.open('x') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
        except OSError:
            result['local_record_written'] = False
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(error=str(error), publication_state='Not confirmed; inspect fresh dry-run before retry'), ensure_ascii=False))
        sys.exit(1)
