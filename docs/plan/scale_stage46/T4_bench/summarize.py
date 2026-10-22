import json,sys
for f in sys.argv[1:]:
    line=[l for l in open(f).read().split('\n') if l.startswith('{"tag"')][-1]
    d=json.loads(line); d.pop('tail')
    print(d['tag'], 'exit',d['exit'],'wall',d['wall_s'],'peak',d['peak_rss_mb'],{k:(v['seconds'],v['maxrss_mb']) if 'maxrss_mb' in v else v['seconds'] for k,v in d['phases'].items()}, 'rows',d.get('rows'),'png',d.get('png_files'),'hidden',d.get('hidden_files_left'))
