from __future__ import annotations

from typing import Any


BRAINDA_COMMIT = "b46a33bcf3cb3625d09571fdeac8c8bc0480db69"
N_HARMONICS = 5
N_BANDS = 3
N_COMPONENTS = 8
PADDING_LEN = 0


def algorithm_metadata() -> dict[str, Any]:
    return {
        "n_harmonics": N_HARMONICS,
        "n_bands": N_BANDS,
        "n_components": N_COMPONENTS,
        "padding_len": PADDING_LEN,
        "filterweights": "band_index^-1.25 + 0.25",
    }
