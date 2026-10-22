"""Load the explicit measurement example without adding runtime test dependencies."""
import importlib.util
from pathlib import Path
import sys
from synthetic_support import lesson

path=Path(__file__).resolve().parents[1]/'examples/measure_synthetic_capacity.py'
spec=importlib.util.spec_from_file_location('capacity_lesson',path)
runner=importlib.util.module_from_spec(spec)
old=sys.modules.get('synthetic_market_demo')
sys.modules['synthetic_market_demo']=lesson
try:spec.loader.exec_module(runner)
finally:
    if old is None:sys.modules.pop('synthetic_market_demo',None)
    else:sys.modules['synthetic_market_demo']=old
