# cole-westin-core
Cole deployment core
Cole Episodic Memory — Release Candidate 1
This package contains the reconciled coordinator, migration, fixtures, and adversarial tests.
Layout
```text
repository-root/
├── episodic_memory.py
├── conftest.py
├── test_episodic_memory_adversarial.py
└── migrations/
    └── 001_initial_episodic_schema.sql
```
Required environment
```bash
export ENV_MODE=test
export TEST_DATABASE_URL='postgresql://postgres:postgres@localhost:5432/cole_test'
export QDRANT_URL='http://localhost:6333'
export MINIO_ENDPOINT='localhost:9000'
export MINIO_ACCESS_KEY='minioadmin'
export MINIO_SECRET_KEY='minioadmin'
export MINIO_SECURE='false'
```
Gate
```bash
python -m py_compile episodic_memory.py conftest.py test_episodic_memory_adversarial.py
ruff check episodic_memory.py conftest.py test_episodic_memory_adversarial.py
mypy episodic_memory.py conftest.py test_episodic_memory_adversarial.py
ADVERSARIAL_RUNS=1 pytest test_episodic_memory_adversarial.py -v -s
ADVERSARIAL_RUNS=10 pytest test_episodic_memory_adversarial.py -v -s
ADVERSARIAL_RUNS=100 pytest test_episodic_memory_adversarial.py -v -s
```
This is a release candidate until the full gate passes against the actual service versions and test database.
