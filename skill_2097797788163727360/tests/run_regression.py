# -*- coding: utf-8 -*-
"""wechat-article-publisher 回归测试（随 skill 走）。

对 tests/ 下每篇「曾出问题的源 HTML」（*.source.html）跑 wechat_convert.py，
三层校验：
  1) 质检门禁：转换退出码必须 0（validate_wechat 不过会 exit 1）。
  2) JS 语法：提取生成预览里的 <script> 用 node --check，
     精确卡住「复制没反应」那类故障（f-string 转义崩 JS）。
  3) 复制模拟：取 #copy-target 内容，断言无 <div>/渐变/工具栏文字/UI 铬层泄漏。

用法（在 skill 目录下）：
  python tests/run_regression.py
依赖：beautifulsoup4 lxml markdown（转换用）；node（JS 语法检查用）。
"""
import io, os, re, subprocess, sys, shutil
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 相对路径：本文件位于 skill/tests/ 下，父目录即 skill 根
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL, "scripts")
FIX = os.path.join(SKILL, "tests")  # 发布平台只允许两级目录，fixture 直放 tests/（不放 tests/fixtures/）
OUT = os.path.join(SKILL, "tests", "out")
PY = sys.executable  # 用当前解释器（应已装 bs4/lxml/markdown）
NODE = os.environ.get("NODE_BIN", "node")


def find_node():
    """找一个可用的 node 可执行（用于 --check）。找不到则返回 None。"""
    for cand in (NODE, "node", "node.exe",
                 r"C:/ProgramData/WorkBuddy/users/1805ac3b/.workbuddy/binaries/node/versions/22.22.2/node.exe"):
        try:
            r = subprocess.run([cand, "--version"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return cand
        except Exception:
            continue
    return None


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    node_bin = find_node()

    fixtures = sorted(f for f in os.listdir(FIX) if f.endswith(".html"))
    results = []
    for fx in fixtures:
        src = os.path.join(FIX, fx)
        name = os.path.splitext(fx)[0]
        odir = os.path.join(OUT, name)
        os.makedirs(odir, exist_ok=True)
        r = subprocess.run([PY, os.path.join(SCRIPTS, "wechat_convert.py"), src,
                           "--outdir", odir], capture_output=True, text=True, encoding="utf-8")
        rc = r.returncode
        wechat = os.path.join(odir, name + "_wechat.html")
        preview = os.path.join(odir, name + "_preview.html")
        info = {"fx": fx, "rc": rc, "out": r.stdout + r.stderr,
                "wechat": wechat, "preview": preview, "problems": []}
        p = info["problems"]

        if rc != 0:
            p.append(f"转换退出码非0: {rc}")
        if not os.path.exists(wechat):
            p.append("缺少 _wechat.html")
        else:
            wh = io.open(wechat, encoding="utf-8").read()
            if re.search(r"<div[\s>]", wh, re.I):
                p.append("wechat 含 <div>")
            if re.search(r"(linear|radial)-gradient", wh, re.I):
                p.append("wechat 含渐变")
            if re.search(r"max-width\s*:\s*(\d+)px", wh, re.I):
                p.append("wechat 含固定 max-width")
            if re.search(r"<(script|style)[\s>]", wh, re.I):
                p.append("wechat 含 script/style")
            if re.search(r"<(html|head|body)[\s>]", wh, re.I):
                p.append("wechat 含 html/head/body")
            if re.search(r"class\s*=\s*[\"']bar", wh, re.I) or \
               re.search(r"id\s*=\s*[\"']phone", wh, re.I):
                p.append("wechat 含 UI 铬层")

        if os.path.exists(preview):
            pv = io.open(preview, encoding="utf-8").read()
            m = re.search(r"<script>(.*?)</script>", pv, re.S)
            if m and node_bin:
                jp = os.path.join(odir, "extracted.js")
                io.open(jp, "w", encoding="utf-8").write(m.group(1))
                nr = subprocess.run([node_bin, "--check", jp],
                                   capture_output=True, text=True)
                if nr.returncode != 0:
                    p.append("JS 语法错误(node --check 失败): " + nr.stderr.strip()[:200])
            elif not node_bin:
                p.append("未找到 node，跳过 JS 语法检查")
            else:
                p.append("preview 无 <script>")
            try:
                from bs4 import BeautifulSoup
                ps = BeautifulSoup(pv, "lxml")
                ct = ps.find(id="copy-target")
                if ct is None:
                    p.append("preview 无 #copy-target（复制按钮会失败）")
                else:
                    copied = "".join(str(c) for c in ct.children)
                    if re.search(r"<div[\s>]", copied, re.I):
                        p.append("复制内容含 <div>")
                    if re.search(r"(linear|radial)-gradient", copied, re.I):
                        p.append("复制内容含渐变")
                    if re.search(r"公众号移动端预览|复制全文|📱", copied):
                        p.append("复制内容含工具栏文字泄漏")
                    if re.search(r"class\s*=\s*[\"']bar", copied, re.I):
                        p.append("复制内容含 .bar 铬层")
            except Exception as e:
                p.append("复制模拟异常: " + repr(e))
        else:
            p.append("缺少 _preview.html")
        results.append(info)

    # ---- CSDN Markdown 转换器回归 ----
    # 同一批 fixtures 也跑 csdn_convert（显式 --keep-img 保留图片导出能力不被回归漏掉），
    # 锁定：产物存在 / 图片渲染（导出文件）/ 纯 Markdown 无 HTML 标签残留 / 不泄漏公众号预览 UI
    # / 不残留文末截图占位区。
    for fx in fixtures:
        src = os.path.join(FIX, fx)
        name = os.path.splitext(fx)[0]
        odir = os.path.join(OUT, name)
        os.makedirs(odir, exist_ok=True)
        r = subprocess.run([PY, os.path.join(SCRIPTS, "csdn_convert.py"), src,
                           "--outdir", odir, "--name", name, "--keep-img"],
                          capture_output=True, text=True, encoding="utf-8")
        rc = r.returncode
        md = os.path.join(odir, name + "_csdn.md")
        info = {"fx": fx + " [csdn]", "rc": rc, "out": r.stdout + r.stderr,
                "wechat": md, "preview": md, "problems": []}
        p = info["problems"]
        if rc != 0:
            p.append(f"CSDN 转换退出码非0: {rc}")
        if not os.path.exists(md):
            p.append("缺少 _csdn.md")
        else:
            raw_src = io.open(src, encoding="utf-8").read()
            has_img = "<img" in raw_src
            mt = io.open(md, encoding="utf-8").read()
            if has_img:
                # 标准 MD 图片是 ![alt](url)，![] 与 ( 之间隔着 alt，
                # 因此不能用 "![](" 字面子串判断，必须用图片语法正则。
                if not re.search(r"!\[[^\]]*\]\([^)]*\)", mt):
                    p.append("CSDN MD 未渲染图片（图文并茂失败）")
                # CSDN 不支持 data-URI 内嵌图（会触发防盗链报错），
                # 所以 csdn_convert 会将 data:image 导出为独立图片文件。
                # 检查：MD 中有图片引用（文件路径或 data:URI 均可），且输出目录有图文件。
                has_data_uri_src = bool(re.search(r'<img[^>]+src="data:image', raw_src, re.I))
                has_img_in_md = bool(re.search(r"!\[[^\]]*\]\([^)]*\)", mt))
                exported_img_files = [f for f in os.listdir(odir)
                                     if re.match(r".*-img-\d+\.(jpg|jpeg|png|gif|webp)$", f, re.I)]
                if has_data_uri_src and not has_img_in_md:
                    p.append("CSDN MD 未渲染图片（图文并茂失败）")
                if has_data_uri_src and not ("data:image" in mt or exported_img_files):
                    p.append("CSDN MD 图片丢失（既无 data-URI 也无导出文件）")
            # 仅标记「真正的结构 / UI 泄漏」（带属性的 div、</div> 闭合、
            # style/button/script 标签）；不误伤正文讲解里对 <div> 的字面提及
            # （如「白名单里没有 <div>」「<div> → <section>」这类合法内容）。
            if re.search(r"<div\s+[^>]*[a-z-]+=|</div>|<style[\s>]|<button[\s>]|<script[\s>]",
                         mt, re.I):
                p.append("CSDN MD 残留 HTML 标签（应已转纯 Markdown）")
            if re.search(r"复制全文|📱 公众号|📋 复制全文", mt):
                p.append("CSDN MD 泄漏公众号预览 UI 文字")
        results.append(info)

    # ---- CSDN 默认「纯文本不嵌图」回归（用户铁律）----
    # 不传 --keep-img，锁定默认行为：剥离所有图片引用 + 无文末截图占位区 + 无 data-URI。
    fx = "csdn_demo.source.html"
    src = os.path.join(FIX, fx)
    name = "csdn_demo_noimg"
    odir = os.path.join(OUT, name)
    os.makedirs(odir, exist_ok=True)
    r = subprocess.run([PY, os.path.join(SCRIPTS, "csdn_convert.py"), src,
                       "--outdir", odir, "--name", name],
                      capture_output=True, text=True, encoding="utf-8")
    rc = r.returncode
    md = os.path.join(odir, name + "_csdn.md")
    info = {"fx": fx + " [csdn·默认纯文本]", "rc": rc, "out": r.stdout + r.stderr,
            "wechat": md, "preview": md, "problems": []}
    p = info["problems"]
    if rc != 0:
        p.append(f"CSDN 转换退出码非0: {rc}")
    elif not os.path.exists(md):
        p.append("缺少 _csdn.md")
    else:
        mt = io.open(md, encoding="utf-8").read()
        if re.search(r"!\[[^\]]*\]\([^)]*\)", mt):
            p.append("CSDN 默认不应有图片引用（应纯文本）")
        if "data:image" in mt:
            p.append("CSDN 默认不应残留 data-URI 内嵌图")
        if re.search(r"<div\s+[^>]*[a-z-]+=|</div>|<style[\s>]|<button[\s>]|<script[\s>]", mt, re.I):
            p.append("CSDN MD 残留 HTML 标签（应已转纯 Markdown）")
    results.append(info)

    ok = True
    for r in results:
        status = "PASS ✅" if not r["problems"] else "FAIL ❌"
        if r["problems"]:
            ok = False
        print(f"[{status}] {r['fx']}  (rc={r['rc']})")
        for prob in r["problems"]:
            print("    - " + prob)
        if not r["problems"]:
            for line in r["out"].splitlines():
                if "质检" in line or "转换完成" in line:
                    print("    · " + line.strip())
    print()
    print("全部通过 ✅" if ok else "存在失败 ❌ —— 重构未达标，需排查")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
