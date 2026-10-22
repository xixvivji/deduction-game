"""Log preserved synthetic evaluation summaries. No live API calls or raw game data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from game.mlflow_tracking import TrackingClient


def main():
    p=argparse.ArgumentParser();p.add_argument('--uri',default='http://127.0.0.1:5500');args=p.parse_args()
    path=Path(__file__).resolve().parents[1]/'reports/claim-guard-reviewed-v2.json'
    report=json.loads(path.read_text());fingerprint=hashlib.sha256(path.read_bytes()).hexdigest()
    client=TrackingClient(args.uri)
    try:
        eid=client.experiment('night-testimony-evaluation')
        for variant in ('before','after'):
            found=client.call('runs/search',{'experiment_ids':[eid],'filter':f"params.report_sha = '{fingerprint}' AND params.variant = '{variant}' AND attributes.status = 'FINISHED'",'max_results':1})
            if found.get('runs'):
                print(variant,'already recorded');continue
            run=client.create_run(eid,'claim-guard-v2-'+variant,{'report_sha':fingerprint,'variant':variant,
                'baseline':report['baseline_commit'],'scope':'22 synthetic fixed-output cases; 2 of 24 excluded with reasons',
                'reviewer':'implementing assistant; not external human benchmark','dataset_sha':report['heldout_sha256']})
            client.log(run,report[variant]['summary'],0);client.end(run)
            print(variant,'recorded')
        print('experiment:',eid)
    finally:client.close()

if __name__=='__main__':main()
