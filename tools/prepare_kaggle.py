"""Fetch a pinned public Kaggle source and build a monthly, non-personal demo."""
import hashlib,io,json,zipfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen
import pandas as pd
HANDLE='rohitsahoo/sales-forecasting'
ROOT=Path(__file__).resolve().parents[1]
def prepare(archive=None,metadata=None):
    url='https://www.kaggle.com/api/v1/datasets/download/'+HANDLE
    raw=Path(archive).read_bytes() if archive else urlopen(url,timeout=45).read(8*1024*1024)
    meta=json.loads(Path(metadata).read_text()) if metadata else json.load(urlopen('https://www.kaggle.com/api/v1/datasets/view/'+HANDLE,timeout=30))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(p.file_size for p in z.infolist())>30*1024*1024:raise ValueError('Unexpectedly large source')
        source=z.read('train.csv');frame=pd.read_csv(io.BytesIO(source))
    dates=pd.to_datetime(frame['Order Date'],format='%d/%m/%Y',errors='raise')
    frame['period']=dates.dt.to_period('M').dt.to_timestamp().dt.strftime('%Y-%m-%d')
    result=frame.groupby(['period','Region','Category'],as_index=False,dropna=False).agg(sales=('Sales','sum'),order_lines=('Sales','size'))
    result=result.rename(columns={'period':'date','Region':'region','Category':'category'})
    result['country']='United States'
    folder=ROOT/'src/alphacentauric_bi/data';destination=folder/'kaggle_retail_monthly.csv';result.to_csv(destination,index=False,float_format='%.6f')
    provenance={'handle':HANDLE,'title':meta['title'],'url':'https://www.kaggle.com/datasets/'+HANDLE,'download_url':url,'license':meta['licenseName'],'source_updated_at':meta.get('lastUpdated'),'retrieved_at':datetime.now(timezone.utc).isoformat(),'source_sha256':hashlib.sha256(source).hexdigest(),'prepared_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'original_rows':len(frame),'prepared_rows':len(result),'first_period':result.date.min(),'last_period':result.date.max(),'transformations':['Parse Order Date explicitly as DD/MM/YYYY','Aggregate Sales by calendar month, region and category','Count order lines; do not claim they are distinct orders','Exclude customer names, IDs, addresses and product-level details'],'unit':'Sales in source currency units; source does not explicitly verify currency','caveat':'Historical educational retail records from Kaggle; not current country or sector market-size estimates. No independent verification of authenticity.'}
    (folder/'kaggle_retail_source.json').write_text(json.dumps(provenance,indent=2))
    return provenance
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--archive');p.add_argument('--metadata');a=p.parse_args();print(json.dumps(prepare(a.archive,a.metadata),indent=2))
