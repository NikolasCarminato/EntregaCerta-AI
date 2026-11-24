#!/usr/bin/env python3
"""VER0.3: reproducible retrospective Olist checkpoint benchmark.
No changes to the input ZIP. All output saved in --out folder.
External weather/road/marketing data are NOT imputed or invented.
"""
from __future__ import annotations
import argparse, zipfile, json, warnings, os, platform, time
from pathlib import Path
from datetime import date, timedelta
from dateutil.easter import easter
import numpy as np
import pandas as pd
from sklearn.metrics import (roc_auc_score,average_precision_score,precision_score,
     recall_score,f1_score,confusion_matrix,precision_recall_curve)
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier, early_stopping, log_evaluation
from xgboost import XGBClassifier

warnings.filterwarnings('ignore', category=FutureWarning)
SEED=42

def dt(v):
    return pd.to_datetime(v, errors='coerce')

def find_holidays(year):
    fixed=[(1,1),(4,21),(5,1),(9,7),(10,12),(11,2),(11,15),(12,25)]
    dates={pd.Timestamp(year,m,d) for m,d in fixed}
    e=pd.Timestamp(easter(year))
    dates |= {e-pd.Timedelta(days=2), e-pd.Timedelta(days=47),e-pd.Timedelta(days=48),e+pd.Timedelta(days=60)}
    return dates

def sigmoid(x): return 1/(1+np.exp(-x))

def haversine(a,b,c,d):
    lat1=np.radians(a.astype(float));lon1=np.radians(b.astype(float))
    lat2=np.radians(c.astype(float));lon2=np.radians(d.astype(float))
    dl=lat2-lat1;dn=lon2-lon1
    h=np.sin(dl/2)**2+np.cos(lat1)*np.cos(lat2)*np.sin(dn/2)**2
    return 6371*2*np.arcsin(np.sqrt(h.clip(0,1)))

def read_tables(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        def read(n,**kwargs):return pd.read_csv(z.open(n),**kwargs)
        orders=read('olist_orders_dataset.csv',parse_dates=['order_purchase_timestamp','order_approved_at','order_delivered_carrier_date','order_delivered_customer_date','order_estimated_delivery_date'])
        customers=read('olist_customers_dataset.csv')
        sellers=read('olist_sellers_dataset.csv')
        items=read('olist_order_items_dataset.csv')
        products=read('olist_products_dataset.csv')
        payments=read('olist_order_payments_dataset.csv')
        geo=read('olist_geolocation_dataset.csv',usecols=['geolocation_zip_code_prefix','geolocation_lat','geolocation_lng'])
    return orders,customers,sellers,items,products,payments,geo

def build_base(zip_path):
    o,customers,sellers,items,products,payments,geo=read_tables(zip_path)
    o=o.loc[(o.order_status=='delivered')&o.order_purchase_timestamp.notna()&o.order_estimated_delivery_date.notna()&o.order_delivered_customer_date.notna()].copy()
    o['late']=(o.order_delivered_customer_date.dt.normalize()>o.order_estimated_delivery_date.dt.normalize()).astype(np.int8)
    print('Pedidos entregues validos',len(o),'atrasos',o.late.sum(),flush=True)
    items['shipping_limit_date']=dt(items.shipping_limit_date)
    pr=products[['product_id','product_category_name','product_weight_g','product_length_cm','product_height_cm','product_width_cm','product_photos_qty','product_description_lenght']].copy()
    pr['volume_cm3']=pr.product_length_cm*pr.product_height_cm*pr.product_width_cm
    items=items.merge(pr,on='product_id',how='left',validate='many_to_one')
    first=items.sort_values(['order_id','order_item_id']).drop_duplicates('order_id')[['order_id','seller_id','product_category_name']].rename(columns={'product_category_name':'category'})
    ag=items.groupby('order_id',sort=False).agg(n_items=('order_item_id','size'),n_sellers=('seller_id','nunique'),subtotal=('price','sum'),avg_item_price=('price','mean'),freight_total=('freight_value','sum'),weight_total=('product_weight_g','sum'),volume_total=('volume_cm3','sum'),photos_avg=('product_photos_qty','mean'),description_len_avg=('product_description_lenght','mean'),shipping_limit=('shipping_limit_date','max')).reset_index()
    ag=ag.merge(first,on='order_id',how='left',validate='one_to_one')
    pay=payments.groupby('order_id',sort=False).agg(payments_n=('payment_sequential','size'),payment_total=('payment_value','sum'),installments_max=('payment_installments','max')).reset_index()
    pfirst=payments.sort_values(['order_id','payment_sequential']).drop_duplicates('order_id')[['order_id','payment_type']]
    pay=pay.merge(pfirst,on='order_id',how='left')
    geo=geo.loc[geo.geolocation_lat.between(-35,6)&geo.geolocation_lng.between(-75,-32)]
    gc=geo.groupby('geolocation_zip_code_prefix',sort=False)[['geolocation_lat','geolocation_lng']].median().reset_index()
    del geo,items
    x=o.merge(customers[['customer_id','customer_zip_code_prefix','customer_state','customer_city']],on='customer_id',how='left',validate='many_to_one')
    x=x.merge(ag,on='order_id',how='left',validate='one_to_one')
    x=x.merge(pay,on='order_id',how='left',validate='one_to_one')
    x=x.merge(sellers[['seller_id','seller_zip_code_prefix','seller_state','seller_city']],on='seller_id',how='left',validate='many_to_one')
    x=x.merge(gc.rename(columns={'geolocation_zip_code_prefix':'customer_zip_code_prefix','geolocation_lat':'cust_lat','geolocation_lng':'cust_lon'}),on='customer_zip_code_prefix',how='left',validate='many_to_one')
    x=x.merge(gc.rename(columns={'geolocation_zip_code_prefix':'seller_zip_code_prefix','geolocation_lat':'sell_lat','geolocation_lng':'sell_lon'}),on='seller_zip_code_prefix',how='left',validate='many_to_one')
    x['haversine_km']=haversine(x.sell_lat,x.sell_lon,x.cust_lat,x.cust_lon)
    x['same_state']=(x.seller_state==x.customer_state).astype(np.int8)
    x['route_state']=x.seller_state.fillna('NA')+'__'+x.customer_state.fillna('NA')
    x['promised_days']=(x.order_estimated_delivery_date-x.order_purchase_timestamp).dt.total_seconds()/86400
    x['payment_delay_hours']=(x.order_approved_at-x.order_purchase_timestamp).dt.total_seconds()/3600
    x['freight_ratio']=x.freight_total/(x.subtotal+0.01)
    x['price_per_kg']=x.subtotal/(x.weight_total/1000+0.1)
    x['purchase_dow']=x.order_purchase_timestamp.dt.dayofweek
    x['purchase_month']=x.order_purchase_timestamp.dt.month
    x['purchase_hour']=x.order_purchase_timestamp.dt.hour
    x['promise_dow']=x.order_estimated_delivery_date.dt.dayofweek
    x['promise_month']=x.order_estimated_delivery_date.dt.month
    x['seller_customer_lat_delta']=(x.sell_lat-x.cust_lat).abs()
    x['seller_customer_lon_delta']=(x.sell_lon-x.cust_lon).abs()
    x['has_geo']=x.haversine_km.notna().astype('int8')
    return x

def attach_history(x,asof_col,key,prefix):
    # Delayed labels are eligible only after order has actually been delivered.
    events=x[[key,'order_delivered_customer_date','late']].dropna(subset=[key,'order_delivered_customer_date']).copy()
    events=events.groupby([key,'order_delivered_customer_date'],as_index=False).agg(n_done=('late','size'),n_late=('late','sum'))
    events=events.sort_values('order_delivered_customer_date')
    events[f'{prefix}_n']=events.groupby(key).n_done.cumsum()
    events[f'{prefix}_late_n']=events.groupby(key).n_late.cumsum()
    events=events[[key,'order_delivered_customer_date',f'{prefix}_n',f'{prefix}_late_n']]
    left=x[['order_id',key,asof_col]].copy().sort_values(asof_col)
    combined=pd.merge_asof(left,events,left_on=asof_col,right_on='order_delivered_customer_date',by=key,direction='backward',allow_exact_matches=False)
    combined=combined[['order_id',f'{prefix}_n',f'{prefix}_late_n']]
    combined[f'{prefix}_n']=combined[f'{prefix}_n'].fillna(0)
    combined[f'{prefix}_late_n']=combined[f'{prefix}_late_n'].fillna(0)
    combined[f'{prefix}_rate']=(combined[f'{prefix}_late_n']+0.8)/(combined[f'{prefix}_n']+12.0)
    return x.merge(combined,on='order_id',how='left',validate='one_to_one')

def build_checkpoint(base,days_before=2):
    x=base.copy()
    x['checkpoint_at']=x.order_estimated_delivery_date.dt.normalize()-pd.to_timedelta(days_before,unit='D')
    x['eligible']=(x.order_purchase_timestamp<=x.checkpoint_at)
    x['pending']=x.order_delivered_customer_date>x.checkpoint_at
    x['approval_known']=x.order_approved_at.notna()&(x.order_approved_at<=x.checkpoint_at)
    x['payment_delay_hours']=x['payment_delay_hours'].where(x.approval_known)
    x['carrier_known']=x.order_delivered_carrier_date.notna()&(x.order_delivered_carrier_date<=x.checkpoint_at)
    x['in_transit_days']=((x.checkpoint_at-x.order_delivered_carrier_date).dt.total_seconds()/86400).where(x.carrier_known).clip(lower=0)
    x['not_dispatched']=~x.carrier_known
    x['age_days']=(x.checkpoint_at-x.order_purchase_timestamp).dt.total_seconds()/86400
    x['order_approval_age_days']=(x.checkpoint_at-x.order_approved_at).dt.total_seconds().div(86400).where(x.approval_known).clip(lower=0)
    x['time_before_due_when_dispatched']=(x.order_estimated_delivery_date-x.order_delivered_carrier_date).dt.total_seconds().div(86400).where(x.carrier_known)
    x['shipping_limit_age_days']=(x.checkpoint_at-x.shipping_limit).dt.total_seconds()/86400
    x['not_sent_and_over_limit']=((x.not_dispatched)&(x.shipping_limit_age_days>0)).astype('int8')
    x['dispatch_late_to_shipping_limit']=((x.order_delivered_carrier_date>x.shipping_limit)&x.carrier_known).astype('int8')
    x['preparation_elapsed_fraction']=x.age_days/x.promised_days.clip(lower=1)
    x['checkpoint_month']=x.checkpoint_at.dt.month
    x['checkpoint_dow']=x.checkpoint_at.dt.dayofweek
    x['checkpoint_year']=x.checkpoint_at.dt.year
    h=set().union(*[find_holidays(year) for year in [2016,2017,2018,2019]])
    x['checkpoint_holiday']=x.checkpoint_at.isin(h).astype('int8')
    x['promised_holiday']=x.order_estimated_delivery_date.dt.normalize().isin(h).astype('int8')
    x['days_to_next_holiday']=x.checkpoint_at.map(lambda q:min((d-q).days for d in h if d>=q) if pd.notna(q) and any(d>=q for d in h) else 30).clip(upper=30)
    x=attach_history(x,'checkpoint_at','seller_id','seller_hist')
    x=attach_history(x,'checkpoint_at','route_state','route_hist')
    x=attach_history(x,'checkpoint_at','category','category_hist')
    return x

def feature_table(x,use_history=True):
    cols=['promised_days','payment_delay_hours','freight_ratio','price_per_kg','purchase_dow','purchase_month','purchase_hour','promise_dow','promise_month','n_items','n_sellers','subtotal','avg_item_price','freight_total','weight_total','volume_total','photos_avg','description_len_avg','payments_n','payment_total','installments_max','haversine_km','same_state','has_geo','seller_customer_lat_delta','seller_customer_lon_delta','age_days','order_approval_age_days','in_transit_days','not_dispatched','carrier_known','approval_known','time_before_due_when_dispatched','shipping_limit_age_days','not_sent_and_over_limit','dispatch_late_to_shipping_limit','preparation_elapsed_fraction','checkpoint_month','checkpoint_dow','checkpoint_year','checkpoint_holiday','promised_holiday','days_to_next_holiday','seller_state','customer_state','seller_city','customer_city','category','payment_type','route_state']
    if use_history: cols+=['seller_hist_n','seller_hist_late_n','seller_hist_rate','route_hist_n','route_hist_late_n','route_hist_rate','category_hist_n','category_hist_late_n','category_hist_rate']
    # Avoid full city categorical high-cardinality overfit in thin checkpoints.
    cats=['seller_state','customer_state','category','payment_type','route_state']
    cols=[c for c in cols if c not in ['seller_city','customer_city']]
    out=x[cols].copy()
    for c in cats:out[c]=out[c].fillna('__MISSING__').astype(str)
    for c in out.columns:
        if c not in cats:out[c]=pd.to_numeric(out[c],errors='coerce').replace([np.inf,-np.inf],np.nan)
    return out,cats

def metrics(y,p,thr):
    pred=(p>=thr).astype(int)
    tn,fp,fn,tp=confusion_matrix(y,pred,labels=[0,1]).ravel()
    return dict(threshold=float(thr),precision=float(precision_score(y,pred,zero_division=0)),recall=float(recall_score(y,pred,zero_division=0)),f1=float(f1_score(y,pred,zero_division=0)),pr_auc=float(average_precision_score(y,p)),roc_auc=float(roc_auc_score(y,p)),tn=int(tn),fp=int(fp),fn=int(fn),tp=int(tp),n=int(len(y)),positives=int(np.sum(y)))

def select_threshold(y,p,min_recall=.70,min_precision=.30):
    precision,recall,thrs=precision_recall_curve(y,p)
    pp=precision[:-1];rr=recall[:-1]
    f1=np.divide(2*pp*rr,pp+rr,out=np.zeros_like(pp),where=(pp+rr)>0)
    ok=(rr>=min_recall)&(pp>=min_precision)
    if not ok.any():
        ok=rr>=min_recall
    if not ok.any(): ok=np.ones_like(rr,dtype=bool)
    j=np.argmax(np.where(ok,f1,-1.0))
    return float(thrs[j]),bool(((rr[j]>=min_recall)&(pp[j]>=min_precision)))

def train_models(x,features,cats,split,days_before,out):
    eligible=x.eligible.to_numpy()
    train_idx=(split=='train')&eligible;val_idx=(split=='val')&eligible;test_idx=(split=='test')&eligible
    # Only active orders require prediction; already delivered before snapshot have known on-time outcomes.
    pending=x.pending.to_numpy()&eligible
    itr=train_idx&pending;iva=val_idx&pending;ite=test_idx&pending
    # To avoid training on outcomes not known at first validation snapshot, purge train cases closed later.
    val_start=x.loc[val_idx,'checkpoint_at'].min()
    purged=(itr&(x.order_delivered_customer_date.to_numpy()>val_start))
    itr=itr&~purged
    print('Checkpoint',days_before,'train pending',int(itr.sum()),'val pending',int(iva.sum()),'test pending',int(ite.sum()),'purged',int(purged.sum()),flush=True)
    y=x.late.to_numpy();all_test_y=y[test_idx]
    # In some datasets a few purchases can be later than checkpoint; assert previously.
    df=features.copy()
    Xtr=df.loc[itr].copy();Xval=df.loc[iva].copy();Xte=df.loc[ite].copy()
    models={}
    for name in ['CatBoost','CatBoost_unbalanced','LightGBM','XGBoost']:
        t=time.perf_counter()
        if name.startswith('CatBoost'):
            model=CatBoostClassifier(iterations=520,depth=6,learning_rate=.045,l2_leaf_reg=9,random_strength=1.0,
                loss_function='Logloss',eval_metric='PRAUC:type=Classic',random_seed=SEED,
                auto_class_weights='Balanced' if name=='CatBoost' else None,thread_count=4,verbose=False,
                allow_writing_files=False,one_hot_max_size=10)
            model.fit(Xtr,y[itr],cat_features=cats,eval_set=(Xval,y[iva]),early_stopping_rounds=65,verbose=False)
            pval=model.predict_proba(Xval)[:,1];pte=model.predict_proba(Xte)[:,1]
            ntrees=model.best_iteration_+1 if model.best_iteration_ is not None else 520
            imp=sorted(zip(df.columns,map(float,model.feature_importances_)),key=lambda q:-q[1])[:15]
        else:
            for c in cats:
                cat=pd.Categorical(Xtr[c].unique())
                Xtr[c]=pd.Categorical(Xtr[c],categories=cat.categories)
                Xval[c]=pd.Categorical(Xval[c],categories=cat.categories)
                Xte[c]=pd.Categorical(Xte[c],categories=cat.categories)
            if name=='LightGBM':
                model=LGBMClassifier(n_estimators=650,learning_rate=.032,num_leaves=17,max_depth=-1,min_child_samples=85,
                    reg_lambda=8,colsample_bytree=.85,subsample=.9,subsample_freq=1,
                    random_state=SEED,n_jobs=4,verbosity=-1)
                model.fit(Xtr,y[itr],eval_set=[(Xval,y[iva])],eval_metric='average_precision',
                    callbacks=[early_stopping(65,verbose=False),log_evaluation(0)])
                ntrees=model.best_iteration_;imp=sorted(zip(df.columns,map(float,model.feature_importances_)),key=lambda q:-q[1])[:15]
            else:
                model=XGBClassifier(n_estimators=650,max_depth=4,learning_rate=.04,min_child_weight=8,
                    subsample=.9,colsample_bytree=.85,reg_lambda=10,tree_method='hist',enable_categorical=True,
                    max_cat_to_onehot=10,eval_metric='aucpr',early_stopping_rounds=65,random_state=SEED,n_jobs=4)
                model.fit(Xtr,y[itr],eval_set=[(Xval,y[iva])],verbose=False)
                ntrees=model.best_iteration+1
                imp=sorted(zip(df.columns,map(float,model.feature_importances_)),key=lambda q:-q[1])[:15]
            pval=model.predict_proba(Xval)[:,1];pte=model.predict_proba(Xte)[:,1]
        fname=(f'Dminus{days_before}_{name}.cbm' if name.startswith('CatBoost') else f'Dminus{days_before}_{name}.txt' if name=='LightGBM' else f'Dminus{days_before}_{name}.json')
        fpath=out/fname
        if name.startswith('CatBoost'):model.save_model(str(fpath))
        elif name=='LightGBM':model.booster_.save_model(str(fpath))
        else:model.save_model(str(fpath))
        threshold,meets=select_threshold(y[iva],pval)
        # all-order cohort operational scoring: already delivered => score zero
        vpred=np.zeros(val_idx.sum());vpred[pending[val_idx]]=pval
        tpred=np.zeros(test_idx.sum());tpred[pending[test_idx]]=pte
        mt=metrics(y[test_idx],tpred,threshold)
        mv=metrics(y[val_idx],vpred,threshold)
        mt['best_iteration']=int(ntrees);mt['n_features']=int(len(df.columns));mt['model_file']=fname
        mt['val_metrics']=mv;mt['validation_constraints_met']=meets
        mt['top_features']=imp
        models[name]=(mt,pval,pte)
        print(name,'test precision',round(mt['precision'],4),'recall',round(mt['recall'],4),'F1',round(mt['f1'],4),'PRAUC',round(mt['pr_auc'],4),'threshold',round(threshold,3),'trees',ntrees,'elapsed',round(time.perf_counter()-t,1),flush=True)
    # Ensemble with two different model types (CatBoost + LightGBM). Threshold tuned only on validation.
    for nm,weights in [('CatBoost_LightGBM_ensemble',[.5,.5]),('CatBoost_XGBoost_LightGBM_ensemble',[.4,.3,.3])]:
        names=['CatBoost','LightGBM'] if len(weights)==2 else ['CatBoost','XGBoost','LightGBM']
        pval=sum(models[n][1]*w for n,w in zip(names,weights))
        pte=sum(models[n][2]*w for n,w in zip(names,weights))
        thr,meets=select_threshold(y[iva],pval)
        vp=np.zeros(val_idx.sum());vp[pending[val_idx]]=pval
        tp=np.zeros(test_idx.sum());tp[pending[test_idx]]=pte
        z=metrics(y[test_idx],tp,thr);z['val_metrics']=metrics(y[val_idx],vp,thr);z['validation_constraints_met']=meets
        models[nm]=(z,pval,pte)
        print(nm,'precision',round(z['precision'],4),'recall',round(z['recall'],4),'F1',round(z['f1'],4),flush=True)
    rule=x.loc[test_idx,'pending'].astype(int).to_numpy()
    tn,fp,fn,tp=confusion_matrix(y[test_idx],rule,labels=[0,1]).ravel()
    r=dict(precision=float(precision_score(y[test_idx],rule)),recall=float(recall_score(y[test_idx],rule)),f1=float(f1_score(y[test_idx],rule)),tn=int(tn),fp=int(fp),fn=int(fn),tp=int(tp),n=int(test_idx.sum()),positives=int(y[test_idx].sum()))
    data={'checkpoint_days_before_due':days_before,'n_train_pending':int(itr.sum()),'n_val_pending':int(iva.sum()),'n_test_pending':int(ite.sum()),'purged_train':int(purged.sum()),'n_val_all':int(val_idx.sum()),'n_test_all':int(test_idx.sum()),'pending_rule':r,'models':{k:v[0] for k,v in models.items()},'all_test_late_rate':float(y[test_idx].mean())}
    with open(out/f'results_tminus{days_before}.json','w') as f:json.dump(data,f,indent=2,ensure_ascii=False,default=float)
    return data

def main():
    p=argparse.ArgumentParser();p.add_argument('--zip',default='/mnt/data/logistic-brazilian-dataset.zip');p.add_argument('--out',default='/mnt/data/VER03_experimento');p.add_argument('--days',type=int,nargs='+',default=[2,3]);args=p.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    print('Loading',args.zip,flush=True)
    base=build_base(args.zip)
    base=base.sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
    split=np.full(len(base),'test',dtype=object);split[:64320]='train';split[64320:64320+13547]='val'
    print('Split:',[(a,int(np.sum(split==a)),int(base.loc[split==a,'late'].sum())) for a in ['train','val','test']],flush=True)
    results={}
    for d in args.days:
        x=build_checkpoint(base,days_before=d)
        df,cats=feature_table(x)
        results[str(d)]=train_models(x,df,cats,split,d,out)
    meta={'source_zip':os.path.basename(args.zip),'input_zip_sha256':__import__('hashlib').sha256(open(args.zip,'rb').read()).hexdigest(),'seed':SEED,'python':platform.python_version(),'pandas':pd.__version__,'catboost':__import__('catboost').__version__,'xgboost':__import__('xgboost').__version__,'lightgbm':__import__('lightgbm').__version__,'train':64320,'validation':13547,'test':18603,'snapshot':'00:00 2/3 dias antes da data prometida','external_data_used':[],'features_used':list(df.columns),'categorical_features':cats,'data_leakage_protection':'Historical labels included only if delivered strictly before checkpoint; model trained only on pending orders; purge training labels not known at first validation checkpoint; validation-only threshold and early stopping.'}
    with open(out/'experiment_metadata.json','w') as f:json.dump(meta,f,indent=2,ensure_ascii=False)
    print('Saved JSON reports',str(out),flush=True)
if __name__=='__main__':main()
