"""Download an annual World Bank indicator with source metadata; no credentials required."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen


def fetch(country, indicator, destination):
    if not re.fullmatch(r'[A-Z]{3}',country):raise ValueError('Use a three-letter country code')
    if not re.fullmatch(r'[A-Z0-9_.]{3,80}',indicator):raise ValueError('Invalid indicator code')
    destination=Path(destination)
    if destination.exists() or destination.with_suffix('.source.json').exists():raise ValueError('Choose a new output filename')
    url=f'https://api.worldbank.org/v2/country/{country}/indicator/{indicator}?format=json&per_page=20000'
    with urlopen(url,timeout=25) as response:
        if response.geturl().split('/')[2]!='api.worldbank.org':raise ValueError('Unexpected redirect host')
        raw=response.read(10*1024*1024+1)
    if len(raw)>10*1024*1024:raise ValueError('Response too large')
    payload=json.loads(raw)
    if len(payload)!=2 or not isinstance(payload[1],list):raise ValueError('Indicator response unavailable')
    rows=[{'date':f"{r['date']}-01-01",'value':r['value'],'country':country,'indicator':indicator} for r in payload[1] if r.get('value') is not None]
    rows.sort(key=lambda r:r['date'])
    if not rows:raise ValueError('No observed values found')
    with destination.open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['date','value','country','indicator']);writer.writeheader();writer.writerows(rows)
    destination.with_suffix('.source.json').write_text(json.dumps({'publisher':'World Bank','url':url,'retrieved_at':datetime.now(timezone.utc).isoformat(),'api_last_updated':payload[0].get('lastupdated'),'indicator_label':payload[1][0]['indicator']['value'],'response_sha256':hashlib.sha256(raw).hexdigest(),'csv_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'rows':len(rows),'note':'Annual observed indicator; verify units, revisions and source-specific reuse terms. Not a sector market-size estimate.'},indent=2))
    return len(rows)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--country',required=True);p.add_argument('--indicator',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();print(fetch(args.country,args.indicator,args.output),'observations saved')
