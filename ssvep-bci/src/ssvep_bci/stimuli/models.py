"""Renderer-facing stimulus scene contracts.

The protocol currently builds a one-node scene.  Keeping the renderer contract
collection-based lets future keyboards and candidate grids add nodes without
changing the runtime/UI boundary.
"""

from __future__ import annotations

from pydantic import Field

from ssvep_bci.config.models import StimulusConfig, StrictModel


class StimulusNode(StrictModel):
    stimulus: StimulusConfig
    visible_requested: bool = True
    flashing_requested: bool = True
    highlighted: bool = False
    z_order: int = 0


class StimulusScene(StrictModel):
    scene_id: str = Field(min_length=1)
    nodes: tuple[StimulusNode, ...] = ()

    @property
    def has_visible_nodes(self) -> bool:
        return any(node.visible_requested for node in self.nodes)

    @property
    def has_flashing_nodes(self) -> bool:
        return any(
            node.visible_requested and node.flashing_requested for node in self.nodes
        )
