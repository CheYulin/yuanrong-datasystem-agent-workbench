"""Counterbalanced product and batch order with separate fresh-write lifetimes."""
BATCHES = [1, 16, 256, 4096]
OPS = ["put", "upsert", "get", "exist_hit", "exist_miss"]
PROTOCOL = "separate-read-and-fresh-put-v1"


def supported(product, batch, op):
    return product != "lmcache" or op.startswith("exist") or batch == 1 and op in {"put", "get"}


def plan(repeats):
    products = ["datasystem", "mooncake", "lmcache"]
    rows = []
    for repeat in range(repeats):
        order = products[repeat % 3:] + products[:repeat % 3]
        batches = BATCHES[repeat % 4:] + BATCHES[:repeat % 4]
        for product in order:
            operations = [{"batch": b, "operation": op, "supported": supported(product, b, op),
                           "epoch": "read" if op != "put" else f"put-b{b}"}
                          for b in batches for op in OPS[1:]]
            operations += [{"batch": b, "operation": "put", "supported": supported(product, b, "put"),
                            "epoch": f"put-b{b}"} for b in batches]
            epochs = []
            for name in ["read", *[f"put-b{b}" for b in batches]]:
                members = [spec for spec in operations if spec["epoch"] == name and spec["supported"]]
                if members:
                    epochs.append({"id": name, "kind": "read" if name == "read" else "fresh_put", "operations": members})
            rows.append({"repeat": repeat, "product": product, "batches": batches,
                         "workload_protocol": PROTOCOL, "operations": operations, "epochs": epochs})
    return rows
