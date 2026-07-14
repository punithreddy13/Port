"""OrderFlow Terminal — institutional order flow & liquidity analysis platform."""

__version__ = "0.1.0"

from .core import Config
from .pipeline import Pipeline

__all__ = ["Config", "Pipeline", "__version__"]
