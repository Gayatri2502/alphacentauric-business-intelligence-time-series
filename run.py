#!/usr/bin/env python3
"""Run the source checkout; keep generated user files separate from source."""
import os
import sys
from pathlib import Path
PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR / 'src'))
os.environ.setdefault('BI_DATA_DIR', str(PROJECT_DIR / 'var'))
from alphacentauric_bi.app import main
if __name__ == '__main__':
    main()
