# AskStream Web Annotation Interfaces

## Overview

This directory contains the three browser-based interfaces used in the AskStream **KVEM** annotation pipeline:

1. **Video Filter** (`01_video_filter`) reviews candidate YouTube videos against
  configurable filtering criteria and exports the accepted video IDs.
2. **Video Annotation** (`02_video_annoation`) defines temporal events, waiting
  and asking windows, entities, key-frame visual prompts, questions, answer
   options, and gold labels.
3. **Human Review** (`03_human_review`) presents masked and revealed videos side
  by side so that a reviewer can verify the final questions, options, entities,  
   and event timing.

## Setup

Run each server from the AskStream repository root. The servers use only the
Python standard library and save annotation updates atomically to JSON files in
their respective interface directories.

### 1. Video Filter

```bash
python3 web/01_video_filter/server.py --host 127.0.0.1 --port 10001
```

Open `http://127.0.0.1:10001/web/01_video_filter/`. Results are saved to
`web/01_video_filter/video_filter_data.json`.

### 2. Video Annotation

```bash
python3 web/02_video_annoation/server.py --host 127.0.0.1 --port 10002
```

Open `http://127.0.0.1:10002/web/02_video_annoation/`. The server discovers
videos under `data/COIN/processed/video/`, supports HTTP byte-range requests for
video seeking, and saves annotations to
`web/02_video_annoation/video_uncertainty_annotations.json`.

### 3. Human Review

```bash
python3 web/03_human_review/server.py --host 127.0.0.1 --port 10003
```

Open `http://127.0.0.1:10003/web/03_human_review/`. Review results are saved to
`web/03_human_review/human_review_annotations.json`.

Use `--host 0.0.0.0` only when remote access is required and the network is
appropriately protected. Stop a server with `Ctrl+C`.
