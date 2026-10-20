"""Package report files and unchanged evidence for offline reading and regeneration."""
import json
from pathlib import Path
import zipfile

from kvcbench.platform.files import digest


def package(out, spec, evidence, *, diagnostic=False):
    out = Path(out)
    source_zip = out / 'provenance/source-bundle.zip'
    references = {}
    def remember(original, relative):
        key = json.dumps(original, sort_keys=True, separators=(',', ':'))
        references.setdefault(key, {'original': original, 'package_path': 'report/' + relative})
    for name, original in spec['inputs'].items():
        remember(original, 'provenance/' + name + '.json')
    for original in spec.get('documents', []):
        remember(original, 'docs/' + Path(original['path']).name)
    remember(spec['source_bundle'], 'provenance/source-bundle.zip')
    for row in evidence:
        if 'source' in row:
            remember(row['source'], row['destination'])
    original_spec = {key: value for key, value in spec.items() if key not in {'relocations', 'relocation_scope'}}
    template = {'schema_version': 1, 'diagnostic': diagnostic, 'inputs_spec': original_spec,
                'files': list(references.values())}
    files = [path for path in sorted(out.rglob('*')) if path.is_file()
             and path.relative_to(out).as_posix() not in {'reproduce.zip', 'SHA256SUMS'}]
    checksums = {path.relative_to(out).as_posix(): digest(path) for path in files}
    checksums['reproduce.zip'] = digest(source_zip)
    with zipfile.ZipFile(out / 'reproduce.zip', 'a', compression=zipfile.ZIP_DEFLATED) as archive:
        if any(name.startswith('kvc-fair/report/') for name in archive.namelist()):
            raise ValueError('The source ZIP already contains a rendered report; use its original source bundle')
        for path in files:
            archive.write(path, 'kvc-fair/report/' + path.relative_to(out).as_posix())
        for row in evidence:
            if 'directory_mode' in row:
                archive.write(out / row['destination'], 'kvc-fair/report/' + row['destination'] + '/')
        archive.write(source_zip, 'kvc-fair/report/reproduce.zip')
        def text(name, content):
            info = zipfile.ZipInfo('kvc-fair/' + name)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED)
        text('report/SHA256SUMS', ''.join(checksum + '  ' + name + '\n' for name, checksum in sorted(checksums.items())))
        text('report-inputs.template.json', json.dumps(template, ensure_ascii=False, indent=2) + '\n')
        flag = ' --diagnostic' if diagnostic else ''
        fence = chr(96) * 3
        text('REPRODUCE-REPORT.md', '# 报告离线复现\n\n直接打开 report/index.html 可离线查看图表和文档，不启动产品。\n\n'
             '重新生成：使用保留 Unix 权限的解压工具，在本目录安装 requirements-report.txt 中的固定依赖，然后执行：\n\n'
             + fence + 'bash\npython3 -m kvcbench.report.relocate --root \"$PWD\" --out /tmp/kvc-inputs-new.json\n'
             'python3 -m kvcbench.report.render --inputs /tmp/kvc-inputs-new.json --out /tmp/kvc-report-new' + flag + '\n' + fence + '\n\n'
             '选择全新输出路径。relocate 仅生成文件位置映射，校验归档字节和 mode，原始分析、审计和原件不改写。'
             '归档 report/reproduce.zip 是原始代码包；外层 ZIP 额外包含此报告和证据快照。\n')
    return out / 'reproduce.zip'
