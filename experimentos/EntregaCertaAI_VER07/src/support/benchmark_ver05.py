#!/usr/bin/env python3
"""EntregaCerta AI VER0.5 — checkpoint CatBoost + Optuna + forward chaining CV.

ALL figures come from actual Olist CSVs and optional explicitly provided public files.
Inaccessible external datasets are NOT silently simulated. Source ZIP never modified.

Run:
 python benchmark_ver05.py --olist /path/logistic-brazilian-dataset.zip --out /path/VER05_experimento --trials 12 --days 2 1 3
Add optional, independently downloaded data with flags shown in --help.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time, platform
from pathlib import Path
from dataclasses import asdict
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score, average_precision_score, confusion_matrix, precision_recall_curve
from catboost import CatBoostClassifier
import catboost, sklearn, optuna
import olist_checkpoint as ck
import ver04_features as vf
import external_features as ef

SEED = 42
optuna.logging.set_verbosity(optuna.logging.WARNING)

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for ch in iter(lambda:f.read(1024*1024),b''):h.update(ch)
    return h.hexdigest()

def json_dump(p, data):
    Path(p).write_text(json.dumps(data,indent=2,ensure_ascii=False,default=lambda x:float(x) if isinstance(x,np.floating) else int(x) if isinstance(x,np.integer) else str(x)),encoding='utf-8')

def make_split(n):
    if n!=96470: raise ValueError(f'Dataset inesperado: {n} entregas. Não forçar split antigo sem auditoria.')
    s=np.full(n,'test',dtype=object)
    s[:64320]='train';s[64320:77867]='validation'
    return s

def add_external_features(x, X, args):
    """Strictly join REAL CSV sources. Missing source means no corresponding features, ever."""
    report={'sources_included':[], 'sources_not_integrated':[], 'joins':{}, 'external_data_are_synthetic':False}
    extras=[]
    if args.marketing_closed:
        f,r=ef.add_marketing(x,args.marketing_closed)
        report['sources_included'].append('Olist Marketing Funnel — closed deals')
        report['joins']['marketing']=r
        if args.marketing_leads:
            # Leads from Olist marketing: only origin before the purchase is permissible.
            deals=pd.read_csv(args.marketing_closed,usecols=['seller_id','mql_id','won_date'])
            leads=pd.read_csv(args.marketing_leads)
            for c in ['mql_id','first_contact_date']:
                if c not in leads: raise ValueError(f'MQL missing {c}')
            cols=[c for c in ['origin','landing_page_id'] if c in leads]
            leads=leads[['mql_id','first_contact_date']+cols].drop_duplicates('mql_id')
            leads['first_contact_date']=pd.to_datetime(leads.first_contact_date,errors='coerce')
            deals['won_date']=pd.to_datetime(deals.won_date,errors='coerce')
            deals=deals.merge(leads,on='mql_id',how='left',validate='many_to_one')
            deals=deals[(deals.first_contact_date.isna())|(deals.first_contact_date<=deals.won_date)]
            deals=deals.sort_values('won_date')
            left=x[['order_id','seller_id','order_purchase_timestamp']].copy();left['seq']=np.arange(len(left))
            left=left.sort_values('order_purchase_timestamp')
            joined=pd.merge_asof(left,deals,left_on='order_purchase_timestamp',right_on='won_date',by='seller_id',direction='backward')
            joined=joined.sort_values('seq')
            for c in cols:
                f[f'marketing_mql_{c}']=joined[c].to_numpy()
            f['marketing_mql_days_since_contact']=(joined.order_purchase_timestamp-joined.first_contact_date).dt.total_seconds().div(86400).to_numpy()
            report['sources_included'].append('Olist Marketing Funnel — MQL')
            report['joins']['mql_prior_coverage_pct']=round(100*joined.first_contact_date.notna().mean(),2)
        extras.append(f)
    elif args.marketing_leads:
        raise ValueError('Marketing leads depend on closed deals seller_id join. Provide --marketing-closed too.')
    else: report['sources_not_integrated'].append('marketing_funnel')
    if args.roads or args.ibge:
        if not(args.roads and args.ibge):raise ValueError('Roads require --roads and --ibge simultaneously.')
        if not args.allow_2024_road_proxy:raise ValueError('OSRM 2024 road network differs from Olist 2016–2018. Pass --allow-2024-road-proxy for an explicit exploratory proxy.')
        f,r=ef.add_roads(x,args.roads,args.ibge)
        extras.append(f);report['sources_included'].append('Road municipal OSRM May 2024 — retrospective proxy');report['joins']['roads']=r
    else:report['sources_not_integrated'].append('road_distances_2024')
    if args.weather:
        f,r=ef.add_weather(x,args.weather)
        extras.append(f);report['sources_included'].append('Historic weather daily with as-of publication lag');report['joins']['weather']=r
    else:report['sources_not_integrated'].append('historic_weather')
    out=X.copy()
    for src in extras:
        if src.order_id.duplicated().any():raise ValueError('Duplicate external order_id')
        merged=x[['order_id']].merge(src,on='order_id',how='left',validate='one_to_one')
        assert merged.order_id.equals(x.order_id)
        for c in merged.columns:
            if c=='order_id':continue
            if c in out.columns:raise ValueError(f'Colliding external column {c}')
            if merged[c].dtype=='object' or pd.api.types.is_string_dtype(merged[c]):out[c]=merged[c].fillna('__MISSING__').astype(str).to_numpy()
            else:out[c]=pd.to_numeric(merged[c],errors='coerce').replace([np.inf,-np.inf],np.nan).to_numpy()
    report['feature_count_after_join']=len(out.columns)
    report['external_feature_names']=[c for c in out.columns if c not in X.columns]
    return out,report

def compute_metrics(y,score,threshold):
    pred=(score>=threshold).astype(int)
    tn,fp,fn,tp=confusion_matrix(y,pred,labels=[0,1]).ravel()
    return {'n':int(len(y)),'positive':int(y.sum()),'precision':float(precision_score(y,pred,zero_division=0)),
     'recall':float(recall_score(y,pred,zero_division=0)),'f1':float(f1_score(y,pred,zero_division=0)),
     'pr_auc':float(average_precision_score(y,score)) if len(np.unique(y))>1 else None,
     'roc_auc':float(roc_auc_score(y,score)) if len(np.unique(y))>1 else None,
     'tp':int(tp),'fp':int(fp),'tn':int(tn),'fn':int(fn)}

def find_threshold(y,scores,min_recall=.70,min_precision=.30):
    prec,rec,t=precision_recall_curve(y,scores)
    p,r=prec[:-1],rec[:-1]
    f=np.divide(2*p*r,p+r,out=np.zeros_like(p),where=(p+r)>0)
    both=(p>=min_precision)&(r>=min_recall)
    keep=both if both.any() else (r>=min_recall)
    if not keep.any():keep=np.ones(len(t),dtype=bool)
    idx=np.argmax(np.where(keep,f,-1))
    return float(t[idx]),{'constraints_feasible':bool(both.any()),'chosen_by':'max F1 under precision >= 0.30 and recall >=0.70, fallback recall then max F1','calibration_threshold_count':int(len(t))}

def cb_params(trial):
    kw={'iterations':trial.suggest_int('iterations',160,420,step=65),
      'depth':trial.suggest_int('depth',4,8),
      'learning_rate':trial.suggest_float('learning_rate',.018,.105,log=True),
      'l2_leaf_reg':trial.suggest_float('l2_leaf_reg',3,25,log=True),
      'random_strength':trial.suggest_float('random_strength',.2,2.5),
      'rsm':trial.suggest_float('rsm',.65,1.0),
      'auto_class_weights':trial.suggest_categorical('auto_class_weights',['Balanced','SqrtBalanced','None'])}
    return kw

def initialize_model(params):
    kwargs=dict(params)
    if kwargs.get('auto_class_weights')=='None':kwargs['auto_class_weights']=None
    return CatBoostClassifier(**kwargs,loss_function='Logloss',random_seed=SEED,thread_count=3,
      allow_writing_files=False,verbose=False,one_hot_max_size=10)

def fold_indices(x,splits,n_splits=3):
    """Forward chaining on purchase-time train cohort. Strict label availability purge."""
    order=np.flatnonzero((splits=='train')&x.eligible.to_numpy())
    pend=x.pending.to_numpy()
    cv=TimeSeriesSplit(n_splits=n_splits)
    folds=[]
    for k,(a,b) in enumerate(cv.split(order),1):
        va=order[b]
        t0=x.checkpoint_at.iloc[va].min()
        tr=order[a]
        tr=tr[pend[tr]]
        tr=tr[(x.checkpoint_at.iloc[tr]<t0).to_numpy() & (x.order_delivered_customer_date.iloc[tr]<t0).to_numpy()]
        positives_tr=int(x.late.iloc[tr].sum());positives_val=int(x.late.iloc[va].sum())
        if len(tr)<70 or positives_tr<5 or positives_val<5:
            raise RuntimeError(f'Fold {k}: insuficiente após purge temporal: ntrain={len(tr)} positives={positives_tr}, nval={len(va)} positives_val={positives_val}')
        assert (x.order_delivered_customer_date.iloc[tr]<t0).all()
        assert (x.checkpoint_at.iloc[tr]<t0).all()
        folds.append((tr,va,{'fold':k,'train_pending_n':int(len(tr)),'validation_all_n':int(len(va)),
                      'validation_pending_n':int(pend[va].sum()),'validation_late_n':positives_val,
                      'validation_first_checkpoint':str(t0),'train_last_completion':str(x.order_delivered_customer_date.iloc[tr].max())}))
    return folds

def run_one_checkpoint(base,splits,day,args,out):
    tic=time.monotonic()
    print(f'[D-{day}] Building snapshots + historical features...',flush=True)
    x=ck.build_checkpoint(base,days_before=day)
    raw,categories=ck.feature_table(x)
    ext=vf.enhanced_features(x,raw)
    X,external_report=add_external_features(x,ext,args)
    categories=list(X.select_dtypes(include=['object','string','category']).columns)
    for c in categories:X[c]=X[c].fillna('__MISSING__').astype(str)
    X=X.replace([np.inf,-np.inf],np.nan)
    eligible=x.eligible.to_numpy()
    pending=x.pending.to_numpy()&eligible
    y=x.late.to_numpy(dtype=int)
    idx_test=np.flatnonzero((splits=='test')&eligible)
    idx_val=np.flatnonzero((splits=='validation')&eligible)
    first_val=x.checkpoint_at.iloc[idx_val].min();first_test=x.checkpoint_at.iloc[idx_test].min()
    idx_train=np.flatnonzero((splits=='train')&pending)
    idx_train=idx_train[(x.checkpoint_at.iloc[idx_train]<first_val).to_numpy() & (x.order_delivered_customer_date.iloc[idx_train]<first_val).to_numpy()]
    idx_cal=idx_val[(x.order_delivered_customer_date.iloc[idx_val]<first_test).to_numpy() & (x.checkpoint_at.iloc[idx_val]<first_test).to_numpy()]
    # Prior-model definition available only for actually delivered by test start.
    idx_cal_pending=idx_cal[pending[idx_cal]]
    idx_test_pending=idx_test[pending[idx_test]]
    assert len(idx_train)>500 and len(idx_cal_pending)>100 and len(idx_test_pending)>300
    print(f'[D-{day}] features {X.shape[1]}, train_pending={len(idx_train)}, calibration={len(idx_cal)} ({len(idx_cal_pending)} pending), test={len(idx_test)} ({len(idx_test_pending)} pending)',flush=True)
    folds=fold_indices(x,splits,args.folds)
    fold_info=[f[2] for f in folds]
    folder=out/f'Dminus{day}';folder.mkdir(parents=True,exist_ok=True)
    json_dump(folder/'cv_folds.json',fold_info)
    json_dump(folder/'external_sources.json',external_report)
    trial_runs=[]
    def objective(trial):
        params=cb_params(trial)
        aps=[];aucs=[]
        for tr,va,fi in folds:
            model=initialize_model(params)
            model.fit(X.iloc[tr],y[tr],cat_features=categories)
            valp=va[pending[va]]
            if len(valp)>0 and len(np.unique(y[valp]))>1:
                scores=model.predict_proba(X.iloc[valp])[:,1]
                aps.append(float(average_precision_score(y[valp],scores)))
                aucs.append(float(roc_auc_score(y[valp],scores)))
            else:aps.append(0.0);aucs.append(0.5)
        mean=float(np.mean(aps))
        trial.set_user_attr('fold_pr_auc_pending',aps)
        trial.set_user_attr('fold_roc_auc_pending',aucs)
        trial_runs.append({'trial':int(trial.number),'cv_mean_pr_auc_pending':mean,'cv_pr_auc_each_fold':aps,'params':params})
        print(f'[D-{day}] Optuna trial {trial.number+1}/{args.trials} PR-AUC(pending)={mean:.4f}',flush=True)
        return mean
    study=optuna.create_study(direction='maximize',sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective,n_trials=args.trials,n_jobs=1,show_progress_bar=False)
    best=study.best_params
    model=initialize_model(best)
    model.fit(X.iloc[idx_train],y[idx_train],cat_features=categories)
    prob_cal=np.zeros(len(idx_cal),dtype=float);prob_test=np.zeros(len(idx_test),dtype=float)
    # Zero score means already delivered at the checkpoint (known to be on time for late target).
    prob_cal[np.isin(idx_cal,idx_cal_pending)]=model.predict_proba(X.iloc[idx_cal_pending])[:,1]
    prob_test[np.isin(idx_test,idx_test_pending)]=model.predict_proba(X.iloc[idx_test_pending])[:,1]
    threshold,thr_details=find_threshold(y[idx_cal],prob_cal)
    met_cal=compute_metrics(y[idx_cal],prob_cal,threshold)
    met_test=compute_metrics(y[idx_test],prob_test,threshold)
    pending_rule=x.pending.iloc[idx_test].astype(int).to_numpy()
    met_rule=compute_metrics(y[idx_test],pending_rule.astype(float),.5)
    # Important operational precision: conditional on still-pending orders.
    met_test_pending=compute_metrics(y[idx_test_pending],model.predict_proba(X.iloc[idx_test_pending])[:,1],threshold)
    (folder/'catboost_ver05.cbm').parent.mkdir(parents=True,exist_ok=True)
    model.save_model(str(folder/'catboost_ver05.cbm'))
    feat_imp=pd.DataFrame({'feature':X.columns,'importance':model.get_feature_importance()}).sort_values('importance',ascending=False)
    feat_imp.to_csv(folder/'feature_importance.csv',index=False)
    preds=pd.DataFrame({'order_id':x.order_id.iloc[idx_test].to_numpy(),'checkpoint_at':x.checkpoint_at.iloc[idx_test].astype(str).to_numpy(),
        'true_late':y[idx_test],'pending':pending[idx_test].astype(int),'risk_score':prob_test,'threshold':threshold,
        'alert':(prob_test>=threshold).astype(int)})
    preds.to_csv(folder/'test_predictions.csv',index=False)
    pd.DataFrame(trial_runs).assign(params=lambda z:z.params.map(json.dumps)).to_csv(folder/'optuna_trials.csv',index=False)
    json_dump(folder/'optuna_trials_detailed.json',trial_runs)
    json_dump(folder/'selected_model.json',{'params':best,'cv_mean_pr_auc_pending':study.best_value,
      'mean_cv_roc_auc_pending':float(np.mean(study.best_trial.user_attrs['fold_roc_auc_pending'])),
      'threshold':threshold,**thr_details,'calibration':met_cal,'test':met_test,
      'test_pending_only':met_test_pending,'rule_test':met_rule,'cv':fold_info,
      'count':{'train_pending':len(idx_train),'calibration_all':len(idx_cal),'calibration_pending':len(idx_cal_pending),'test_all':len(idx_test),'test_pending':len(idx_test_pending)},
      'first_val_checkpoint':str(first_val),'first_test_checkpoint':str(first_test),
      'important_features':feat_imp.head(25).to_dict('records'), 'n_features':len(X.columns),
      'external':external_report,'training_duration_seconds':round(time.monotonic()-tic,1)})
    print(f'[D-{day}] RESULT precision={met_test["precision"]:.4f}, recall={met_test["recall"]:.4f}, F1={met_test["f1"]:.4f}; ruleF1={met_rule["f1"]:.4f}, PR-AUC test={met_test["pr_auc"]:.4f}; CV pending PRAUC={study.best_value:.4f}',flush=True)
    return {'day':day,'features_count':X.shape[1], 'params':best, 'cv_mean_pr_auc_pending':study.best_value,'threshold':threshold,
      'validation':met_cal,'test':met_test,'test_pending_only':met_test_pending,'pending_rule':met_rule,
      'external_data_sources_included':external_report['sources_included'],
      'external_data_sources_missing':external_report['sources_not_integrated'],'elapsed_sec':round(time.monotonic()-tic,1)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--olist',required=True,help='Original ZIP Olist (read-only)')
    p.add_argument('--out',required=True,help='Output directory separate from originals')
    p.add_argument('--days',nargs='+',type=int,default=[2,1,3])
    p.add_argument('--trials',type=int,default=12)
    p.add_argument('--folds',type=int,default=3)
    p.add_argument('--marketing-closed',help='REAL Olist closed-deals CSV')
    p.add_argument('--marketing-leads',help='REAL Olist MQL leads CSV')
    p.add_argument('--roads',help='REAL dist_brasil.zip CSV from Zenodo')
    p.add_argument('--ibge',help='REAL municipality lookup columns: ibge_code,city,state')
    p.add_argument('--weather',help='REAL weather_daily.csv columns: state,date,precipitation_mm,temperature_c,available_at(optional)')
    p.add_argument('--allow-2024-road-proxy',action='store_true',help='Explicit historical mismatch approval: road network is May 2024, orders 2016–2018.')
    p.add_argument('--strict-all-external',action='store_true',help='Refuse to run unless Marketing leads + closed, roads + ibge, weather all supplied')
    args=p.parse_args()
    if args.strict_all_external and not all([args.marketing_closed,args.marketing_leads,args.roads,args.ibge,args.weather]):
        p.error('Strict mode: all actual external datasets must be supplied; cannot claim they were used otherwise.')
    if args.trials<1 or args.folds<2: p.error('trials>=1 and folds>=2 required')
    if any(d not in (1,2,3) for d in args.days):p.error('Only D-3, D-2, D-1 supported')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    before=sha256(args.olist)
    base=ck.build_base(args.olist).sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
    splits=make_split(len(base))
    meta={'version':'VER0.5-exploratory','seed':SEED,'zip_sha256_before':before,'python':platform.python_version(),
       'libraries':{'catboost':catboost.__version__,'optuna':optuna.__version__,'sklearn':sklearn.__version__,'pandas':pd.__version__},
       'n_delivered':len(base),'n_late':int(base.late.sum()),
       'split_counts':{s:{'n':int((splits==s).sum()),'late':int(base.late[splits==s].sum())} for s in ['train','validation','test']},
       'n_trials_per_checkpoint':args.trials,'kfold_temporal':args.folds,
       'test_is_independent_of_prior_versions':False,'external_sources_requested':{'weather':bool(args.weather),'roads':bool(args.roads),'marketing_closed':bool(args.marketing_closed),'marketing_leads':bool(args.marketing_leads)},
       'retrospective_target_scope':'Olist delivered orders only; excludes undelivered/canceled','original_zip_unchanged':None}
    json_dump(out/'experiment_metadata.json',meta)
    results=[]
    for d in args.days:
        results.append(run_one_checkpoint(base,splits,d,args,out))
        json_dump(out/'results_ver05.json',results)
    after=sha256(args.olist);meta['zip_sha256_after']=after;meta['original_zip_unchanged']=bool(before==after)
    json_dump(out/'experiment_metadata.json',meta)
    if before!=after:raise RuntimeError('Original ZIP hash changed!')
    print('Finished. Original ZIP unmodified:',before==after,flush=True)

if __name__=='__main__':main()
