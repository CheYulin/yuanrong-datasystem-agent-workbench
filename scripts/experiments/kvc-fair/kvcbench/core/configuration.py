"""Pure display of the frozen native-batch feature and capacity profile."""


def configuration_profile(products):
    records = products.get("products", []) if isinstance(products, dict) else products
    mooncake = next((row for row in records if row.get("name") == "mooncake"), {})
    env = mooncake.get("env", {})
    settings = []
    for key, default in [("MC_TCP_MAX_QUEUED_TRANSFERS_PER_PEER", 1024), ("MC_TCP_MAX_PENDING_ADMISSIONS_PER_PEER", 1024),
                         ("MC_TCP_LANES_PER_PEER", 4), ("MC_TCP_ADMISSION_TIMEOUT_MS", 1000)]:
        value = int(env[key]) if key in env else default
        settings.append({"key": key, "value": value, "pinned_source_default": default,
                         "origin": "explicit frozen product env" if key in env else "pinned source default"})
    adapted = settings[0]["value"] == 8192 and settings[1]["value"] == 8192 and settings[2]["value"] == 4 and settings[3]["value"] == 1000
    pure_kv = ({row.get("name") for row in records} == {"datasystem", "mooncake", "lmcache"}
               and all(row.get("env", {}).get("KVC_FEATURE_PROFILE") == "pure-kv" for row in records))
    return {"name": ("纯 KV 内存 · " if pure_kv else "") + ("Mooncake TCP 批量容量适配（queue / pending = 8192 / 8192）" if adapted else "冻结输入中记录的原生配置"),
            "feature_profile": "pure-kv" if pure_kv else "legacy-isolated",
            "capacity_adapted": adapted, "settings": settings,
            "scope": ("Mooncake queued/pending capacities are 8192; lane count 4 and admission timeout 1000ms retain pinned source defaults."
                      if adapted else "Recorded values only; this configuration does not match the reviewed 8192/8192/4/1000 batch capacity profile."),
            "source_commit": "719735896c86b56fabec6cf3e825fb2ea640597a",
            "source_location": "mooncake-transfer-engine/src/transport/tcp_transport/tcp_transport.cpp:301-338"}
