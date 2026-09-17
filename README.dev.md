# FormatMaster · 万能格式转换器

一句话：**把视频、音频、图像、文档、压缩包丢进去，统一转成你要的格式。**
国内音乐平台下载的加密文件（.ncm / .qmc* / .kgm / .kwm）可以直接解锁成原始音频。

不是"又一个格式工厂"，而是把引擎调度做对了的版本：
格式工厂最大的毛病是界面丑、参数反直觉、批量任务管理混乱——
这个项目按"引擎可插拔 + 参数说人话 + 表格化管理"重做了一遍。

---

## 它靠什么转换

软件本身不实现任何编解码，它是**引擎聚合器**。真正的转换全部交给成熟的
开源引擎，本程序只负责选引擎、拼参数、报进度、管队列：

| 类型 | 引擎 | 说明 |
|---|---|---|
| 音视频 / 动图 | **FFmpeg 6.1.1** | 随包附带，无需另装 |
| 图像 | **Pillow** + pillow-heif | 进程内执行，EXIF / 透明通道处理比 FFmpeg 精细 |
| 文档 | **PyMuPDF / python-docx / openpyxl / mammoth** | 内置通道，不依赖外部软件 |
| 文档（高保真） | **本机 WPS / Microsoft Office** | 走 COM，版式 100% 保留 |
| 压缩包 | **py7zr**（可选 7z.exe） | 7z / zip / tar / gz / bz2 / xz |

**设计要点：容器与编码器是硬约束。**
把 AAC 塞进 WebM、把 H.264 塞进 MPEG-PS，FFmpeg 会直接报 EINVAL 退出。
所以 `ffmpeg_engine.py` 里维护了一张容器规则表，严格枚举每种容器允许装
什么编码器，而不是按扩展名瞎猜。

---

## 快速开始

### 方式一：安装包（推荐）

双击 `FormatMaster_Setup_v1.1.1.exe`，一路下一步。
支持自定义安装目录、桌面快捷方式、控制面板卸载。

### 方式二：源码运行

```bat
pip install -r requirements.txt
python app/main.py
```

FFmpeg 二进制已放在 `tools/ffmpeg/bin/`。

---

## 支持范围

| 类型 | 输入 | 输出 |
|---|---|---|
| 视频 | mp4 mkv avi mov wmv flv webm ts mpg vob mxf 3gp rmvb ... | 17 种容器 |
| 音频 | mp3 wav flac aac m4a ogg opus wma ac3 aiff amr ... | 12 种 |
| 图像 | jpg png webp bmp tiff gif ico tga avif heic jp2 pcx ... | 13 种 |
| 文档 | pdf docx doc odt rtf txt html md xlsx xls csv pptx epub ... | 14 种 |
| 压缩包 | zip 7z tar gz bz2 xz iso rar(读) | 6 种 |
| 平台加密音乐 | ncm qmc* kgm kgma kgg vpr kwm ... | 剥壳后同上 |

视频源可直接提取音轨；图像源可合成 PDF；PDF 可拆成图片或文本。
一个 1280×720 的 mp4 最多能转出 27 种目标格式。

---

## 国内平台加密音乐解锁

国内各音乐平台的"下载文件"本质是**自研加密封装**：音频本体还是标准的
FLAC / MP3 / OGG / M4A，外面套了一层平台自己的壳（异或掩码、或用平台密钥
加密的文件头）。本软件内置解密器把壳剥掉，得到能通用播放的文件。

| 平台 | 扩展名 | 剥壳后 | 状态 |
|---|---|---|---|
| 网易云音乐 | `.ncm` | FLAC / MP3 | ✅ 支持 |
| QQ 音乐 | `.qmc0/2/3/4/6/8` `.qmcflac` `.qmcogg` `.tkm` `.bkc*` | MP3 / FLAC / OGG / M4A | ✅ 支持 |
| 酷狗音乐 | `.kgm` `.kgma` `.vpr` | MP3 / FLAC / OGG / M4A | ✅ 支持 |
| 酷我音乐 | `.kwm` | MP3 / FLAC / M4A / OGG / WAV | ✅ 支持 |
| QQ 音乐新版 | `.mflac` `.mgg*` `.mmp4` | — | ❌ 密钥在服务端，离线无法解密 |
| 酷狗音乐新版 | `.kgg` | — | ❌ 密钥由 70 MB 公钥表派生 |

### 酷狗的多代头部

酷狗这几年前后换过好几套壳，靠文件头**前 8 字节**区分家族：

| 头部前 8 字节 | 代次 | 处理 |
|---|---|---|
| `7C D5 32 EB 86 02 7F 4B` | KGM / KGMA | 异或掩码解密 |
| `05 28 BC 96 E9 E4 5A 43` | VPR | 异或掩码 + Viper 附加层 |
| `7F 4B 47 4D`（`0x7F` + `"KGM"`） | KGG / 新版 | 不支持，密钥靠公钥表派生 |
| `4B 47 4D 41`（ASCII `"KGMA"`） | 旧版 KGMA | 不支持，AES 封装 |

两个容易踩的坑，都在解密器里处理掉了：

1. **只有前 8 字节稳定**。后 8 字节随客户端版本变化，所以判定用前缀匹配；
   用全 16 字节相等会把一部分真实文件直接判死。
2. **音频起点不能只信文件里 `0x10` 那个字段**（不同变体含义不一样，有的甚至是 0）。
   解密器依次尝试"声明值 → 常见值 → 声明值附近微调"，每一步都拿
   "解出来像不像音频"来反验；校正生效时会记在日志里。

认得出代次但解不开的（KGG、旧版 KGMA），会明确说出是**哪一代、为什么**，
而不是笼统回一句"文件头不匹配"。

### 密钥流是两层叠出来的

酷狗的掩码不是"查一张表"那么简单，而是乘性的两层：

```
msk8  = MASK_V2_PRE_DEF[i % 272] ^ MaskV2[i >> 4]      ← 少一层都不行
msk8 ^= (msk8 & 0x0F) << 4
```

第一层是 272 字节固定修正表；第二层是随包分发的 4 MB 表（每 16 字节共用一个表项，
覆盖前 64 MB 音频，超过会在日志里提示）。

**漏掉第一层，99% 的字节都会解错，但自测照样全绿** —— 因为样本是拿同一个函数
反向造出来的，错了也是"自洽地错"。这个坑真踩过一次（`.kgma` 就是这个原因），
现在靠官方金标向量钉住，见下。

### 官方金标向量

`tests/fixtures/` 里的密钥流是从 unlock-music 现役的酷狗解密模块
（`@xhacker/kgmwasm`）里导出来的：

* 把密钥与密文都置零，酷狗解密公式就退化成"输出 = 本次生效的密钥流"，
  于是喂一个全零文件即可把官方密钥流原样导出；
* 拿到的是**算法常量**，不是音乐内容，可以随仓库分发；
* 生成脚本：`node build/ref/make_kgm_fixtures.cjs`。

用法是**让官方实现出题、本项目解答**：测试拿官方密钥流去构造密文，再要求本项目
解回原始音频。这样自洽性再也掩盖不了算法错误。打包流程还会核对一次密钥流指纹，
对不上直接中断构建（`build/build.py` 的 `酷狗密钥流指纹` 一项）。

**默认目标是「原始格式（仅解密）」**——无损拿到原始音频，不重编码。
需要转码时再在右侧下拉框里换目标格式，此时才会"先解密、再转码"。

加密格式在侧栏有独立的「平台加密音乐」筛选项，列表里会显示平台名与
剥壳后的真实格式，不用等转换完才知道这是哪个平台的文件。

### 法律边界

只处理**你自己从平台下载/缓存的文件**，剥掉平台自研加密壳。
不涉及商业 DRM（Apple Music FairPlay、Spotify、Netflix 等）。
请遵守各平台用户协议，勿用于传播盗版内容。

### 视频平台暂不支持

`.qsv`（爱奇艺）、`.kux`（优酷）、`.qlv`（腾讯视频）这类**单文件多轨**
容器没有纳入：它们不是"一个文件对应一个音视频流"，而是分片索引 +
独立音轨 + 服务端下发的解密票据，无法套用现有的"单文件解密"契约。
`.kux` 还需要优酷自带的那份改了 demuxer 的 ffmpeg 才能直读。

---

## 使用

1. **拖进来**——文件或整个文件夹拖到窗口即可，自动识别类型；
2. **改参数**——左侧按类型筛选，右侧面板一次改完应用到所有选中文件；
   工具栏的「批量设为」可以把全表统一成某个格式；
3. **开始转换**——表格里实时看每个文件的状态与进度，日志区记录细节。

参数面板按文件类型自动切换：视频出现编码器/分辨率/帧率，图像出现尺寸/
EXIF，文档出现转换通道选择。选「原始格式（仅解密）」时参数面板自动收起
——不重编码，那些参数没有意义。

输出位置可选「与源文件同目录」或「统一输出到指定目录」。

---

## 目录结构

```
FormatMaster/
├── app/
│   ├── main.py                     程序入口
│   ├── assets/                     图标 + 酷狗密钥表 kgm.mask
│   ├── core/
│   │   ├── formats.py              格式目录（唯一事实来源）
│   │   ├── registry.py             引擎注册与格式路由
│   │   ├── jobs.py                 任务模型 + 并发调度器
│   │   ├── runtime.py              外部程序定位
│   │   ├── decryptors/             平台加密格式解密层
│   │   │   ├── base.py             解密器抽象基类 + 容器嗅探
│   │   │   ├── _aes.py             纯 Python AES-128-ECB（零依赖）
│   │   │   ├── ncm.py              网易云音乐
│   │   │   ├── qmc.py              QQ 音乐
│   │   │   ├── kugou.py            酷狗音乐（KGM / KGMA / VPR，多代头部识别）
│   │   │   ├── kuwo.py             酷我音乐
│   │   │   └── _kgm_tables.py      由 build/ref/gen_tables.py 生成
│   │   └── engines/
│   │       ├── base.py             引擎抽象基类
│   │       ├── ffmpeg_engine.py    音视频引擎
│   │       ├── image_engine.py     图像引擎
│   │       ├── doc_engine.py       文档引擎（双通道）
│   │       └── archive_engine.py   压缩包引擎
│   └── ui/
│       ├── main_window.py          主窗口
│       ├── widgets.py              自绘委托等组件
│       └── theme.py                配色与样式表
├── tools/
│   ├── ffmpeg/bin/                 ffmpeg.exe / ffprobe.exe
│   ├── make_icon.py                生成应用图标
│   ├── diagnose_encrypted.py       加密文件诊断（只读，看头部与代次）
│   ├── try_doc.py                  手动试跑文档转换（打印走哪条通道 + 产物体检）
│   ├── ui_probe.py                 离屏渲染辅助（注册字体/套样式表/整窗取样）
│   ├── shot_ui.py                  渲染主窗口截图，改版前后对比
│   ├── ui_crop.py                  截图局部裁剪 + 主色统计
│   └── icon_sheet.py               图标对照表，肉眼验收
├── tests/
│   ├── selftest.py                 通用转换端到端（33 个用例）
│   ├── selftest_decrypt.py         解密层逐字节校验（33 个用例，含官方金标）
│   ├── selftest_pipeline.py        调度链路：剥壳 → 转码（19 个用例）
│   ├── selftest_ui.py              界面集成 + 外观像素断言，无头（27 个用例）
│   ├── selftest_docs.py            文档转换内容正确性（28 个用例）
│   └── fixtures/                   官方实现导出的金标密钥流
├── build/
│   ├── FormatMaster.spec           PyInstaller 配置
│   ├── installer.iss               Inno Setup 安装包配置
│   ├── build.py                    一键打包（含密钥流指纹校验）
│   └── ref/                        参考实现、常量提取脚本
│       ├── kgm.cpp                 官方酷狗算法源码（KgmWasm 项目）
│       ├── KgmWasm/ QmcWasm/       官方 WASM 模块（用作"裁判"）
│       ├── make_kgm_fixtures.cjs   从官方 WASM 导出金标密钥流
│       └── gen_tables.py           从参考实现提取常量表
└── requirements.txt
```

---

## 开发

### 跑自测

```bat
python tests/selftest.py            :: 通用转换，33 个用例
python tests/selftest_decrypt.py    :: 解密正确性，33 个用例（含官方金标）
python tests/selftest_pipeline.py   :: 调度链路，19 个用例
python tests/selftest_ui.py         :: 界面集成 + 外观像素断言（无头），27 个用例
python tests/selftest_docs.py       :: 文档转换内容正确性，28 个用例
```

`selftest.py` 会自动生成测试素材；解密相关三套按各平台算法**反向构造**加密
样本，不需要真实的平台下载文件，比对输出与原始音频是否逐字节一致。
酷狗部分另有 4 条**官方金标**用例（见上文），样本由官方实现的密钥流构造。

### 排查加密文件

某个加密文件转不了时，先用诊断工具看它卡在哪一步——不用开软件、不用重新打包：

```bat
python tools\diagnose_encrypted.py "D:\Music\某首歌.kgma"
python tools\diagnose_encrypted.py "D:\Music"          :: 也可以整个目录一起看
```

它会打印文件头 hexdump、与各代头部家族逐个比对、判定代次、真实格式，
以及"能不能解密"。**只读**，不会修改或解密你的文件。

写测试样本时有两个坑，都踩过：

1. **不要只用自家常量反向构造样本**。那样样本永远与实现自洽，测不出真实世界的
   头部变体——`.kgma` 的多代头部当初就是这么漏掉的。变体要用
   `make_kgm(..., magic=..., declared=...)` 显式造出来。
2. **自洽性检查不等于正确性检查**。加解密两边共用同一个函数时，算法整体错位也
   照样恒真通过。凡是能拿到官方实现的格式，都应当补一组官方金标向量
   （`tests/fixtures/`，生成脚本在 `build/ref/`）。

### 新增一个格式

1. 在 `app/core/formats.py` 的目录表里加一行；
2. 确认对应引擎能处理（必要时在引擎里补一条路由）。

不需要改动界面——下拉框、批量选项、类型徽标都会自动出现。

### 新增一个引擎

1. 在 `app/core/engines/` 下写一个适配器，继承 `BaseEngine`
   （命令行型继承 `SubprocessEngine`，只需实现 `build_args` 和 `parse_progress`）；
2. 在 `app/core/registry.py` 的引擎列表里注册一行。

### 新增一个平台解密器

1. 在 `app/core/decryptors/` 下写一个 `BaseDecryptor` 子类，实现
   `matches`（魔数校验）、`probe`（读元数据 + 判断真实格式）、`decrypt`；
2. 在 `app/core/decryptors/__init__.py` 的 `_DECRYPTORS` 里加一行；
3. 在 `app/core/formats.py` 的 `_ENCRYPTED_EXT` 里登记扩展名。

界面、格式路由、任务调度都会自动跟上，不需要改别处。
解密常量建议用 `build/ref/gen_tables.py` 那类脚本从参考实现里提取，
手工抄 272 字节的掩码表迟早出错。

需要注意：**解密函数未必可逆**（例如酷狗的公式对操作数做了位变换），
写自测时不要拿"再加密一次能否还原"当判据，要单独推导逆函数。

### 打包

```bat
python build/build.py
```

依次完成：生成图标 → PyInstaller 打包 → Inno Setup 编译安装包 →
把成品复制到 `F:\project\`。

若构建环境禁止批量删除（沙箱、企业安全策略），PyInstaller 在 COLLECT
阶段清理旧输出会报错。绕过办法：构建前把 `dist\FormatMaster` 与
`build\work` **改名**移开（改名不算删除），让它写全新目录。

---

## 已知限制

- **RMVB / RM / CAB / RAR** 只能作为输入（缺编码器或依赖外部工具）；
- **HTML / Markdown → PDF** 走 Office 通道，未装 WPS/Office 时退化为纯文本排版；
- **PDF → DOCX** 需要额外安装 `pdf2docx`；
- **ISO / WIM / RAR** 需要系统里有 7z.exe 才能解锁；
- 硬件加速目前只用于解码，NVENC / QSV 编码未默认开启（核显兼容性差）；
- **QQ 音乐新版加密（`.mflac` / `.mgg*`）离线解不开**——密钥由服务端下发。
  软件会在添加文件时就明确告知，不会等到转换才失败；
- **酷狗新版 `.kgg`、旧版 ASCII 头 KGMA** 离线解不开——密钥不随文件下发。
  软件会识别出代次并说明原因，而不是只回一句"文件头不匹配"；
- **爱奇艺 `.qsv` / 优酷 `.kux` / 腾讯视频 `.qlv`** 未支持，原因见上文
  「视频平台暂不支持」；
- ~~**DOCX → PDF 中文乱码**~~（2026-09-16 用户反馈，**2026-09-17 已修**，见 v1.1.4）。
  根因有两个，详见 [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) 修复记录 #1：
  ① `_com_dispatch` 里写了 `win32com.client.client`，AttributeError 让**整条 COM
  通道对所有权 Office 文档静默失败**；② 降级后的兜底路径把 DOCX 的 ZIP 流当纯文本
  读，排出 34 页二进制垃圾。现已改为「专用排版器 + 显式嵌入中文字体 + 失败就说清」。
- **老版二进制 Office 格式**（`.doc` / `.xls` / `.ppt`）内置通道读不出正文，
  会明确报错而不是硬凑乱码；装了 WPS / Office 即可正常转换。

### 界面层面踩过的坑（写代码时请遵守）

- **给容器控件 `setStyleSheet` 必须写全选择器。** Qt 会把控件上的样式表级联给
  它的**全部子孙**，一句无选择器的 `background: #FFFFFF` 等于给里面所有按钮刷白底。
  底部动作栏就这么把「开始转换」变成白底白字 —— 按钮上的字用户根本看不见。
  容器样式一律写进 `theme.py` 的全局表、用 objectName 定位；只有叶子控件
  （QLabel、单个按钮）才可以用无选择器写法。
- **QSS 画不出「勾」「圆点」「三角箭头」。** 前三样没有形状原语；箭头那套
  CSS"用透明边框拼三角形"的技巧 Qt 不实现，照抄出来是个实心小方块。
  这三样由 `theme.stylesheet()` 在运行时用 QPainter 画好、落到临时目录、再以
  `url()` 喂给 QSS；拿不到 QApplication 时退回基础样式表。
- **`widget.grab()` 对子控件的结果不可信**（取决于样式是否已 polish，同一份代码
  会给出不同颜色），量颜色请截整窗口再按坐标取像素。同时注意
  `widget.geometry()` 是**相对父控件**的，要 `mapTo(win, ...)` 换算。
- **offscreen 平台不枚举系统字体**，中文会全变豆腐块。截图/量像素前必须
  `addApplicationFont` 注册字体，否则看到的东西和用户看到的不是一回事。
  这两条统一收在 `tools/ui_probe.py`，工具与自测共用。
