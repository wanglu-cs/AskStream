from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path

from eval.tasks import TASK_DATASETS

from .assets import AssetManifest
from .audit import write_json_atomic
from .cli_config import resolve_evaluation_config
from .config import CONFIG_SCHEMA_VERSION, load_api_credentials
from .dataset import compile_dataset
from .health import wait_for_streaming, wait_for_vllm
from .metrics import score_run
from .response_modes import EVALUATION_MODES, apply_evaluation_mode
from .runner import HarnessRunner, validation_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AskStream evaluation", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate a current dataset release", allow_abbrev=False)
    validate.add_argument("--dataset", type=Path, default=TASK_DATASETS["KVEM"])
    validate.add_argument("--report", type=Path)
    score = commands.add_parser("score", help="score a current evaluation run", allow_abbrev=False)
    score.add_argument("--run-dir", type=Path, required=True)
    score.add_argument("--output", type=Path)
    run = commands.add_parser("run", help="evaluate a local model adapter or remote service", allow_abbrev=False)
    run.add_argument("--dataset", type=Path, default=TASK_DATASETS["KVEM"])
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--config", type=Path, help=f"{CONFIG_SCHEMA_VERSION} JSON")
    run.add_argument("--mode", choices=EVALUATION_MODES)
    run.add_argument("--backend", choices=("api", "vllm", "streaming"))
    run.add_argument("--model")
    run.add_argument("--model-adapter", help="local model.<category>.<module>:<factory>; factory(config, api_key=...) returns the matching backend interface")
    run.add_argument("--base-url")
    run.add_argument("--privacy-file", type=Path, help="JSON {api_key, base_url?}; defaults to model/offline_api/.privacy.json when credentials are required")
    judge = run.add_mutually_exclusive_group()
    judge.add_argument("--judge-model")
    judge.add_argument("--no-judge", action="store_true", help="disable remote judging; open responses other than AUQ answers still require a judge")
    run.add_argument("--judge-base-url")
    for name in ("reasoning-effort", "judge-reasoning-effort"):
        run.add_argument(f"--{name}", choices=("none", "low", "medium", "high", "xhigh", "max"))
    run.add_argument("--store-responses", action="store_true", default=None)
    for name in ("skip-invalid", "disable-memory-summaries", "skip-backend-health-check", "disable-thinking"):
        run.add_argument(f"--{name}", action="store_true")
    run.add_argument("--video-id", action="append")
    run.add_argument("--limit", type=int)
    for name in ("playback-speed", "rehydrate-seconds", "ask-start-tolerance", "ask-end-tolerance",
                 "answer-start-tolerance", "answer-end-tolerance", "tick-seconds", "short-term-seconds",
                 "temperature", "request-timeout"):
        run.add_argument(f"--{name}", type=float)
    for name in ("rehydrate-max-frames", "mid-term-capacity", "long-term-capacity", "max-frame-pixels",
                 "mid-term-max-tokens", "long-term-max-tokens", "action-max-tokens", "jpeg-quality", "request-retries"):
        run.add_argument(f"--{name}", type=int)
    run.add_argument("--backend-health-timeout", type=float, default=120.0)
    run.add_argument("--backend-health-interval", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "score":
        print(json.dumps(score_run(args.run_dir, args.output), ensure_ascii=False, indent=2))
        return 0
    dataset = compile_dataset(args.dataset)
    if args.command == "validate":
        report = validation_report(dataset)
        if args.report:
            write_json_atomic(args.report.resolve(), report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(bool(dataset.errors))
    config, _key = load_api_credentials(resolve_evaluation_config(args), args.privacy_file)
    dataset = apply_evaluation_mode(dataset, config.evaluation_mode)
    if args.backend_health_timeout <= 0 or args.backend_health_interval <= 0:
        raise ValueError("backend health timeout and interval must be positive")
    health = None
    if config.model_adapter is not None:
        health = {"status": "local_adapter", "entrypoint": config.model_adapter, "model": config.model}
    elif config.backend != "api":
        if args.skip_backend_health_check:
            health = {"status": "skipped", "base_url": config.base_url, "model": config.model}
        elif config.backend == "streaming":
            health = wait_for_streaming(config, timeout_seconds=args.backend_health_timeout,
                                        interval_seconds=args.backend_health_interval)
        else:
            health = wait_for_vllm(config.base_url, config.model, timeout_seconds=args.backend_health_timeout,
                                   interval_seconds=args.backend_health_interval).as_dict()
    runner = HarnessRunner(dataset=dataset, assets=AssetManifest.from_dataset(dataset), config=config,
                           output_dir=args.output, privacy_file=args.privacy_file,
                           summaries_enabled=not args.disable_memory_summaries, skip_invalid=args.skip_invalid,
                           backend_health=health)

    def handle_sigterm(signum: int, _frame: object) -> None:
        signal.signal(signum, signal.SIG_DFL)
        raise InterruptedError(f"received {signal.Signals(signum).name}")

    previous_sigterm = signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        runner.run(video_ids=set(args.video_id) if args.video_id else None, limit=args.limit)
    finally:
        runner.close()
        signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
