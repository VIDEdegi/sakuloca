"""Ranking oracle, cache/filter/page regressions and timing on isolated SQLite."""
from array import array
import importlib.util,json,math,random,struct,subprocess,sys,tempfile,time
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'worker'))
from library import Library
from exact_search import ExactDot,implementation

report={'implementation':implementation(),'synthetic':True}
assert implementation()=='native-cpython-3.14-compensated', 'Build the helper before benchmarking'
rng=random.Random(740)
for n in (1,2,3,64,768,1024):
    for _ in range(200):
        values=array('f',(rng.uniform(-1,1) for i in range(n)))
        query=[rng.uniform(-1,1) for i in range(n)]
        assert struct.pack('d',ExactDot(query)(values.tobytes()))==struct.pack('d',sum(a*b for a,b in zip(values,query)))
report['bit_identical_random_dots']=1200
assert ExactDot([1])(b'bad') is None
assert ExactDot([1])(array('f',[float('nan')]).tobytes()) is None
with tempfile.TemporaryDirectory(prefix='dot-fallback-',dir=root/'tmp') as fallback:
    folder=Path(fallback);(folder/'native').mkdir()
    (folder/'exact_search.py').write_bytes((root/'worker/exact_search.py').read_bytes())
    (folder/'native/exact-search.dll').write_bytes(b'Invalid DLL fixture')
    subprocess.run([sys.executable,'-c',"from exact_search import ExactDot,implementation;from array import array;assert implementation()=='python-sum';assert ExactDot([0.3,-0.2])(array('f',[0.1,0.2]).tobytes())==sum(a*b for a,b in zip(array('f',[0.1,0.2]),[0.3,-0.2]))"],cwd=folder,check=True,timeout=10)
report['missing_or_corrupt_helper_falls_back']=True
query=[rng.uniform(-1,1) for _ in range(768)]
query=[v/math.sqrt(sum(x*x for x in query)) for v in query]
patterns=[array('f',[rng.uniform(-1,1) for _ in range(768)]) for _ in range(17)]
scores=[sum(a*b for a,b in zip(v,query)) for v in patterns]
best=max(range(17),key=lambda i:scores[i])
calls=0
class Engine:
    def text(self,text):
        global calls
        calls+=1
        return query
    def close(self):pass

with tempfile.TemporaryDirectory(dir=root/'tmp') as temporary:
    old_source=Path(temporary)/'baseline_library.py'
    old_source.write_bytes(subprocess.check_output(['git','show','3ea644b:worker/library.py'],cwd=root))
    spec=importlib.util.spec_from_file_location('baseline_library',old_source)
    baseline_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline_module)
    lib=Library(Path(temporary)/'index')
    try:
        lib.engine=Engine();lib.model_id='synthetic';lib.ensure_engine=lambda:None;lib.maintain_runtime_cache=lambda:None
        with lib.db:
            lib.db.execute("INSERT INTO folders(id,path,status) VALUES(1,?,'ready')",(temporary,))
            lib.db.execute("INSERT INTO models VALUES('synthetic','not-a-model',768,'test','l2','test','test','test')")
        for count in (10000,100000):
            old=lib.db.execute('SELECT COUNT(*) FROM files').fetchone()[0]
            with lib.db:
                if old:lib.db.execute('UPDATE embeddings SET vector=? WHERE file_id=?',(patterns[old%17].tobytes(),old))
                lib.db.executemany('INSERT INTO files(id,path,name,size,mtime_ns,identity,format) VALUES(?,?,?,?,?,?,?)',((i,f'{temporary}/{i}.jpg',f'{"late" if i>90000 else "early"}-{i}.jpg',1,1704067200000000000+(i%3)*86400000000000,str(i),'png' if i%2 else 'jpg') for i in range(old+1,count+1)))
                lib.db.executemany("INSERT INTO folder_files VALUES(1,?,'test')",((i,) for i in range(old+1,count+1)))
                lib.db.executemany("INSERT INTO embeddings VALUES(?,'synthetic',?)",((i,patterns[i%17].tobytes()) for i in range(old+1,count+1)))
                lib.db.execute('UPDATE embeddings SET vector=? WHERE file_id=?',(patterns[best].tobytes(),count))
            expected=sorted(((scores[best if i==count else i%17],i) for i in range(1,count+1)),key=lambda x:(-x[0],x[1]))
            lib.invalidate_search();start=time.perf_counter();first=lib.search(text='日本語');elapsed=time.perf_counter()-start
            assert [(s,-i) for s,i in lib.search_cache['ranked']]==expected
            seen=[];start=time.perf_counter()
            for offset in range(0,1000,200):
                page=lib.search(text='日本語',offset=offset,limit=200)
                assert page['total']==count
                seen.extend((x['score'],x['id']) for x in page['items'])
            cached_pages_elapsed=time.perf_counter()-start
            assert seen==expected[:1000], 'All 1000 IDs/scores must equal full reference sorting'
            assert first['items']==lib.search(text='日本語')['items']
            for offset in (1000,count//2,count-60):
                later=lib.search(text='日本語',offset=offset)
                assert [(x['score'],x['id']) for x in later['items']]==expected[offset:offset+60]
            last_offset=((count-1)//60)*60
            last_page=lib.search(text='日本語',offset=100000)
            assert last_page['offset']==last_offset
            assert [(x['score'],x['id']) for x in last_page['items']]==expected[last_offset:]
            cache=lib.search_cache['ranked']
            cache_bytes=sys.getsizeof(cache)+sum(sys.getsizeof(pair)+sum(sys.getsizeof(value) for value in pair) for pair in cache)
            report[str(count)]={'first_seconds':round(elapsed,6),'five_cached_pages_seconds':round(cached_pages_elapsed,6),'exact_all_candidates':True,'exact_top_1000':True,'ranking_cache_bytes':cache_bytes,'database_bytes':lib.status()['database_bytes']}
            baseline=baseline_module.Library(lib.data)
            try:
                baseline.engine=Engine();baseline.model_id='synthetic';baseline.ensure_engine=lambda:None;baseline.maintain_runtime_cache=lambda:None
                start=time.perf_counter();before=baseline.search(text='日本語');old_elapsed=time.perf_counter()-start
                report[str(count)]['dev1_same_data_seconds']=round(old_elapsed,6)
                report[str(count)]['speedup']=round(old_elapsed/elapsed,3)
                report[str(count)]['dev1_top_1000_matches_oracle']=[(v['score'],v['id']) for v in baseline.search_cache['items']]==expected[:1000]
            finally:baseline.close()
            print(json.dumps({str(count):report[str(count)]}),flush=True)
        # A filename filter must rank candidates outside the unfiltered cache.
        late=lib.search(text='日本語',filename='late-',limit=200)
        oracle=sorted(((scores[best if i==100000 else i%17],i) for i in range(90001,100001)),key=lambda x:(-x[0],x[1]))[:200]
        assert [(x['score'],x['id']) for x in late['items']]==oracle
        for filters in ({'format':'png'},{'after':'2024-01-02','before':'2024-01-02'},{'filename':'late-','format':'jpg'}):
            where,params=lib._where(filters)
            # Independent full Python dot + sort, no top-k/cache helper.
            reference=[]
            for row in lib.db.execute(f'SELECT f.id,e.vector FROM files f JOIN embeddings e ON e.file_id=f.id WHERE {where}',params):
                v=array('f');v.frombytes(row['vector']);reference.append((sum(a*b for a,b in zip(v,query)),row['id']))
            reference.sort(key=lambda x:(-x[0],x[1]))
            result=lib.search(text='日本語',offset=800,limit=200,**filters)
            assert [(x['score'],x['id']) for x in result['items']]==reference[800:1000]
        lib.folder_action(1,'enable',False);assert lib.search(text='日本語')['total']==0
        lib.folder_action(1,'enable',True);assert lib.search(text='日本語')['items'][0]['id']==expected[0][1]
        lib._scan(lib.folders()[0]);assert lib.search(text='日本語')['total']==0
        assert lib.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        report['filters_pages_ties_invalidation_integrity']='passed'
    finally:lib.close()
out=root/'data/release-tests/rc-audit';out.mkdir(exist_ok=True)
(out/'exact-search.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
