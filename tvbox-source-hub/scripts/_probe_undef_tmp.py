# 本文件语法完全合法，但引用了不存在的名字（模拟"改调用姿势漏清旧变量"）
def f():
    try:
        raise ValueError("x")
    except Exception:
        return _this_name_does_not_exist_anywhere
