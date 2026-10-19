import json,re,csv,io
from urllib.parse import urlparse
from collections import Counter

rows=json.load(open('/tmp/raw_rows.json',encoding='utf-8'))
# 库内已有
lib=json.load(open('/tmp/src_main.csv',encoding='utf-8'))
exist=set(r['api接口'].strip() for r in csv.DictReader(io.StringIO(lib['content'])))
exist.add('')

A='A·CatVod标准系';B='B·moji Guard系';C='C·零jar纯直链系';D='D·Amns系';X='🔞成人直链系'
def systems(api):
    a=api.strip()
    if a.startswith('csp_'):
        cls=a[4:]
        if cls.endswith('Guard'): return [B],'B'
        if cls.endswith('Amns'):  return [D],'D'
        return [A],'A'
    if a.startswith('http'): return [A,B,C,D],'HTTP'
    return None,None

kept=[]; drop_rel=0; drop_dup=0; null=0
seen=set()
for r in rows:
    api=(r['api接口'] or '').strip()
    if not api: null+=1; continue
    # 相对路径 → 死源, 不入库
    if api.startswith('./') or api.startswith('../'): drop_rel+=1; continue
    # 多行URL只取首行作为数据源标识
    first=api.split('\n')[0].strip()
    if not first.startswith(('http','csp_')): drop_rel+=1; continue
    k=first
    if k in exist: drop_dup+=1; continue
    if k in seen: drop_dup+=1; continue
    seen.add(k)
    sys,_=systems(k)
    kept.append({'名称':r['名称'],'api接口':k,'来源包':r['来源包'],'kind':r['kind'],'可用体系':sys})

print(f'原始 {len(rows)}')
print(f'  剔除 相对路径/非法 {drop_rel}')
print(f'  剔除 库内已有+包间重复 {drop_dup}')
print(f'  空值 {null}')
print(f'→ 待入库 {len(kept)}')
print()
print('按体系分布:',dict(Counter(tuple(x['可用体系']) for x in kept)))
print('按形态:',dict(Counter(('csp' if x['api接口'].startswith('csp_') else 'http') for x in kept)))
json.dump(kept,open('/tmp/to_add.json','w',encoding='utf-8'),ensure_ascii=False)
