"""Surface detectors (ADR 0013): read WHICH application / sheet / page a frame shows from its chrome
text, with no model, and compare two frames' locators to say whether the surface changed."""
from core.screen.surfaces.base import diff_locators
from core.screen.surfaces.registry import locate

__all__ = ["diff_locators", "locate"]
