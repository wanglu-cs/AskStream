"""Load an optional model.<category>.<module>:<factory> implementation.

The factory receives (config, *, api_key) and returns GenerationBackend for
api/vllm, or StreamingModelBackend for streaming. Without a factory, evaluation
uses the configured remote HTTP service. Importing the evaluator never imports
model code; the selected factory is loaded only when constructing the runner.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from eval.paths import MODEL_ROOT

from .config import EvaluationConfig
from .interfaces import GenerationBackend, StreamingModelBackend


MODEL_PACKAGES = {
    "api": "model.offline_api",
    "vllm": "model.offline_vllm",
    "streaming": "model.stream",
}


def adapter_entrypoint(backend: str, value: str) -> tuple[str, str]:
    module_name, separator, factory_name = value.partition(":")
    package = MODEL_PACKAGES[backend]
    if (
        not separator
        or not factory_name.isidentifier()
        or not all(part.isidentifier() for part in module_name.split("."))
        or not (module_name == package or module_name.startswith(package + "."))
    ):
        raise ValueError(f"model_adapter must be {package}.<module>:<factory>")
    return module_name, factory_name


def load_model_adapter(config: EvaluationConfig, *, api_key: str | None) -> GenerationBackend | StreamingModelBackend:
    if config.model_adapter is None:
        raise ValueError("model_adapter is required to load a local implementation")
    module_name, factory_name = adapter_entrypoint(config.backend, config.model_adapter)
    module = import_module(module_name)
    origin = getattr(module, "__file__", None)
    if origin is None or not Path(origin).resolve().is_relative_to(MODEL_ROOT.resolve()):
        raise ValueError(f"model adapter must reside under {MODEL_ROOT}")
    factory = getattr(module, factory_name, None)
    if not callable(factory):
        raise TypeError(f"model adapter factory is not callable: {config.model_adapter}")
    model = factory(config, api_key=api_key)
    expected = StreamingModelBackend if config.backend == "streaming" else GenerationBackend
    if not isinstance(model, expected):
        raise TypeError(f"{config.model_adapter} must return {expected.__name__}")
    return model
