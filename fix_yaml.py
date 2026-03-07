import re

with open('targets.yaml', 'r', encoding='utf-8') as f:
    text = f.read()

targets = text.split('- name: ')
new_targets = [targets[0]]

for target in targets[1:]:
    name = target.split('\n')[0].strip()
    if 'mode: api_query' in target and 'request:' not in target:
        # Insert request block
        request_block = f"\n  request:\n    query: '{name}'\n    pages: 1\n    sort: sim\n    filter: null\n"
        target = target.replace('  mode: api_query', f'  mode: api_query{request_block}')
    new_targets.append('- name: ' + target)

with open('targets.yaml', 'w', encoding='utf-8') as f:
    f.write(''.join(new_targets))
