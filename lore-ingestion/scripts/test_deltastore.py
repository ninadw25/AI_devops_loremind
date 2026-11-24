from infra.outlook.fetcher import DeltaStore
from pathlib import Path
import shutil

p = Path('output') / 'test_delta'
if p.exists():
    shutil.rmtree(p)
p.mkdir(parents=True, exist_ok=True)
store_path = p / 'test_tokens.json'

store = DeltaStore('testorg', path=store_path)
store.set('mail:test@company.com', 'abc123', 50)
store.save()

store2 = DeltaStore('testorg', path=store_path)
print(store2.get('mail:test@company.com'))
