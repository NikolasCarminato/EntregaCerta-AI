#!/usr/bin/env python3
"""EntregaCerta AI VER0.4: exploratory offline ablation + time-aware validation.

Does not mutate the user's source ZIP or prior versions. A no-external-data
experiment. This study does NOT claim climate/road/marketing enrichment.
"""
from __future__ import annotations
import argparse, json, time, hashlib, platform, warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score, confusion_matrix,precision_score,recall_score,f1_score,precision_recall_curve
from sklearn.model_selection import TimeSeriesSplit
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier, early_stopping, log_evaluation
import lightgbm, catboost, sklearn
import olist_checkpoint as ref

SEED = 42
warnings.filterwarnings('ignore',category=FutureWarning)

def asof_counts(x,key,window_days:int):
    """Count historical delivered orders for key during (asof - window, asof).

    The target outcome of an order becomes usable only after the delivery event.
    Does not use future outcomes for its rate features.
    """
    def maskkey(s):return s.fillna('__MISSING__').astype(str)
    events=x[[key,'order_delivered_customer_date','late']].copy()
    events[key]=maskkey(events[key]);events=events.dropna(subset=['order_delivered_customer_date'])
    events=events.groupby([key,'order_delivered_customer_date'],sort=False,observed=True).agg(n=('late','size'),bad=('late','sum')).reset_index()
    events=events.sort_values('order_delivered_customer_date',kind='mergesort')
    events['n']=events.groupby(key,sort=False).n.cumsum()
    events['bad']=events.groupby(key,sort=False).bad.cumsum()
    l=x[[key,'checkpoint_at']].copy(); l[key]=maskkey(l[key]);l['row_pos']=np.arange(len(l))
    def asof(delta):
        left=l.copy()
        left['asof']=left['checkpoint_at']-pd.Timedelta(days=delta)
        left=left.sort_values('asof',kind='mergesort')
        tmp=pd.merge_asof(left,events,left_on='asof',right_on='order_delivered_customer_date',by=key,direction='backward',allow_exact_matches=False)
        return tmp.set_index('row_pos')[['n','bad']].reindex(range(len(x))).fillna(0)
    now=asof(0);old=asof(window_days)
    n=(now.n-old.n).clip(lower=0).astype('float32')
    bad=(now.bad-old.bad).clip(lower=0).astype('float32')
    return n,bad

def enhanced_features(x, base_df):
    z=base_df.copy()
    # Multi-window historical failure rates: cutoff is ALWAYS strictly before asof.
    for key,prefix,windows in [('seller_id','seller',[30,90]),('route_state','route',[90]),('category','category',[90])]:
        for days in windows:
            n,bad=asof_counts(x,key,days)
            z[f'{prefix}_completed_{days}d']=n.to_numpy()
            z[f'{prefix}_late_{days}d']=bad.to_numpy()
            z[f'{prefix}_rate_{days}d']=((bad+0.8)/(n+12.0)).to_numpy().astype('float32')
    # No route distances are invented: all geography remains Haversine straight-line.
    z['geo_km_per_day_promised']=(x.haversine_km/x.promised_days.clip(lower=1)).to_numpy()
    z['geo_east_west_sign']=np.sign((x.cust_lon-x.sell_lon).to_numpy(dtype=float))
    z['geo_north_south_sign']=np.sign((x.cust_lat-x.sell_lat).to_numpy(dtype=float))
    z['same_city']=(x.seller_city.fillna('').str.strip().str.lower()==x.customer_city.fillna('@@').str.strip().str.lower()).astype('int8').to_numpy()
    z['freight_per_kg']=(x.freight_total/(x.weight_total.clip(lower=10)/1000)).to_numpy()
    z['freight_per_km']=(x.freight_total/x.haversine_km.clip(lower=10)).to_numpy()
    z['total_weight_per_item']=(x.weight_total/x.n_items.clip(lower=1)).to_numpy()
    z['days_fulfillment_margin']=(x.promised_days-x.age_days).to_numpy()
    z['km_weight_interaction']=(x.haversine_km*x.weight_total.clip(lower=0)/1000).to_numpy()
    for col in z.columns:
        if col not in ['seller_state','customer_state','category','payment_type','route_state']:
            z[col]=pd.to_numeric(z[col],errors='coerce').replace([np.inf,-np.inf],np.nan)
    assert len(z)==len(x)
    return z

def get_masks(x,split):
    elig=x.eligible.to_numpy()
    y=x.late.to_numpy(dtype=int)
    pending=x.pending.to_numpy()&elig
    idxval=np.where((split=='val')&elig)[0]
    half=len(idxval)//2
    early=idxval[:half]
    recent=idxval[half:]
    test=np.where((split=='test')&elig)[0]
    train=np.where((split=='train')&pending)[0]
    # Purge training label that was not known at the first early-stopping snapshot.
    tmin=x.checkpoint_at.iloc[early].min()
    train=train[(x.order_delivered_customer_date.iloc[train]<tmin).to_numpy()]
    # Early stopping and calibration are two temporally separated windows.
    # Both may use outcomes known by the first test checkpoint, but never future test labels.
    cal_start=x.checkpoint_at.iloc[recent].min()
    ev=early[(pending[early])&(x.order_delivered_customer_date.iloc[early]<x.checkpoint_at.iloc[np.where((split=='test')&elig)[0]].min()).to_numpy()]
    # Calibrator cannot use future labels unavailable at initial test snapshot.
    test_min=x.checkpoint_at.iloc[test].min()
    calib=recent[(x.order_delivered_customer_date.iloc[recent]<test_min).to_numpy()]
    calib_pending=calib[pending[calib]]
    tepending=test[pending[test]]
    if min(len(train),len(ev),len(calib_pending),len(tepending))==0:raise ValueError('Invalid time split')
    return dict(train=train, early_eval=ev, calibration=calib, calibration_pending=calib_pending, test=test,test_pending=tepending,pending=pending,y=y,
                first_early_stop_checkpoint=str(tmin),first_calibration_checkpoint=str(cal_start),first_test_checkpoint=str(test_min))

def model_fit(name,Xtr,ytr,Xes,yes,cats):
    if name=='CatBoost':
        model=CatBoostClassifier(iterations=480,depth=6,learning_rate=.045,l2_leaf_reg=9,random_strength=1.,
                 loss_function='Logloss',eval_metric='PRAUC:type=Classic',random_seed=SEED,thread_count=4,
                 verbose=False,allow_writing_files=False,one_hot_max_size=10)
        model.fit(Xtr,ytr,cat_features=cats,eval_set=(Xes,yes),early_stopping_rounds=55,verbose=False)
        iters=model.best_iteration_+1 if model.best_iteration_ is not None else 480
        return model,int(iters)
    elif name=='LightGBM':
        # Category levels are aligned across train, early-stop, calibration and test.
        model=LGBMClassifier(n_estimators=560,learning_rate=.032,num_leaves=17,max_depth=-1,min_child_samples=85,
                        reg_lambda=8,colsample_bytree=.85,subsample=.9,subsample_freq=1,random_state=SEED,n_jobs=4,verbosity=-1)
        model.fit(Xtr,ytr,eval_set=[(Xes,yes)],eval_metric='average_precision',callbacks=[early_stopping(55,verbose=False),log_evaluation(0)])
        return model,int(model.best_iteration_)
    else:raise KeyError(name)

def setup_cats(X,cats,train_idx):
    X=X.copy()
    for c in cats:
        X[c]=pd.Categorical(X[c],categories=sorted(X.loc[train_idx,c].unique()))
    return X

def met(y,p,threshold):
    pr=(p>=threshold).astype(int)
    tn,fp,fn,tp=confusion_matrix(y,pr,labels=[0,1]).ravel()
    d=dict(n=int(len(y)),positives=int(y.sum()),precision=float(precision_score(y,pr,zero_division=0)),recall=float(recall_score(y,pr,zero_division=0)),f1=float(f1_score(y,pr,zero_division=0)),
           pr_auc=float(average_precision_score(y,p)),roc_auc=float(roc_auc_score(y,p)),tn=int(tn),fp=int(fp),fn=int(fn),tp=int(tp))
    return d

def select_thr(y,p):
    precision,recall,thresholds=precision_recall_curve(y,p)
    f1=np.divide(2*precision[:-1]*recall[:-1],precision[:-1]+recall[:-1],out=np.zeros(len(thresholds)),where=(precision[:-1]+recall[:-1])>0)
    acceptable=(precision[:-1]>=0.30)&(recall[:-1]>=0.70)
    feasible=bool(acceptable.any())
    if not feasible:acceptable=recall[:-1]>=.70
    if not acceptable.any():acceptable=np.ones(len(thresholds),dtype=bool)
    k=int(np.argmax(np.where(acceptable,f1,-1)))
    return float(thresholds[k]),feasible

def train_predict(X,x,split,day,model_name,variant,output):
    cats=list(X.select_dtypes(include=['object','string','category']).columns)
    m=get_masks(x,split)
    train,eval_idx,cal,calp,test,testp=[m[k] for k in ['train','early_eval','calibration','calibration_pending','test','test_pending']]
    if model_name=='LightGBM': X=setup_cats(X,cats,train)
    start=time.monotonic()
    model,niter=model_fit(model_name,X.iloc[train],m['y'][train],X.iloc[eval_idx],m['y'][eval_idx],cats)
    pcal_p=model.predict_proba(X.iloc[calp])[:,1]
    pte_p=model.predict_proba(X.iloc[testp])[:,1]
    pred_cal=np.zeros(len(cal),dtype=float); pred_test=np.zeros(len(test),dtype=float)
    pred_cal[np.isin(cal,calp)]=pcal_p;pred_test[np.isin(test,testp)]=pte_p
    thr,feasible=select_thr(m['y'][cal],pred_cal)
    score_cal=met(m['y'][cal],pred_cal,thr)
    score_test=met(m['y'][test],pred_test,thr)
    pd.DataFrame({'row_index':test,'late':m['y'][test],
                  'score':pred_test,'alert':(pred_test>=thr).astype('int8'),
                  'pending':m['pending'][test].astype('int8')}).to_csv(
                      output/f'Dminus{day}_{variant}_{model_name}_test_predictions.csv',index=False)
    if model_name=='LightGBM':
        imp=sorted(zip(X.columns,map(float,model.feature_importances_)),key=lambda t:-t[1])[:25]
        modelfile=f'Dminus{day}_{variant}_{model_name}.txt';model.booster_.save_model(str(output/modelfile))
    else:
        imp=sorted(zip(X.columns,map(float,model.feature_importances_)),key=lambda t:-t[1])[:25]
        modelfile=f'Dminus{day}_{variant}_{model_name}.cbm';model.save_model(str(output/modelfile))
    result=dict(day=day,variant=variant,model=model_name,threshold=thr,n_features=len(X.columns),n_estimators=niter,calibration_feasible=feasible,
           masks={k:len(m[k]) for k in ['train','early_eval','calibration','calibration_pending','test','test_pending']},calendar_boundaries={k:m[k] for k in ['first_early_stop_checkpoint','first_calibration_checkpoint','first_test_checkpoint']},
           calibration=score_cal,test=score_test,top_features=imp,model_file=modelfile,elapsed_sec=round(time.monotonic()-start,1))
    print(f'{day=} {variant=} {model_name=} valF1={score_cal["f1"]:.4f} testF1={score_test["f1"]:.4f} testRecall={score_test["recall"]:.4f} testPrec={score_test["precision"]:.4f} PR_AUC={score_test["pr_auc"]:.4f} time={result["elapsed_sec"]:.1f}s',flush=True)
    return result

def do_cv(X,x,split,out):
    """Fixed-parameter 3-fold forward-chain diagnosis on TRAIN only; no model selection on test."""
    cats=list(X.select_dtypes(include=['object','string','category']).columns)
    y=x.late.to_numpy(dtype=int);pending=x.pending.to_numpy()&x.eligible.to_numpy()
    alltrain=np.where((split=='train')&x.eligible.to_numpy())[0]
    cv=TimeSeriesSplit(n_splits=3)
    folds=[]
    for k,(tr0,va0) in enumerate(cv.split(alltrain),1):
        tr=alltrain[tr0]; va=alltrain[va0]
        tr=tr[pending[tr]];va=va[pending[va]]
        earliest=x.checkpoint_at.iloc[va].min()
        tr=tr[(x.order_delivered_customer_date.iloc[tr]<earliest).to_numpy()]
        tmp=setup_cats(X,cats,tr)
        if y[va].sum()==0 or y[tr].sum()==0:continue
        model=LGBMClassifier(n_estimators=170,learning_rate=.032,num_leaves=17,min_child_samples=85,reg_lambda=8,colsample_bytree=.85,
                        subsample=.9,subsample_freq=1,random_state=SEED,n_jobs=4,verbosity=-1)
        model.fit(tmp.iloc[tr],y[tr]);p=model.predict_proba(tmp.iloc[va])[:,1]
        f={'fold':k,'train_n':len(tr),'validation_n':len(va),'validation_late_n':int(y[va].sum()),'validation_late_pct':float(y[va].mean()*100),
        'average_precision_pending':float(average_precision_score(y[va],p)),'ROC_AUC_pending':float(roc_auc_score(y[va],p)),
        'training_labels_before':str(earliest),'train_max_purchase':str(x.order_purchase_timestamp.iloc[tr].max()),'validation_min_purchase':str(x.order_purchase_timestamp.iloc[va].min())}
        print('forward CV fold',k,'PR AUC',round(f['average_precision_pending'],4), 'n',len(va),flush=True)
        folds.append(f)
    (out/'rolling_cv_3folds.json').write_text(json.dumps(folds,indent=2,ensure_ascii=False))
    return folds

def main():
    pa=argparse.ArgumentParser();pa.add_argument('--zip',default='/mnt/data/logistic-brazilian-dataset.zip');pa.add_argument('--out',default='/mnt/data/VER04_experimento');pa.add_argument('--days',nargs='+',type=int,default=[2,1]);pa.add_argument('--external-csv',help='CSV generated by external_features.py, never raw target labels');args=pa.parse_args()
    if args.external_csv and len(args.days)!=1:
        raise ValueError('An external features CSV is specific to ONE checkpoint; execute --days 2 or --days 1 separately.')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    zhash=hashlib.sha256(Path(args.zip).read_bytes()).hexdigest()
    base=ref.build_base(args.zip).sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
    assert len(base)==96470, 'Unexpected source population; inspect rather than force benchmark.'
    split=np.full(len(base),'test',dtype=object); split[:64320]='train';split[64320:77867]='val'
    print('Split',[(a,int(np.sum(split==a)),int(base.loc[split==a,'late'].sum())) for a in ['train','val','test']],flush=True)
    res=[]
    for day in args.days:
        x=ref.build_checkpoint(base,days_before=day)
        Xb,cats=ref.feature_table(x)
        print('adding causal historical features:',day,flush=True)
        Xe=enhanced_features(x,Xb)
        print('Base features',Xb.shape[1], 'Extended features',Xe.shape[1],flush=True)
        variants=[('baseline',Xb),('extended',Xe)]
        if args.external_csv:
            raw=pd.read_csv(args.external_csv)
            assert 'order_id' in raw and raw.order_id.is_unique, 'External features require one row per order'
            allowed=('marketing_','road_km_2024','road_minutes_2024','seller_rain_','seller_temp_','customer_rain_','customer_temp_')
            forbidden=[c for c in raw.columns if c!='order_id' and not c.startswith(allowed)]
            if forbidden: raise ValueError('External CSV contains unapproved feature names: '+str(forbidden))
            join=x[['order_id']].merge(raw,on='order_id',how='left',validate='one_to_one')
            if not join.order_id.equals(x.order_id):raise ValueError('Misaligned external feature join')
            Xext=Xe.copy()
            for c in join.columns:
                if c=='order_id':continue
                Xext[c]=join[c].to_numpy()
                if Xext[c].dtype=='object':Xext[c]=Xext[c].fillna('__MISSING__').astype(str)
                else:Xext[c]=pd.to_numeric(Xext[c],errors='coerce').replace([np.inf,-np.inf],np.nan)
            variants.append(('extended_external',Xext))
            print('External-feature column count',len(Xext.columns),'nonmissing',int(Xext.drop(columns=list(Xe.columns)).notna().sum().sum()),flush=True)
        # Enumerate a small preregistered ablation grid, not a test-set hyperparameter hunt.
        for variant,X in variants:
            for model in ['LightGBM','CatBoost']:
                res.append(train_predict(X,x,split,day,model,variant,out))
        if day==2:
            print('Three forward time folds on train, extended features D-2:',flush=True)
            do_cv(Xe,x,split,out)
    # choose via calibration only, never by test scores
    winners={}
    for d in args.days:
        cands=[a for a in res if a['day']==d]
        feasible=[a for a in cands if a['calibration_feasible']]
        best=max(feasible if feasible else cands,key=lambda a:a['calibration']['f1'])
        winners[f'Dminus{d}']={'model':best['model'],'variant':best['variant'],'calibration_f1':best['calibration']['f1'],'test_f1_after_selection':best['test']['f1'],'model_file':best['model_file'],'threshold':best['threshold']}
    with (out/'results_ver04.json').open('w') as f:
        json.dump({'source_sha256':zhash,'split':{'train':64320,'validation':13547,'test':18603},'source_original_modified':False,
          'external_csv_provided':bool(args.external_csv),'external_data_provenance':'external_features.py with time-aware joins' if args.external_csv else 'none (all offline baseline + internally derived features)', 'test_not_independent_of_prior_ver03':True,
          'model_selection_by':'chronologically recent calibration subset only','experiments':res,'calibration_selected':winners,
          'versions':{'python':platform.python_version(),'catboost':catboost.__version__,'lightgbm':lightgbm.__version__,'pandas':pd.__version__,'scikit_learn':sklearn.__version__}},f,ensure_ascii=False,indent=2)
    print('WINNERS SELECTED BY VALIDATION',json.dumps(winners,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
