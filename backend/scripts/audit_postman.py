"""Maintenance helper for audit postman workflows."""

import argparse
import json
import re
from pathlib import Path


def resolve_collection_path(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    # backend/scripts -> backend -> project root
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "docs" / "api" / "AdaptIQ_Complete_Postman.json"


parser = argparse.ArgumentParser(description="Audit Postman collection variables.")
parser.add_argument(
    "--collection",
    help="Path to Postman collection JSON. Defaults to docs/api/AdaptIQ_Complete_Postman.json",
)
args = parser.parse_args()

collection_path = resolve_collection_path(args.collection)

with collection_path.open(encoding="utf-8") as f:
    data = json.load(f)

print("COLLECTION FILE:", collection_path)

defined_vars = {v['key'] for v in data['variable']}
print('DEFINED VARIABLES:', sorted(defined_vars))

raw = json.dumps(data)
used_vars = set(re.findall(r'\{\{(\w+)\}\}', raw))
print('\nUSED VARIABLES:', sorted(used_vars))

set_vars = set(re.findall(r"collectionVariables\.set\('(\w+)'", raw))
print('\nSET BY SCRIPTS:', sorted(set_vars))

undefined = used_vars - defined_vars
runtime_generated = used_vars & set_vars
undefined_runtime = undefined & runtime_generated
undefined_static = undefined - runtime_generated

if undefined_static:
    print('\nWARNING - Used but NOT defined:', undefined_static)
elif undefined_runtime:
    print('\nNOTE - Runtime-generated variables are not predeclared:', undefined_runtime)
else:
    print('\nAll used variables are defined.')

dynamic = used_vars - {'baseUrl', 'email1', 'email2', 'password', 'adminBootstrapKey', 'adminEmail', 'adminPassword'}
unset = dynamic - set_vars
if unset:
    print('WARNING - Used but NOT set by any script:', unset)
else:
    print('All dynamic variables are set by test scripts.')

def count_items(items, count=0):
    for item in items:
        if 'item' in item:
            count = count_items(item['item'], count)
        elif 'request' in item:
            count += 1
    return count

total = count_items(data['item'])
print(f'\nTotal requests: {total}')
