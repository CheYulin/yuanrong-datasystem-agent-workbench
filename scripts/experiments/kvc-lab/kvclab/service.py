"""HTTP facade; execution belongs to the independent controller process."""
import json
from pathlib import Path
import re
import tempfile
from . import spec
from .store import Store


class LabService:
    def __init__(self, settings):
        self.settings = settings
        self.csrf_secret = settings['csrf_secret']
        self.root = Path(settings['state_root'])
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root / 'jobs.sqlite3')

    def catalog(self, user):
        return {**spec.catalog(), 'user': 'yche.me 管理账户', 'deployment_scope': '单物理机、顺序运行产品',
                'guide_url': '/site-api/kvc-lab/ui/guide.html'}

    def preview_plan(self, config):
        return spec.preview_plan(config)

    def create_job(self, user, action, config, idempotency_key):
        if action not in ('deploy', 'run', 'stop'):
            raise ValueError('未知动作')
        plan = spec.preview_plan(config or {})
        job_id = self.store.submit(user, action, plan['config'], plan, idempotency_key)
        return self.store.get(job_id, user)

    def state(self, user):
        return dict(deployment=self.store.deployment(), jobs=self.store.recent(user))

    def get_job(self, user, job_id):
        try:
            return self.store.get(job_id, user)
        except KeyError as error:
            raise FileNotFoundError('任务不存在') from error

    def get_artifact(self, user, job_id, name):
        job = self.get_job(user, job_id)
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise FileNotFoundError('任务不存在')
        root = self.root / 'jobs' / job_id
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
        raise FileNotFoundError('产物尚未产生或不在下载白名单')
