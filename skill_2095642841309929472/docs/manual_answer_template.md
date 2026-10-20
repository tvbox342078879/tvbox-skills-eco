# 参考答案文件（详细版）模板

> 外置自 SKILL.md（S179 体积治理，M-R77⑥ 法③）。正本在本文件。

### 参考答案文件（详细版）模板

单独成文件（文件名与试卷对应：如 `X卷.html` → `X卷_答案.html`）。结构：标题（试卷名 + "参考答案（详细版）"）+ 副标题（版本·满分·说明）+ 按大题分节；每题给红色【答案】+【解析】，阅读/完形/材料题加【原文依据】，实验/计算题给分步，主观题给评分维度与范文。版式为黑白印刷风，不需要密封线、考生信息栏，保持简洁清晰。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{学科}{年级} 参考答案（详细版）</title>
<style>
  :root{ --ink:#111; --sub:#555; }
  *{box-sizing:border-box;}
  body{font-family:"SimSun","Songti SC","STSong","FangSong",serif;color:var(--ink);background:#fff;font-size:14px;line-height:1.9;max-width:780px;margin:16px auto;padding:0 18px;}
  h1{font-family:"SimHei","Heiti SC",sans-serif;font-size:18px;text-align:center;font-weight:700;margin:0 0 4px;}
  .sub{text-align:center;font-size:12px;color:var(--sub);margin-bottom:14px;}
  .sec{font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;font-size:15px;margin:16px 0 6px;border-bottom:1.5px solid #000;padding-bottom:3px;}
  .q{margin:9px 0;}
  .no{font-weight:700;}
  .ans{color:#b00020;font-weight:700;font-family:"SimHei","Heiti SC",sans-serif;}
  .why{color:#222;margin-top:2px;}
  .eq{font-family:"Cambria Math","Times New Roman",Georgia,serif;font-style:italic;}
  .calc{background:#f5f5f5;border:1px solid #ddd;padding:8px 12px;margin:5px 0;font-family:"Cambria Math","Times New Roman",serif;font-size:13px;line-height:1.7;}
  .calc b{font-family:"SimHei","Heiti SC",sans-serif;}
  .lab{display:inline-block;min-width:40px;font-family:"SimHei","Heiti SC",sans-serif;font-weight:700;}
  .warn{border:1px solid #999;background:#fff8e8;padding:6px 10px;margin:8px 0;font-size:13px;}
  .warn b{font-family:"SimHei","Heiti SC",sans-serif;color:#8a5a00;}
  .pf{color:#666;font-size:12px;margin-top:10px;}
  @media print{body{font-size:13px;}}
</style>
</head>
<body>

<h1>{学科}{年级}测试卷 · 参考答案（详细版）</h1>
<div class="sub">【{教材版本}】{单元/知识点}　满分 {满分} 分　（本文件独立成卷，与试卷配套使用）</div>

<!-- 理科（化学/物理等）可加评分门槛提示框；其他学科省略 -->
<div class="warn"><b>{学科}阅卷评分门槛（供教师把握）：</b>{化学式错整题0分；方程式未配平/漏条件/漏↑↓按步扣分；计算缺"已知—求—解"按步扣分}</div>

<div class="sec">一、{题型}（{分} 分）</div>
<div class="q"><span class="no">1.</span><span class="ans">{精确答案}</span>
  <div class="why">【解析】{考点/规则/易错提醒；客观题说明为何选、为何不选}</div></div>

<div class="sec">四、阅读理解（{分} 分）</div>
<div class="q"><span class="no">1.</span><span class="ans">{答案}</span>
  <div class="why">【原文依据】{出处句}；【解析】{推理过程}</div></div>

<div class="sec">{计算题大题}</div>
<div class="q"><span class="no">17.</span>（{分} 分）
  <div class="calc"><span class="lab">已知：</span>{条件}<br>
  <span class="lab">求：</span>{目标}<br>
  解：{关系式 / 方程式}<br>
  &nbsp;&nbsp;{代入数据（带单位）} → <b>{结果（带单位）}</b></div>
  <div class="why">【评分】{公式或方程式 n 分，代入 n 分，结果 n 分}</div></div>

<div class="sec">六、书面表达（{分} 分）</div>
<div class="q"><span class="no">评分维度：</span>
  <div class="why">内容要点 / 语言（时态语法）/ 书写 / 字数，共 {分} 分，分项列出。<br>
  【参考范文】{示例短文，便于教师把握满分尺度}</div></div>

<div class="pf">提示：{如实验装置图以占位框呈现，教学时补绘；或阅读素材标注（自拟）的说明}</div>

</body>
</html>
```
