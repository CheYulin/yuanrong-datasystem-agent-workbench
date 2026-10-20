"""Materialize a recorded compiler driver for legacy third-party header compatibility."""
import argparse
import json
import os
from pathlib import Path
import shlex

from kvcbench.platform.files import atomic_json, identity, regular


def prepare(root, compiler, c_compiler=None):
    root = Path(root).resolve(strict=True)
    compiler = Path(compiler).resolve(strict=True)
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise ValueError('Executable C++ compiler required')
    if c_compiler is not None:
        c_compiler = Path(c_compiler).resolve(strict=True)
        if not c_compiler.is_file() or not os.access(c_compiler, os.X_OK):
            raise ValueError('Executable C compiler required')
    directory = root / 'compiler-inputs/datasystem'
    directory.mkdir(parents=True, exist_ok=False)
    wrapper = directory / 'g++'
    options = ['-include', 'cstdint', '-include', 'algorithm']
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([str(compiler), *options]) + ' "$@"\n')
    wrapper.chmod(0o755)
    record = {'schema_version': 1, 'product': 'datasystem',
              'driver': {'path': str(compiler), **identity(compiler)}, 'prefix_arguments': options,
              'wrapper': {'path': str(wrapper.relative_to(root)), **identity(wrapper)},
              'scope': 'C++ header preincludes for GCC 15 compatibility; native feature and optimization options unchanged'}
    atomic_json(directory / 'record.json', record, exclusive=True)
    if c_compiler is not None:
        c_wrapper = directory / 'gcc'
        c_options = ['-std=gnu17']
        c_wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([str(c_compiler), *c_options]) + ' "$@"\n')
        c_wrapper.chmod(0o755)
        c_record = {'schema_version': 1, 'product': 'datasystem',
                    'driver': {'path': str(c_compiler), **identity(c_compiler)}, 'prefix_arguments': c_options,
                    'wrapper': {'path': str(c_wrapper.relative_to(root)), **identity(c_wrapper)},
                    'scope': 'Explicit C17 for legacy Autoconf checks; compiler optimization and native features unchanged'}
        atomic_json(directory / 'c-record.json', c_record, exclusive=True)
    return record


def verify(root):
    root = Path(root)
    record = json.loads(regular(root, 'compiler-inputs/datasystem/record.json').read_text())
    if (record.get('schema_version') != 1 or record.get('product') != 'datasystem'
            or record.get('prefix_arguments') != ['-include', 'cstdint', '-include', 'algorithm']
            or record.get('wrapper', {}).get('path') != 'compiler-inputs/datasystem/g++'
            or not Path(record['driver']['path']).is_absolute()):
        raise ValueError('Unexpected compiler compatibility contract')
    for key, path in [('driver', Path(record['driver']['path'])),
                      ('wrapper', regular(root, record['wrapper']['path']))]:
        observed = identity(path)
        if any(observed[field] != record[key][field] for field in ('sha256', 'bytes', 'mode')):
            raise ValueError('Compiler input changed: ' + key)
    project_cache = root / 'datasystem/build/CMakeCache.txt'
    cache = project_cache if project_cache.is_file() else root / 'datasystem/build/_deps/rocksdb-build/CMakeCache.txt'
    compiler_path = str(root / record['wrapper']['path'])
    if not any(line.startswith('CMAKE_CXX_COMPILER:') and line.split('=', 1)[-1] == compiler_path
               for line in cache.read_text(errors='surrogateescape').splitlines()):
        raise ValueError('DataSystem CMake did not use the recorded compatibility compiler driver')
    inputs = [root / record['wrapper']['path'], root / 'compiler-inputs/datasystem/record.json']
    c_path = root / 'compiler-inputs/datasystem/c-record.json'
    if c_path.exists():
        c_record = json.loads(c_path.read_text())
        if (c_record.get('schema_version') != 1 or c_record.get('product') != 'datasystem'
                or c_record.get('prefix_arguments') != ['-std=gnu17']
                or c_record.get('wrapper', {}).get('path') != 'compiler-inputs/datasystem/gcc'):
            raise ValueError('Unexpected C compiler compatibility contract')
        for key, path in [('driver', Path(c_record['driver']['path'])),
                          ('wrapper', regular(root, c_record['wrapper']['path']))]:
            if identity(path) != {field: c_record[key][field] for field in ('sha256', 'bytes', 'mode')}:
                raise ValueError('C compiler input changed: ' + key)
        config = root / 'datasystem/build/_deps/iconv-src/config.log'
        if config.is_file():
            c_verified = "CC='" + str(root / c_record['wrapper']['path']) + "'" in config.read_text(errors='surrogateescape')
        else:
            c_verified = project_cache.is_file() and any(
                line.startswith('CMAKE_C_COMPILER:') and line.split('=', 1)[-1] == str(root / c_record['wrapper']['path'])
                for line in project_cache.read_text(errors='surrogateescape').splitlines())
        if not c_verified:
            raise ValueError('iconv or DataSystem CMake did not use the recorded C17 driver')
        inputs.extend([c_path, root / c_record['wrapper']['path']])
    return {str(path.relative_to(root)): identity(path) for path in inputs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--c-compiler', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.compiler, args.c_compiler)))


if __name__ == '__main__':
    main()
