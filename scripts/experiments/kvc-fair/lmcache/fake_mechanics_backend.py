"""TEST ONLY: local dictionary to check harness mechanics, never product results."""


class FakeAdapter:
    def __init__(self, config):
        self.values = {}
        self.fault = config.get("fault")

    def put(self, key, value):
        self.values[key] = value

    def get(self, key):
        if self.fault == "get_wrong_bytes":
            return b"wrong"
        return self.values[key]

    def exists(self, key):
        return key in self.values

    def close(self):
        pass


def new_adapter(config):
    return FakeAdapter(config)
