"""Renderer-facing stimulus scene contracts."""

from __future__ import annotations

from pydantic import Field, model_validator

from psychopy_ssvep.config.models import (
    HorizontalLayout,
    StimulusConfig,
    StrictModel,
    TargetSide,
)


class StimulusPlacementOverride(StrictModel):
    """Per-scene placement independent of a stimulus's default visual location."""

    side: TargetSide | None = None
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    center_y: float = Field(ge=0, le=1)
    horizontal_layout: HorizontalLayout
    center_x: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_center(self) -> "StimulusPlacementOverride":
        if (
            self.horizontal_layout == HorizontalLayout.MANUAL
            and self.center_x is None
        ):
            raise ValueError("manual placement requires center_x")
        if (
            self.horizontal_layout == HorizontalLayout.EQUAL_GAPS
            and self.center_x is not None
        ):
            raise ValueError("equal-gap placement does not accept center_x")
        if (
            self.horizontal_layout == HorizontalLayout.EQUAL_GAPS
            and self.side is None
        ):
            raise ValueError("equal-gap placement requires side")
        return self

    def resolve_rect(
        self, viewport_width_px: int, viewport_height_px: int
    ) -> tuple[int, int, int, int]:
        if self.horizontal_layout == HorizontalLayout.EQUAL_GAPS:
            assert self.side is not None
            gap = max(0.0, (viewport_width_px - 2 * self.width_px) / 3.0)
            center_x_px = (
                gap + self.width_px / 2.0
                if self.side == TargetSide.LEFT
                else viewport_width_px - gap - self.width_px / 2.0
            )
        else:
            assert self.center_x is not None
            center_x_px = viewport_width_px * self.center_x
        center_y_px = viewport_height_px * self.center_y
        return (
            round(center_x_px - self.width_px / 2.0),
            round(center_y_px - self.height_px / 2.0),
            self.width_px,
            self.height_px,
        )


class StimulusNode(StrictModel):
    stimulus: StimulusConfig
    visible_requested: bool = True
    flashing_requested: bool = True
    highlighted: bool = False
    z_order: int = 0
    placement_override: StimulusPlacementOverride | None = None


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
