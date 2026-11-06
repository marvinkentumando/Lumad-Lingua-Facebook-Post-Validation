#!/usr/bin/env python3
"""Produce a self-contained, offline HTML preview with browser-local annotation storage."""
import csv
import json
from pathlib import Path

root = Path(__file__).resolve().parent
out = root.parent / 'Lumad_Lingua_Validator_Offline.html'
with (root/'data/mansaka_candidates.csv').open('r', encoding='utf-8-sig', newline='') as f:
    posts = list(csv.DictReader(f))
seed = json.dumps(posts, ensure_ascii=False).replace('</', '<\\/').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
css = (root/'static/style.css').read_text()
js = (root/'static/app.js').read_text()
html = (root/'static/index.html').read_text()
html = html.replace('<link rel="icon" type="image/svg+xml" href="/favicon.svg">', '')
html = html.replace('<link rel="stylesheet" href="/style.css">', '<style>'+css+'''\n.offline-tag{background:#fff3dc;color:#735421;border-bottom:1px solid #ecdaba;font-size:11px;text-align:center;padding:9px 15px;font-weight:650}.offline-tag strong{color:#493b20}.offline-tag a{color:#604a2a}
</style>''')
html = html.replace('<script src="/app.js" defer></script>', '')
html = html.replace('<body>', '''<body><div class="offline-tag"><strong>OFFLINE COPY</strong> · Saves only in this browser, not on a shared server. Export the complete CSV regularly as a backup.</div>''')
script = r'''
<script type="application/json" id="seed-records">SEED_JSON_PLACEHOLDER</script>
<script>
'use strict';
(function(){
 const seed=JSON.parse(document.getElementById('seed-records').textContent);
 const storageKey='lumad_lingua_offline_annotations_v1';
 const importsKey='lumad_lingua_offline_imports_v1';
 const historyKey='lumad_lingua_offline_history_v1';
 function read(key,fallback){try{return JSON.parse(localStorage.getItem(key))||fallback;}catch{return fallback;}}
 function persist(key,value){try{localStorage.setItem(key,JSON.stringify(value));}catch{alert('Browser storage is full or unavailable. Download an export now.');}}
 function response(payload, code=200, type='application/json'){
   return new Response(type==='application/json'?JSON.stringify(payload):payload,{status:code,headers:{'Content-Type':type}});
 }
 function parseCsv(raw){
   raw=raw.replace(/^\ufeff/,'');let rows=[],r=[],value='',q=false;
   for(let i=0;i<raw.length;i++){
     const c=raw[i];
     if(q){if(c==='"'&&raw[i+1]==='"'){value+='"';i++;}else if(c==='"')q=false;else value+=c;}
     else if(c==='"')q=true;
     else if(c===','){r.push(value);value='';}
     else if(c==='\n'||c==='\r'){if(c==='\r'&&raw[i+1]==='\n')i++;r.push(value);value='';if(r.some(v=>v!==''))rows.push(r);r=[];}
     else value+=c;
   }
   if(q)throw Error('Unclosed quotation mark in CSV.');
   if(value!==''||r.length){r.push(value);rows.push(r);}
   const keys=rows.shift()||[];
   return rows.map(row=>Object.fromEntries(keys.map((k,i)=>[k.trim(),row[i]||''])));
 }
 const quote=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"';
 const toCsv=(items,columns)=>'\ufeff'+columns.map(quote).join(',')+'\r\n'+items.map(row=>columns.map(c=>quote(row[c])).join(',')).join('\r\n')+'\r\n';
 function combined(){
   const validations=read(storageKey,{});
   return [...seed,...read(importsKey,[])].map(row=>{
     const item=validations[row.research_post_id];
     return {...row,human_language:item?.human_language||null,human_sentiment:item?.sentiment||null,human_notes:item?.notes||'',human_status:item?.status||'pending',validated_at:item?.updated_at||null,revision:item?.revision||0};
   });
 }
 const nativeFetch=window.fetch.bind(window);
 window.fetch=async function(url,options={}){
   const path=String(url);
   if(!path.startsWith('/api/'))return nativeFetch(url,options);
   try{
     if(path==='/api/session'||path==='/api/login')return response({authenticated:true,offline:true});
     if(path==='/api/logout')return response({ok:true});
     if(path==='/api/posts')return response({posts:combined()});
     if(path==='/api/validation'){
       const body=JSON.parse(options.body||'{}'),id=body.post_id;
       if(!combined().some(p=>p.research_post_id===id))return response({error:'Unknown post.'},404);
       let lang=body.language, sentiment=body.sentiment,status;
       if(body.skip){status='skipped';lang=null;sentiment=null;}
       else if(!['mansaka','mixed','not_mansaka','unsure'].includes(lang))return response({error:'Please choose a Mansaka language judgment.'},400);
       else if(['mansaka','mixed'].includes(lang)){
         if(!['Positive','Negative','Neutral'].includes(sentiment))return response({error:'Select a sentiment.'},400);
         status='validated';
       } else {status=lang==='not_mansaka'?'excluded':'needs_review'; sentiment=null;}
       const notes=String(body.notes||'').slice(0,5000),validations=read(storageKey,{});
       const revision=(validations[id]?.revision||0)+1;
       const updated_at=new Date().toISOString();validations[id]={human_language:lang,sentiment,notes,status,updated_at,revision};persist(storageKey,validations);
       const history=read(historyKey,[]);history.push({research_post_id:id,revision,human_language_label:lang||'',human_sentiment_label:sentiment||'',validation_notes:notes,human_review_status:status,changed_at:updated_at});persist(historyKey,history);
       return response({ok:true,post_id:id,language:lang,sentiment,notes,status,validated_at:updated_at,revision});
     }
     if(path==='/api/import'){
       const body=JSON.parse(options.body||'{}');const items=parseCsv(body.csv||'');
       if(items.length>15000)return response({error:'Too many rows.'},400);
       if(items.length&&!Object.prototype.hasOwnProperty.call(items[0],'research_post_id'))return response({error:'CSV must have research_post_id and cleaned_text headers.'},400);
       const existing=read(importsKey,[]);const ids=new Set([...seed,...existing].map(p=>p.research_post_id));let inserted=0,duplicates=0,invalid=0;
       for(const r of items){const id=String(r.research_post_id||'').trim();if(!id||!(r.cleaned_text||r.anonymized_text)){invalid++;continue;}if(ids.has(id)){duplicates++;continue;}existing.push(r);ids.add(id);inserted++;}
       persist(importsKey,existing);return response({ok:true,inserted,duplicates,invalid});
     }
     if(path.startsWith('/api/export')){
       const mode=(path.split('mode=')[1]||'training').split('&')[0],items=combined();let rows,cols;
       if(mode==='history'){rows=read(historyKey,[]);cols=['research_post_id','revision','human_language_label','human_sentiment_label','validation_notes','human_review_status','changed_at'];}
       else if(mode==='all'){
         cols=[...new Set(items.flatMap(r=>Object.keys(r)))].filter(k=>!['human_language','human_sentiment','human_notes','human_status','validated_at','revision'].includes(k));
         cols.push('human_language_label','human_sentiment_label','validation_notes','human_review_status','validated_at','review_revision');
         rows=items.map(r=>({...r,human_language_label:r.human_language||'',human_sentiment_label:r.human_sentiment||'',validation_notes:r.human_notes,human_review_status:r.human_status,review_revision:r.revision}));
       }else{
         cols=['research_post_id','cleaned_text','anonymized_text','sentiment_label','language_verified','validated_at'];
         rows=items.filter(r=>r.human_status==='validated').map(r=>({...r,sentiment_label:r.human_sentiment,language_verified:r.human_language}));
       }
       return response(toCsv(rows,cols),200,'text/csv; charset=utf-8');
     }
     return response({error:'Not found'},404);
   }catch(e){return response({error:e.message},400);}
 };
 window.addEventListener('DOMContentLoaded',()=>{
   const el=document.querySelector('.side-info .tiny');if(el)el.textContent='Stored in this browser only';
 });
})();
</script>
<script>APP_JS_PLACEHOLDER</script>
'''.replace('SEED_JSON_PLACEHOLDER',seed).replace('APP_JS_PLACEHOLDER',js)
html=html.replace('</body>',script+'</body>')
out.write_text(html,encoding='utf-8')
print('Created',out,'bytes',out.stat().st_size)
