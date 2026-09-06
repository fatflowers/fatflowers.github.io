import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';
import worker from '../worker/index.ts';
import type {D1Database,D1PreparedStatement,D1Result} from '../worker/types.ts';

class SqliteD1 implements D1Database {
  db=new DatabaseSync(':memory:');
  beforeBatch?:()=>void;
  constructor() {
    for (const file of ['0001_initial.sql','0002_baseline_items.sql','0003_article_enrichment.sql','0004_analysis_headline.sql']) this.db.exec(readFileSync(new URL(`../migrations/${file}`,import.meta.url),'utf8'));
    for (const id of ['a','b']) {
      this.db.prepare("INSERT INTO targets(id,slug,name,target_type,created_at,updated_at) VALUES(?,?,?,'company','2026-09-06','2026-09-06')").run(id,id,id);
      this.db.prepare("INSERT INTO channels(id,target_id,slug,name,channel_type,collector_type,created_at,updated_at) VALUES(?,?,?,?,'blog','mcp','2026-09-06','2026-09-06')").run(id,id,id,id);
    }
    for (const [id,target] of [['a1','a'],['a2','a'],['b1','b']]) this.db.prepare(`INSERT INTO items(id,target_id,channel_id,url,title,fetched_at,content_text,content_hash,raw_metadata_json,created_at,is_baseline)
      VALUES(?,?,?,'https://example.com/article','Discovery','2026-09-06T01:00:00Z','Snippet',?,'{"discovery_only":true}','2026-09-06',1)`).run(id,target,target,id);
  }
  prepare(query:string):D1PreparedStatement {
    let values:unknown[]=[];
    const stmt:D1PreparedStatement={bind:(...args)=>{values=args;return stmt;},
      first:async<T>()=>(this.db.prepare(query).get(...values as never[])??null) as T|null,
      all:async<T>()=>({success:true,results:this.db.prepare(query).all(...values as never[]) as T[]}),
      run:async<T>()=>({success:true,results:[] as T[],meta:{changes:Number(this.db.prepare(query).run(...values as never[]).changes)}})};
    return stmt;
  }
  async batch<T>(statements:D1PreparedStatement[]):Promise<D1Result<T>[]> {
    this.beforeBatch?.();this.db.exec('BEGIN');
    try {const result:D1Result<T>[]=[];for(const s of statements) result.push(await s.run<T>());this.db.exec('COMMIT');return result;}
    catch(e){this.db.exec('ROLLBACK');throw e;}
  }
}
const TOKEN='a-long-test-token-with-24-characters';
const payload={expected_revision:0,status:'ready',reason:'Read original article',title:'Full article',content_text:'A concrete article with dated source evidence. '.repeat(8),
  published_at:'2026-09-06T00:00:00Z',fetched_at:'2026-09-06T01:00:00Z',final_url:'https://example.com/article',tool_name:'firecrawl',
  date_evidence:{kind:'article_metadata',value:'2026-09-06T00:00:00Z',source_url:'https://example.com/article'}};
function call(db:SqliteD1,path:string,body?:unknown,key='test-key') {
  return worker.fetch(new Request(`https://example.com${path}`,{method:body?'POST':'GET',headers:{authorization:`Bearer ${TOKEN}`,'content-type':'application/json','idempotency-key':key},...(body?{body:JSON.stringify(body)}:{})}),{DB:db,API_TOKEN:TOKEN});
}
test('baseline discovery candidates are queued fairly across targets and bounded by date',async()=>{
  const db=new SqliteD1();
  const response=await call(db,'/v1/items/pending-enrichment?since=2026-09-05T00:00:00Z&limit=2');
  assert.equal(response.status,200);
  assert.deepEqual((await response.json() as any).items.map((i:any)=>i.target_id),['a','b']);
  const newer=await call(db,'/v1/items/pending-enrichment?since=2026-09-07T00:00:00Z');
  assert.equal((await newer.json() as any).items.length,0);
});
test('analysis context contains only recent published daily memberships and is bounded',async()=>{
  const db=new SqliteD1();
  const now=new Date().toISOString();
  const old=new Date(Date.now()-8*86400000).toISOString();
  for(const id of ['a1','a2','b1']) {
    db.db.prepare("INSERT INTO analyses(item_id,summary,key_change,importance,confidence,model,prompt_version,analyzed_at) VALUES(?,?,?,4,0.9,'test','v1',?)").run(id,'Summary '+id,'Change '+id,now);
    db.db.prepare("UPDATE items SET canonical_url=?,published_at=? WHERE id=?").run('https://example.com/'+id,old,id);
  }
  const addReport=(id:string,status:string,edition:string,at:string,item:string)=>{
    db.db.prepare("INSERT INTO reports(id,report_date,edition,window_start,window_end,title,slug,report_status,content_markdown,created_at,published_at) VALUES(?,?,?, ?,?,'Report',?,?,'Body',?,?)").run(id,id,edition,old,now,id,status,now,at);
    db.db.prepare("INSERT INTO report_items(report_id,item_id,rank,section) VALUES(?,?,0,'primary')").run(id,item);
  };
  addReport('recent','published','morning',now,'a1');
  addReport('duplicate-membership','published','evening',now,'a1');
  addReport('stale','published','morning',old,'a2');
  addReport('not-live','ready','midday',now,'a2');
  addReport('weekly-only','published','weekly',now,'b1');
  const first=await call(db,'/v1/items/pending-analysis?target_id=b');
  assert.equal(first.status,200);
  const events=(await first.json() as any).recent_published_events;
  assert.deepEqual(events.map((event:any)=>event.id),['a1']);
  assert.equal(events[0].summary,'Summary a1');
  assert.equal(events[0].key_change,'Change a1');
  assert.equal(events[0].canonical_url,'https://example.com/a1');
  assert.equal(events[0].published_at,old); // Recently published catch-up is context.
  assert.equal(events[0].reported_at,now);
  for(let n=0;n<105;n++) {
    const id='more-'+n;
    db.db.prepare("INSERT INTO items(id,target_id,channel_id,url,title,fetched_at,content_hash,created_at) VALUES(?,'a','a',?,'Item',?,?,?)").run(id,'https://example.com/'+id,now,id,now);
    db.db.prepare("INSERT INTO analyses(item_id,summary,importance,confidence,model,prompt_version,analyzed_at) VALUES(?,?,4,0.9,'test','v1',?)").run(id,'Long'.repeat(1000),now);
    db.db.prepare("INSERT INTO report_items(report_id,item_id,rank,section) VALUES('recent',?,1,'brief')").run(id);
  }
  const bounded=(await (await call(db,'/v1/items/pending-analysis')).json() as any).recent_published_events;
  assert.equal(bounded.length,100);
  assert.ok(bounded.every((event:any)=>event.summary.length<=2000));
});
test('new article links outrank dated baseline backlog without starving other targets',async()=>{
  const db=new SqliteD1();
  db.db.exec(`UPDATE items SET published_at='2026-09-05T23:00:00Z' WHERE id='a1';
    UPDATE items SET raw_metadata_json='{"discovery_only":true,"discovered_from":"https://example.com/blog"}' WHERE id='a2';`);
  const response=await call(db,'/v1/items/pending-enrichment?since=2026-09-05T00:00:00Z&limit=2');
  assert.deepEqual((await response.json() as any).items.map((i:any)=>i.id),['a2','b1']);
});
test('hydration replaces same item, archives discovery, enters analysis queue, and retry is idempotent',async()=>{
  const db=new SqliteD1();
  assert.equal((await call(db,'/v1/items/a1/enrichment',payload)).status,200);
  const item=db.db.prepare("SELECT * FROM items WHERE id='a1'").get()!;
  assert.equal(item.content_text,payload.content_text);assert.equal(item.is_baseline,0);assert.equal(item.content_revision,1);
  assert.equal(JSON.parse(String(item.raw_metadata_json)).discovery_only,false);
  const audit=db.db.prepare('SELECT * FROM item_enrichments').get()!;
  assert.equal(JSON.parse(String(audit.before_json)).content_text,'Snippet');
  const pending=await call(db,'/v1/items/pending-analysis');
  assert.equal((await pending.json() as any).items[0].id,'a1');
  assert.equal((await call(db,'/v1/items/a1/enrichment',payload)).headers.get('x-idempotent-replay'),'true');
  const stats=await call(db,'/v1/coverage?since=2026-09-05T00:00:00Z');
  const a=(await stats.json() as any).targets[0];assert.equal(a.enriched,1);assert.equal(a.pending_enrichment,1);
});
test('ready rejects snippets, missing or invented date evidence and future publication',async()=>{
  for(const change of [{content_text:'Snippet'},{date_evidence:undefined},{date_evidence:{...payload.date_evidence,kind:'llm_guess'}},{published_at:'2026-09-08T00:00:00Z'}]) {
    const db=new SqliteD1();assert.equal((await call(db,'/v1/items/a1/enrichment',{...payload,...change})).status,400);
    assert.equal(db.db.prepare('SELECT count(*) n FROM item_enrichments').get()!.n,0);
  }
});
test('rejected candidates leave queue; failed candidates stop after three attempts',async()=>{
  const db=new SqliteD1();
  assert.equal((await call(db,'/v1/items/a1/enrichment',{expected_revision:0,status:'rejected',reason:'Old article'})).status,200);
  for(let n=0;n<3;n++) assert.equal((await call(db,'/v1/items/b1/enrichment',{expected_revision:n,status:'failed',reason:'Fetch failed'},`fail-${n}`)).status,200);
  const response=await call(db,'/v1/items/pending-enrichment?since=2026-09-05T00:00:00Z');
  assert.deepEqual((await response.json() as any).items.map((i:any)=>i.id),['a2']);
});
test('analysis stale revision rejected, re-enrichment archives/deletes previous analysis',async()=>{
  const db=new SqliteD1();await call(db,'/v1/items/a1/enrichment',payload);
  const analysis={item_id:'a1',summary:'Concrete summary',importance:4,confidence:0.8,model:'test',prompt_version:'v1',analyzed_at:'2026-09-06T02:00:00Z'};
  assert.equal((await call(db,'/v1/analyses/batch',{analyses:[analysis]})).status,409);
  assert.equal((await call(db,'/v1/analyses/batch',{analyses:[{...analysis,content_revision:1}]},'correct')).status,200);
  assert.equal((await call(db,'/v1/items/a1/enrichment',{...payload,expected_revision:1,content_text:payload.content_text+'Updated'},'update')).status,200);
  assert.equal(db.db.prepare('SELECT count(*) n FROM analyses').get()!.n,0);
  const audit=db.db.prepare('SELECT previous_analysis_json FROM item_enrichments WHERE revision=2').get()!;
  assert.equal(JSON.parse(String(audit.previous_analysis_json)).summary,analysis.summary);
});
test('editorial headline persists to report input without changing source title',async()=>{
  const db=new SqliteD1();await call(db,'/v1/items/a1/enrichment',payload);
  const analysis={item_id:'a1',content_revision:1,headline:'工具新增稳定授权接口',summary:'Concrete summary',importance:4,confidence:0.8,model:'test',prompt_version:'v1',analyzed_at:'2026-09-06T02:00:00Z'};
  assert.equal((await call(db,'/v1/analyses/batch',{analyses:[analysis]})).status,200);
  const response=await call(db,'/v1/reports/input?from=2026-09-05T00:00:00Z&to=2026-09-07T00:00:00Z');
  const item=(await response.json() as any).items[0];
  assert.equal(item.headline,analysis.headline);
  assert.equal(item.title,payload.title);
  assert.equal((await call(db,'/v1/analyses/batch',{analyses:[{...analysis,headline:'字'.repeat(61)}]},'long')).status,400);
});
test('concurrent revision cannot overwrite and transaction rollback preserves discovery',async()=>{
  const db=new SqliteD1();db.beforeBatch=()=>db.db.exec("UPDATE items SET content_revision=1 WHERE id='a1'");
  assert.equal((await call(db,'/v1/items/a1/enrichment',payload)).status,409);
  assert.equal(db.db.prepare('SELECT count(*) n FROM item_enrichments').get()!.n,0);
  const fail=new SqliteD1();fail.db.exec("CREATE TRIGGER fail_update BEFORE UPDATE ON items BEGIN SELECT RAISE(ABORT,'test'); END");
  assert.equal((await call(fail,'/v1/items/a1/enrichment',payload)).status,500);
  assert.equal(fail.db.prepare('SELECT count(*) n FROM item_enrichments').get()!.n,0);
});

test('daily inputs omit already-published items while weekly override includes them',async()=>{
  const db=new SqliteD1();await call(db,'/v1/items/a1/enrichment',payload);
  await call(db,'/v1/analyses/batch',{analyses:[{item_id:'a1',content_revision:1,summary:'Concrete summary',importance:4,confidence:0.8,model:'test',prompt_version:'v1',analyzed_at:'2026-09-06T02:00:00Z'}]});
  db.db.exec(`INSERT INTO reports(id,report_date,edition,window_start,window_end,title,slug,report_status,content_markdown,created_at)
    VALUES('r','2026-09-06','morning','2026-09-05','2026-09-06','Report','report','published','Report','2026-09-06');
    INSERT INTO report_items VALUES('r','a1',0,'brief')`);
  const path='/v1/reports/input?from=2026-09-05T00:00:00Z&to=2026-09-07T00:00:00Z';
  assert.equal((await (await call(db,path)).json() as any).items.length,0);
  assert.equal((await (await call(db,path+'&include_reported=true')).json() as any).items.length,1);
  db.db.exec("UPDATE reports SET report_status='draft'");
  assert.equal((await (await call(db,path)).json() as any).items.length,1);
});

test('dated summaries and placeholder analyses re-enter research but old known articles do not',async()=>{
  const db=new SqliteD1();
  db.db.exec(`UPDATE items SET raw_metadata_json='{}',published_at=datetime('now','-1 hour'),fetched_at=datetime('now') WHERE id IN ('a1','a2');
    UPDATE items SET raw_metadata_json='{}',published_at='2020-01-01',fetched_at=datetime('now') WHERE id='b1';
    UPDATE items SET content_text=replace(hex(zeroblob(500)),'00','article') WHERE id='a2';
    INSERT INTO analyses(item_id,summary,importance,confidence,model,prompt_version,analyzed_at)
      VALUES('a2','公开来源显示：placeholder',3,0.5,'old','old',datetime('now'))`);
  const response=await call(db,'/v1/items/pending-enrichment');
  assert.deepEqual((await response.json() as any).items.map((i:any)=>i.id),['a1','a2']);
});

test('analysis queue recovers recent complete baseline posts but rejects unknown old failed or snippet records',async()=>{
  const db=new SqliteD1();
  db.db.exec(`UPDATE channels SET channel_type='twitter';
    UPDATE items SET raw_metadata_json='{}',published_at=datetime('now','-1 hour') WHERE id='a1';
    UPDATE items SET raw_metadata_json='{}',published_at='2020-01-01' WHERE id='a2';
    UPDATE items SET raw_metadata_json='{}' WHERE id='b1'`);
  const ids=async()=>((await (await call(db,'/v1/items/pending-analysis')).json() as any).items.map((i:any)=>i.id));
  assert.deepEqual(await ids(),['a1']);
  db.db.exec("UPDATE items SET enrichment_status='failed' WHERE id='a1'");assert.deepEqual(await ids(),[]);
  db.db.exec("UPDATE items SET enrichment_status=NULL WHERE id='a1'; UPDATE channels SET channel_type='rss'");assert.deepEqual(await ids(),[]);
  db.db.exec(`UPDATE items SET raw_metadata_json='{"content_complete":true}',content_text=replace(hex(zeroblob(500)),'00','article') WHERE id='a1'`);
  assert.deepEqual(await ids(),['a1']);
});

test('current-revision analysis clears only recent complete native baseline items',async()=>{
  const db=new SqliteD1();
  db.db.exec(`UPDATE channels SET channel_type='twitter';
    UPDATE items SET raw_metadata_json='{}',published_at=datetime('now','-1 hour') WHERE id='a1';
    UPDATE items SET raw_metadata_json='{}',published_at='2020-01-01' WHERE id='a2'`);
  const analysis=(id:string)=>({item_id:id,content_revision:0,summary:'A concrete summary',importance:3,confidence:0.8,model:'test',prompt_version:'v1',analyzed_at:new Date().toISOString()});
  assert.equal((await call(db,'/v1/analyses/batch',{analyses:[analysis('a1'),analysis('a2'),analysis('b1')]})).status,200);
  assert.equal(db.db.prepare("SELECT is_baseline FROM items WHERE id='a1'").get()!.is_baseline,0);
  assert.equal(db.db.prepare("SELECT is_baseline FROM items WHERE id='a2'").get()!.is_baseline,1);
  assert.equal(db.db.prepare("SELECT is_baseline FROM items WHERE id='b1'").get()!.is_baseline,1);
});
