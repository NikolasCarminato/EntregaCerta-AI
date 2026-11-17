#!/usr/bin/env python3
"""Optional external source joins for EntregaCerta AI (not used in the VER0.4 scorecard).

Inputs are explicit, locally provided files. The module does NOT manufacture any
weather/road/marketing observations or auto-fill gaps with synthetic records.
Temporal controls: marketing won_date<=purchase; historical weather available_at<=checkpoint.
Road 2024 has historical mismatch for 2016-2018 and must be marked as a proxy.
"""
from __future__ import annotations
import argparse, io, json, zipfile, unicodedata, re
from pathlib import Path
import numpy as np
import pandas as pd
import olist_checkpoint as ref


def _assert_cols(df, cols, name):
    missing=set(cols)-set(df.columns)
    if missing:raise ValueError(f'{name} is missing required columns: {sorted(missing)}')

def normal_city(text):
    if pd.isna(text):return None
    t=''.join(c for c in unicodedata.normalize('NFKD',str(text)) if not unicodedata.combining(c))
    t=re.sub(r'[^a-z ]',' ',t.lower());return re.sub(r'\s+',' ',t).strip()

def add_marketing(base,filename):
    """Join closed deals by seller using nearest PRIOR win event, never future deal info."""
    d=pd.read_csv(filename)
    _assert_cols(d,['seller_id','won_date'],'olist_closed_deals_dataset.csv')
    selected=['seller_id','won_date']+[c for c in ['business_segment','lead_type','lead_behaviour_profile','has_company','has_gtin','average_stock','business_type','declared_product_catalog_size','declared_monthly_revenue'] if c in d.columns]
    d=d[selected].copy();d['won_date']=pd.to_datetime(d['won_date'],errors='coerce')
    d=d.dropna(subset=['seller_id','won_date']).sort_values('won_date',kind='mergesort')
    x=base[['order_id','seller_id','order_purchase_timestamp']].copy();x['row_order']=np.arange(len(x))
    x=x.sort_values('order_purchase_timestamp',kind='mergesort')
    z=pd.merge_asof(x,d,left_on='order_purchase_timestamp',right_on='won_date',by='seller_id',direction='backward',allow_exact_matches=True)
    assert (z.won_date.dropna()<=z.loc[z.won_date.notna(),'order_purchase_timestamp']).all()
    z=z.sort_values('row_order')
    z['marketing_has_prior_deal']=z.won_date.notna().astype('int8')
    z['marketing_deal_age_days']=(z.order_purchase_timestamp-z.won_date).dt.total_seconds()/86400
    cols=['order_id','marketing_has_prior_deal','marketing_deal_age_days']
    for c in selected:
        if c not in ['seller_id','won_date']:
            cols.append('marketing_'+c)
            z['marketing_'+c]=z[c]
    return z[cols].reset_index(drop=True), {'rows_in_source':len(d),'covered_orders_with_prior_win':int(z.marketing_has_prior_deal.sum()),'covered_pct':float(z.marketing_has_prior_deal.mean()*100)}

def map_cities(base,filename):
    """A user-supplied municipality lookup must have: ibge_code, city, state."""
    m=pd.read_csv(filename,dtype={'ibge_code':str,'state':str,'city':str})
    _assert_cols(m,['ibge_code','city','state'],'municipios_ibge.csv')
    m=m.dropna(subset=['ibge_code','city','state']).copy()
    m['uf']=m.state.str.upper().str.strip()
    m['norm_city']=m.city.map(normal_city)
    m['key']=m.uf+'|'+m.norm_city
    cnt=m.groupby('key').ibge_code.nunique()
    ambiguous=set(cnt[cnt>1].index)
    m=m.loc[~m.key.isin(ambiguous)].drop_duplicates('key').set_index('key').ibge_code
    x=base[['order_id','seller_city','seller_state','customer_city','customer_state']].copy()
    for prefix in ('seller','customer'):
        k=x[f'{prefix}_state'].fillna('').str.upper().str.strip()+'|'+x[f'{prefix}_city'].map(normal_city).fillna('')
        x[f'{prefix}_ibge']=k.map(m)
    return x[['order_id','seller_ibge','customer_ibge']], {'unique_municipios':int(len(m)),'ambiguous_city_state_keys':len(ambiguous),'seller_code_coverage_pct':float(x.seller_ibge.notna().mean()*100),'customer_code_coverage_pct':float(x.customer_ibge.notna().mean()*100)}

def iter_road_chunks(filename, chunksize=400_000):
    """The Zenodo CSV ZIP is semicolon-delimited; use streaming to control memory."""
    p=Path(filename)
    kwargs=dict(sep=';',dtype={'orig':str,'dest':str},chunksize=chunksize,low_memory=False)
    if p.suffix.lower()=='.zip':
        with zipfile.ZipFile(p) as z:
            files=[x for x in z.namelist() if x.lower().endswith('.csv')]
            if len(files)!=1:raise ValueError('Road ZIP must have exactly one CSV file.')
            with z.open(files[0]) as f:
                yield from pd.read_csv(f,**kwargs)
    else:yield from pd.read_csv(p,**kwargs)

def add_roads(base,roadfile,lookupfile):
    """Directed municipality road distances FROM 2024. NOT a 2016–2018 network."""
    cities,cityreport=map_cities(base,lookupfile)
    x=cities.copy()
    known=x.seller_ibge.notna()&x.customer_ibge.notna()
    x['key']=x.seller_ibge.fillna('')+'|'+x.customer_ibge.fillna('')
    wanted=set(x.loc[known,'key'].unique())
    origins=set(x.loc[known,'seller_ibge'].unique());destinations=set(x.loc[known,'customer_ibge'].unique())
    pieces=[]
    count_rows=0
    for chunk in iter_road_chunks(roadfile):
        _assert_cols(chunk,['orig','dest','dist','dur'],'dist_brasil.csv')
        count_rows+=len(chunk)
        ch=chunk.loc[chunk.orig.isin(origins)&chunk.dest.isin(destinations),['orig','dest','dist','dur']].copy()
        if not len(ch):continue
        ch['key']=ch.orig+'|'+ch.dest
        ch=ch.loc[ch.key.isin(wanted),['key','dist','dur']]
        if len(ch):pieces.append(ch)
    found=pd.concat(pieces,ignore_index=True).drop_duplicates('key') if pieces else pd.DataFrame(columns=['key','dist','dur'])
    found['road_km_2024']=pd.to_numeric(found.dist,errors='coerce')/1000
    found['road_minutes_2024']=pd.to_numeric(found.dur,errors='coerce')
    z=x[['order_id','key']].merge(found[['key','road_km_2024','road_minutes_2024']],on='key',how='left',validate='many_to_one')
    return z[['order_id','road_km_2024','road_minutes_2024']], {'road_file_rows_examined':count_rows,'unique_requested_pairs':len(wanted),'matched_orders':int(z.road_km_2024.notna().sum()),'matched_pct':float(z.road_km_2024.notna().mean()*100),'network_date':'May 2024 - not historically faithful to 2016-2018',**cityreport}

def add_weather(x,filename):
    """Merge lagged, already-published observations to checkpoint by UF.

    Required columns: state, date, precipitation_mm, temperature_c.
    Optional available_at; if absent, deliberately impose a CONSERVATIVE 7-day
    publication lag to approximate lagged ERA5. This is not real-time rain.
    Intended format: daily state-level approximation assembled outside this tool.
    """
    w=pd.read_csv(filename)
    _assert_cols(w,['state','date','precipitation_mm','temperature_c'],'weather_daily.csv')
    w=w.copy()
    w['state']=w.state.astype(str).str.upper().str.strip()
    w['date']=pd.to_datetime(w['date'],errors='coerce').dt.normalize()
    w['precipitation_mm']=pd.to_numeric(w.precipitation_mm,errors='coerce')
    w['temperature_c']=pd.to_numeric(w.temperature_c,errors='coerce')
    if 'available_at' in w.columns:w['available_at']=pd.to_datetime(w.available_at,errors='coerce')
    else:w['available_at']=w.date+pd.Timedelta(days=7)
    w=w.dropna(subset=['state','date','available_at']).sort_values(['state','date'])
    if w.duplicated(['state','date']).any():raise ValueError('weather_daily must have at most one row per state and day (state-level proxy)')
    gaps=w.groupby('state',sort=False)['date'].diff().dropna()
    if (gaps>pd.Timedelta(days=1)).any():
        raise ValueError('weather_daily has gaps between daily observations: fill absent days as explicit NaN, never as zero rainfall')
    # Require last seven OBSERVATION days in rolling window, not the future.
    w['rain_last7_available_mm']=w.groupby('state',observed=True).precipitation_mm.transform(lambda s:s.rolling(7,min_periods=5).sum())
    w['temp_last7_available_c']=w.groupby('state',observed=True).temperature_c.transform(lambda s:s.rolling(7,min_periods=5).mean())
    if (w.available_at<w.date).any():raise ValueError('A weather observation cannot be available before its observation day')
    # Ensure last daily value used is always published BEFORE checkpoint.
    z=x[['order_id','checkpoint_at','seller_state','customer_state']].copy();z['row_order']=np.arange(len(z))
    weather_cols=[];report={'weather_rows':len(w),'weather_publication_delay':'explicit available_at, else date + 7d','spatial_resolution':'state proxy, not actual location'}
    for side in ('seller','customer'):
        k=f'{side}_state'
        left=z[['row_order','checkpoint_at',k]].copy().rename(columns={k:'state'})
        left['state']=left.state.fillna('__MISSING__').astype(str).str.upper().str.strip()
        left=left.sort_values('checkpoint_at',kind='mergesort')
        right=w[['state','date','available_at','rain_last7_available_mm','temp_last7_available_c']].sort_values('available_at',kind='mergesort')
        t=pd.merge_asof(left,right,left_on='checkpoint_at',right_on='available_at',by='state',direction='backward',allow_exact_matches=True)
        assert (t.loc[t.available_at.notna(),'available_at']<=t.loc[t.available_at.notna(),'checkpoint_at']).all()
        t=t.set_index('row_order').reindex(range(len(z)))
        for origin,dest in [('rain_last7_available_mm',f'{side}_rain_7d_published_mm'),('temp_last7_available_c',f'{side}_temp_7d_published_c')]:
            z[dest]=t[origin].to_numpy();weather_cols.append(dest)
        report[f'{side}_weather_coverage_pct']=float(z[f'{side}_rain_7d_published_mm'].notna().mean()*100)
    return z[['order_id']+weather_cols].reset_index(drop=True),report

def main():
    pa=argparse.ArgumentParser();pa.add_argument('--zip',required=True);pa.add_argument('--out',required=True)
    pa.add_argument('--marketing');pa.add_argument('--roads');pa.add_argument('--ibge');pa.add_argument('--weather');pa.add_argument('--days-before',type=int,default=2)
    args=pa.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    base=ref.build_base(args.zip).sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
    x=ref.build_checkpoint(base,days_before=args.days_before)
    z=x[['order_id']].copy();reports={'source_rows':len(x),'checkpoint':args.days_before,'integrations':{}}
    if args.marketing:
        f,r=add_marketing(x,args.marketing)
        z=z.merge(f,on='order_id',how='left',validate='one_to_one');reports['integrations']['marketing']=r
    if args.roads or args.ibge:
        if not(args.roads and args.ibge):raise ValueError('Roads requires BOTH --roads and --ibge')
        f,r=add_roads(x,args.roads,args.ibge)
        z=z.merge(f,on='order_id',how='left',validate='one_to_one');reports['integrations']['roads']=r
    if args.weather:
        f,r=add_weather(x,args.weather)
        z=z.merge(f,on='order_id',how='left',validate='one_to_one');reports['integrations']['weather']=r
    assert len(z)==len(base) and z.order_id.is_unique
    reports['columns']=list(z.columns)
    z.to_csv(out/'external_enrichment.csv',index=False)
    (out/'join_report.json').write_text(json.dumps(reports,indent=2,ensure_ascii=False))
    print(json.dumps(reports,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
