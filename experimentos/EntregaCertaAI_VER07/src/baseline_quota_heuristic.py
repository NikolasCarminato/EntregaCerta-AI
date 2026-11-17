#!/usr/bin/env python3
"""Compare fixed 65%-per-day budget to transparent non-ML rankings, validation selection only."""
from pathlib import Path
import numpy as np
import pandas as pd
import benchmark_ver07 as v
import olist_checkpoint as ck
import benchmark_ver06 as b

def run(olist='/mnt/data/logistic-brazilian-dataset.zip',out='/mnt/data/VER07_experimento/results'):
 out=Path(out);base=ck.build_base(olist).sort_values(['order_purchase_timestamp','order_id']).reset_index(drop=True)
 x=ck.build_checkpoint(base,days_before=1)
 split=np.array(['train']*64320+['validation']*13547+['test']*18603)
 idx=b.make_train_cal_test_indices(x,split);y=x.late.to_numpy(dtype=int)
 # All values available at the checkpoint (age of order, days in transit, dispatched/not).
 strategies={'order_age':x.age_days.to_numpy(),
  'in_transit_age':x.in_transit_days.fillna(-1).to_numpy(),
  'not_dispatched_then_age':(x.not_dispatched.astype(float)*1000 + x.age_days.fillna(0)).to_numpy(),
  'days_after_shipping_limit':x.shipping_limit_age_days.fillna(-100).to_numpy()}
 rows=[]
 for name,score in strategies.items():
  cv=v.top_ratio_policy(score[idx['cal']],idx['pending'][idx['cal']],x.checkpoint_at.iloc[idx['cal']],.65)
  test=v.top_ratio_policy(score[idx['test']],idx['pending'][idx['test']],x.checkpoint_at.iloc[idx['test']],.65)
  rows.append({'heuristic':name,'validation':v.metric(y[idx['cal']],cv),'test_diagnostic':v.metric(y[idx['test']],test)})
 chosen=max(rows,key=lambda r:r['validation']['f1'])
 v.write_json(out/'VER07_heuristic_comparison.json',{'rule':'top 65% of pending per day based only on a simple as-of attribute', 'selection_by':'validation F1 only','validation_selected':chosen['heuristic'],'selected_test':chosen['test_diagnostic'],'candidates':rows,'test_is_reused':True})
 print('HEURISTICS:',[(r['heuristic'],round(r['validation']['f1']*100,2),round(r['test_diagnostic']['f1']*100,2)) for r in rows],flush=True)
if __name__=='__main__':run()
