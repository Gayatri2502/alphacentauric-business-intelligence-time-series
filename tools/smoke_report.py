"""Generate a deterministic real-Kaggle evidence bundle for R/runtime checks."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from alphacentauric_bi.bi_engine import load_table
from alphacentauric_bi.bi_plus import analyze,export_report
import hashlib
source=ROOT/'src/alphacentauric_bi/data/kaggle_retail_monthly.csv';data=source.read_bytes()
frame,_,_=load_table(data,source.name)
config=dict(date_column='date',value_column='sales',filters={'region':'West','category':'Technology'},frequency='monthly',aggregation='sum',missing_policy='reject',model='Naive',season=12,horizon=6,confidence=95,review_accepted=True,unit='source sales units')
export_report(Path(sys.argv[1]),analyze(frame,config,{'filename':source.name,'sha256':hashlib.sha256(data).hexdigest()}))
print('Kaggle smoke report generated')
