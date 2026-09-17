# 已知问题清单

> 记录用户实测反馈但**尚未修复**的问题。修完一条就把条目移到下面的「修复记录」里，
> 并在原条目位置留一行指向。当前基线版本：**v1.2.0**。

---

*（当前没有未修问题。）*

---

## 修复记录

### #1 DOCX → PDF 输出乱码 —— 已修（2026-09-17，v1.1.4）

- **状态**：✅ 已修复并回归
- **报告原话**："docx转pdf之后乱码，先记着这个文档转换这部分有问题，明天再改"
- **实际根因**：**两个独立 bug 叠加**，而且都不是原来推测的那三条候选根因。

#### 根因 A（致命）：COM 通道从未生效过

`DocumentEngine._com_dispatch()` 里写的是：

```python
app = win32com.client.DispatchEx(prog_id)     # ← 这里
```

但调用方传进来的 `win32com_client` **本身就是 `win32com.client` 模块**：

```python
def _convert_via_com(...):
    import win32com.client
    return self._com_dispatch(win32com.client, ...)   # 传的是模块
```

于是 `win32com.client.client` → `AttributeError: module 'win32com.client' has no
attribute 'client'`。这个异常被循环里的 `except Exception` 吃掉，逐个 ProgID 试完
全部失败，最后返回"Office 通道失败"。**结果：所有 Office 文档转换（docx/doc/xls/
pptx ↔ pdf/txt/html…）从来没有走过高保真通道**，全部静默降级到内置通道。
`describe()` 却仍然显示"文档转换（高保真）"——把它伪装成了正常。

> 排查时一度以为"本机没装 Office/WPS"，实际直接 `DispatchEx("Word.Application")`
> 秒回 `Microsoft Word 12.0`，装的是 Office 2007。**判据：能 DispatchEx 成功就不是
> 安装问题，是调用写法问题。**

#### 根因 B（致命）：降级后的兜底把 DOCX 当纯文本读

COM 失败 → 落到内置通道 → `docx→pdf` 没有专用实现 → `_generic_to_pdf()` →
`_text_to_pdf()` → `_read_text(src)`。而 `_read_text()` 只会按编码 `read_text()`：

```python
for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
    return path.read_text(encoding=enc)         # .docx 是 ZIP，latin-1 永远"成功"
```

latin-1 对任何字节都成立，所以**永远不报错**。DOCX 的 ZIP 流被当正文，排成
**34 页** `PK... [Content_Types].xml ...` 垃圾，还报"成功"。用户看到的"乱码"就是它。

#### 根因 C：字体不嵌入 + 静默失败

- `_font_plan()` 探测内置字体失败后试系统字体，**两条都失败时返回 `(False, None)`，
  调用方仍继续 `writer.append(..., font=None)`** → 静默出豆腐块；
- 内置 `china-s`（Heiti）导出的 PDF 字体 `ext == 'n/a'`（**未嵌入**），
  在当前机器显示正常，换机器/换阅读器必掉字；
- Story/TextWriter 若显式传 `fontfile`，会把**整个字体**（simhei 近 10 MB）塞进 PDF，
  一页文档 10 MB。

#### 顺带挖出的其他"成功但是错的"

| 现象 | 原因 | 修法 |
|---|---|---|
| `.doc → .txt` 输出的是 RTF 源码 | `doc.SaveAs2(path)` 不带 `FileFormat`，Word 2007 自己猜 | 显式 `FileFormat=2` |
| `.txt` 导出是 GBK 编码 | Word 2007 不认 `Encoding=65001` | Office 退出后转 UTF-8 |
| `.html` 导出是 gb2312 | 同上，且与内置通道的 UTF-8 不一致 | 转 UTF-8 并改 charset 声明 |
| `.doc → pdf`（内置）462 页垃圾 | OLE 二进制解码后是"可打印的 ASCII 表"，可读性 0.91 骗过了旧判据 | 加严格"像不像正文"判据，不合格就报错 |
| `.xlsx → pdf`（内置）13 页垃圾 | 同上（ZIP 二进制） | 走 openpyxl 生成 HTML 表格 |
| `.rtf → pdf`（内置）中文丢失 | 按编码读 RTF 只能看到控制字，中文藏在 `\uNNNN?` | 手写 RTF 解析器（含字体表跳过、codepage） |
| 降级后用户只看到一半真相 | `convert()` 把 COM 的失败原因丢了 | 降级成功/失败都把两条通道的原因合并上报 |

#### 修复后的行为

| 组合 | 高保真通道 | 内置通道 |
|---|---|---|
| docx → pdf | Office 导出，字体正常嵌入 | mammoth → HTML → PyMuPDF `Story` 多页排版，显式嵌入 SimHei |
| doc/rtf/html/md → pdf | 同上 | rtf 走自研解析器；html/md 走 Story 排版 |
| xlsx → pdf | Excel `ExportAsFixedFormat` | openpyxl → HTML 表格 |
| doc/xls/ppt（老二进制） | 正常 | **明确报错**并提示装 Office，不再产乱码 |

字体策略统一为：**优先显式嵌入系统中文字体**（simhei → msyh → simsun → Deng →
simfang），两条路都不通才用内置 `china-s`，并在结果消息里注明"字体未嵌入，
换机器可能显示异常"。PDF 写完后统一 `subset_fonts()` + `garbage=4` 子集化，
一页文档 10.0 MB → 66 KB。

#### 踩坑记录（值得记住的）

1. **`fitz.DocumentWriter` 在 `close()` 之后仍持有文件句柄。** Windows 上表现为
   后续 `os.replace()` 直接 `PermissionError`。因为它只支持写文件（不能写内存），
   所以必须先写 `.stage.pdf` 中间文件，`del writer` + `gc.collect()` 释放句柄后
   再子集化、再移到最终路径。——这个 bug 一开始被 `except Exception` 吞掉，
   表现成"子集化偶发不生效"，白查了一轮。
2. **工具函数不要吞异常。** `_com_dispatch` 的 `except Exception: continue` 把一个
   `AttributeError` 藏了一年多（这个模块从第一版就写错了）。
3. **`_read_text` 这类"多编码轮询"函数天生不会失败**（latin-1 兜底），
   必须显式拒绝二进制容器，否则它就是乱码发生器。
4. **测试只验"文件非空"等于没验。** 34 页垃圾当初能全绿通过。

#### 新增的工具与测试

- `tools/try_doc.py <源> <目标> [--engine auto|builtin] [--expect 中文串]` ——
  打印实际通道 + 产物体检（页数 / 文本层 / 字体是否嵌入 / 坏码点 / 断言）。
- `tests/selftest_docs.py`（**28 项**）—— 文档内容正确性专项。
- `tests/selftest.py` 末尾新增「内容正确性校验」三条断言（文本层含已知中文、
  字体已嵌入、页数合理），总项数 30 → 33。

---

### 原条目存档（供追溯）

<details>
<summary>修复前记录的候选根因与诊断命令（点击展开）</summary>

- **状态**：待修（2026-09-16 用户报告，约定次日处理）
- **报告原话**："docx转pdf之后乱码，先记着这个文档转换这部分有问题，明天再改"
- **影响面**：`DocumentEngine`，路由键 `("docx","pdf")`（以及所有 `dst_ext == "pdf"` 的兜底路径）

### 还需要向用户确认的 5 件事

| # | 要问的 | 为什么关键 |
|---|---|---|
| 1 | 在哪台机器上转的（本机 WPS / 另一台） | 决定走哪条通道 |
| 2 | "乱码"长什么样：方框/问号/错字/复制出来乱 | 三种形态对应三条不同根因 |
| 3 | 用什么阅读器打开的（Edge、WPS、Adobe、国产阅读器） | 区分"文件坏"还是"阅读器缺字体" |
| 4 | 源 docx 里中文用的什么字体（宋体/黑体/微软雅黑/方正系） | 系统无该字体时会触发替换 |
| 5 | 是拖进 exe 转的，还是源码跑的 | exe 里依赖可能被裁 |

### 三条候选根因（按可能性排序）

#### 通道 A：COM 高保真（本机默认走这条）

本机 `office_com_available() == True`（已装 WPS + `win32com` 可用），
`DocumentEngine._com_channel("docx","pdf")` 为真 → **正常情况下 docx→pdf 走 WPS COM**。

于是乱码最可能是 **PDF 未嵌入中文字体**：WPS 导出时若按"不嵌入字体"输出，
在原机器上看着正常，换机器/换阅读器就掉字成方框。

- 修法：COM 导出后加一道**字体嵌入自检**（见下方诊断命令第 2 步）。
  检出 `ext == "n/a"`（未嵌入）时，要么提示用户，要么自动改走内置通道重来。
- 备选：调用 `ExportAsFixedFormat` 时显式带上嵌入字体的参数，
  或写入 WPS 对应注册表项强制嵌入（需实测确认键名）。

#### 通道 B：内置排版 `_generic_to_pdf()` → `_text_to_pdf()`

内置通道下 docx→pdf **没有专用实现**，直接落到兜底：`mammoth.extract_raw_text()`
抽纯文本 → PyMuPDF `insert_text(fontname="china-s")` 重排。版式丢失属预期
（函数注释已声明是兜底），但乱码有两个独立来源：

1. **`_font_plan()` 有静默失败分支**（bug）
   探测 `china-s` 失败 → 退到 `_pick_cjk_font()`（msyh.ttc / msyh l / simsun.ttc）；
   **系统字体也取不到时返回 `(False, None)`，而 `_text_to_pdf()` 仍继续
   `writer.append(point, line, None, fontsize=...)`** → 必然乱码/豆腐块。
   这里缺一个"两条路都不可用 → 明确报错"的分支。
2. **PyMuPDF 内置 CJK 字体（Droid Sans Fallback）文本层编码不是 Unicode**
   视觉正常，但**复制 / 搜索 / 用别的工具再提取出来就是乱码**。
   如果用户的"乱码"是复制出来的，根因就是这条。

- 修法：不再依赖 `china-s`，改为显式 `fontfile` 嵌入系统 CJK 字体
  （`fitz.Font(fontfile=...)` + `TextWriter`），或升级用 `page.insert_htmlbox()`
  （PyMuPDF ≥1.24，内部走 HTML 排版，字体可控）。

#### 通道 C：`_docx_xml_text()` 兜底扒 XML

mammoth 缺失或抛异常时的路径：`re.sub(r"<[^>]+>", "", xml)` 会把
`<?xml ...?>` 声明、`w:` 命名空间残留等漏进正文。严格说不是"乱码"，
但用户看到的就是一串垃圾字符。修法：先精确取 `<w:t>` 文本节点再 `unescape`。

### 明天的诊断命令（直接照抄）

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
cd F:/project/FormatMaster

# 1) 确认实际走哪条通道
$PY -c "import sys;sys.path.insert(0,'.');from app.core.engines.doc_engine import DocumentEngine as E;e=E();print('com_ok=',e.com_ok(),'| pymupdf_ok=',e.pymupdf_ok(),'|',e.describe('docx','pdf'))"

# 2) 转一份，再查字体是否嵌入（ext == 'n/a' 就是没嵌入）
$PY tools/try_doc.py "一些带中文的.docx" tests/_work/doc_out.pdf
$PY -c "import fitz;d=fitz.open('tests/_work/doc_out.pdf');print([(f[1],f[3]) for p in d for f in p.get_fonts(full=True)])"

# 3) 查文本层对不对（提取乱 = 文本层编码坏；提取对但视觉乱 = 字体没嵌入）
$PY -c "import fitz;print(repr(fitz.open('tests/_work/doc_out.pdf')[0].get_text()[:200]))"

# 4) 强制走内置通道复现一遍（隔离 COM 变量）
#    用 options.doc_engine = "builtin"（ConvertOptions 字段）
```

**判定表**

| `get_text()` 提取 | 视觉 | 结论 |
|---|---|---|
| 正确 | 正常 | 文件没问题，是对方阅读器/字体问题 |
| 乱 | 乱 | 内置通道字体编码问题（通道 B-2） |
| 正确 | 乱 | 字体未嵌入（通道 A） |

### 缺失的测试（明天一起补）

- `tests/selftest.py:145` 有一条 `("docx","docx","pdf","DOCX→PDF 高保真")`，
  只验了"能转出 PDF"，**没验"中文不乱码"**——这正是这次的盲区。
- 补两条断言：
  1. 产出的 PDF `page.get_text()` 必须包含源文档里的已知中文串（防编码坏）；
  2. `page.get_fonts(full=True)` 里中文字体的 `ext != "n/a"`（防没嵌入）。
- 样本自己造，别用外部文件：`python-docx` 写一段固定中文 + 指定"宋体"，
  跑完立刻断言（参考 `selftest_pipeline.make_audio_with_cover` 的教训：
  **样本坏会伪装成产品坏**）。

### 顺手补的工具

`tools/` 下目前只有音视频的手动试跑脚本（`try_pipeline.py` / `try_real.py`），
文档转换还没有。明天加一个 `tools/try_doc.py <源> <目标>`，复用 `DocumentEngine`，
打印走哪条通道 + 结果，便于以后报障时一步定位。

</details>

---

### #2 界面：底部「开始转换」按钮上的字看不见 —— 已修（2026-09-17，v1.2.0）

- **状态**：✅ 已修复并加了像素级回归
- **报告原话**："优化ui"
- **实际根因**：**Qt 样式表在容器上的级联**。动作栏是这么写的：

```python
bar = QFrame()
bar.setStyleSheet(f"background: {theme.BG_PANEL}; border-top: 1px solid {theme.BORDER};")
```

  这条规则**没有选择器**。Qt 的规矩是"控件上的样式表会作用于该控件及其全部子孙"，
  于是一句 `background: #FFFFFF` 把动作栏里三个按钮统统刷成白底。而
  `QPushButton#PrimaryButton` 里的 `color` 仍然是白色 —— **白底白字**，
  「开始转换」四个字彻底看不见，只剩一个空边框（按钮本身还占着 132×36）。

  实测证据（同一份代码，只改这一处）：

| 动作栏样式表 | 「开始转换」内部像素 |
|---|---|
| `background: #FFFFFF; border-top: ...`（原样） | `#ffffff` ← 白底白字 |
| 清空 | `#1f365d` ✅ |
| 改成 `#ActionBar { ... }` | `#1f365d` ✅ |

- **修复**：
  1. 动作栏的样式挪进全局表，用 `QFrame#ActionBar` 选择器；
  2. 在 `theme.py` 顶部写下铁律：**往容器上 `setStyleSheet` 必须写全选择器**，
     只有叶子控件（QLabel、单个按钮）才允许无选择器写法；
  3. 全项目复查了其余 5 处内联样式：`engine_strip`（有 QLabel 选择器）、
     `empty_tip` / `log_toggle` / `clear`（叶子）都安全，一并挪进全局表统一管理。

### #3 界面：下拉框右侧是「小方块」而不是三角箭头 —— 已修

`QComboBox::down-arrow` 里抄了 CSS 圈子那套"用透明边框拼三角形"的写法：

```css
border-left: 4px solid transparent;
border-right: 4px solid transparent;
border-top: 5px solid #6B7280;
```

**Qt 的 QSS 不支持这个技巧**，渲染出来就是个实心小方块。改成用 `icons.py`
画的真箭头图片（见下）。

### #4 界面：复选框勾上之后是实心蓝方块，没有「勾」—— 已修

原样式只给了 `background: {PRIMARY}`，`image: none`。勾选与未勾选只差一个填色，
看不出"勾"的语义，和 Windows 自身的习惯也不一致。

### #5 界面：首次启动时空表格和空状态提示同时显示 —— 已修

`_update_empty_tip()` 只在"添加文件"和"重建列表"时被调用，**构建界面时从没调过**。
所以第一次打开程序，上半屏是空表格、下半屏才是"把文件拖到这里"，两块挤在一起。
修复：`_build_ui()` 末尾调一次。

### #6 界面：「…」浏览输出目录按钮是空的 —— 已修

`QPushButton { padding: 7px 16px; }` 配 `setFixedWidth(34)`，内容区宽 = 34−32−2 = 0，
文字被挤没了。改成图标按钮（文件夹图标）+ `#MiniButton` 样式。

### #7 界面：参数面板底衬色与窗口不一致 —— 已修

`QScrollArea { background: transparent; }` 对 viewport 无效 —— viewport 是独立子控件，
仍按 Qt 默认窗口色 `#efefef` 画，和本窗口的 `#F4F6F9` 对不上。
修复：给参数面板容器一个显式 objectName（`#ParamHolder`）并在全局表里指定底色，
比去猜 viewport 的内部 objectName 可靠。

#### 顺带做的界面优化（非 bug）

| 项 | 说明 |
|---|---|
| 参数分组折叠 | 8 张卡片常年全展开、要滚动才能看完。改成「输出格式/输出位置/画质」默认展开，其余收起；展开状态记进 QSettings |
| 工具栏图标 | 7 个矢量图标（`app/ui/icons.py`，QPainter 现画，不引资源文件） |
| 侧栏数量徽标 | 自绘 delegate，名称左对齐 + 数字右对齐（QSS 表达不了两端布局） |
| 空状态改版 | 图形 + 主副文案 + 「选择文件 / 添加文件夹」两个入口 + 加密音乐能力说明 |
| 运行日志默认收起 | 标题上带条数「▸ 运行日志（6 条）」，把纵向空间留给列表 |
| 总体进度条 | 显示百分比文字，高度 10 → 16px。同时保持「3/11 完成」的文字说明 |
| 表格 | 行高 40 → 38，打开极淡斑马纹；补上 `item:selected:hover` 免得悬停时选中态被盖掉 |
| 拖拽反馈 | 拖入时空状态变蓝底虚线框 + 状态栏提示"松开鼠标即可添加" |
| 路径框 | 只读框默认显示路径尾部（盘符被截掉），改为 `setCursorPosition(0)` 显示开头 |
| 顶栏 | 加品牌标记（与 app.ico 呼应），引擎状态点换成深底上更协调的浅绿/浅红 |

#### 踩坑记录

1. **`widget.grab()` 对子控件的结果不可信**。同一份样式表，两次跑出
   `#ffffff` 和 `#000000`，因为它取决于样式是否已完成 polish。
   量颜色必须**截整窗口再按坐标取像素** —— 那才是用户看到的东西。
2. **`widget.geometry()` 是相对父控件的**，拿去当窗口坐标采样会取到完全无关的位置。
   要 `widget.mapTo(win, widget.rect().topLeft())`。
3. **Qt 的 QSS 画不出勾、圆点、三角箭头**。前三样都没有形状原语，
   第四样的 CSS 边框技巧 Qt 不实现。只能喂图片 —— 而这里又不想为三个 12px
   图形引入资源文件与打包路径配置，于是 `theme.stylesheet()` 在运行时用
   QPainter 画一次、写到 `%TEMP%\formatmaster_ui\`、把路径塞进 `url()`。
   拿不到 QApplication（纯 import 做静态检查）就退回基础样式表。
4. **离屏（offscreen）平台不枚举系统字体**，`QFontDatabase.families()` 返回空列表，
   中文全渲染成豆腐块。截图/量像素前必须手动 `addApplicationFont` 注册字体，
   否则截出来的图根本不是用户看到的样子。这两条坑统一收在 `tools/ui_probe.py`。
5. **"界面自测全绿"不等于界面对**。上面 #2 那个白底白字的 bug 存在期间，
   19 项界面自测全部通过 —— 因为它们只查数据，不查像素。

#### 新增的工具与测试

- `tools/ui_probe.py`：离屏渲染辅助（注册字体、套真实样式表、整窗取样、
  行内扫色、抽干事件队列）。工具与自测共用，避免两边各写一份。
- `tools/shot_ui.py`：一条命令渲染主窗口截图（`--scenario busy|empty`、`--scale 2`），
  用于改版前后对比。
- `tools/ui_crop.py`：截图局部裁剪 + 主色统计，客观确认"看起来有问题"的地方。
- `tools/icon_sheet.py`：把全部图标渲染成对照表，肉眼验收图标质量。
- `tests/selftest_ui.py` **新增第 9 节「界面外观：截真实窗口取像素」**（19 → 27 项）：
  - 主按钮底色必须是品牌深蓝（直接冲着 #2 来的回归）；
  - 侧栏选中行为浅蓝底；
  - 侧栏各类计数与列表一致；
  - 视频参数分组默认收起、输出格式默认展开；
  - 运行日志默认收起；
  - 有文件时表格可见且空状态隐藏。

  这一节的意义在于：**把"用户能不能看见按钮上的字"变成一条会自动失败的断言**。
