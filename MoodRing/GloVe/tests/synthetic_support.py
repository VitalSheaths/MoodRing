"""Load the standalone synthetic lesson without runtime imports from test helpers."""
import importlib.util
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'examples/synthetic_market_demo.py'
spec=importlib.util.spec_from_file_location('synthetic_lesson',path)
lesson=importlib.util.module_from_spec(spec)
spec.loader.exec_module(lesson)
