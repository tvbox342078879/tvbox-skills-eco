# 2 5 2 识别流程

> 从 SKILL.md 外置（S130 P008 瘦身），正文未改。

### 2.5.2 识别流程（固定三步）

**铁律：spider 真身 = 判体系的唯一权威依据。** 命名形态（api 是否带 Guard/Amns）只作辅助印证，不能单独定案。

```python
# 1) 下载 spider（先去掉 ;md5; 后缀），看首字节
#    PK\x03\x04 → zip（Android jar，常见伪装成 .png/.jpg）→ 解出内部结构
# 2) 解 zip，看内部文件：
#    assets/*guard*.so  或  *.guard  → 【加壳】→ 体系 B
#    只有 classes.dex（含 ProGuard 混淆不算加壳）→ 【标准】→ 体系 A
# 3) 辅助印证 api：
apis = [s.get('api','') for s in d.get('sites',[]) if isinstance(s.get('api'),str)]
csp   = [a for a in apis if a.startswith('csp_')]
amns  = [a for a in csp if a.endswith('Amns')]
grd   = [a for a in csp if 'Guard' in a]
```

**判定表（实测验证 · S114 改锚 2026-10-18：体系的归属看 jar 挂了什么，不看站点清单用了什么）：**

| 条件 | 体系 |
|---|---|
| 无 spider 且无 csp | **C**（无 jar 可用，内容不依赖 jar） |
| 无 spider 但有 csp | **A?**（类名像 A，但 jar 挂载不明——采集丢字段≠作者没挂，先查仓库实录；M-R60 不静默判） |
| csp 带 `Amns` 后缀 | **D**（Amns 系） |
| spider zip 内含 `assets/*guard*.so` / `*.guard` | **B**（加壳系，api 常带 `Guard` 后缀） |
| spider zip 内只有 `classes.dex`（无 guard 资源） | **A**（CatVod 标准系，csp 裸类名） |
| spider 解包既无 guard 也无 dex（非 zip/dex 形态） | **E?**（非标准 jar，待人工 §2.5.4） |
| **有 spider 但无 csp** | **仍归 jar 所属系**（S114：不因配置没用 csp 就掉 C——cluntop 同 jar 三包旧判跨两系的实锤；直链主导是内容标签的事） |

> S114 锚点说明：体系回答「线路能不能平移」，取决于 jar；站点清单用法是 L3 魔改层。
> 旧表「无 spider 且无 csp → C」把「有 jar 但没用 csp」的包漏成 A/C 摇摆，
> 现已改为——**spider 在，体系随 jar；spider 不在，才看 csp 佐证**。
> 可平移性的显式三态判定（同名类实证）见《标准1015_底包细分类体系.md》§二·补。

**铁证案例（15 个存量包全部实测）：**
- 加壳证据 = zip 内 `assets/ftyguard_v7.so`+`ftyguard_v8.so`+`ftyshinidie.guard` → **fty/cainisi 属 B**；`assets/wexguard_v7/v8.so` → **wangerxiao 属 B**
- `easy1012.png` / `xxx.jpg` 等伪装名，解出来只是 `classes.dex` → **A**
- **反例警示**：XPB 混了 10 个 `*Guard` 后缀 csp，但 spider 无 guard 资源 → **仍判 A**。**只看 api 名字会判错。**

**关键：spider 的"真身"必须先验**——它常被伪装成 `.png` / `.jpg` / 无扩展名，**要下下来看首字节 + 解 zip 看内部文件**（`PK\x03\x04`=zip/dex）。**光看后缀、光看 api 名字都会判错体系。**

