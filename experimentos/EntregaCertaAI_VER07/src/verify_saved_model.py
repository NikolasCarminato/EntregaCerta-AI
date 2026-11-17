#!/usr/bin/env python3
"""Load saved D-1 CatBoost and reproduce its scores from the source ZIPs."""
from pathlib import Path
import argparse,json
import numpy as np,pandas as pd
from catboost import CatBoostClassifier
import benchmark_ver07 as v
import olist_checkpoint as ck
import ver04_features as vf
import external_features as ef
import benchmark_ver06 as b06

def main():
 pa=argparse.ArgumentParser()
 pa.add_argument('--olist',type=Path,default=Path('/mnt/data/logistic-brazilian-dataset.zip'))
 pa.add_argument('--external',type=Path,default=Path('/mnt/data/VER06_reais/data'))
 pa.add_argument('--roads',type=Path,default=Path('/mnt/data/dist_brasil.zip'))
 pa.add_argument('--out',type=Path,default=Path('/mnt/data/VER07_experimento/results'))
 args=pa.parse_args()
 base=ck.build_base(args.olist).sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
 x=ck.build_checkpoint(base,days_before=1)
 X0,_=ck.feature_table(x);X=vf.enhanced_features(x,X0)
 for src in [ef.add_marketing(base,args.external/'olist_closed_deals_dataset.csv')[0],ef.add_roads(base,args.roads,args.external/'municipios_ibge.csv')[0],ef.add_weather(x,args.external/'weather_daily.csv')[0]]:
  assert src.order_id.equals(base.order_id)
  for c in src.columns:
   if c!='order_id':X[c]=src[c].to_numpy()
 X=b06.clean_categorical(X);features=v.add_practical_features(x,X)
 splits=np.array(['train']*64320+['validation']*13547+['test']*18603)
 ind=b06.make_train_cal_test_indices(x,splits)
 scores=pd.read_csv(args.out/'pilot_CB07_closed_only_test_predictions.csv')
 assert len(scores)==len(ind['test']) and np.array_equal(scores.order_id.to_numpy(),x.order_id.iloc[ind['test']].to_numpy())
 model=CatBoostClassifier();model.load_model(str(args.out/'pilot_Dminus1_CB07_closed_only.cbm'))
 calc=model.predict_proba(features.iloc[ind['testpending']])[:,1]
 stored=scores.score.to_numpy()[np.isin(ind['test'],ind['testpending'])]
 error=float(np.max(np.abs(calc-stored)))
 if error>1e-9:raise AssertionError(f'Prediction reproducibility failed, max_error {error}')
 # policy recompute from scores only, independently of model postprocess
 pred=v.top_ratio_policy(scores.score.to_numpy(),scores.pending.astype(bool).to_numpy(),pd.to_datetime(scores.checkpoint_at),.65)
 if not np.array_equal(pred,scores.alert.to_numpy(dtype=np.int8)):raise AssertionError('Alert policy mismatch')
 oldhash='967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784'
 if v.sha256(args.olist)!=oldhash:raise AssertionError('Original Olist changed!')
 report={'saved_model_reproduced':True,'max_probability_abs_diff':error,'policy_reproduced':True,'original_olist_sha256_unchanged':True,'n_prediction_rows':int(len(scores)), 'n_model_scored_pending':int(len(calc)), 'feature_count':int(features.shape[1])}
 v.write_json(args.out/'VER07_reproduction_audit.json',report)
 print('VER0.7 reproduction VERIFIED',report,flush=True)
if __name__=='__main__':main()
