import yaml

def update_yaml():
    with open('targets.yaml', 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    changed = False
    for t in data.get('targets', []):
        if '갤럭시' in t['name'] and t.get('mode') == 'api_query':
            product_id = t.get('match', {}).get('product_id')
            if product_id:
                t['mode'] = 'browser_url'
                t['url'] = f'https://search.shopping.naver.com/catalog/{product_id}'
                t['browser'] = {
                    'wait_until': 'domcontentloaded',
                    'click_selectors': [],
                    'price_selector': '.lowestPrice_num__',
                    'seller_selector': '.productByMall_mall__mOihx',
                    'offer_row_selector': '.productByMall_list_item__B2_I_'
                }
                t.pop('query', None)
                t.pop('fallback_url', None)
                t.pop('request', None)
                changed = True

    if changed:
        with open('targets.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print('Updated targets.yaml')
    else:
        print('No changes needed')

if __name__ == '__main__':
    update_yaml()
