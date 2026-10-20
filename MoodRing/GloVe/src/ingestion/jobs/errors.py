"""Fixed persistence errors; never include connection or SQL/provider details."""
class StorageError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)
