#!/usr/bin/env bash
set -o errexit

python3 -m pip install --upgrade pip
pip install -r requirements.txt
python3 manage.py collectstatic --noinput
python3 manage.py migrate
