"""The eight benchmark tasks exposed by the evaluation CLI."""

from .paths import DATA_ROOT

TASK_DATASETS = {
    "KVEM": DATA_ROOT / "KVEM/KVEM.json",
    "VIM": DATA_ROOT / "VIM/VIM.json",
    "AUQ": DATA_ROOT / "AUQ/AUQ.json",
    "WUQ": DATA_ROOT / "WUQ/WUQ.json",
    "PACM": DATA_ROOT / "PACM/PACM.json",
    "ICM": DATA_ROOT / "ICM/ICM.json",
    "UPM": DATA_ROOT / "UPM/UPM.json",
    "UPC": DATA_ROOT / "UPC/UPC.json",
}
