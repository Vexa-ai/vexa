"""Operator-only import CLI; identity mapping is explicit, never guessed by email."""
import argparse
import json
import os
from pathlib import Path
from sqlalchemy import create_engine
from crm.importer import import_export

parser=argparse.ArgumentParser()
parser.add_argument('export_directory',type=Path)
parser.add_argument('--tenant',required=True)
parser.add_argument('--actor',required=True)
parser.add_argument('--user-map',type=Path,required=True,help='JSON source User ID → existing Vexa subject ID')
args=parser.parse_args()
print(json.dumps(import_export(create_engine(os.environ['CRM_DATABASE_URL']),args.export_directory,
    args.tenant,json.loads(args.user_map.read_text()),args.actor)))
