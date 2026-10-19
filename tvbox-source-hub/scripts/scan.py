import json,os,glob,re,csv,io
from urllib.parse import urlparse
from collections import Counter

PACKS=[]
# baseline 15包
for p in sorted(glob.glob('/root/.codebuddy/artifact/baseline/*.json')):
    PACKS.append((os.path.basename(p).replace('.json',''),p))
# workspace 里的 tvbox 包
for p in sorted(glob.glob('/workspace/*.json')):
    PACKS.append((os.path.basename(p).replace('.json',''),p))
# hzm
PACKS.append(('盒子迷58站','/root/.codebuddy/artifact/hzm/hzm_pure.json'))

def load(path):
    try:
        raw=open(path,encoding='utf-8',errors='replace').read().strip().lstrip('\ufeff')
        if not raw: return None
        lines=[l for l in raw.split('\n') if not l.strip().startswith('//')]
        return json.loads('\n'.join(lines))
    except Exception as e:
        return None

def host_of(a):
    if a.startswith('http'):
        try: return urlparse(a).netloc
        except: return ''
    if a.startswith('csp_'): return a.lower()
    return ''

rows=[]; stats=Counter(); failed=[]
for name,path in PACKS:
    d=load(path)
    if d is None: failed.append(name); continue
    if isinstance(d,list):
        d={'sites':[x for x in d if isinstance(x,dict) and x.get('type') not in (0,)] or [],
           'lives':[x for x in d if isinstance(x,dict) and x.get('type')==0] or []}
    n_s=n_l=n_p=0
    # sites
    for s in d.get('sites',[]) or []:
        api=str(s.get('api') or '').strip()
        if not api: continue
        rows.append({'名称':s.get('name') or '','api接口':api,'来源包':name,'kind':'site'})
        n_s+=1
    # lives
    for l in d.get('lives',[]) or []:
        u=str(l.get('url') or '').strip()
        if not u: continue
        rows.append({'名称':l.get('name') or '','api接口':u,'来源包':name,'kind':'live'})
        n_l+=1
    # parses
    for p in d.get('parses',[]) or []:
        u=str(p.get('url') or '').strip()
        if not u: continue
        rows.append({'名称':p.get('name') or '','api接口':u,'来源包':name,'kind':'parse'})
        n_p+=1
    if n_s+n_l+n_p: stats[name]=(n_s,n_l,n_p)
    else: failed.append(name+'(空)')

print(f'扫描包数 {len(PACKS)} | 成功 {len(stats)} | 失败/空 {len(failed)}')
print('失败:',failed)
print(f'\n原始提取 rows: {len(rows)}')
json.dump(rows,open('/tmp/raw_rows.json','w',encoding='utf-8'),ensure_ascii=False)
print('\n各包提取数 (sites/lives/parses):')
for k,v in sorted(stats.items()): print(f'  {k:28} {v[0]:4}站 {v[1]:4}直播 {v[2]:4}解析')
