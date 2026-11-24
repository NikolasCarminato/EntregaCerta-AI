#!/usr/bin/env python3
"""Daily capacity policy (65% pending D-1) — exploratory post-hoc scenario.

The 65% alert budget was proposed after exploration of a *reused historical
period*. It is NOT a prospectively chosen threshold and must not be advertised
as proof of generalization. Rebuild, audit, and compare two Marketing Funnel
variants; the comparison between variants is selected ONLY by validation F1.
"""
import sys, time, argparse, json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import average_precision_score,roc_auc_score
import benchmark_ver07 as v
import olist_checkpoint as ck
import ver04_features as vf
import external_features as ef
import benchmark_ver06 as b06


def add_mql_feature(base,closed,leads):
 d=pd.read_csv(closed,usecols=['seller_id','mql_id','won_date']);l=pd.read_csv(leads,usecols=['mql_id','first_contact_date','origin','landing_page_id'])
 d['won_date']=pd.to_datetime(d.won_date,errors='coerce')
 l['first_contact_date']=pd.to_datetime(l.first_contact_date,errors='coerce')
 d=d.merge(l.drop_duplicates('mql_id'),on='mql_id',how='left',validate='many_to_one')
 d=d[d.won_date.notna()&((d.first_contact_date.isna())|(d.first_contact_date<=d.won_date))].sort_values('won_date')
 left=base[['seller_id','order_purchase_timestamp']].copy();left['_row']=np.arange(len(left));left=left.sort_values('order_purchase_timestamp')
 z=pd.merge_asof(left,d[['seller_id','won_date','first_contact_date','origin','landing_page_id']],left_on='order_purchase_timestamp',right_on='won_date',by='seller_id',direction='backward')
 z=z.sort_values('_row')
 assert (z.loc[z.won_date.notna(),'won_date']<=z.loc[z.won_date.notna(),'order_purchase_timestamp']).all()
 res=pd.DataFrame({'marketing_mql_origin':z.origin.fillna('__MISSING__').astype(str).to_numpy(),
     'marketing_mql_landing_page_id':z.landing_page_id.fillna('__MISSING__').astype(str).to_numpy(),
     'marketing_mql_days_since_contact':(z.order_purchase_timestamp-z.first_contact_date).dt.total_seconds().div(86400).to_numpy()})
 return res,{'covered_with_prior_win_and_mql':int(z.won_date.notna().sum()),'origin_nonmissing':int(z.origin.notna().sum())}

def block_bootstrap(y,a,b,dates,seed=42,B=450):
 # Days as blocks, preserving within-day label correlation. NOT independent final test.
 r=np.random.default_rng(seed)
 blocks=pd.Series(range(len(y))).groupby(pd.DatetimeIndex(dates).normalize()).apply(lambda s:s.to_numpy()).tolist()
 delta=[]
 for j in range(B):
  picks=r.integers(0,len(blocks),len(blocks))
  ids=np.concatenate([blocks[k] for k in picks]);delta.append(v.metric(y[ids],a[ids])['f1']-v.metric(y[ids],b[ids])['f1'])
 return {'bootstraps':B,'block':'checkpoint_date','difference_f1':float(v.metric(y,a)['f1']-v.metric(y,b)['f1']),
         'ci95_lower':float(np.quantile(delta,.025)),'ci95_upper':float(np.quantile(delta,.975)),
         'caveat':'descriptive uncertainty; reused test and exploratory policy selection, no generalization proof'}

def main():
 pa=argparse.ArgumentParser()
 pa.add_argument('--olist',type=Path,default=Path('/mnt/data/logistic-brazilian-dataset.zip'))
 pa.add_argument('--external',type=Path,default=Path('/mnt/data/VER06_reais/data'))
 pa.add_argument('--roads',type=Path,default=Path('/mnt/data/dist_brasil.zip'))
 pa.add_argument('--out',type=Path,default=Path('/mnt/data/VER07_experimento/results'))
 args=pa.parse_args();args.out.mkdir(parents=True,exist_ok=True)
 base=ck.build_base(args.olist).sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
 x=ck.build_checkpoint(base,days_before=1)
 X0,_=ck.feature_table(x);X=vf.enhanced_features(x,X0)
 mark,markr=ef.add_marketing(base,args.external/'olist_closed_deals_dataset.csv')
 roads,roadr=ef.add_roads(base,args.roads,args.external/'municipios_ibge.csv')
 weather,wr=ef.add_weather(x,args.external/'weather_daily.csv')
 for src in [mark,roads,weather]:
  assert src.order_id.equals(base.order_id)
  for c in src.columns:
   if c!='order_id':X[c]=src[c].to_numpy()
 X=b06.clean_categorical(X);Xp=v.add_practical_features(x,X)
 mql,mqlr=add_mql_feature(base,args.external/'olist_closed_deals_dataset.csv',args.external/'olist_marketing_qualified_leads_dataset.csv')
 Xp_mql=Xp.copy()
 for c in mql.columns:Xp_mql[c]=mql[c].to_numpy()
 Xp_mql=b06.clean_categorical(Xp_mql)
 split=np.array(['train']*64320+['validation']*13547+['test']*18603)
 idx=b06.make_train_cal_test_indices(x,split);y=x.late.to_numpy(dtype=int)
 models=[('CB07_closed_only',Xp),('CB07_with_mql',Xp_mql)]
 configs={'iterations':420,'depth':5,'learning_rate':.035,'l2_leaf_reg':12.0,'auto_class_weights':'Balanced','random_strength':.6,'rsm':.85}
 results=[]
 for name,features in models:
  print('Pilot fit',name,'features',features.shape[1],flush=True)
  model=v.train_model('CB07',configs,features,y,idx['train'])
  val_all=np.zeros(len(idx['cal'])); val_all[np.isin(idx['cal'],idx['calpending'])]=model.predict_proba(features.iloc[idx['calpending']])[:,1]
  val_pred=v.top_ratio_policy(val_all,idx['pending'][idx['cal']],x.checkpoint_at.iloc[idx['cal']],.65)
  valmet=v.metric(y[idx['cal']],val_pred)
  # test is scored for pre-specified diagnostics but does not determine which variant is picked
  test_scores=np.zeros(len(idx['test']));test_scores[np.isin(idx['test'],idx['testpending'])]=model.predict_proba(features.iloc[idx['testpending']])[:,1]
  test_pred=v.top_ratio_policy(test_scores,idx['pending'][idx['test']],x.checkpoint_at.iloc[idx['test']],.65)
  test_met=v.metric(y[idx['test']],test_pred)
  test_met.update(pr_auc_pending_only=float(average_precision_score(y[idx['testpending']],model.predict_proba(features.iloc[idx['testpending']])[:,1])))
  rule=v.metric(y[idx['test']],idx['pending'][idx['test']].astype(int))
  boot=block_bootstrap(y[idx['test']],test_pred,idx['pending'][idx['test']].astype(int),x.checkpoint_at.iloc[idx['test']])
  modelfile=f'pilot_Dminus1_{name}.cbm';model.save_model(str(args.out/modelfile))
  pd.DataFrame({'feature':features.columns,'importance':model.feature_importances_}).sort_values('importance',ascending=False).to_csv(args.out/f'pilot_{name}_importance.csv',index=False)
  pd.DataFrame({'order_id':x.order_id.iloc[idx['test']].to_numpy(),'checkpoint_at':x.checkpoint_at.iloc[idx['test']].to_numpy(),
     'late':y[idx['test']],'pending':idx['pending'][idx['test']].astype(int),'score':test_scores,'alert':test_pred}).to_csv(args.out/f'pilot_{name}_test_predictions.csv',index=False)
  results.append({'variant':name,'features':list(features.columns),'model_file':modelfile,'val':valmet,'test':test_met,'rule':rule,'paired_block_bootstrap':boot})
  print(name,'Val P,R,F1',*[round(valmet[k]*100,2) for k in ('precision','recall','f1')],
    'Test P,R,F1',*[round(test_met[k]*100,2) for k in ('precision','recall','f1')],flush=True)
 # Select the Marketing variant strictly by VALIDATION F1, not by test F1
 chosen=max(results,key=lambda r:r['val']['f1'])
 report={'project':'EntregaCerta AI','version':'VER0.7 pilot','policy':'For each checkpoint_date D-1, alert ceil(0.65 * pending_today) with highest score',
   'policy_selection_notice':'65% quota defined EXPLORATORILY after consulting reused test labels: not independent or prospective',
   'variant_selection':'validation F1 ONLY among fixed 65% quota candidates',
   'selected':chosen['variant'],'selected_validation':chosen['val'],'selected_test':chosen['test'],
   'baseline_all_pending_test':chosen['rule'],'variants':results,
   'sources':{'marketing':markr,'roads':roadr,'weather':wr,'mql':mqlr},
   'original_file_sha256':v.sha256(args.olist),'originals_modified':False,
   'caveat':'All 2018 tests reused in VER0.1 through VER0.6. Candidate metrics are illustrative only; seek new blind period.'}
 v.write_json(args.out/'VER07_pilot_operational.json',report)
 print('Pilot selected',chosen['variant'],'F1test',round(chosen['test']['f1'],4),flush=True)

if __name__=='__main__':main()
