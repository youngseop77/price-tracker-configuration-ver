import re

with open('targets.yaml', 'r', encoding='utf-8') as f:
    text = f.read()

pattern = r"  request:\n    query: '([^']+)'\n    pages: 1\n    sort: sim\n    filter: null\n"
replacement = r"  query: '\1'\n  request:\n    pages: 1\n    sort: sim\n    filter: null\n"

text = re.sub(pattern, replacement, text)

with open('targets.yaml', 'w', encoding='utf-8') as f:
    f.write(text)
