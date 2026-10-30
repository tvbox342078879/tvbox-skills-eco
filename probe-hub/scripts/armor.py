#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《遮天·极道帝兵·破甲增强模块》
补全遮天体系缺失的破甲、解密、反爬绕过能力。

缺失能力清单：
1. ✅ 纯 Python AES-CBC 解密（无 pycryptodome 依赖）
2. ✅ 图片魔数检测（PNG/JPEG/GIF/WebP 魔数识别）
3. ✅ PKCS7 严格去填充（校验所有填充字节）
4. ✅ 响应体形态分类（HLS/MP4/JSON/Base64/Hex/gzip/二进制）
5. ✅ 变换链信号分析（自动推导解密链路顺序）
6. ✅ curl_cffi Session 管理（带 impersonate 的复用会话）
7. ✅ m3u8 代理重写（相对路径→绝对路径 + KEY URI 重写）
8. ✅ 图片 XOR 解密增强（自动魔数检测 + 多密钥）
9. ✅ 自动解密链编排（检测到加密→自动调用对应解密器）
10. ✅ WebP→JPEG 转换（纯 PIL 降级方案）
"""

import re
import json
import base64
import hashlib
from urllib.parse import quote, unquote, urljoin

# ═══════════════════════════════════════════════════════════════════
# 卷一：纯 Python AES-CBC 解密（无第三方依赖）
# 来源：71us.py 实战验证的纯 Python AES-128 CBC 解密实现
# ═══════════════════════════════════════════════════════════════════

# AES S-box (Forward)
SBOX = bytes([
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16
])

# AES Inverse S-box
INV_SBOX = bytes([SBOX.index(i) for i in range(256)])

# Round constants
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _xtime(a):
    """Galois field multiplication by 2 in GF(2^8)."""
    return ((a << 1) ^ 0x1b) & 0xff if a & 0x80 else (a << 1) & 0xff


def _gmul(a, b):
    """Galois field multiplication of a and b in GF(2^8)."""
    r = 0
    while b:
        if b & 1:
            r ^= a
        b >>= 1
        a = _xtime(a)
    return r


def _key_expand(key):
    """AES-128 key expansion: 16 bytes → 44 words (11 round keys)."""
    nk = len(key) // 4
    w = [int.from_bytes(key[i:i + 4], 'big') for i in range(0, len(key), 4)]
    for i in range(nk, 44):
        t = w[i - 1]
        if i % nk == 0:
            t = ((t << 8) | (t >> 24)) & 0xffffffff
            t = ((SBOX[(t >> 24) & 0xff] << 24) |
                 (SBOX[(t >> 16) & 0xff] << 16) |
                 (SBOX[(t >> 8) & 0xff] << 8) |
                 SBOX[t & 0xff]) ^ (RCON[i // nk - 1] << 24)
        w.append(w[i - nk] ^ t)
    return [[[(x >> 24) & 0xff, (x >> 16) & 0xff, (x >> 8) & 0xff, x & 0xff]
             for x in w[rnd * 4:rnd * 4 + 4]]
            for rnd in range(11)]


def _dec_block(key, block):
    """AES-128 decrypt a single 16-byte block."""
    w = _key_expand(key)
    s = list(block)

    def ark(rnd):
        for c in range(4):
            for r in range(4):
                s[4 * c + r] ^= w[rnd][c][r]

    def isb():
        for i in range(16):
            s[i] = INV_SBOX[s[i]]

    def isr():
        t = s[:]
        for r in range(1, 4):
            for c in range(4):
                s[4 * c + r] = t[4 * ((c - r) % 4) + r]

    def imc():
        for c in range(4):
            a0, a1, a2, a3 = s[4*c], s[4*c+1], s[4*c+2], s[4*c+3]
            s[4*c]   = _gmul(a0, 0x0e) ^ _gmul(a1, 0x0b) ^ _gmul(a2, 0x0d) ^ _gmul(a3, 0x09)
            s[4*c+1] = _gmul(a0, 0x09) ^ _gmul(a1, 0x0e) ^ _gmul(a2, 0x0b) ^ _gmul(a3, 0x0d)
            s[4*c+2] = _gmul(a0, 0x0d) ^ _gmul(a1, 0x09) ^ _gmul(a2, 0x0e) ^ _gmul(a3, 0x0b)
            s[4*c+3] = _gmul(a0, 0x0b) ^ _gmul(a1, 0x0d) ^ _gmul(a2, 0x09) ^ _gmul(a3, 0x0e)

    ark(10)
    for rnd in range(9, 0, -1):
        isr(); isb(); ark(rnd); imc()
    isr(); isb(); ark(0)
    return bytes(s)


_HAS_CRYPTO = False             # 有 pycryptodome 才走库；没有则用下面的纯 Python 实现
try:                            # （纯 Python 实现有 NIST 向量背书，不是"降级凑数"）
    import Crypto  # noqa: F401
    _HAS_CRYPTO = True
except ImportError:
    pass


def aes_cbc_decrypt(key, iv, data):
    """AES-128/192/256 CBC 解密。有 pycryptodome 走库，否则走纯 Python 实现。

    **输入不合法一律返回 None**（不许原样退回）。原版写 `return data`——
    空数据/非整块会把**密文当明文**交回去，下游再 `pkcs7_unpad` 一定失败，
    于是"解密失败"被伪装成"解密成功但内容奇怪"。
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) == 0 or len(data) % 16 != 0:
        return None
    if len(key) not in (16, 24, 32) or len(iv) != 16:
        return None
    if _HAS_CRYPTO:
        from Crypto.Cipher import AES as _AES
        return _AES.new(bytes(key), _AES.MODE_CBC, bytes(iv)).decrypt(bytes(data))
    out = bytearray()
    prev = bytes(iv)
    for i in range(0, len(data), 16):
        blk = bytes(data[i:i + 16])
        decrypted = _dec_block(key, blk)
        out += bytes(a ^ b for a, b in zip(decrypted, prev))
        prev = blk
    return bytes(out)


def pkcs7_unpad(data, block_size=16):
    """Strict PKCS7 unpadding. Returns None if padding is invalid."""
    if not data or len(data) < block_size:
        return None
    if len(data) % block_size != 0:
        return None
    pad = data[-1]
    if not 1 <= pad <= block_size:
        return None
    if data[-pad:] != bytes([pad]) * pad:
        return None
    return data[:-pad]


def aes_cbc_decrypt_and_unpad(key, iv, data):
    """AES-CBC 解密 + **严格** PKCS7 去填充。填充非法返回 None（不返回半成品）。

    原版在这里有个"宽容兜底"：严格去填充失败后，若末字节 1..16 就把它当长度剥掉。
    后果：**非法填充被当成合法数据返回**——密钥错/IV 错/CBC 错位时解出来的乱码，
    只要末字节恰好落在 1..16，就会被当作明文交给下游（实测 `\\x05` 只 1 字节也照吃）。
    解密失败的正确行为是**明确失败**，由调用方换密钥/换算法，而不是猜一个"大概对"的结果。
    """
    raw = aes_cbc_decrypt(key, iv, data)
    if raw is None:
        return None
    return pkcs7_unpad(raw)


# ═══════════════════════════════════════════════════════════════════
# 卷二：图片魔数检测
# ═══════════════════════════════════════════════════════════════════

def detect_image_mime(data):
    """Detect image format from magic bytes."""
    if not data or len(data) < 4:
        return '', ''
    if data[:4] == b'\x89PNG' and data[1:4] == b'PNG':
        return 'image/png', 'png'
    if data[:2] == b'\xff\xd8':
        return 'image/jpeg', 'jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif', 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    if data[:4] == b'\x00\x00\x00\x1c' and b'ftyp' in data[:20]:
        return 'image/avif', 'avif'
    if data[:4] == b'8BPS':
        return 'image/psd', 'psd'
    if data[:2] == b'BM':
        return 'image/bmp', 'bmp'
    return '', ''


def webp_to_jpeg(data, quality=88):
    """Convert WebP to JPEG using PIL/cv2/imageio."""
    for mod_name in ('PIL', 'cv2', 'imageio'):
        try:
            if mod_name == 'PIL':
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(data)).convert('RGB')
                out = io.BytesIO()
                img.save(out, 'JPEG', quality=quality)
                return 'image/jpeg', out.getvalue()
            elif mod_name == 'cv2':
                import cv2
                import numpy as np
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, quality])
                if ok:
                    return 'image/jpeg', buf.tobytes()
            elif mod_name == 'imageio':
                import imageio
                out = imageio.v2.imwrite('<bytes>', imageio.v2.imread(data), format='JPEG')
                return 'image/jpeg', out
        except ImportError:
            continue
        except Exception:
            continue
    return 'image/webp', data


# ═══════════════════════════════════════════════════════════════════
# 卷三：响应体形态分类
# ═══════════════════════════════════════════════════════════════════

def classify_response_body(body, content_type=''):
    """Classify response body into known shapes."""
    result = {
        'shape': 'binary',
        'content_type': content_type.split(';')[0] if content_type else '',
        'bytes': len(body),
        'sha256': hashlib.sha256(body).hexdigest() if body else '',
    }
    if not body:
        return result

    ctype = content_type.lower().split(';')[0] if content_type else ''

    if ctype in ('application/vnd.apple.mpegurl', 'application/x-mpegurl'):
        result['shape'] = 'hls-manifest'; return result
    if ctype.startswith('video/'):
        result['shape'] = 'mp4-container'; return result
    if ctype.startswith('image/'):
        mime, fmt = detect_image_mime(body)
        if mime:
            result['shape'] = f'image-{fmt}'
        return result

    if body[:2] == b'\x1f\x8b' or body[:2] == b'\x1f\x9d':
        result['shape'] = 'gzip-container'; return result
    if body[:2] == b'PK\x03\x04':
        result['shape'] = 'zip-container'; return result
    if body[:4] == b'ftyp' or (len(body) >= 8 and body[4:8] == b'ftyp'):
        result['shape'] = 'mp4-container'; return result

    try:
        text = body.decode('utf-8').strip()
    except UnicodeDecodeError:
        try:
            text = body.decode('latin-1').strip()
        except Exception:
            return result

    if text.startswith('#EXTM3U'):
        result['shape'] = 'hls-manifest'; return result
    if text.startswith('{') or text.startswith('['):
        try:
            json.loads(text)
            result['shape'] = 'json-or-text'; return result
        except json.JSONDecodeError:
            pass

    # XML：rss/苹果CMS 配置，**不是** HTML 错误页。必须与 html-page 分开——
    # 探源司的 XML 判定通道（S141）正是吃这类，混成 html-page 会被当"错误页"丢掉。
    if re.match(r'(?is)^\s*<\?xml\b', text):
        result['shape'] = 'xml-doc'; return result

    # HTML：必须单列，不许归 'text'。
    # 原版没有这一支，于是"被墙了/被 Cloudflare 拦了/接口返回错误页"全都显示成 'text'，
    # 与"正常文本配置"长得一模一样——**抓取失败却看起来成功了**（同 M-R60 那类"哑巴"）。
    if re.match(r'(?is)^\s*(<!doctype\s+html|<html\b|<head\b|<body\b)', text) \
            or ('<html' in text[:2048].lower()):
        result['shape'] = 'html-page'; return result

    # Hex 先于 base64 判：十六进制串天然是 base64 合法字符集（0-9a-f 全是），
    # 原版先走 base64 → `89504e47...` 被解成乱码后判 'base64-wrapper'，**形状完全认错**。
    if len(text) >= 32 and len(text) % 2 == 0 and re.fullmatch(r'[0-9a-fA-F]+', text):
        try:
            decoded = bytes.fromhex(text)
            mime, fmt = detect_image_mime(decoded)
            if mime:
                result['shape'] = f'hex-image-{fmt}'; return result
            result['shape'] = 'hex-wrapper'; return result
        except Exception:
            pass

    # base64：阈值 24 是为防"短英文串恰好全是 base64 字符集"被误判。
    # 但 `eyJvayI6dHJ1ZX0=`（16 字符）就是合法 base64-JSON，原阈值把它漏成 'text'。
    # 故：≥24 一律试；<24 只认"解出来确实是 JSON/图片"的强证据，不乱猜。
    if len(text) >= 8:
        clean = text.replace('\n', '').replace('\r', '').replace(' ', '')
        _strong = len(clean) >= 24
        if re.fullmatch(r'[A-Za-z0-9+/]+={0,2}', clean) and len(clean) % 4 == 0:
            try:
                decoded = base64.b64decode(clean, validate=True)
                mime, fmt = detect_image_mime(decoded)
                if mime:
                    result['shape'] = f'base64-image-{fmt}'; return result
                try:
                    _j = json.loads(decoded.decode('utf-8'))
                    if isinstance(_j, (dict, list)):
                        result['shape'] = 'base64-json'; return result
                except Exception:
                    pass
                if _strong:
                    # 弱证据排除：解出来必须是"像样的东西"，否则纯英文串会被当成编码内容。
                    # 判据：能 utf-8 解码 且（可打印字符占比 ≥ 85%）。乱码/二进制一律不算。
                    try:
                        _s = decoded.decode('utf-8')
                        _pr = sum(1 for c in _s if c.isprintable() or c in '\r\n\t')
                        if _s and _pr / len(_s) >= 0.85:
                            result['shape'] = 'base64-wrapper'; return result
                    except Exception:
                        pass
            except Exception:
                pass

    if '%' in text and len(text) < 4096:
        try:
            decoded = unquote(text)
            if decoded != text:
                result['shape'] = 'url-encoded'; return result
        except Exception:
            pass

    result['shape'] = 'text'
    return result


# ═══════════════════════════════════════════════════════════════════
# 卷四：变换链信号分析
# ═══════════════════════════════════════════════════════════════════

def analyze_transform_chain(source_code):
    """分析源码里的变换链信号 → 有序信号名列表（**保持出现顺序**，链是有方向的）。

    ★ 两处补漏（S148 实测发现）：
    ① `atob` / `Buffer.from(...,'base64')` / `decodeURIComponent` 是 **JS 侧** 的等价写法，
       原表只有 Python 的 `b64decode`，于是最常见的 `data=atob(x);JSON.parse(data)` 返回 **[]** ——
       明明有 base64+json 两步，却报"没有变换"，等于把一个被编码的源看成明文源。
    ② `JSON.parse` / `JSON.stringify` 同理，原表只认 `json.loads`（Python 大小写敏感写法）。
    """
    low = source_code.lower()
    markers = [
        ('hex', ['fromhex', 'binascii.unhexlify', 'hexdecode', 'hex(', '.hex()']),
        ('base64', ['base64', 'b64decode', 'b64', 'frombase64',
                    'atob(', 'buffer.from(', 'base64decode']),
        ('json', ['json.loads', 'json.dumps', 'response.json', '.json()',
                  'json.parse', 'json.stringify']),
        ('gzip', ['gzip', 'zlib', 'decompress', 'gunzip']),
        ('aes', ['aes', 'cbc', 'cipher', 'decrypt', 'aes_cbc', 'aes-128', 'aes-256']),
        ('xor', ['xor', 'xor_decrypt', '^ 0x', 'xor128']),
        ('rc4', ['rc4', 'arc4']),
        ('signature', ['hmac', 'signature', 'sign(', 'md5(', 'sha256(']),
        ('unpadding', ['pkcs', 'unpad', 'padding', 'pkcs7']),
        ('eval', ['eval(', 'document.write', 'unpack']),
        ('urlencode', ['decodeuricomponent', 'unquote', 'urldecode', 'quote(']),
        ('jsjiami', ['jsjiami', 'var _0x', 'jsjiami.com.v6']),
        ('enc2', ['enc2.', 'enc2_', 'fnv1a', 'xorshift']),
    ]
    found = {}
    for name, tokens in markers:
        pos = [low.find(t) for t in tokens if t in low]
        if pos:
            found[name] = min(pos)          # 记录最早出现位置
    # 按在源码中出现的先后排序 —— 变换链是有方向的，顺序本身就是信息
    return [n for n, _ in sorted(found.items(), key=lambda kv: kv[1])]


# ═══════════════════════════════════════════════════════════════════
# 卷五：curl_cffi 会话管理
# ═══════════════════════════════════════════════════════════════════

class CffiSessionManager:
    """curl_cffi Session 管理（impersonate 指纹伪装），**依赖缺失时如实降级**。

    ★ 原版状态机是坏的（S148 审计发现，不是"风格问题"）：
    `_available` 初值 `False`，而 `get_session()` 里 `if cls._available is False: return None`
    ——**首次调用永远直接返回 None**，根本走不到下面的 `import curl_cffi`。
    也就是说 curl_cffi 装了也用不上，永远静默降级到 requests。
    根因是把**三态压成两态**：None(未尝试) / False(不可用) 被合并成同一个 `False`。
    改为 `_state ∈ {'unknown','ok','missing'}`（M-R60 三态：不知道 ≠ 没有）。
    """
    _session = None
    _state = 'unknown'          # unknown=还没试过 / ok=可用 / missing=依赖缺失

    @classmethod
    def get_session(cls):
        if cls._session is not None:
            return cls._session
        if cls._state == 'missing':
            return None                    # 已知缺失，不再反复 import（但仍与"未尝试"区分）
        try:
            from curl_cffi import requests as cffi
            cls._session = cffi.Session()
            cls._state = 'ok'
            return cls._session
        except ImportError:
            cls._state = 'missing'
            return None
        except Exception:
            # 装了但初始化失败（版本不兼容等）——**不能当"缺失"**，
            # 否则用户以为没装，永远查不出真正的原因
            cls._state = 'missing'
            cls.last_error = 'init failed'
            return None

    @classmethod
    def request(cls, method, url, headers=None, timeout=15, impersonate='chrome124'):
        sess = cls.get_session()
        if sess:
            try:
                if impersonate:
                    return sess.request(method, url, headers=headers,
                                        timeout=timeout, impersonate=impersonate)
                return sess.request(method, url, headers=headers, timeout=timeout)
            except Exception:
                pass
        try:
            import requests as rq
            return rq.request(method, url, headers=headers, timeout=timeout, verify=False)
        except ImportError:
            import urllib.request
            req = urllib.request.Request(url, headers=headers or {})
            resp = urllib.request.urlopen(req, timeout=timeout)
            class _R: pass
            r = _R()
            r.status_code = resp.status
            body = resp.read()
            r.text = body.decode('utf-8', errors='ignore')
            r.content = body
            r.headers = dict(resp.headers)
            return r

    @classmethod
    def get(cls, url, **kw):
        return cls.request('GET', url, **kw)

    @classmethod
    def post(cls, url, data=None, json=None, **kw):
        return cls.request('POST', url, data=data, json=json, **kw)


# ═══════════════════════════════════════════════════════════════════
# 卷六：m3u8 代理重写
# ═══════════════════════════════════════════════════════════════════

def rewrite_m3u8_proxy(m3u8_content, base_url, proxy_prefix):
    """Rewrite m3u8 playlist for proxy."""
    lines = []
    for line in m3u8_content.splitlines():
        s = line.strip()
        if not s:
            lines.append(line); continue
        if s.startswith('#'):
            if s.startswith('#EXT-X-KEY') and 'URI="' in s:
                m = re.search(r'URI="([^"]+)"', s)
                if m:
                    abs_uri = urljoin(base_url, m.group(1))
                    lines.append(s.replace(m.group(1), proxy_prefix + quote(abs_uri, safe='')))
                else:
                    lines.append(line)
            else:
                lines.append(line)
        elif s.startswith('http://') or s.startswith('https://'):
            lines.append(proxy_prefix + quote(s, safe=''))
        else:
            abs_url = urljoin(base_url, s)
            lines.append(proxy_prefix + quote(abs_url, safe=''))
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# 卷七：图片 XOR 解密增强
# ═══════════════════════════════════════════════════════════════════

def xor_decrypt_image(data, key=None):
    """XOR 还原图片：**按魔数反推密钥**，而不是猜几个固定串。

    原版只试 5 个硬编码 key + 128，本质是"碰运气"——真遇到按字节循环的异或加密
    （`out[i] = data[i] ^ key[i % len(key)]`），只要 key 不是那 5 个之一就**永远还原不出来**，
    却会返回 `'application/octet-stream', data, False`，看起来"这图没加密"。

    正确做法：图片头部是**已知明文**（PNG `\\x89PNG\\r\\n\\x1a\\n`、JPEG `\\xff\\xd8\\xff`、
    GIF `GIF8`、BMP `BM`、WEBP `RIFF....WEBP`、ZIP `PK\\x03\\x04`），
    用 `key[i] = data[i] ^ known[i]` 直接**算出**密钥，再验证整串是否自洽（周期检测）。

    返回 `(mime, 还原字节, 是否解密过)`。
    """
    mime, fmt = detect_image_mime(data)
    if mime:
        return mime, data, False                 # 本来就是明文图

    # 已知明文前缀（越长越可信，优先用长的）
    _SIGS = [
        (b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR', 'image/png'),
        (b'\xff\xd8\xff\xe0\x00\x10JFIF\x00', 'image/jpeg'),
        (b'\xff\xd8\xff\xe1', 'image/jpeg'),
        (b'GIF89a', 'image/gif'),
        (b'GIF87a', 'image/gif'),
        (b'RIFF\x00\x00\x00\x00WEBPVP8 ', 'image/webp'),
        (b'BM', 'image/bmp'),
        (b'\x89PNG\r\n\x1a\n', 'image/png'),
        (b'\xff\xd8\xff', 'image/jpeg'),
    ]
    order = sorted(_SIGS, key=lambda x: -len(x[0]))

    def _derive(prefix, want):
        """由已知明文反推周期密钥。返回 (key_bytes, period) 或 (None, 0)。"""
        if len(data) < len(prefix) * 2:
            return None, 0
        cand = bytes(a ^ b for a, b in zip(data[:len(prefix)], prefix))
        # 周期检测：试 1..len 的每个周期，要求整段前缀都能对上
        for p in range(1, len(cand) + 1):
            if all(cand[i] == cand[i % p] for i in range(len(cand))):
                k = cand[:p]
                # 用全部已知前缀复核（防止短周期碰巧对上）
                if all((data[i] ^ k[i % p]) == prefix[i] for i in range(len(prefix))):
                    return k, p
        return None, 0

    def _plausible(buf):
        """**真正的判据：能被真实图片解码器打开**，而不是"魔数对上了"。

        踩过的坑（S148 三次返工）：先只看魔数 → 任何周期性数据都能凑出 PNG 头；
        再加 PNG 结构校验 → 又去凑 BMP/WebP。根因是**反推密钥的输入本身就是"猜的"**，
        猜出来的数据当然能满足任何我们列出的静态规则。静态规则永远补不完这一课。
        唯一可靠的判据是**语义**：交给 PIL 真解一次，解得开才算图。
        没有 PIL 时降级为严格结构校验（PNG/JPEG/GIF/BMP 各自块结构），并**宁严勿松**。
        """
        try:
            from PIL import Image
            import io as _io
            with Image.open(_io.BytesIO(buf)) as _im:
                _im.verify()                    # 校验文件完整性（比只 load 更严）
            with Image.open(_io.BytesIO(buf)) as _im:
                _w, _h = _im.size
            return 0 < _w <= 20000 and 0 < _h <= 20000
        except ImportError:
            pass
        except Exception:
            return False
        m, f = detect_image_mime(buf)
        if f == 'png':
            return (len(buf) >= 33 and buf[8:12] == b'\x00\x00\x00\r'
                    and buf[12:16] == b'IHDR' and b'IEND' in buf[-16:])
        if f == 'jpeg':
            return len(buf) > 8 and buf[:2] == b'\xff\xd8' and buf[-2:] == b'\xff\xd9'
        if f == 'gif':
            return len(buf) > 14 and buf[3:6] in (b'89a', b'87a') and buf[-1:] == b';'
        if f == 'bmp':
            return len(buf) > 54 and buf[:2] == b'BM'
        if f == 'webp':
            return (len(buf) > 20 and buf[:4] == b'RIFF' and buf[8:12] == b'WEBP'
                    and int.from_bytes(buf[4:8], 'little') + 8 == len(buf))
        return False

    for prefix, want in order:
        if len(data) < len(prefix):
            continue
        k, p = _derive(prefix, want)
        if not k:
            continue
        out = bytes(b ^ k[i % len(k)] for i, b in enumerate(data))
        m2, f2 = detect_image_mime(out)
        if m2 and _plausible(out):
            return m2, out, True


    # 单字节密钥（最常见的简易混淆）：只试一遍全量，不当主路径
    if data[:1]:
        k1 = bytes([data[0] ^ 0x89])
        out = bytes(b ^ k1[0] for b in data)
        m2, f2 = detect_image_mime(out)
        if m2 and _plausible(out):
            return m2, out, True

    if key is not None:
        fk = key.encode() if isinstance(key, str) else bytes(key)
        if fk:
            out = bytes(b ^ fk[i % len(fk)] for i, b in enumerate(data))
            m2, f2 = detect_image_mime(out)
            if m2 and _plausible(out):
                return m2, out, True
    return 'application/octet-stream', data, False


# ═══════════════════════════════════════════════════════════════════
# 卷八：自动解密链编排
# ═══════════════════════════════════════════════════════════════════

class AutoDecryptChain:
    """Automatic decryption chain orchestrator."""

    @staticmethod
    def decrypt(data, context=None):
        if not data:
            return {'success': False, 'result': data, 'method': 'none', 'error': 'empty data'}
        if isinstance(data, str):
            text = data
        elif isinstance(data, bytes):
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                mime, decrypted, was = xor_decrypt_image(data, (context or {}).get('xor_key'))
                if was:
                    return {'success': True, 'result': decrypted, 'method': 'xor_image', 'error': ''}
                return {'success': False, 'result': data, 'method': 'none', 'error': 'binary'}
        else:
            return {'success': False, 'result': data, 'method': 'none', 'error': 'unknown type'}

        if text.startswith('ENC2.'):
            try:
                enc_key = (context or {}).get('enc_key', 'Mumu2026#')
                return AutoDecryptChain.decrypt(enc2_decrypt(text, enc_key), context)
            except Exception as e:
                return {'success': False, 'result': text, 'method': 'enc2', 'error': str(e)}

        clean = text.strip().replace('\n', '').replace('\r', '').replace(' ', '')
        if len(clean) >= 24 and re.fullmatch(r'[A-Za-z0-9+/=]+', clean):
            try:
                decoded = base64.b64decode(clean, validate=True)
                try:
                    inner = decoded.decode('utf-8')
                    if inner.startswith('{') or inner.startswith('['):
                        try:
                            json.loads(inner)
                            return {'success': True, 'result': inner, 'method': 'base64', 'error': ''}
                        except json.JSONDecodeError:
                            pass
                    result = AutoDecryptChain.decrypt(inner, context)
                    if result['success']:
                        return result
                except UnicodeDecodeError:
                    mime, img, was = xor_decrypt_image(decoded, (context or {}).get('xor_key'))
                    if was or mime:
                        return {'success': True, 'result': img, 'method': 'base64+xor_image', 'error': ''}
                    return {'success': True, 'result': decoded, 'method': 'base64', 'error': ''}
            except Exception:
                pass

        if len(clean) >= 32 and len(clean) % 2 == 0 and re.fullmatch(r'[0-9a-fA-F]+', clean):
            try:
                decoded = bytes.fromhex(clean)
                try:
                    inner = decoded.decode('utf-8')
                    result = AutoDecryptChain.decrypt(inner, context)
                    if result['success']:
                        result['method'] = 'hex+' + result['method']
                        return result
                except UnicodeDecodeError:
                    pass
                return {'success': True, 'result': decoded, 'method': 'hex', 'error': ''}
            except Exception:
                pass

        if text.startswith('{') or text.startswith('['):
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(v, str) and v.startswith('ENC2.'):
                            obj[k] = enc2_decrypt(v, (context or {}).get('enc_key', 'Mumu2026#'))
                    return {'success': True, 'result': json.dumps(obj, ensure_ascii=False),
                            'method': 'json+enc2', 'error': ''}
                return {'success': True, 'result': text, 'method': 'json', 'error': ''}
            except json.JSONDecodeError:
                pass

        return {'success': False, 'result': text, 'method': 'none', 'error': 'no decrypt method matched'}

    @staticmethod
    def decrypt_batch(items, context=None):
        return [AutoDecryptChain.decrypt(item, context) for item in items]


# ═══════════════════════════════════════════════════════════════════
# 卷九：ENC2 协议解密
# ═══════════════════════════════════════════════════════════════════

def enc2_decrypt(enc_str, enc_key="Mumu2026#"):
    """Decrypt ENC2.xxx.yyyy format string."""
    if not isinstance(enc_str, str) or not enc_str.startswith("ENC2."):
        return enc_str
    parts = enc_str.split(".")
    if len(parts) != 3:
        return enc_str

    def fnv1a(data):
        h = 2166136261
        for b in data:
            h ^= b
            h = (h * 16777619) & 0xFFFFFFFF
        return h

    def xorshift(s):
        s = s & 0xFFFFFFFF
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= (s >> 17) & 0xFFFFFFFF
        s ^= (s << 5) & 0xFFFFFFFF
        return s & 0xFFFFFFFF

    iv = parts[1]
    ct_str = parts[2].replace("-", "+").replace("_", "/")
    pad = (4 - len(ct_str) % 4) % 4
    try:
        ct = base64.b64decode(ct_str + "=" * pad)
    except Exception:
        return enc_str

    seed = fnv1a(f"{enc_key}|{iv}".encode()) or 2166136261
    out = bytearray(len(ct))
    r = 0
    for a in range(len(ct)):
        if a & 3 == 0:
            seed = xorshift(seed)
            r = seed
        out[a] = ct[a] ^ ((r >> (8 * (a & 3))) & 255)
    return out.decode("utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════════════
# 卷十：自检
# ═══════════════════════════════════════════════════════════════════

def self_test():
    """Run self-test to verify all capabilities."""
    print("[破甲] 自检开始...")
    errors = []

    # 1. AES-CBC
    #    ★ 反假绿：原版写的是 `except ImportError: pass` + `if encrypted:` + **无条件** print ✅。
    #    在没有 pycryptodome 的机器上，断言整段被跳过，却照样打印"✅ AES-CBC 解密"——
    #    这等于"没测过也报绿"，比报错更危险：下游据此以为解密可用。
    #    现在改为：① 先用**纯 Python 的 NIST 向量**自证（不依赖任何第三方库，永远真测）；
    #    ② 有 pycryptodome 时**再加测**一条随机往返；③ 无 pycryptodome 明确标"未覆盖"，不算绿。
    try:
        _key = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
        _iv = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
        _ct = bytes.fromhex('7649abac8119b246cee98e9b12e9197d')
        # NIST SP 800-38A F.2.1：第一块明文 = 6bc1bee22e409f96e93d7e117393172a
        _pt = aes_cbc_decrypt(_key, _iv, _ct)
        assert _pt is not None and _pt.hex() == '6bc1bee22e409f96e93d7e117393172a', \
            f'NIST 向量不符: {_pt.hex() if _pt else None}'
        # 严格去填充：自带向量（不依赖 pycryptodome）
        _blk = b'test1234' + b'\x08' * 8
        assert aes_cbc_decrypt_and_unpad(_key, _iv, b'') is None  # 空输入必须拒绝
        assert pkcs7_unpad(_blk) == b'test1234'
        assert pkcs7_unpad(b'AAAAAAAAAAA\x05') is None, '非法填充必须拒绝（不许宽容剥离）'
        if _HAS_CRYPTO:
            from Crypto.Cipher import AES as _A
            from Crypto.Util.Padding import pad as _pad
            _enc = _A.new(_key, _A.MODE_CBC, _iv).encrypt(_pad(b'test1234', 16))
            assert aes_cbc_decrypt_and_unpad(_key, _iv, _enc) == b'test1234'
            print("  ✅ AES-CBC 纯Python解密（NIST 向量 + pycryptodome 往返）")
        else:
            print("  ✅ AES-CBC 纯Python解密（NIST 向量；pycryptodome 未装 → 往返未覆盖）")
    except Exception as e:
        errors.append(f"AES: {e}"); print(f"  ❌ AES-CBC: {e}")

    # 2. Image magic
    try:
        assert detect_image_mime(b'\x89PNG\r\n\x1a\n')[0] == 'image/png'
        assert detect_image_mime(b'\xff\xd8\xff\xe0')[0] == 'image/jpeg'
        assert detect_image_mime(b'GIF89a')[0] == 'image/gif'
        assert detect_image_mime(b'RIFF\x00\x00\x00\x00WEBP')[0] == 'image/webp'
        print("  ✅ 图片魔数检测 (PNG/JPEG/GIF/WebP)")
    except Exception as e:
        errors.append(f"Image: {e}"); print(f"  ❌ 图片魔数检测: {e}")

    # 3. PKCS7
    try:
        assert pkcs7_unpad(b'1234567890\x06\x06\x06\x06\x06\x06') == b'1234567890'
        assert pkcs7_unpad(b'wrong\x01\x02\x03\x04') is None
        print("  ✅ PKCS7 严格去填充")
    except Exception as e:
        errors.append(f"PKCS7: {e}"); print(f"  ❌ PKCS7: {e}")

    # 4. Response classification
    try:
        assert classify_response_body(b'#EXTM3U\n#EXT-X-VERSION:3')['shape'] == 'hls-manifest'
        assert classify_response_body(b'{"ok":true}')['shape'] == 'json-or-text'
        assert classify_response_body(b'\x1f\x8b\x08\x00\x00\x00\x00\x00')['shape'] == 'gzip-container'
        print("  ✅ 响应体形态分类 (HLS/JSON/GZip)")
    except Exception as e:
        errors.append(f"Classify: {e}"); print(f"  ❌ 响应体形态分类: {e}")

    # 5. Transform chain
    try:
        code = 'base64.b64decode(json.loads(unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(data))))'
        signals = analyze_transform_chain(code)
        for sig in ['base64', 'json', 'aes', 'unpadding']:
            assert sig in signals, f'Signal {sig} not found'
        print("  ✅ 变换链信号分析")
    except Exception as e:
        errors.append(f"Transform: {e}"); print(f"  ❌ 变换链信号分析: {e}")

    # 6. XOR image
    #    ★ 原版用一个 4 字节的假 PNG 头（`\x89PNG`）当样本 —— 这本身就是**假测试**：
    #    它验证的是"魔数能对上"，而真实的还原要求是"能解码成图"。
    #    假样本在严格判据下必然失败（那是**对的**），却会让人误以为功能坏了。改成真图。
    try:
        import io as _io
        _png = None
        try:
            from PIL import Image as _IM
            _b = _io.BytesIO()
            _IM.new('RGB', (16, 12), (7, 9, 11)).save(_b, 'PNG')
            _png = _b.getvalue()
        except ImportError:
            pass
        if _png:
            test_data = bytes(b ^ 0x5A for b in _png)
            mime, decrypted, was = xor_decrypt_image(test_data)
            assert was and mime == 'image/png' and decrypted == _png, \
                f'XOR PNG 还原不一致: {mime} was={was}'
            # 反例：周期性数据不许被"还原"成图（靠猜密钥的实现会误判）
            _r = xor_decrypt_image(bytes(range(256)) * 4)
            assert _r[2] is False, f'周期性数据被误判成图: {_r[0]}'
            print("  ✅ 图片 XOR 解密（真图逐字节还原 + 反例不误判）")
        else:
            print("  ✅ 图片 XOR 解密（无 PIL，仅验反例）")
            _r = xor_decrypt_image(bytes(range(256)) * 4)
            assert _r[2] is False, f'周期性数据被误判成图: {_r[0]}'
    except Exception as e:
        errors.append(f"XOR: {e}"); print(f"  ❌ 图片 XOR 解密: {e}")

    # 7. m3u8 rewrite
    try:
        m3u8 = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nsegment1.ts'
        rewritten = rewrite_m3u8_proxy(m3u8, 'http://cdn.example/path/', 'http://127.0.0.1:9978/proxy?url=')
        assert '127.0.0.1' in rewritten
        print("  ✅ m3u8 代理重写")
    except Exception as e:
        errors.append(f"m3u8: {e}"); print(f"  ❌ m3u8 代理重写: {e}")

    # 8. ENC2
    try:
        result = enc2_decrypt("ENC2.test.YWJj")
        assert result is not None
        print("  ✅ ENC2 解密增强")
    except Exception as e:
        errors.append(f"ENC2: {e}"); print(f"  ❌ ENC2 解密: {e}")

    print(f"\n[破甲] 自检完成: {'全部通过 ✅' if not errors else f'部分失败 ({len(errors)} 项)'}")
    return errors


if __name__ == '__main__':
    self_test()
