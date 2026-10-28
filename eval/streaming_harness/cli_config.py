from __future__ import annotations

import argparse

from .config import EvaluationConfig, defaults_for_backend


def resolve_evaluation_config(args: argparse.Namespace) -> EvaluationConfig:
    config = (
        EvaluationConfig.from_json(args.config)
        if getattr(args, "config", None)
        else EvaluationConfig()
    )
    overrides = {}
    requested_backend = getattr(args, "backend", None)
    if requested_backend is not None and requested_backend != config.backend:
        overrides.update(
            backend=requested_backend,
            model_adapter=None,
            **defaults_for_backend(requested_backend),
        )
    mapping = {
        "mode": "evaluation_mode",
        "model": "model",
        "model_adapter": "model_adapter",
        "base_url": "base_url",
        "reasoning_effort": "reasoning_effort",
        "store_responses": "store_responses",
        "judge_model": "judge_model",
        "judge_base_url": "judge_base_url",
        "judge_reasoning_effort": "judge_reasoning_effort",
        "ask_start_tolerance": "ask_start_tolerance_s",
        "ask_end_tolerance": "ask_end_tolerance_s",
        "answer_start_tolerance": "answer_start_tolerance_s",
        "answer_end_tolerance": "answer_end_tolerance_s",
        "tick_seconds": "tick_seconds",
        "short_term_seconds": "short_term_seconds",
        "mid_term_capacity": "mid_term_capacity",
        "long_term_capacity": "long_term_capacity",
        "max_frame_pixels": "max_frame_pixels",
        "mid_term_max_tokens": "mid_term_max_tokens",
        "long_term_max_tokens": "long_term_max_tokens",
        "action_max_tokens": "action_max_tokens",
        "temperature": "temperature",
        "jpeg_quality": "jpeg_quality",
        "request_timeout": "request_timeout_seconds",
        "request_retries": "request_retries",
        "playback_speed": "playback_speed",
        "rehydrate_seconds": "rehydrate_seconds",
        "rehydrate_max_frames": "rehydrate_max_frames",
    }
    overrides.update({
        config_name: getattr(args, argument_name)
        for argument_name, config_name in mapping.items()
        if getattr(args, argument_name, None) is not None
    })
    if getattr(args, "disable_thinking", False):
        overrides["enable_thinking"] = False
    if getattr(args, "no_judge", False):
        overrides["judge_model"] = None
    if overrides:
        config = config.with_overrides(**overrides)
    return config
