#!/usr/bin/env python3
"""EntregaCerta AI VER0.7 — exploratory, chronological, read-only.

Models evaluated solely with past-matured label training; models and alert policies
selected on a temporally later *validation* block. The historical test was used
in previous versions, so this is NOT an independent final holdout.

Inputs: Olist original ZIP, VER0.6 prepared weather_daily.csv, dist_brasil.zip,
municipios_ibge.csv (partial 83 cities), and Marketing Funnel closed deals/MQL.
"""
from __future__ import annotations
import argparse,sys,time,json,platform,hashlib,zipfile
from pathlib import Path
from types import SimpleNamespace
import numpy as np,pandas as pd
from catboost import CatBoostClassifier
from lightgbm import LGBMClassifier
from sklearn.metrics import precision_score,recall_score,f1_score,average_precision_score,roc_auc_score,confusion_matrix,precision_recall_curve
SRC0=Path(__file__).resolve().parent/'support'
if not SRC0.exists(): SRC0=Path('/mnt/data/VER06_reais/src')
sys.path.insert(0,str(SRC0))
import olist_checkpoint as ck, ver04_features as vf, benchmark_ver05 as b05, external_features as ef, benchmark_ver06 as b06
SEED=42

def sha256(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def write_json(path,obj):
 Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=lambda o:float(o) if isinstance(o,(np.floating,)) else int(o) if isinstance(o,(np.integer,)) else str(o)),encoding='utf8')

def history_mean(x,key,measure,name):
 """Historical completed orders strictly before checkpoint, not outcome leakage."""
 events=x[[key,'order_delivered_customer_date',measure]].copy().dropna(subset=[key,'order_delivered_customer_date',measure])
 events=events[events[measure].between(-120,180)]
 events=events.groupby([key,'order_delivered_customer_date'],sort=False,observed=True)[measure].agg(['size','sum']).reset_index()
 events=events.sort_values('order_delivered_customer_date',kind='mergesort')
 events['cum_n']=events.groupby(key,sort=False)['size'].cumsum()
 events['cum_sum']=events.groupby(key,sort=False)['sum'].cumsum()
 left=x[[key,'checkpoint_at']].copy();left[key]=left[key].fillna('__MISSING__');left['_row_id']=np.arange(len(left))
 left=left.sort_values('checkpoint_at',kind='mergesort')
 z=pd.merge_asof(left,events[[key,'order_delivered_customer_date','cum_n','cum_sum']],left_on='checkpoint_at',right_on='order_delivered_customer_date',by=key,direction='backward',allow_exact_matches=False)
 z=z.sort_values('_row_id')
 assert (z.loc[z.order_delivered_customer_date.notna(),'order_delivered_customer_date'] < z.loc[z.order_delivered_customer_date.notna(),'checkpoint_at']).all()
 ratio=(z.cum_sum/z.cum_n.where(z.cum_n>=4,np.nan)).to_numpy().astype('float32')
 return {name:ratio, name+'_n':z.cum_n.fillna(0).to_numpy().astype('float32')}

def add_practical_features(x,X):
 z=X.copy()
 x=x.copy()
 x['real_delay_days']=(x.order_delivered_customer_date.dt.normalize()-x.order_estimated_delivery_date.dt.normalize()).dt.days.astype(float)
 x['real_transit_days']=(x.order_delivered_customer_date-x.order_delivered_carrier_date).dt.total_seconds()/86400
 x.loc[x.real_transit_days<0,'real_transit_days']=np.nan
 for key,measure,label in [('seller_id','real_delay_days','seller_mean_delay_past'),('route_state','real_delay_days','route_mean_delay_past'),('seller_id','real_transit_days','seller_mean_transit_past'),('route_state','real_transit_days','route_mean_transit_past')]:
  for c,v in history_mean(x,key,measure,label).items():z[c]=v
 z['road_detour_ratio_2024']=(z.road_km_2024/x.haversine_km.clip(lower=3)).to_numpy() if 'road_km_2024' in z else np.nan
 z['road_expected_hours_2024']=(z.road_minutes_2024/60).to_numpy() if 'road_minutes_2024' in z else np.nan
 z['road_speed_kmh_2024']=(z.road_km_2024/(z.road_minutes_2024.clip(lower=10)/60)).to_numpy() if 'road_km_2024' in z else np.nan
 z['road_km_per_promised_day_2024']=(z.road_km_2024/x.promised_days.clip(lower=1)).to_numpy() if 'road_km_2024' in z else np.nan
 z['shipment_after_limit_known_days']=((x.order_delivered_carrier_date-x.shipping_limit).dt.total_seconds()/86400).where(x.carrier_known).to_numpy()
 z['customer_seller_price_per_km']=(x.subtotal/x.haversine_km.clip(lower=5)).to_numpy()
 z['seller_avg_transit_minus_elapsed']= (z['seller_mean_transit_past']-x.in_transit_days).to_numpy()
 z['route_avg_transit_minus_elapsed']= (z['route_mean_transit_past']-x.in_transit_days).to_numpy()
 # explicit status must be captured as-of checkpoint, NEVER a future delivery time.
 for col in z.columns:
  if col not in z.select_dtypes(include=['object','string','category']).columns:
   z[col]=pd.to_numeric(z[col],errors='coerce').replace([np.inf,-np.inf],np.nan)
 return b06.clean_categorical(z)

def metric(y,p):
 tn,fp,fn,tp=confusion_matrix(y,p,labels=[0,1]).ravel()
 out={'n':int(len(y)),'positives':int(np.sum(y)), 'precision':float(precision_score(y,p,zero_division=0)), 'recall':float(recall_score(y,p,zero_division=0)), 'f1':float(f1_score(y,p,zero_division=0)),'tp':int(tp),'fp':int(fp),'fn':int(fn),'tn':int(tn),'alerts':int(np.sum(p))}
 return out

def score_metrics(y,p,sel):
 out=metric(y,sel)
 if len(np.unique(y))==2:
  out['pr_auc_all']=float(average_precision_score(y,p))
 return out

def top_ratio_policy(scores,pending,dates,ratio):
 """At each checkpoint day, alert top k scores among pending orders ON THAT DAY."""
 out=np.zeros(len(scores),dtype=np.int8)
 frame=pd.DataFrame({'loc':np.arange(len(scores)), 'score':scores, 'pending':pending, 'date':pd.DatetimeIndex(dates).normalize()})
 # stable tiebreaker preserves deterministic alert volume; no labels or future days are used
 frame=frame[frame.pending]
 for _,grp in frame.groupby('date',sort=False):
  n=max(1,int(np.ceil(len(grp)*ratio)))
  best=grp.sort_values(['score','loc'],ascending=[False,True],kind='mergesort').iloc[:n]
  out[best['loc'].to_numpy(dtype=int)]=1
 return out

def threshold_grid(y,p,pending):
 prec,rec,t=precision_recall_curve(y,p)
 f=np.divide(2*prec[:-1]*rec[:-1],prec[:-1]+rec[:-1],out=np.zeros(len(t)),where=prec[:-1]+rec[:-1]>0)
 # 3 priorities; all selection uses VALIDATION only
 policies={}
 for label,precmin,recmin in [('F1_recall70',.30,.70),('precision55_recall70',.55,.70),('precision65_recall70',.65,.70),('precision55_recall80',.55,.80)]:
  feasible=(prec[:-1]>=precmin)&(rec[:-1]>=recmin)
  if feasible.any():idx=int(np.argmax(np.where(feasible,f,-1.0)))
  else:
   fallback=(rec[:-1]>=recmin)&(prec[:-1]>=.30)
   if not fallback.any():fallback=rec[:-1]>=recmin
   if not fallback.any():fallback=np.ones(len(t),dtype=bool)
   idx=int(np.argmax(np.where(fallback,f,-1.0)))
  thr=float(t[idx]);pred=(p>=thr)&pending
  policies[label]={'kind':'threshold','threshold':thr,'selection_met_constraints':bool(feasible.any()),'val':metric(y,pred.astype(int))}
 return policies

def find_policies(y,p,pending,dates):
 d=threshold_grid(y,p,pending)
 for r in [.35,.45,.50,.55,.60,.70,.80]:
  pr=top_ratio_policy(p,pending,dates,r)
  d[f'daily_top_{int(r*100)}pct']={'kind':'daily_quota','fraction':r,'val':metric(y,pr)}
 return d

def apply_policy(policy,scores,pending,dates):
 if policy['kind']=='threshold':return ((scores>=policy['threshold'])&pending).astype(np.int8)
 return top_ratio_policy(scores,pending,dates,policy['fraction'])

def selection_rank(v):
 m=v['val'];valid=m['recall']>=.7 and m['precision']>=.55
 return (valid,m['f1'],m['recall'])

def train_model(name,params,X,y,train):
 if name.startswith('CB'):
  p=dict(params);p.update(loss_function='Logloss',random_seed=SEED,thread_count=4,allow_writing_files=False,verbose=False,one_hot_max_size=10)
  model=CatBoostClassifier(**p)
  model.fit(X.iloc[train],y[train],cat_features=list(X.select_dtypes(include=['object','string','category']).columns))
 else:
  # lightGBM categorical dtypes must have categories aligned to training only
  model=LGBMClassifier(**params,random_state=SEED,n_jobs=4,verbosity=-1)
  model.fit(X.iloc[train],y[train])
 return model

def cast_lgbm(X,train):
 Y=X.copy()
 for c in Y.select_dtypes(include=['object','string','category']).columns:
  Y[c]=pd.Categorical(Y[c],categories=sorted(Y.iloc[train][c].astype(str).unique()))
 return Y

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--olist',type=Path,default=Path('/mnt/data/logistic-brazilian-dataset.zip'))
 p.add_argument('--external',type=Path,default=Path('/mnt/data/VER06_reais/data'))
 p.add_argument('--roads',type=Path,default=Path('/mnt/data/dist_brasil.zip'))
 p.add_argument('--out',type=Path,default=Path('/mnt/data/VER07_experimento/results'))
 p.add_argument('--days',type=int,nargs='+',default=[2,1],choices=[1,2,3])
 args=p.parse_args();args.out.mkdir(parents=True,exist_ok=True)
 clock=time.monotonic();print('VER0.7: carregando Olist...',flush=True)
 base=ck.build_base(args.olist).sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
 assert len(base)==96470 and int(base.late.sum())==6534
 split=np.array(['train']*64320+['validation']*13547+['test']*18603)
 print('VER0.7: enriquecendo marketing e matriz rodoviaria REAL (o mesmo mapeamento parcial)...',flush=True)
 marketing,report_marketing=ef.add_marketing(base,args.external/'olist_closed_deals_dataset.csv')
 road,report_roads=ef.add_roads(base,args.roads,args.external/'municipios_ibge.csv')
 assert marketing.order_id.equals(base.order_id) and road.order_id.equals(base.order_id)
 # static, checkpoint-independent features
 static=pd.concat([marketing.drop(columns='order_id'),road.drop(columns='order_id')],axis=1)
 report={'project':'EntregaCerta AI', 'version':'VER0.7', 'origin_read_only':True,
         'olist_sha256_before':sha256(args.olist),'source_coverage':{'marketing':report_marketing,'roads':report_roads},'model_selection':'validation only', 'independent_test':False,'details':{},'experiment_status':'running'}
 for day in args.days:
  print(f'\nVER0.7: D-{day}: snapshots, features, clima...',flush=True)
  t0=time.monotonic();x=ck.build_checkpoint(base,days_before=day)
  X0,cats=ck.feature_table(x);Xi=vf.enhanced_features(x,X0)
  wx,rweather=ef.add_weather(x,args.external/'weather_daily.csv')
  X=Xi.copy()
  for col in static.columns:X[col]=static[col].to_numpy()
  for col in wx.columns:
   if col!='order_id':X[col]=wx[col].to_numpy()
  X=b06.clean_categorical(X)
  Xp=add_practical_features(x,X)
  idx=b06.make_train_cal_test_indices(x,split); y=x.late.to_numpy(dtype=np.int8)
  # same fixed model families pre-declared; feature sets chosen ONLY by validation
  configs=[('CB06',{'iterations':290,'depth':6,'learning_rate':.01954,'l2_leaf_reg':10.878,'auto_class_weights':'SqrtBalanced','random_strength':1.0,'rsm':.85},X),
           ('CB07',{'iterations':420,'depth':5,'learning_rate':.035,'l2_leaf_reg':12.,'auto_class_weights':'Balanced','random_strength':.6,'rsm':.85},Xp),
           ('CB07_unbalanced',{'iterations':500,'depth':5,'learning_rate':.035,'l2_leaf_reg':12.,'auto_class_weights':None,'random_strength':.6,'rsm':.85},Xp),
           ('LGBM07',{'n_estimators':400,'learning_rate':.030,'num_leaves':13,'min_child_samples':40,'reg_lambda':12.,'colsample_bytree':.85,'subsample':.85,'subsample_freq':1},Xp)]
  model_results=[]; models={}
  for name,param,xraw in configs:
   xfit=cast_lgbm(xraw,idx['train']) if name=='LGBM07' else xraw
   model=train_model(name,param,xfit,y,idx['train'])
   sc=model.predict_proba(xfit.iloc[idx['calpending']])[:,1]
   scores=np.zeros(len(idx['cal']),dtype=float);scores[np.isin(idx['cal'],idx['calpending'])]=sc
   cp=x.checkpoint_at.iloc[idx['cal']]
   pending=idx['pending'][idx['cal']]
   ps=find_policies(y[idx['cal']],scores,pending,cp)
   for pname,policy in ps.items():
    model_results.append({'name':name,'policy':pname,'val':policy['val'],'params':param,'type':policy['kind'],
         'threshold':policy.get('threshold'), 'fraction':policy.get('fraction'),'feasible':policy.get('selection_met_constraints')})
   models[name]=(model,xfit,ps)
   print('  ',name,'val bestF1',round(max(v['val']['f1'] for v in ps.values()),4),'features',xfit.shape[1],flush=True)
  winner=max(model_results,key=selection_rank)
  # Compare fixed experimental alternatives, never reselect the winner based on test data.
  record={'checkpoint':f'D-{day}', 'n_train_pending':len(idx['train']),'n_val_all':len(idx['cal']),
          'n_test_all':len(idx['test']),'n_test_pending':len(idx['testpending']),
          'n_features_original':int(X.shape[1]),'n_features_derived':int(Xp.shape[1]),
          'first_validation_checkpoint':str(idx['firstval']),'first_test_checkpoint':str(idx['firsttest']),
          'weather_coverage':rweather,
          'winner_selected_validation_only':{'model':winner['name'],'policy':winner['policy'],'val':winner['val'],'params':winner['params']},
          'validation_candidates':model_results, 'exploratory_test_diagnostics':[]}
  for name in [q[0] for q in configs]:
   model,fullX,policies=models[name]
   testidx=idx['test'];pendtest=idx['pending'][testidx];ptest=np.zeros(len(testidx),dtype=float)
   ptest[np.isin(testidx,idx['testpending'])]=model.predict_proba(fullX.iloc[idx['testpending']])[:,1]
   pd.DataFrame({'order_id':x.order_id.iloc[testidx].values,'checkpoint':x.checkpoint_at.iloc[testidx].values,
     'late':y[testidx],'pending':pendtest.astype('int8'),'score':ptest}).to_csv(args.out/f'Dminus{day}_{name}_test_scores.csv',index=False)
   for policyname,policy in policies.items():
    pred=apply_policy(policy,ptest,pendtest,x.checkpoint_at.iloc[testidx]);res=metric(y[testidx],pred)
    record['exploratory_test_diagnostics'].append({'model':name,'policy':policyname,'test':res})
   if name==winner['name']:
    chosen=policies[winner['policy']];finalpred=apply_policy(chosen,ptest,pendtest,x.checkpoint_at.iloc[testidx])
    record['winner_test']=metric(y[testidx],finalpred)
    record['winner_test']['pr_auc_pending_only']=float(average_precision_score(y[idx['testpending']],ptest[np.isin(testidx,idx['testpending'])]))
    record['winner_test']['roc_auc_pending_only']=float(roc_auc_score(y[idx['testpending']],ptest[np.isin(testidx,idx['testpending'])]))
    record['winner_test']['policy_type']=chosen['kind']
    record['winner_test']['threshold']=chosen.get('threshold')
    record['winner_test']['fraction']=chosen.get('fraction')
    pd.DataFrame({'order_id':x.order_id.iloc[testidx].values,'checkpoint_at':x.checkpoint_at.iloc[testidx].values,'late':y[testidx],
       'pending':pendtest.astype('int8'),'score':ptest,'alert':finalpred.astype('int8')}).to_csv(args.out/f'Dminus{day}_winner_predictions.csv',index=False)
    file=args.out/f'Dminus{day}_winner_{name}.cbm' if name.startswith('CB') else args.out/f'Dminus{day}_winner_{name}.txt'
    if name.startswith('CB'):model.save_model(str(file))
    else:model.booster_.save_model(str(file))
    record['winner_model_file']=file.name
    record['winner_feature_names']=list(fullX.columns)
    pd.DataFrame({'feature':fullX.columns,'importance':model.feature_importances_}).sort_values('importance',ascending=False).to_csv(args.out/f'Dminus{day}_winner_feature_importance.csv',index=False)
  rule=metric(y[idx['test']],pendtest.astype(int))
  record['rule_all_pending_test']=rule
  # Diagnostic metrics of explicitly pre-declared fixed quota 50%, and own validation score
  record['baseline_previous_f1']={2:.6007,1:.6311,3:.5032}.get(day)
  record['elapsed_sec']=round(time.monotonic()-t0,1)
  report['details'][f'D-{day}']=record
  write_json(args.out/f'Dminus{day}_results.json',record)
  print('VER0.7 SELECTED D-',day,winner['name'],winner['policy'],'valF1',round(winner['val']['f1'],4),
        'testF1',round(record['winner_test']['f1'],4),'P',round(record['winner_test']['precision'],4),'R',round(record['winner_test']['recall'],4),
        'ruleF1',round(rule['f1'],4),'elapsed',round(time.monotonic()-t0,1),'sec',flush=True)
  del x,X,Xp,models
 report['olist_sha256_after']=sha256(args.olist)
 assert report['olist_sha256_after']==report['olist_sha256_before']
 report['status']='executed; exploratory; nonindependent test'
 report['duration_sec']=round(time.monotonic()-clock,1)
 write_json(args.out/'VER07_complete_results.json',report)
 print('Complete VER0.7.',flush=True)

if __name__=='__main__':main()
