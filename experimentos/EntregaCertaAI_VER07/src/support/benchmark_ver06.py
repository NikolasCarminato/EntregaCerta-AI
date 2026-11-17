#!/usr/bin/env python3
"""EntregaCerta AI VER0.6: CatBoost + Optuna + temporal CV + real external data.

By DEFAULT the benchmark REFUSES to run unless official external files exist.
Use --internal-only-smoke solely to validate that code trains on ORIGINAL Olist,
not to claim any improvement from INMET, OSRM or Olist Marketing Funnel.

Examples:
 python benchmark_ver06.py --olist logistic-brazilian-dataset.zip --external-dir ./external_data --out ./results --days 2 1 --trials 20 --folds 3
 python benchmark_ver06.py --olist logistic-brazilian-dataset.zip --out ./smoke --internal-only-smoke --days 2 --trials 1 --folds 2

External requirements: official INMET 2016/2017/2018 ZIPs must first be processed
using prepare_inmet_weather.py; dist_brasil.zip + IBGE lookup; both Funnel CSVs.
"""
from __future__ import annotations
import argparse, hashlib, json, platform, sys, time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import optuna
import catboost
from catboost import CatBoostClassifier
from sklearn.metrics import average_precision_score, roc_auc_score
import olist_checkpoint as ck
import ver04_features as vf
import benchmark_ver05 as prev

SEED=42
optuna.logging.set_verbosity(optuna.logging.WARNING)
EXTERNAL_FILES={
  'inmet_2016':'inmet_2016.zip','inmet_2017':'inmet_2017.zip','inmet_2018':'inmet_2018.zip',
  'weather_daily':'weather_daily.csv',
  'roads_zenodo':'dist_brasil.zip','ibge_municipalities':'municipios_ibge.csv',
  'marketing_closed':'olist_closed_deals_dataset.csv',
  'marketing_leads':'olist_marketing_qualified_leads_dataset.csv'}

def fmt(x):
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(np.floating,)):return float(x)
    if isinstance(x,(pd.Timestamp,)):return x.isoformat()
    if isinstance(x,Path):return str(x)
    raise TypeError(type(x).__name__)

def save_json(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,default=fmt),encoding='utf-8')

def inventory(root):
    root=Path(root)
    out={}
    for k,fname in EXTERNAL_FILES.items():
        p=root/fname
        out[k]={'path':str(p),'present':p.is_file(),'bytes':p.stat().st_size if p.exists() else None,
                'sha256':prev.sha256(p) if p.is_file() else None}
    return out

def require_real_source_inventory(root,internal_only=False):
    inv=inventory(root)
    missing=[k for k,v in inv.items() if not v['present']]
    if missing and not internal_only:raise FileNotFoundError('External data missing: '+', '.join(missing)+'. Run download_sources_ver06.py and prepare_inmet_weather.py. NO RESULTS WILL BE CLAIMED.')
    if not internal_only and not all(v['bytes'] and v['bytes']>=100 for v in inv.values()):
        raise ValueError('External input empty/implausibly small')
    return inv

def verify_external_schema(root):
    root=Path(root)
    deals=pd.read_csv(root/'olist_closed_deals_dataset.csv')
    mql=pd.read_csv(root/'olist_marketing_qualified_leads_dataset.csv')
    if not {'mql_id','seller_id','won_date','business_segment'}.issubset(deals):raise ValueError('Unexpected closed-deals fields')
    if not {'mql_id','first_contact_date','origin'}.issubset(mql):raise ValueError('Unexpected MQL fields')
    if deals.seller_id.notna().sum()<200 or mql.mql_id.notna().sum()<1000:raise ValueError('Marketing source too short: possible partial or synthetic input')
    if mql.mql_id.duplicated().any():raise ValueError('Duplicate MQL identifiers')
    w=pd.read_csv(root/'weather_daily.csv',nrows=10)
    if not {'state','date','precipitation_mm','temperature_c','available_at'}.issubset(w):raise ValueError('INMET prepared weather schema invalid')
    m=pd.read_csv(root/'municipios_ibge.csv',nrows=10)
    if not {'ibge_code','city','state'}.issubset(m):raise ValueError('IBGE lookup schema invalid')
    import zipfile
    for year in [2016,2017,2018]:
        with zipfile.ZipFile(root/f'inmet_{year}.zip') as z:
            if not any(f.lower().endswith('.csv') for f in z.namelist()):raise ValueError(f'INMET {year} archive missing CSVs')
    with zipfile.ZipFile(root/'dist_brasil.zip') as z:
        if not any(f.lower().endswith('.csv') for f in z.namelist()):raise ValueError('Zenodo road matrix not a CSV ZIP')
    return {'closed_deals_rows':int(len(deals)),'mql_rows':int(len(mql))}

def get_groups(X,report):
    groups={'internal':X.columns.tolist()}
    cats={
      'marketing':[c for c in X if c.startswith('marketing_')],
      'weather':[c for c in X if c.startswith(('seller_rain_','seller_temp_','customer_rain_','customer_temp_'))],
      'roads':[c for c in X if c in ('road_km_2024','road_minutes_2024')]}
    # full columns are provided separately; this helper declares the ablation groups.
    return cats

def create_variants(Xbase,Xall):
    basecols=list(Xbase.columns)
    others={
     'marketing':[c for c in Xall if c.startswith('marketing_')],
     'roads':[c for c in Xall if c.startswith('road_')],
     'weather':[c for c in Xall if c.startswith(('seller_rain_','seller_temp_','customer_rain_','customer_temp_'))]
    }
    variants={'olist_calendario':Xbase}
    if list(Xall.columns)!=list(Xbase.columns):variants['completo']=Xall
    for key,cols in others.items():
        if cols:variants['sem_'+key]=Xall.drop(columns=cols)
    return variants, {key:cols for key,cols in others.items()}

def clean_categorical(X):
    X=X.copy()
    for c in X.select_dtypes(include=['object','string','category']).columns:
        X[c]=X[c].fillna('__MISSING__').astype(str)
    return X.replace([np.inf,-np.inf],np.nan)

def make_train_cal_test_indices(x,split):
    eligible=x.eligible.to_numpy()
    pending=x.pending.to_numpy() & eligible
    val=np.flatnonzero((split=='validation')&eligible)
    test=np.flatnonzero((split=='test')&eligible)
    firstval=x.checkpoint_at.iloc[val].min();firsttest=x.checkpoint_at.iloc[test].min()
    train=np.flatnonzero((split=='train')&pending)
    train=train[(x.checkpoint_at.iloc[train]<firstval).to_numpy() & (x.order_delivered_customer_date.iloc[train]<firstval).to_numpy()]
    cal=val[(x.checkpoint_at.iloc[val]<firsttest).to_numpy() & (x.order_delivered_customer_date.iloc[val]<firsttest).to_numpy()]
    calpending=cal[pending[cal]]
    testpending=test[pending[test]]
    if len(train)<300 or len(calpending)<50:raise ValueError('Insufficient mature labels in the selected time windows')
    return {'train':train,'cal':cal,'calpending':calpending,'test':test,'testpending':testpending,
            'pending':pending,'firstval':firstval,'firsttest':firsttest}

def predict_all_cohort(model,X,indices,pend):
    scores=np.zeros(len(indices),dtype=float)
    use=np.flatnonzero(pend[indices])
    if len(use):scores[use]=model.predict_proba(X.iloc[indices[use]])[:,1]
    return scores

def train_one(X,idx,y,params):
    model=prev.initialize_model(params)
    model.fit(X.iloc[idx],y[idx],cat_features=X.select_dtypes(include=['object','string','category']).columns.tolist())
    return model

def eval_variant(X,x,y,inds,params,name,out,compute_test=False):
    X=clean_categorical(X)
    mod=train_one(X,inds['train'],y,params)
    scores_cal=predict_all_cohort(mod,X,inds['cal'],inds['pending'])
    thr,details=prev.find_threshold(y[inds['cal']],scores_cal)
    cal_metrics=prev.compute_metrics(y[inds['cal']],scores_cal,thr)
    result={'name':name,'n_features':int(X.shape[1]),'threshold':thr,'threshold_logic':details,'calibration':cal_metrics}
    if compute_test:
        scores=predict_all_cohort(mod,X,inds['test'],inds['pending'])
        result['test']=prev.compute_metrics(y[inds['test']],scores,thr)
        scores_pending=mod.predict_proba(X.iloc[inds['testpending']])[:,1]
        result['test_pending_only']=prev.compute_metrics(y[inds['testpending']],scores_pending,thr)
        out.mkdir(parents=True,exist_ok=True)
        mod.save_model(str(out/f'{name}.cbm'))
        pd.DataFrame({'order_id':x.order_id.iloc[inds['test']].values,
            'checkpoint_at':x.checkpoint_at.iloc[inds['test']].astype(str).values,
            'target_late':y[inds['test']],'pending':inds['pending'][inds['test']].astype(int),
            'score':scores,'threshold':thr,'alert':(scores>=thr).astype(int)}).to_csv(out/f'{name}_test_predictions.csv',index=False)
        pd.DataFrame({'feature':X.columns,'importance':mod.get_feature_importance()}).sort_values('importance',ascending=False).to_csv(out/f'{name}_importance.csv',index=False)
        del scores_pending
    return result

def run_day(base,split,args,out,day):
    tic=time.monotonic()
    x=ck.build_checkpoint(base,days_before=day)
    Xorig,_=ck.feature_table(x)
    Xinternal=vf.enhanced_features(x,Xorig)
    if args.internal_only_smoke:
        Xall=Xinternal
        source_report={'sources_included':[],'sources_not_integrated':['INMET','Zenodo road','Olist Marketing Funnel'],'all_real_external_included':False}
    else:
        inputs=SimpleNamespace(marketing_closed=str(args.external_dir/'olist_closed_deals_dataset.csv'),
            marketing_leads=str(args.external_dir/'olist_marketing_qualified_leads_dataset.csv'),
            roads=str(args.external_dir/'dist_brasil.zip'),ibge=str(args.external_dir/'municipios_ibge.csv'),
            weather=str(args.external_dir/'weather_daily.csv'),allow_2024_road_proxy=True)
        Xall,source_report=prev.add_external_features(x,Xinternal,inputs)
        source_report['all_real_external_included']=True
        actual=source_report['joins']
        # A source loaded but not mapping to Olist orders is an input without meaningful enrichment.
        if actual['marketing']['covered_orders_with_prior_win']<1:raise ValueError('Zero prior seller deals matched. Check Olist edition and seller_id.')
        if actual['roads']['matched_orders']<1:raise ValueError('Zero road pairs matched. Check IBGE municipality mapping.')
        if actual['weather']['seller_weather_coverage_pct']<1:raise ValueError('Zero published weather matches. Check INMET temporal coverage.')
    Xall=clean_categorical(Xall)
    Xinternal=clean_categorical(Xinternal)
    variants,groups=create_variants(Xinternal,Xall)
    indices=make_train_cal_test_indices(x,split)
    y=x.late.to_numpy(dtype=int)
    folds=prev.fold_indices(x,split,n_splits=args.folds)
    fold_notes=[f[2] for f in folds]
    print(f'\nD-{day}: n_features={Xall.shape[1]}, train_pending={len(indices["train"])}, calibrated={len(indices["cal"])}, test={len(indices["test"])}',flush=True)
    trial_results=[]
    def objective(trial):
        params=prev.cb_params(trial)
        fold_ap=[]
        for tr,val,_ in folds:
            pval=val[indices['pending'][val]]
            if len(pval)<10 or len(np.unique(y[pval]))!=2:raise ValueError('Few late + non-late pending validation points')
            mod=train_one(Xall,tr,y,params)
            score=mod.predict_proba(Xall.iloc[pval])[:,1]
            fold_ap.append(float(average_precision_score(y[pval],score)))
        trial_results.append({'trial':trial.number,'params':params,'fold_pr_auc_pending':fold_ap,'mean_pr_auc_pending':float(np.mean(fold_ap))})
        print(f'D-{day}: Optuna trial {trial.number+1}/{args.trials}: CV PR-AUC={np.mean(fold_ap):.5f}',flush=True)
        return float(np.mean(fold_ap))
    study=optuna.create_study(direction='maximize',sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective,n_trials=args.trials,n_jobs=1,show_progress_bar=False)
    params=study.best_params
    print(f'D-{day}: best CV PR-AUC {study.best_value:.5f}',flush=True)
    results=[]
    for name,X in variants.items():
        r=eval_variant(X,x,y,indices,params,name,out/f'Dminus{day}',compute_test=False)
        results.append(r)
        print(f'D-{day} ablation {name}: val F1={r["calibration"]["f1"]:.4f}',flush=True)
    # DO NOT select by test. Winner is determined by validation F1 exclusively.
    def validrank(r):
        met=r['calibration']
        return (met['recall']>=.70 and met['precision']>=.30,met['f1'],met['recall'])
    winner=max(results,key=validrank)['name']
    # Report all variants on the same test only as fixed, PRESELECTED ablations; 
    # do not change winner after inspecting these scores.
    completed=[]
    for r in results:
        rr=eval_variant(variants[r['name']],x,y,indices,params,r['name'],out/f'Dminus{day}',compute_test=True)
        completed.append(rr)
    chosen=next(r for r in completed if r['name']==winner)
    rule_score=indices['pending'][indices['test']].astype(float)
    rule_test=prev.compute_metrics(y[indices['test']],rule_score,.5)
    save_json(out/f'Dminus{day}'/'study.json',{'best_params':params,'best_cv_pr_auc':study.best_value,'trials':trial_results,'folds':fold_notes,'groups':groups,
                 'winner_selected_on_validation_only':winner,'all_variant_test_analyses_are_exploratory':True})
    ret={'checkpoint':f'D-{day}','n_features_full':len(Xall.columns),'n_train_pending':len(indices['train']),
         'n_validation':len(indices['cal']),'n_test':len(indices['test']),'positives_in_test':int(y[indices['test']].sum()),
         'time_boundaries':{'validation_start_checkpoint':str(indices['firstval']),'test_start_checkpoint':str(indices['firsttest'])},
         'winner_selected_by_validation':winner,'test_winner':chosen['test'],'rule_pending_test':rule_test,
         'variants':completed,'external_source_report':source_report,'seconds':round(time.monotonic()-tic,1)}
    save_json(out/f'Dminus{day}'/'results.json',ret)
    return ret

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--olist',required=True,type=Path)
    p.add_argument('--external-dir',type=Path,default=Path('external_data'))
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--days',nargs='+',type=int,default=[2,1],choices=[1,2,3])
    p.add_argument('--trials',type=int,default=12)
    p.add_argument('--folds',type=int,default=3)
    p.add_argument('--internal-only-smoke',action='store_true',help='DIAGNOSTIC ONLY. DO NOT attribute metrics to the three external datasets.')
    p.add_argument('--audit-only',action='store_true',help='Audit external files without training or reading full Olist zip.')
    args=p.parse_args()
    if args.folds<2 or args.trials<1:p.error('Require at least 2 temporal folds and 1 trial')
    args.out.mkdir(parents=True,exist_ok=True)
    inv=inventory(args.external_dir)
    meta={'experiment':'EntregaCerta AI VER0.6','status':'source_audit', 'source_inventory':inv,
          'sources_included':[],'original_olist_sha256':prev.sha256(args.olist),
          'originals_are_read_only':True,'external_data_synthetic':False,
          'spatial_caveat':'Road network May 2024; training Olist 2016–2018',
          'inmet_availability_caveat':'7-day lag is a conservative assumption, not verified publication time',
          'independent_out_of_time_test':False}
    missing=[k for k in inv if not inv[k]['present']]
    meta['missing_external']=missing
    save_json(args.out/'run_manifest.json',meta)
    if args.audit_only:
        if not missing:meta['source_schema_report']=verify_external_schema(args.external_dir)
        meta['status']='all_sources_present' if not missing else 'blocked_missing_sources'
        save_json(args.out/'run_manifest.json',meta)
        print(json.dumps({'status':meta['status'],'missing':missing},ensure_ascii=False))
        return 0 if not missing else 2
    if missing and not args.internal_only_smoke:
        print('Not training: real external data missing: '+', '.join(missing),file=sys.stderr)
        meta['status']='blocked_missing_sources';save_json(args.out/'run_manifest.json',meta)
        return 2
    if not args.internal_only_smoke:
        meta['source_schema_report']=verify_external_schema(args.external_dir)
        meta['sources_included']=['Olist Marketing Funnel','INMET hourly 2016-2018 aggregated state day with 7d lag','Zenodo municipality OSRM May 2024']
    else:meta['status']='smoke_only_no_real_external_features'
    meta['training_is_real_olist']=True
    before=prev.sha256(args.olist)
    base=ck.build_base(args.olist).sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
    split=prev.make_split(len(base))
    meta['split_counts']={k:{'n':int((split==k).sum()),'late':int(base.late[split==k].sum())} for k in ['train','validation','test']}
    meta['version']='VER0.6-exploratory' if not args.internal_only_smoke else 'VER0.6-internal-only-smoke'
    meta['status']='training'
    save_json(args.out/'run_manifest.json',meta)
    results=[]
    for day in args.days:
        results.append(run_day(base,split,args,args.out,day))
        save_json(args.out/'results_all.json',results)
    meta['status']='finished' if not args.internal_only_smoke else 'finished_internal_smoke_no_external'
    meta['after_sha256']=prev.sha256(args.olist)
    meta['original_unchanged']=before==meta['after_sha256']
    meta['test_used_in_previous_versions']=True
    save_json(args.out/'run_manifest.json',meta)
    if not meta['original_unchanged']:raise RuntimeError('Original Olist ZIP has changed unexpectedly!')
    return 0

if __name__=='__main__':sys.exit(main())
