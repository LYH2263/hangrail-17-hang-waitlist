import os

# 测试不依赖 Postgres;测试使用自己的 StaticPool 内存库并覆盖 get_db。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
