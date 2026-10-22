#!/bin/bash
# usage: launch.sh NAME args...
cd /Users/jonghyeonlee/git/particle-sim-compare
name=$1; shift
.venv/bin/python scripts/benchmark_scale_compare.py --work /tmp/bench_real/$name --cases 1682 --workers 4 --stages 1-6 --max-operation-bytes 34359738368 --window-um 6 --min-count 3 "$@" > /tmp/bench_real/$name.json 2> /tmp/bench_real/$name.err
echo "exit $?" > /tmp/bench_real/$name.done
