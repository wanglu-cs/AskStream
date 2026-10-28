<h1 align="center">
  <img src="assets/icon.png" width="65" alt="AskStream Logo">
  AskStream：流式视频理解不确定性下何时主动提问、提问什么
</h1>

<p align="center">
  <a href="README.md">English</a> &nbsp; | &nbsp;
  <strong>简体中文</strong>
</p>

<p align="center">
  <em>从被动等待到主动提问</em>
</p>

<p align="center">
  <a href="YOUR_PAPER_URL"><img src="https://img.shields.io/badge/Paper-arXiv-B31B1B?style=flat-square&amp;logo=arxiv&amp;logoColor=white" alt="论文"></a>
  <a href="YOUR_DATASET_URL"><img src="https://img.shields.io/badge/Dataset-Hugging%20Face-24292F?style=flat-square&amp;logo=huggingface&amp;logoColor=FFD21E" alt="数据集"></a>
  <a href="YOUR_CODE_URL"><img src="https://img.shields.io/badge/Code-GitHub-24292F?style=flat-square&amp;logo=github&amp;logoColor=white" alt="代码"></a>
  <a href="YOUR_PROJECT_URL"><img src="https://img.shields.io/badge/Project-Page-2563EB?style=flat-square&amp;logo=googlechrome&amp;logoColor=white" alt="项目主页"></a>
</p>

<p align="center">
  <img src="assets/overview.png" alt="AskStream 概览" width="90%">
</p>

---

## 🎞️ 概览

现有的主动式视频理解方法主要关注**何时回答**，并假设回答查询所需的信息最终都会出现。然而，真实世界的流式视频中经常存在由视觉证据缺失、用户问题含糊或个性化信息不完整所引发的不确定性。为应对这一挑战，**AskStream** 提出了一种新的主动交互范式：在传统的 **WAIT** 和 **ANSWER** 动作之外加入 **ASK**，使模型能够在回答之前主动请求缺失的信息。

### 核心评估维度

- 🎯 **何时提问（When to Ask）：** 模型能否识别不确定性，并在恰当的时机主动提问？
- 💬 **提问什么（What to Ask）：** 模型能否向视频或用户请求正确的缺失信息？
- ⏱️ **何时回答（When to Answer）：** 模型能否判断给出答案的恰当时机？
- ✅ **回答什么（What to Answer）：** 模型能否基于获取到的信息给出准确答案？

### 数据集统计

- 🎬 **438** 个视频，覆盖 **9** 个不同领域
- 📝 **1,401** 个问题，涵盖 **8** 个子任务
- ⏱️ **2,714** 个人工标注的帧级时间戳
- 🔄 每个视频平均包含 **3.2** 轮交互

### 🎬 视频类别

AskStream 覆盖九类多样化的视频领域，包括美食餐饮、体育运动、服装穿搭、日常生活、影视、维修、教育、旅行和科技。

![视频类别](assets/domain_03.png)

### 🔍 任务分类

AskStream 包含八个子任务，涉及两类主要的不确定性来源：**视频不确定性（Video Uncertainty）**和**用户不确定性（User Uncertainty）**，并进一步划分为四类任务。

![任务分类](assets/domain_01.png)

### ⏱️ 时间窗口分布

AskStream 为三种交互动作 **WAIT**、**ASK** 和 **ANSWER** 提供了细粒度时间标注，其有效总时长分别为 **33.92**、**30.47** 和 **12.00** 小时。

![时间窗口分布](assets/domain_02.png)

## 📁 项目结构

```
AskStream/
├── assets/                         # README 使用的图片资源
├── data/                           # AskStream 数据集
├── eval/                           # 评估代码
│   ├── run_streaming_parallel.py    # 并行流式评估入口
│   ├── tasks.py                     # 评估任务定义
│   ├── paths.py                     # 项目与数据路径工具
│   └── streaming_harness/           # 流式评估核心框架
│       ├── __main__.py              # 命令行入口
│       ├── config.py                # 运行时配置
│       ├── cli_config.py            # CLI 参数解析
│       ├── dataset.py               # 数据集加载与迭代
│       ├── assets.py                # 评估资源管理
│       ├── model_loader.py          # 模型与后端加载
│       ├── video.py                 # 视频加载与处理
│       ├── frames.py                # 帧采样与帧处理工具
│       ├── streaming.py             # 流式执行逻辑
│       ├── memory.py                # 交互历史与记忆状态
│       ├── actions.py               # WAIT / ASK / ANSWER 动作定义
│       ├── protocol.py              # 交互协议
│       ├── interfaces.py            # 后端接口定义
│       ├── response_modes.py        # 模型响应格式
│       ├── prompts.py               # 模型提示词模板
│       ├── generation.py            # 响应生成辅助工具
│       ├── evaluator.py             # 评估流程编排
│       ├── judge.py                 # 裁判模型评估
│       ├── judge_prompts.py         # 裁判提示词模板
│       ├── metrics.py               # 评估指标计算
│       ├── validation.py            # 输入与输出校验
│       ├── schema.py                # 共享数据结构
│       ├── records.py               # 评估记录结构
│       ├── provenance.py            # 结果来源追踪
│       ├── audit.py                 # 评估审计工具
│       ├── health.py                # 后端健康检查
│       ├── release.py               # 结果发布与导出工具
│       └── runner.py                # 主评估运行器
├── model/                          # 模型实现与配置
│   ├── offline_api/                 # OpenAI 兼容 API 后端
│   │   └── .privacy.json           # API 地址与密钥配置
│   ├── offline_vllm/                # 本地 vLLM 后端
│   └── stream/                      # 流式模型后端
├── result/
│   └── main/                        # 评估输出
├── web/                            # 浏览器标注界面
│   ├── 01_video_filter/             # 候选视频筛选
│   ├── 02_video_annoation/          # 时间与问答标注
│   ├── 03_human_review/             # 最终人工审核
│   └── README.md                    # Web 界面文档
├── README.md                       # 英文文档
├── README_zh-CN.md                 # 中文文档
├── requirements.txt                # Python 依赖
└── LICENSE                         # 项目许可证
```

## ⚙️ 评估流程

### 环境配置

#### 🐳 Docker（推荐）

```bash
docker pull wanglucs/askstream:latest

docker run --gpus all -it --rm \
  -v "$PWD:/workspace" \
  -w /workspace \
  wanglucs/askstream:latest \
  bash
```

#### 🐍 Conda

```bash
conda create -n AskStream python=3.12 pip -y
conda activate AskStream

python -m pip install uv
python -m pip install -r requirements.txt
```

安装 vLLM：

```bash
uv pip install --torch-backend auto vllm
```

#### API 配置

在 `model/offline_api` 目录下的 `.privacy.json` 文件中配置 `base_url` 和 `api_key`：

```json
{
  "base_url": "BASE_URL",
  "api_key": "API_KEY"
}
```

### 数据准备

安装 Hugging Face CLI，并将完整的 AskStream 数据集下载到 `data` 目录：

```bash
python -m pip install -U huggingface_hub

hf download wanglucs/AskStream \
  --repo-type dataset \
  --local-dir /mnt/userdata/AskStream/data
```

### 评估

#### API 后端

API 后端支持任意兼容 OpenAI Chat Completions 的接口。API 密钥和默认服务地址从 `model/offline_api/.privacy.json` 中读取。

以多项选择模式运行所有任务：

```bash
python -m eval.run_streaming_parallel \
  --all \
  --mode multi-choice \
  --backend api \
  --model gpt-5.4 \
  --privacy-file model/offline_api/.privacy.json \
  --no-judge
```

以开放式模式运行所有任务：

```bash
python -m eval.run_streaming_parallel \
  --all \
  --mode open-ended \
  --backend api \
  --model gpt-5.4 \
  --judge-model gemini-3.8-flash \
  --privacy-file model/offline_api/.privacy.json
```

#### vLLM 后端

启动一个兼容 OpenAI API 的 vLLM 服务。服务所使用的模型名称必须与评估时传给 `--model` 的值一致。

```bash
MODEL_PATH_OR_HF_ID=/path/to/model

vllm serve "$MODEL_PATH_OR_HF_ID" \
  --served-model-name Qwen3-VL-4B-Instruct \
  --host 127.0.0.1 \
  --port 18003
```

以多项选择模式运行所有任务，不使用外部裁判模型：

```bash
python -m eval.run_streaming_parallel \
  --all \
  --mode multi-choice \
  --backend vllm \
  --model Qwen3-VL-4B-Instruct \
  --base-url http://127.0.0.1:18003/v1 \
  --no-judge
```

以开放式模式运行所有任务。开放式 ASK/ANSWER 评估需要裁判模型接口，请在隐私配置文件中设置其密钥和默认 URL。

```bash
python -m eval.run_streaming_parallel \
  --all \
  --mode open-ended \
  --backend vllm \
  --model Qwen3-VL-4B-Instruct \
  --base-url http://127.0.0.1:18003/v1 \
  --judge-model gemini-3.8-flash \
  --privacy-file model/offline_api/.privacy.json
```

#### 原生流式后端

本地流式适配器必须放置在 `model/stream/` 目录下，并继承 `eval.streaming_harness.interfaces` 中的 `StreamingModelBackend`。

```python
open_video(*, video_id, metadata)
push_observation(observation)
decide(request) -> ModelCallResult
reset_context(*, video_id, metadata)
abort_video(*, video_id, metadata=None)
close_video(*, video_id)
```

适配器模块还必须暴露一个签名为 `factory(config, *, api_key)` 的工厂函数，该函数返回 `StreamingModelBackend` 实例。通过 `model.stream.<module>:<factory>` 的格式传入该入口。以下示例使用 `model.stream.my_model:create_backend`，请将其替换为实际的适配器入口和模型名称。

使用本地适配器以多项选择模式运行所有任务：

```bash
python -m eval.run_streaming_parallel \
  --all \
  --mode multi-choice \
  --backend streaming \
  --model my-streaming-model \
  --model-adapter model.stream.my_model:create_backend \
  --max-parallel 1 \
  --no-judge
```

以开放式模式运行所有任务：

```bash
python -m eval.run_streaming_parallel \
  --all \
  --mode open-ended \
  --backend streaming \
  --model my-streaming-model \
  --model-adapter model.stream.my_model:create_backend \
  --max-parallel 1 \
  --judge-model gemini-3.8-flash \
  --privacy-file model/offline_api/.privacy.json
```


评估产物和汇总指标会自动写入 `result/main/`。使用 `--video-id VIDEO_ID` 可评估指定视频；运行 `python -m eval.run_streaming_parallel --help` 可查看所有可用选项。

---

## 🌐 Web 标注界面

`web/` 目录包含 AskStream 标注流程所使用的三个浏览器界面。各界面的详细说明请参阅 [`web/README.md`](web/README.md)。

## 📚 引用

```

```
