"""Vercel ASGI entrypoint. Computation is scoped to the request, with no paid AI."""
import os
import sys
from pathlib import Path
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from alphacentauric_bi.cloud import app
