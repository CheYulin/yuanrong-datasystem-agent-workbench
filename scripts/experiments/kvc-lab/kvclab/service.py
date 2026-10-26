"""HTTP facade; execution belongs to the independent controller process."""
import json
from pathlib import Path
import re
import tempfile
from . import spec
from .store import Store, TERMINAL
from .acceptance import reconcile
from datetime import datetime, timezone


class LabService:
    def __init__(self, settings):
        self.settings = settings
        self.csrf_secret = settings['csrf_secret']
        self.root = Path(settings['state_root'])
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root / 'jobs.sqlite3')

    def catalog(self, user):
        return {**spec.catalog(), 'user': 'yche.me 管理账户', 'deployment_scope': '单物理机、顺序运行产品',
                'guide_url': '/site-api/kvc-lab/ui/guide.html',
                'links': [{'title': '同机基础性能与扩展实验报告', 'url': '/site-api/kvc-lab/ui/study.html'}, {'title': '构建与用例设计文档网页', 'url': '/site-api/kvc-lab/ui/docs/'}, {'title': '安装源码包', 'url': '/site-api/kvc-lab/source/source.tar.gz'},
                          {'title': '源码包SHA与文件清单', 'url': '/site-api/kvc-lab/source/source.json'}]}

    def preview_plan(self, config):
        return spec.preview_plan(config)

    def create_job(self, user, action, config, idempotency_key):
        if action not in ('deploy', 'run', 'stop'):
            raise ValueError('未知动作')
        plan = spec.preview_plan({} if action == 'stop' else config or {})
        job_id = self.store.submit(user, action, plan['config'], plan, idempotency_key)
        return self.store.get(job_id, user)

    def state(self, user):
        return dict(deployment=self.store.deployment(), jobs=[self.decorate(job) for job in self.store.recent(user)], cleanup_required=self.store.cleanup_required())

    def get_job(self, user, job_id):
        try:
            return self.decorate(self.store.get(job_id, user))
        except KeyError as error:
            raise FileNotFoundError('任务不存在') from error

    @staticmethod
    def remote_artifacts(job):
        import hashlib
        names = [step.get('artifact') for step in job.get('steps', [])]
        names += [row.get('raw_artifact') for row in job.get('results', [])]
        names += [row.get('name') for row in job.get('completion', {}).get('artifacts', []) if row.get('downloadable')]
        return {'raw-' + hashlib.sha256(name.encode()).hexdigest()[:20] + (Path(name).suffix if Path(name).suffix in {'.json', '.jsonl', '.log', '.bt', '.txt', '.gz'} else '.bin'): name
                for name in names if isinstance(name, str) and name and not name.startswith('/')
                and all(part not in ('', '.', '..') for part in name.split('/'))}

    def decorate(self, job):
        if job['action'] == 'run':
            checked = reconcile(job['plan'], job.get('results', []), include_missing=job['status'] in TERMINAL)
            job['results'], job['coverage'] = checked['rows'], checked['coverage']
        for field in ['created', 'updated']:
            job[field + '_at'] = datetime.fromtimestamp(job[field], timezone.utc).isoformat()
        mapping = self.remote_artifacts(job)
        reverse = {value: key for key, value in mapping.items()}
        base = '/site-api/kvc-lab/jobs/' + job['id'] + '/artifacts/'
        job['artifacts'] = [{'name': name, 'title': label, 'url': base + name} for name, label in
                            [('results.json', '结果JSON'), ('steps.json', '逐步回执'),
                             ('events.jsonl', '事件日志'), ('reproduce.sh', '复现脚本')]]
        if (job.get('provenance') or {}).get('source_sha256'):
            job['artifacts'].append({'name': 'source.tar.gz', 'title': '本任务安装源码包', 'url': base + 'source.tar.gz'})
        for name, original in mapping.items():
            if original.endswith(('resources.jsonl', 'observe-iostat.log', 'observe-mpstat.log', 'observe-pidstat.log', 'observe-dstat.log', 'commands.json', 'probe.log', 'probe.bt', 'resources.jsonl.gz')):
                job['artifacts'].append({'name': name, 'title': original, 'url': base + name})
        for step in job.get('steps', []):
            if step.get('artifact') in reverse:
                step['artifact_url'] = base + reverse[step['artifact']]
        for row in job.get('results', []):
            if row.get('raw_artifact') in reverse:
                row['raw_url'] = base + reverse[row['raw_artifact']]
        return job

    def get_artifact(self, user, job_id, name):
        job = self.get_job(user, job_id)
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise FileNotFoundError('任务不存在')
        root = self.root / 'jobs' / job_id
        if name == 'source.tar.gz':
            return self.source_file((job.get('provenance') or {}).get('source_sha256'), '.tar.gz'), name
        choices = {'results.json': job['results'], 'steps.json': job['steps'],
                   'job.json': {k: job[k] for k in ('id', 'action', 'status', 'config', 'plan', 'created', 'updated', 'error')}}
        if name in choices:
            root.mkdir(parents=True, exist_ok=True)
            path = root / name
            with tempfile.NamedTemporaryFile(mode='w', dir=root, suffix='.tmp', delete=False) as stream:
                stream.write(json.dumps(choices[name], ensure_ascii=False, indent=2) + '\n')
                temporary = Path(stream.name)
            temporary.replace(path)
            return path, name
        if name in ('events.jsonl', 'reproduce.sh', 'remote-state.json'):
            path = root / name
            if path.is_file() and not path.is_symlink():
                return path, name
        mapping = self.remote_artifacts(job)
        if name in mapping:
            import base64
            import hashlib
            from .transport import rpc
            result = rpc(self.settings, 'artifact', {'job_id': job_id, 'name': mapping[name]})
            payload = base64.b64decode(result['data'], validate=True)
            if len(payload) != result['size'] or hashlib.sha256(payload).hexdigest() != result['sha256']:
                raise RuntimeError('远端产物传输校验失败')
            root.mkdir(parents=True, exist_ok=True)
            path = root / name
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
                stream.write(payload)
                temporary = Path(stream.name)
            temporary.replace(path)
            return path, name
        raise FileNotFoundError('产物尚未产生或不在下载白名单')

    def source_file(self, identity, suffix):
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{64}', identity):
            raise FileNotFoundError('当前记录没有绑定源码包')
        path = self.root / 'sources' / (identity + suffix)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError('源码包尚未安装')
        return path

    def get_source(self, name):
        suffix = {'source.tar.gz': '.tar.gz', 'source.json': '.json'}.get(name)
        if suffix is None:
            raise FileNotFoundError('下载名称不在白名单')
        return self.source_file(self.settings.get('source_sha256'), suffix), name
