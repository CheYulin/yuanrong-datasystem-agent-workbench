"""Create explicit file relocations after extraction without rewriting original evidence."""
import argparse
import json
from pathlib import Path

from kvcbench.platform.files import atomic_json, identity, regular, real_directory


def materialize(root, template, out):
    root = real_directory(root)
    record = json.loads(regular(root, template).read_text())
    if record.get('schema_version') != 1:
        raise ValueError('Unsupported portable report template')
    spec = record['inputs_spec']
    relocations = []
    for item in record['files']:
        source = regular(root, item['package_path'])
        original = item['original']
        if identity(source) != {key: original[key] for key in ('sha256', 'bytes', 'mode')}:
            raise ValueError('Extracted input bytes/mode differ; preserve original Unix modes')
        relocations.append({'original': original,
                            'local': {'root': str(source.parent), 'path': source.name, **identity(source)}})
    spec['relocations'] = relocations
    spec['relocation_scope'] = 'Explicit references to identical archived files; original analysis/audit/input bytes unchanged'
    atomic_json(out, spec, exclusive=True)
    return spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--template', default='report-inputs.template.json')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    materialize(args.root, args.template, args.out)


if __name__ == '__main__':
    main()
