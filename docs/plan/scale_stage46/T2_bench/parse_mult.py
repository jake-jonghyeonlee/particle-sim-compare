import json, sys, tracemalloc, gc
from pathlib import Path
for path in sys.argv[1:]:
    data = Path(path).read_bytes(); size = len(data)
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    text = data.decode('utf-8'); del data
    obj = json.loads(text); del text; gc.collect()
    retained, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    print(f'{path.split("/")[-3]}/{path.split("/")[-2]}: file {size} retained {retained-base} ({(retained-base)/size:.2f}x) peak {peak-base} ({(peak-base)/size:.2f}x)')
