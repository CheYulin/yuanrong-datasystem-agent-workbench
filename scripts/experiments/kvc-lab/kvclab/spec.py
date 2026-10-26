"""Bounded declarative experiment plans; client requests never select commands or paths."""
import copy
import itertools
import math

PRODUCTS = ('datasystem', 'mooncake')
OPERATIONS = ('put', 'get', 'exist_hit', 'exist_miss')
DEFAULTS = dict(profile='baseline', nodes=1, workers_per_node=2, clients=8,
                server_cpus=8, client_cpus=8, memory_gib=16, master_cpus=2,
                products=list(PRODUCTS), batches=[1], value_sizes=[1024],
                operations=list(OPERATIONS), duration_s=5, repeats=1)
CHOICES = dict(nodes=(1, 2, 4), workers_per_node=(1, 2, 4, 8),
               clients=(1, 2, 4, 8, 16, 32), server_cpus=(4, 8, 16, 24),
               client_cpus=(2, 4, 8, 12), memory_gib=(8, 16, 32, 64),
               master_cpus=(1, 2, 4, 8), duration_s=(5, 10, 20), repeats=(1, 3))
PROFILES = ('baseline', 'worker_scaling', 'client_scaling', 'metadata_scaling')


def validate(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError('实验参数含未知字段，不能指定主机、路径或命令')
    config = {**copy.deepcopy(DEFAULTS), **copy.deepcopy(raw)}
    if config['profile'] not in PROFILES:
        raise ValueError('未知实验组')
    for key, choices in CHOICES.items():
        if type(config[key]) is not int or config[key] not in choices:
            raise ValueError(f'{key} 的值不在允许范围 {choices}')
    for key, choices in [('products', PRODUCTS), ('operations', OPERATIONS),
                         ('batches', (1, 16, 256, 4096)), ('value_sizes', (1024, 65536, 1048576))]:
        values = config[key]
        if not isinstance(values, list) or not values or len(values) != len(set(map(str, values))):
            raise ValueError(f'{key} 必须是非空、不重复数组')
        if any(type(value) is not type(choices[0]) or value not in choices for value in values):
            raise ValueError(f'{key} 包含不支持的值')
    if config['nodes'] * config['workers_per_node'] > 8:
        raise ValueError('最多8个worker；每个节点规格必须可实现')
    if config['master_cpus'] > config['server_cpus']:
        raise ValueError('中心CPU配额不能大于服务端总配额')
    if config['server_cpus'] + config['client_cpus'] + 2 > 40:
        raise ValueError('总物理核配额超过40核，已预留2核观测')
    if max(config['batches']) * max(config['value_sizes']) > 16 * 1024 * 1024:
        raise ValueError('每次调用最多16MiB；大对象与大批量请分组实验')
    if max(config['batches']) * config['clients'] * max(config['value_sizes']) > (config['memory_gib'] << 30) // 8:
        raise ValueError('客户端同时在途批量超过服务端内存的1/8，请减少并发/批量或增加内存')
    return config


def preview_plan(raw):
    config = validate(raw)
    variants = [copy.deepcopy(config)]
    if config['profile'] in ('worker_scaling', 'metadata_scaling'):
        variants = [{**copy.deepcopy(config), 'workers_per_node': total // config['nodes']}
                    for total in (1, 2, 4, 8) if total >= config['nodes']]
    if config['profile'] == 'client_scaling':
        variants = [{**copy.deepcopy(config), 'clients': clients}
                    for clients in CHOICES['clients'] if clients <= config['clients']]
    if config['profile'] == 'metadata_scaling':
        variants = [{**variant, 'master_cpus': master, 'operations': ['exist_hit', 'exist_miss']}
                    for variant in variants for master in (1, 2, 4) if master <= config['server_cpus']]
    records = []
    total_cases = 0
    for index, variant in enumerate(variants):
        variant = validate(variant)
        cases = math.prod(len(variant[key]) for key in ('products', 'batches', 'value_sizes', 'operations')) * variant['repeats']
        total_cases += cases
        records.append(dict(id=f'v{index + 1:02d}', label=f"{variant['nodes']}逻辑节点/{variant['nodes'] * variant['workers_per_node']}workers/{variant['clients']}clients/中心{variant['master_cpus']}核", config=variant, cases=cases))
    if total_cases > 384:
        raise ValueError('单任务最多384条case；请减少规格、批量或重复数后分组运行')
    return dict(schema_version=1, config=config, variants=records, expected_cases=total_cases,
                estimated_measurement_s=sum(v['cases'] * v['config']['duration_s'] for v in records),
                topology_scope='同物理机cgroup逻辑节点；不代表跨物理机网络扩展性',
                success_criteria='原生结果完整、数据校验和每步验收通过；产品失败保持失败，不补零',
                hypotheses=['DS PREFERRED_META_OWNER；标量写一跳需要独立资格证据',
                            'MC中心瓶颈需吞吐/延迟与master CPU、throttling及配额敏感性共同证明'])


def catalog():
    presets = []
    for profile, title, description, changes in [
        ('baseline', '基础性能', '固定总配额，原生PUT/GET/EXIST；先核验元数据亲和与数据正确性。', {}),
        ('worker_scaling', 'Worker扩展', '固定总CPU/内存；比较1/2/4/8workers的收益与代价。', {'operations': ['put', 'get', 'exist_hit', 'exist_miss']}),
        ('client_scaling', 'Client并发扩展', '逐级提高客户端数量，观察吞吐平台与P99。', {'clients': 32}),
        ('metadata_scaling', '中心敏感性', 'EXIST压力下联动workers与中心CPU配额；仅检验假设，不预设结论。', {'operations': ['exist_hit', 'exist_miss']})]:
        presets.append(dict(id=profile, title=title, description=description, config={**copy.deepcopy(DEFAULTS), 'profile': profile, **changes}))
    return dict(defaults=copy.deepcopy(DEFAULTS), presets=presets,
                limits={key: list(value) for key, value in CHOICES.items()},
                versions=[dict(product='datasystem', tag='v0.9.2.rc44', sha='2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6'),
                          dict(product='mooncake', tag='v0.3.13.post1', sha='719735896c86b56fabec6cf3e825fb2ea640597a')],
                environment=dict(label='tiantiyun-80c128g', physical_hosts=1, physical_cores=40, logical_cpus=80, numa_nodes=2))
