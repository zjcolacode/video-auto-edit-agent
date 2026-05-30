# Video Highlight Agent

基于**阿里云百炼 Coding Plan**（OpenAI 兼容协议 + `qwen3.6-plus` 视觉模型）的精华视频自动剪辑智能体（MVP）。

输入任意视频，自动完成：
1. 视频类型识别（演讲 / Vlog / 影视 / 教程 / 访谈 / 综合）
2. 按语义切片并对每段评分（信息密度、情绪强度、独立可观看性）
3. 按目标时长贪心筛选 + 主题去重 + 时间顺序重排
4. ffmpeg 无损切割与拼接，输出精华短视频

## 一、架构

```
video.mp4
  -> VideoIngestor  : ffprobe 探测元数据 + ffmpeg 均匀抽帧（默认 16 帧，可选 ASR 转写）
  -> VisionAnalyst  : 多帧 base64 喂给 qwen3.6-plus，单次推理产出结构化片段标注
  -> ContentCurator : 按 score + 目标时长 + 主题脉络筛选与排序
  -> VideoComposer  : ffmpeg 切片 + concat
  -> highlight.mp4 + highlight.json
```

> Coding Plan 当前**不提供视频原生输入**，因此采用「等距抽帧 → 多张图片传入」的方式做时空理解；未来 Coding Plan 若开放视频输入，只需替换 `tools/llm_client.py` 即可。

## 二、安装

### 1. 系统依赖
确保 `ffmpeg` 与 `ffprobe` 已在 PATH：
```bash
ffmpeg -version
ffprobe -version
```
Ubuntu：`sudo apt install ffmpeg`

### 2. Python 依赖
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置 API Key
```bash
cp .env.example .env
# 编辑 .env，填入 CODING_API_KEY（套餐专属 sk-sp-... 开头的密钥）
```
获取 Key：阿里云百炼 Coding Plan 控制台 → 套餐专属 API Key → 复制。

> ⚠️ Coding Plan 的 Key **不能**用普通百炼 DashScope 端点，它有专属 base_url：`https://coding.dashscope.aliyuncs.com/v1`（已写在 `.env.example`）。

## 三、使用

### 端到端：生成精华短视频
```bash
python main.py run input.mp4 \
    --target 60 \
    --output highlight.mp4
```

可选参数：
- `--model plus` 视觉模型别名，默认 `plus` = `qwen3.6-plus`
  - 别名：`plus`(qwen3.6-plus) / `pro`(qwen3.5-plus) / `kimi`(kimi-k2.5)
  - 也可直接传完整模型名
- `--min-score 0.5` 片段最低保留分数
- `--target 60` 目标时长（秒）
- `--num-frames 16` 抽帧数量（多 = 更精准但更慢/贵）

### 仅分析（调试 prompt）
```bash
python main.py analyze input.mp4 --num-frames 24
```
输出 `workspace/output/<video>.analysis.json`，含视频类型、整体摘要与全部片段评分。

## 四、Coding Plan 可用模型清单

仅以下模型具备**视觉理解**能力（用于本项目）：

| 别名 | 完整名 | 说明 |
|---|---|---|
| `plus` | `qwen3.6-plus` | **默认**，性价比首选 |
| `pro`  | `qwen3.5-plus` | 失败回退 / 兜底 |
| `kimi` | `kimi-k2.5`    | 备选，长上下文擅长 |

`qwen3-max-2026-01-23` / `qwen3-coder-*` / `glm-5` / `glm-4.7` / `MiniMax-M2.5` **不支持视觉**，不能用于本项目。

## 五、ASR（语音识别）

Coding Plan 套餐**不含 ASR 模型**，所以默认 `ASR_PROVIDER=noop`（不转写，纯视觉分析）。

如果需要语音内容（演讲/访谈等强口播场景效果更好）：
1. 另开通普通百炼 DashScope Key（用于 `paraformer-v2`），填到 `ASR_API_KEY`
2. 把 `ASR_PROVIDER` 改成 `paraformer`
3. v0.2 会实现该 Provider（当前 `tools/asr.py` 已有抽象接口骨架）

## 六、目录结构

```
video-auto-edit-agent/
├── agents/                # 四个智能体（Ingestor/Analyst/Curator/Composer + Orchestrator）
├── tools/
│   ├── llm_client.py      # OpenAI 兼容 Coding Plan 客户端
│   ├── frame_extractor.py # ffmpeg 均匀抽帧 + base64
│   ├── ffmpeg_tools.py    # 切片 / 拼接 / probe
│   └── asr.py             # ASR Provider 抽象（NoopASR 默认）
├── prompts/               # 分析 prompt 模板（含 {FRAMES_HEADER} 占位符）
├── schemas.py             # pydantic 数据契约
├── config.py              # 配置加载
├── main.py                # CLI 入口
├── workspace/             # 运行时中间产物（git ignore）
└── tests/
```

## 七、冒烟测试建议

1. 准备一段 3-10 分钟的视频（演讲 / Vlog / Tutorial 都可）
2. 配置好 `.env` 中的 `CODING_API_KEY`
3. 运行：
   ```bash
   python main.py run sample.mp4 --target 45 --num-frames 16
   ```
4. 检查：
   - `workspace/output/sample_highlight.mp4` 应可正常播放
   - `workspace/output/sample_highlight.json` 含被选中的片段元信息

## 八、示例（Showcase）

拿一段 **5’23” 的 Quivr 本地知识库教程**（1280×720，21 MB）跑了一次全流程。

![精华片段封面：Quivr 工作原理图解](docs/images/showcase_quivr_pipeline.jpg)

> 这一帧取自 Curator 挑中的精华片段（原视频 171–209s）。模型从这类架构图 + 字幕中提炼出“Quivr 如何检索与回答”这一核心价值点。

### 1. 调用命令
```bash
python main.py run input.mp4 \
    --target 30 \
    --min-score 0.9 \
    --num-frames 16 \
    --output workspace/output/quivr_highlight.mp4
```

### 2. 模型识别出的全视频所有片段（17 个）

```
video_type = tutorial    duration = 323.3s    segments = 17
摘要：本视频是一个关于如何使用开源工具 Quivr 搭建本地 ChatGPT 知识库的教程。视频详细演示了从购买 API Key、安装环境依赖 (Git, Docker)，到一键运行 Quivr 服务，再到上传本地文档（如 PDF）构建知识库并进行对话的全过程，最后还介绍了如何配置私有本地大模型。
```

| #  |  start |    end |  score | topic                   | tags         |
|----|--------|--------|--------|-------------------------|--------------|
| 1  |   0.0  |  19.0  |  0.60  | 视频开场与痛点引入          | 开场         |
| 2  |  19.0  |  38.0  |  0.80  | Quivr 项目介绍                | 知识点, 金句  |
| 3  |  38.0  |  57.0  |  0.70  | Quivr 核心功能展示            | 知识点       |
| 4  |  57.0  |  76.0  |  0.50  | 准备工作：API Key             | 知识点       |
| 5  |  76.0  |  95.0  |  0.60  | 环境安装：Git 与 Docker       | 知识点       |
| 6  |  95.0  | 114.0  |  0.80  | 一键运行 Quivr                | 高潮, 知识点  |
| 7  | 114.0  | 133.0  |  0.70  | 上传语料：文本与文件          | 知识点       |
| 8  | 133.0  | 152.0  |  0.60  | 上传示例 PDF                  | 案例         |
| 9  | 152.0  | 171.0  | **0.90** | **Quivr 工作原理图解**       | 知识点, 总结 |
| 10 | 171.0  | 190.0  |  0.70  | 无法找到语料时的处理        | 知识点       |
| 11 | 190.0  | 209.0  |  0.80  | 对话演示：杭州亚运会          | 案例, 高潮   |
| 12 | 209.0  | 228.0  |  0.60  | Prompt Engineer 进阶         | 知识点       |
| 13 | 228.0  | 247.0  |  0.70  | 模型配置与自定义              | 知识点       |
| 14 | 247.0  | 266.0  | **0.90** | **私有本地大模型 (Private LLM)** | 高潮, 知识点 |
| 15 | 266.0  | 285.0  |  0.70  | 本地模型部署方法              | 知识点       |
| 16 | 285.0  | 304.0  |  0.60  | 未来计划与总结                | 总结         |
| 17 | 304.0  | 323.3  |  0.50  | 结尾呼吁                    | 收尾         |

### 3. Curator 挑出的精华片段

```json
{
  "target_duration": 30,
  "total_duration": 38.0,
  "picks": [
    {
      "start": 171.0,
      "end": 209.0,
      "score": 0.9,
      "topic": "Private LLM 本地化部署",
      "summary": "介绍了 Private LLM 功能，允许用户将大语言模型下载到本地运行，保护数据隐私。",
      "reason": "这是 Quivr 的一大亮点（数据不出本地），对于关注隐私的用户极具吸引力，属于高价值信息。",
      "tags": ["知识点", "高潮"]
    }
  ]
}
```

### 4. 耗时与产出

| 阶段 | 耗时 |
|---|---|
| ffprobe + ffmpeg 抽 16 帧 | ~3s |
| qwen3.6-plus 多模态推理 | ~51s |
| Curator 筛选 + ffmpeg 拼接 | <1s |
| **总计** | **~55s** |

产出三件套：
```
workspace/output/
  quivr_highlight.mp4              # 1.6 MB 精华视频 (38s, 1280x720, h264+aac)
  quivr_highlight.analysis.json    # 4.0 KB 完整分析报告
  quivr_highlight.highlight.json   # 668 B  被选中的片段元信息
```

### 5. 观察

- **模型能读出 UI 语义**：Private LLM 这个亮点不是画面中的显著文字，是模型从设置面板 + 教程上下文推理出来的。
- **JSON Schema 严格命中**：17 个 segment 全部字段合法，未触发兜底。
- **局限**：由于未接 ASR，模型只能按抽帧均匀划分片段（每段 ≈ 19s）。v0.2 接入 paraformer-v2 后，切分粒度可精准到语义跳变点。

## 九、后续迭代（Roadmap）

- v0.2 接入 paraformer-v2 ASR Provider（含 OSS 上传 + 异步转写）
- 自动字幕烧录（ffmpeg subtitles）
- 节奏卡点与转场
- LLM 解说稿 + TTS 配音重剪版
- 长视频 map-reduce 摘要（>30min）
- Web UI

## 十、许可证

MIT
