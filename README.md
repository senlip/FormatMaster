# FormatMaster · 万能格式转换器

把**视频、音频、图像、文档、压缩包**丢进窗口，统一转成你要的格式。
国内音乐平台下载的加密文件（`.ncm` / `.qmc*` / `.kgm` / `.kwm`）可以剥掉平台自研的
加密壳，还原成通用播放器能直接播的原始音频。

> **当前状态**：`v1.2.0` · 仅 Windows 10 / 11
>
> 📦 **下载安装包** → [Releases · v1.2.0](https://github.com/senlip/FormatMaster/releases/latest)
>
> 源码已开源。仓库**不含** ffmpeg 二进制、构建产物与第三方参考实现，
> 完整排除规则见 [`.gitignore`](.gitignore)。

---

## 为什么不是"又一个格式工厂"

格式工厂那类工具的通病很一致：引擎选错、参数反直觉、批量任务管理混乱。
这个项目就按这三条重做了一遍：

| 原则 | 具体做法 |
|---|---|
| **引擎可插拔** | 程序自身不做任何编解码，只负责选引擎、拼参数、报进度、管队列 |
| **参数说人话** | 参数面板按文件类型自动切换；音频文件不会看到"分辨率"，选"仅解密"时面板自动收起 |
| **表格化管理** | 一个窗口管完整批任务，每个文件独立进度与状态，日志区保留细节 |

---

## 转换能力

| 类型 | 输入 | 输出 |
|---|---|---|
| 视频 | mp4 mkv avi mov wmv flv webm ts mpg vob mxf 3gp rmvb … | 17 种容器 |
| 音频 | mp3 wav flac aac m4a ogg opus wma ac3 aiff amr … | 12 种 |
| 图像 | jpg png webp bmp tiff gif ico tga avif heic jp2 pcx … | 13 种 |
| 文档 | pdf docx doc odt rtf txt html md xlsx xls csv pptx epub … | 14 种 |
| 压缩包 | zip 7z tar gz bz2 xz iso rar（读） | 6 种 |
| 平台加密音乐 | ncm qmc* kgm kgma vpr kwm | 剥壳后同上 |

跨类型也支持：视频源可直接提取音轨、图像源可合成 PDF、PDF 可拆成图片或文本。
一个 1280×720 的 mp4 最多能转出 27 种目标格式。

## 它靠什么转换

软件本身不实现任何编解码，它是**引擎聚合器**，真正的转换全部交给成熟的开源引擎：

| 类型 | 引擎 | 说明 |
|---|---|---|
| 音视频 / 动图 | **FFmpeg 6.1.1** | 随包附带，无需另装 |
| 图像 | **Pillow** + pillow-heif | 进程内执行，EXIF / 透明通道处理比 FFmpeg 精细 |
| 文档 | **PyMuPDF / python-docx / openpyxl / mammoth** | 内置通道，不依赖外部软件 |
| 文档（高保真） | **本机 WPS / Microsoft Office** | 走 COM，版式 100% 保留 |
| 压缩包 | **py7zr**（可选 7z.exe） | 7z / zip / tar / gz / bz2 / xz |

**设计要点：容器与编码器是硬约束。**
把 AAC 塞进 WebM、把 H.264 塞进 MPEG-PS，FFmpeg 会直接报 `EINVAL` 退出。
所以引擎里维护了一张容器规则表，严格枚举每种容器允许装什么编码器，
而不是按扩展名瞎猜——这是"转出来能播"和"转完打不开"的分水岭。

---

## 快速开始

### 安装包（推荐）

到 [Releases](https://github.com/senlip/FormatMaster/releases/latest) 下载
`FormatMaster_Setup_v1.2.0.exe`（约 105 MB），双击安装。已装旧版可直接覆盖。

安装包**自带 FFmpeg**，无需另行配置。

### 源码运行

```bat
git clone https://github.com/senlip/FormatMaster
cd FormatMaster
pip install -r requirements.txt
python app\main.py
```

> ⚠️ **本仓库不含 `tools\ffmpeg\`**（第三方 GPL 二进制，约 158 MB）。
> 源码运行要处理视频 / 音频时，需自行下载 ffmpeg 与 ffprobe 放进
> `tools\ffmpeg\bin\`；图像、文档、压缩包、加密音乐解锁这四类不依赖它。

**环境要求**

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（文档高保真通道依赖 COM，暂无跨平台计划） |
| Python | 3.11+ |
| 界面 | PySide6 |
| 可选 | 本机装有 WPS 或 Microsoft Office → 文档转换启用高保真通道 |

### 自己出包

```bat
python build\build.py
```

会依次完成：生成图标 → PyInstaller 打包 → **冻结产物自检** → Inno Setup 编译安装包。
自检不通过会直接中断构建。需要本机具备 PyInstaller 与 Inno Setup
（Inno Setup 放在 `build\tools\`，未入库）。

出完包用同一个脚本发版：

```bat
python tools\gh_release.py --tag v1.2.1 ^
    --asset F://project//FormatMaster_Setup_v1.2.1.exe
```

---

## 国内平台加密音乐解锁

> ⚠️ **仅用于处理你自己从平台下载或缓存的文件，请先阅读文末「法律声明」。**

国内各音乐平台的"下载文件"本质是**自研加密封装**：音频本体仍然是标准的
FLAC / MP3 / OGG / M4A，外面套了一层平台自己的壳（异或掩码，或用平台密钥
加密的文件头）。本软件内置解密器把壳剥掉，得到能通用播放的文件。

| 平台 | 扩展名 | 剥壳后 | 状态 |
|---|---|---|---|
| 网易云音乐 | `.ncm` | FLAC / MP3 | ✅ 支持 |
| QQ 音乐 | `.qmc0/2/3/4/6/8` `.qmcflac` `.qmcogg` `.tkm` `.bkc*` | MP3 / FLAC / OGG / M4A | ✅ 支持 |
| 酷狗音乐 | `.kgm` `.kgma` `.vpr` | MP3 / FLAC / OGG / M4A | ✅ 支持 |
| 酷我音乐 | `.kwm` | MP3 / FLAC / M4A / OGG / WAV | ✅ 支持 |
| QQ 音乐新版 | `.mflac` `.mgg*` `.mmp4` | — | ❌ 密钥在服务端，离线无法解密 |
| 酷狗音乐新版 | `.kgg` | — | ❌ 密钥由 70 MB 公钥表派生 |

**默认目标是「原始格式（仅解密）」**——无损拿到原始音频，不做重编码。
需要转码时再在右侧下拉框里换目标格式，此时才会"先解密、再转码"。
加密格式在侧栏有独立的「平台加密音乐」筛选项，列表里直接显示平台名与
剥壳后的真实格式，不用等转换完才知道这是哪个平台的文件。

### 多代头部：只有前 8 字节稳定

酷狗这几年前后换过好几套壳，靠文件头**前 8 字节**区分家族：

| 头部前 8 字节 | 代次 | 处理 |
|---|---|---|
| `7C D5 32 EB 86 02 7F 4B` | KGM / KGMA | 异或掩码解密 |
| `05 28 BC 96 E9 E4 5A 43` | VPR | 异或掩码 + Viper 附加层 |
| `7F 4B 47 4D`（`0x7F` + `"KGM"`） | KGG / 新版 | 不支持，密钥靠公钥表派生 |
| `4B 47 4D 41`（ASCII `"KGMA"`） | 旧版 KGMA | 不支持，AES 封装 |

两个容易踩的坑都在解密器里处理掉了：

1. **判定必须用前缀匹配。** 后 8 字节随客户端版本变化，用全 16 字节相等
   会把一部分真实文件直接判死；
2. **音频起点不能只信文件里 `0x10` 那个字段**（不同变体含义不同，有的甚至是 0）。
   解密器依次尝试"声明值 → 常见值 → 声明值附近微调"，每一步都拿
   "解出来像不像音频"反验，校正生效时记进日志。

认得出代次但解不开的（KGG、旧版 KGMA），会明确说出是**哪一代、为什么**，
而不是笼统回一句"文件头不匹配"。

### 密钥流是两层叠出来的

酷狗的掩码不是"查一张表"那么简单，而是乘性的两层：

```
msk8  = MASK_V2_PRE_DEF[i % 272] ^ MaskV2[i >> 4]      ← 少一层都不行
msk8 ^= (msk8 & 0x0F) << 4
```

第一层是 272 字节固定修正表，第二层是随包分发的 4 MB 表（每 16 字节共用一个表项，
覆盖前 64 MB 音频，超出会在日志里提示）。

> **漏掉第一层，99% 的字节都会解错，但自测照样全绿** —— 因为样本是拿同一个函数
> 反向造出来的，错了也是"自洽地错"。这个坑真踩过一次（`.kgma` 就是这个原因），
> 现在靠官方金标向量钉住。

### 官方金标向量：让官方出题、本项目解答

`tests\fixtures\` 里的密钥流是从 unlock-music 现役的酷狗解密模块
（`@xhacker/kgmwasm`）里导出来的：

- 把密钥与密文都置零，解密公式退化成"输出 = 本次生效的密钥流"，
  喂一个全零文件即可把官方密钥流原样导出；
- 拿到的是**算法常量**，不是音乐内容，可以随仓库分发；
- 生成脚本：`node build\ref\make_kgm_fixtures.cjs`。

测试拿官方密钥流去构造密文，再要求本项目解回原始音频——这样"自洽性"
再也掩盖不了算法错误。打包流程还会核对一次密钥流指纹，对不上直接中断构建。

---

## 使用

1. **拖进来** —— 文件或整个文件夹拖到窗口即可，自动识别类型；
2. **改参数** —— 左侧按类型筛选，右侧面板一次改完应用到所有选中文件；
   工具栏的「批量设为」可以把全表统一成某个格式；
3. **开始转换** —— 表格里实时看每个文件的状态与进度，日志区记录细节。

输出位置可选「与源文件同目录」或「统一输出到指定目录」。

**转不了的加密文件怎么查？** 不用开软件、不用重新打包，用只读诊断脚本：

```bat
python tools\diagnose_encrypted.py "D:\Music\某首歌.kgma"
python tools\diagnose_encrypted.py "D:\Music"          :: 也可以整个目录一起看
```

它会打印文件头 hexdump、与各代头部家族逐个比对、判定代次与真实格式，
以及"能不能解密"。**只读**，不会修改或解密你的文件。

---

## 项目结构

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
│   │   ├── decryptors/             平台加密格式解密层（ncm / qmc / kugou / kuwo）
│   │   └── engines/                引擎适配器（ffmpeg / image / doc / archive）
│   └── ui/                         主窗口、自绘组件、主题
├── tools/
│   ├── ffmpeg/bin/                 ffmpeg.exe / ffprobe.exe ← 未入库，自行获取
│   ├── diagnose_encrypted.py       加密文件诊断（只读）
│   ├── try_doc.py                  手动试跑文档转换（打印走哪条通道 + 产物体检）
│   ├── shot_ui.py / ui_probe.py    界面离屏截图与像素探针（改版前后对比）
│   └── gh_release.py               发布 GitHub Release 并上传安装包
├── tests/                          五套自测 + 官方金标向量
├── docs/                           问题修复记录、界面截图、发行说明
└── build/                          PyInstaller 配置 / 打包脚本 / Inno Setup 脚本
```

设计上做了两处"加格式不改界面"：新增格式只要在 `formats.py` 加一行，
新增引擎只要写一个适配器再注册一行——下拉框、批量选项、类型徽标、路由、
调度全部自动跟上。

> 开发向的文档（目录职责详解、新增格式/引擎/解密器的步骤、打包流程与踩过的坑）
> 单独放在 [`README.dev.md`](README.dev.md)。

## 自测

```bat
python tests\selftest.py            :: 通用转换端到端，33 项
python tests\selftest_decrypt.py    :: 解密层逐字节校验，33 项（含官方金标）
python tests\selftest_pipeline.py   :: 调度链路：剥壳 → 转码，19 项
python tests\selftest_ui.py         :: 界面集成 + 外观像素断言（无头），27 项
python tests\selftest_docs.py       :: 文档转换内容正确性，28 项
```

五套共 **140 项**，全部通过。测试素材自动生成，解密相关三套按各平台算法
反向构造加密样本，不需要真实的平台下载文件，比对输出与原始音频是否逐字节一致；
文档一套则自己造带中文的 docx/rtf/xlsx，逐项核对产物。

写测试样本时有两个坑，都在踩过之后写进了注释：

1. **不要只用自家常量反向构造样本。** 那样样本永远与实现自洽，测不出真实世界的
   头部变体——`.kgma` 的多代头部当初就是这么漏掉的；
2. **自洽性检查不等于正确性检查。** 加解密两边共用同一个函数时，算法整体错位也
   照样恒真通过。凡是能拿到官方实现的格式，都应当补一组官方金标向量。

还有第三个坑，是文档转换那边补上的：

3. **"转出来了"不等于"内容是对的"。** DOCX → PDF 曾经输出了 34 页二进制垃圾，
   而测试只断言"文件非空"，照样全绿。现在文档自测强制校验：PDF 文本层必须含源
   文档里的已知中文串、中文字体 `ext != 'n/a'`（真的嵌进去了）、页数合理。

第四个坑在界面上：

4. **"界面自测全绿"不等于"界面对"。** 底部动作栏曾经用一条没有选择器的样式表
   把子按钮级联成白底，而按钮文字是白的 —— 「开始转换」四个字用户根本看不见，
   19 项界面自测却全部通过，因为它们只查数据、不查像素。现在界面自测会**截真实
   窗口取像素**，直接断言主按钮底色必须是品牌深蓝。

---

## 已知限制

- **RMVB / RM / CAB / RAR** 只能作为输入（缺编码器或依赖外部工具）；
- **HTML / Markdown → PDF** 走 Office 通道，未装 WPS / Office 时退化为纯文本排版；
- **PDF → DOCX** 需要额外安装 `pdf2docx`；
- **ISO / WIM / RAR** 需要系统里有 7z.exe 才能解锁；
- 硬件加速目前只用于解码，NVENC / QSV 编码未默认开启（核显兼容性差）；
- **QQ 音乐新版（`.mflac` / `.mgg*`）离线解不开** —— 密钥由服务端下发。
  软件会在添加文件时就明确告知，不会等到转换才失败；
- **酷狗新版 `.kgg`、旧版 ASCII 头 KGMA** 离线解不开 —— 密钥不随文件下发。
  软件会识别出代次并说明原因，而不是只回一句"文件头不匹配"；
- **爱奇艺 `.qsv` / 优酷 `.kux` / 腾讯视频 `.qlv`** 未支持。这类**单文件多轨**
  容器不是"一个文件对应一个音视频流"，而是分片索引 + 独立音轨 + 服务端下发的
  解密票据，无法套用现有的"单文件解密"契约；`.kux` 还需要优酷自带的那份改了
  demuxer 的 ffmpeg 才能直读；
- **老版二进制 Office 格式**（`.doc` / `.xls` / `.ppt`）在内置通道下无法解析 ——
  这类 OLE 复合文档纯 Python 读不出正文。软件会**明确报错并提示改用高保真通道**，
  不会硬凑出一份乱码；装了 WPS / Office 就能直接转。
- **`.doc` → PDF 等所有 Office 转换依赖本机 Office/WPS**，没装则只有文本类格式可用。

---

## 法律声明

本项目**只处理用户自己从平台下载或缓存的文件**，剥除的是平台自研的
**文件封装**（异或掩码一类），目的是让用户能在通用播放器上播放自己已获取的音频。

- 项目**不包含、不提供任何解密密钥**，也不支持任何需要服务端下发密钥才能解密的格式
  （`.mflac` / `.mgg*` / `.kgg` 等均已明确标注为不支持）；
- 项目**不涉及商业 DRM 系统**（Apple Music FairPlay、Spotify、Netflix 等）；
- 项目**不提供任何音乐内容的下载、搜索或分发能力**；
- 请遵守各音乐平台的用户协议与当地法律，**切勿用于传播盗版内容**；
- 使用者须自行承担因使用本软件产生的一切后果。

如果你是版权方或平台方，认为本项目存在不当之处，请通过仓库 Issue 联系，
我们会配合处理。

## 致谢

本项目站在这些开源项目与社区逆向成果的肩膀上：

- [FFmpeg](https://ffmpeg.org/) —— 音视频转换的基石；
- [unlock-music](https://git.unlock-music.dev/um/web) 及其现役解密模块
  `@xhacker/kgmwasm`、`@xhacker/qmcwasm` —— 官方金标向量的来源；
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF)、
  [Pillow](https://python-pillow.org/)、
  [python-docx](https://github.com/python-openxml/python-docx)、
  [mammoth](https://github.com/mwilliamson/python-mammoth)、
  [py7zr](https://github.com/miurahr/py7zr) —— 各类型转换引擎；
- [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/) —— 界面框架。

## 许可

源码暂未公开发布，本仓库当前仅包含项目说明文档，许可协议待源码公开时一并补充。
