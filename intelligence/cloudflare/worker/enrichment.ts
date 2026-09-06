import { ApiError, parseLimit, requireIsoDate, requireObject, requireString, sha256Hex } from './http.ts';
import type { ApiResponse, AuthContext } from './types.ts';

function since(url: URL): string {
  return requireIsoDate(url.searchParams.get('since') ?? new Date(Date.now() - 7 * 86400000).toISOString(), 'since');
}

export async function pendingEnrichment({env, url}: AuthContext): Promise<ApiResponse> {
  const target = url.searchParams.get('target_id');
  const result = await env.DB.prepare(`WITH candidates AS (
    SELECT i.*, ROW_NUMBER() OVER (PARTITION BY i.target_id ORDER BY i.published_at DESC, i.fetched_at DESC, i.id) AS target_rank
    FROM items i JOIN targets t ON t.id=i.target_id JOIN channels c ON c.id=i.channel_id
    WHERE t.enabled=1 AND c.enabled=1 AND julianday(i.fetched_at)>=julianday(?)
      AND (COALESCE(json_extract(i.raw_metadata_json,'$.discovery_only'),0)=1 OR (
        (i.published_at IS NULL OR julianday(i.published_at)>=julianday('now','-72 hours'))
        AND (length(COALESCE(i.content_text,''))<400 OR EXISTS (
          SELECT 1 FROM analyses a WHERE a.item_id=i.id AND a.summary LIKE '公开来源显示%'
        ))
      ))
      AND (i.enrichment_status IS NULL OR i.enrichment_status='failed') AND i.enrichment_attempts<3
      AND (? IS NULL OR i.target_id=?)
  ) SELECT * FROM candidates ORDER BY target_rank, target_id LIMIT ?`)
    .bind(since(url), target, target, parseLimit(url,100,500)).all();
  return {status:200,body:{items:result.results ?? []}};
}

export async function getItem({env,params}: AuthContext): Promise<ApiResponse> {
  const item=await env.DB.prepare('SELECT * FROM items WHERE id=?').bind(params.id).first();
  if (!item) throw new ApiError(404,'item_not_found','Item not found');
  return {status:200,body:{item}};
}

function publicUrl(value: unknown, name: string): string {
  const text = requireString(value,name,{max:4096})!;
  let url: URL;
  try { url = new URL(text); } catch { throw new ApiError(400,'invalid_request',`${name} must be an HTTP URL`); }
  if (!['https:','http:'].includes(url.protocol) || url.username || url.password) throw new ApiError(400,'invalid_request',`${name} must be an HTTP URL without credentials`);
  return text;
}

export async function enrichItem({env,body,params}: AuthContext): Promise<ApiResponse> {
  const p=requireObject(body), id=params.id;
  const status=requireString(p.status,'status')!;
  if (!['ready','rejected','failed'].includes(status)) throw new ApiError(400,'invalid_request','Invalid enrichment status');
  if (!Number.isInteger(p.expected_revision) || Number(p.expected_revision)<0) throw new ApiError(400,'invalid_request','expected_revision must be a nonnegative integer');
  const revision=Number(p.expected_revision), reason=requireString(p.reason,'reason',{min:1,max:2000})!;
  if (!reason.trim()) throw new ApiError(400,'invalid_request','reason must not be blank');
  const current=await env.DB.prepare('SELECT * FROM items WHERE id=?').bind(id).first<Record<string,unknown>>();
  if (!current) throw new ApiError(404,'item_not_found','Item not found');
  if (current.content_revision!==revision) throw new ApiError(409,'enrichment_conflict','Reload item revision before enriching');
  const now=new Date().toISOString();
  let content:string|null=null, title:string|null=null, finalUrl:string|null=null, published:string|null=null, fetched:string|null=null, hash:string|null=null;
  let evidence:Record<string,unknown>|null=null;
  if (status==='ready') {
    if (p.publication_precision != null && !['day','second'].includes(String(p.publication_precision))) throw new ApiError(400,'invalid_request','publication_precision must be day or second');
    content=requireString(p.content_text,'content_text',{min:200,max:100000})!;
    title=requireString(p.title,'title',{min:1,max:2000})!;
    if (content.trim().length<200 || !title.trim()) throw new ApiError(400,'invalid_request','Article body/title must contain meaningful text');
    finalUrl=publicUrl(p.final_url,'final_url');
    published=requireIsoDate(p.published_at,'published_at'); fetched=requireIsoDate(p.fetched_at,'fetched_at');
    if (Date.parse(published)>Date.parse(fetched)+300000) throw new ApiError(400,'invalid_date_evidence','Publication cannot be later than retrieval');
    evidence=requireObject(p.date_evidence,'date_evidence');
    if (!['article_metadata','article_text','feed','platform'].includes(String(evidence.kind))) throw new ApiError(400,'invalid_date_evidence','Publication date requires source evidence');
    requireString(evidence.value,'date_evidence.value',{min:4,max:2000});
    publicUrl(evidence.source_url,'date_evidence.source_url');
    requireString(p.tool_name,'tool_name',{min:1,max:256});
    // No caller-supplied verified flag is accepted: source evidence remains inspectable.
    hash=await sha256Hex(content);
  }
  const after=JSON.stringify({status,reason,content_text:content,title,final_url:finalUrl,published_at:published,fetched_at:fetched,date_evidence:evidence,publication_precision:p.publication_precision ?? 'second',tool_name:p.tool_name ?? null});
  const audit=crypto.randomUUID();
  const metadata=status==='ready' ? JSON.stringify({...JSON.parse(String(current.raw_metadata_json ?? '{}')),discovery_only:false,
    enrichment:{date_evidence:evidence,tool_name:p.tool_name,fetched_at:fetched},publication_date_source:evidence!.kind,
    publication_precision:p.publication_precision ?? 'second'}) : null;
  const statements=[env.DB.prepare(`INSERT INTO item_enrichments
    (id,item_id,revision,status,reason,before_json,after_json,previous_analysis_json,created_at)
    SELECT ?,i.id,?, ?,?,json_object('title',i.title,'url',i.url,'canonical_url',i.canonical_url,'content_text',i.content_text,
      'content_hash',i.content_hash,'published_at',i.published_at,'raw_metadata_json',i.raw_metadata_json,'is_baseline',i.is_baseline),?,
      (SELECT json_object('summary',a.summary,'key_change',a.key_change,'why_it_matters',a.why_it_matters,'company_impact',a.company_impact,
       'importance',a.importance,'confidence',a.confidence,'topics_json',a.topics_json,'watch_next_json',a.watch_next_json,
       'evidence_json',a.evidence_json,'model',a.model,'prompt_version',a.prompt_version,'analyzed_at',a.analyzed_at) FROM analyses a WHERE a.item_id=i.id),?
    FROM items i WHERE i.id=? AND i.content_revision=?`).bind(audit,revision+1,status,reason,after,now,id,revision),
    env.DB.prepare(`UPDATE items SET enrichment_status=?, enrichment_reason=?, enrichment_attempts=enrichment_attempts+1,
      enriched_at=?,content_revision=content_revision+1 WHERE id=? AND content_revision=? AND EXISTS(SELECT 1 FROM item_enrichments WHERE id=?)`)
      .bind(status,reason,now,id,revision,audit)];
  if (status==='ready') statements.push(env.DB.prepare(`UPDATE items SET title=?,canonical_url=?,content_text=?,content_hash=?,published_at=?,
    fetched_at=?,raw_metadata_json=?,is_baseline=0 WHERE id=? AND EXISTS(SELECT 1 FROM item_enrichments WHERE id=?)`)
    .bind(title,finalUrl,content,hash,published,fetched,metadata,id,audit));
  statements.push(env.DB.prepare('DELETE FROM analyses WHERE item_id=? AND EXISTS(SELECT 1 FROM item_enrichments WHERE id=?)').bind(id,audit));
  const result=await env.DB.batch(statements);
  if (result[0]?.meta?.changes!==1) throw new ApiError(409,'enrichment_conflict','Item changed during enrichment');
  return {status:200,body:{id,enrichment_status:status,content_revision:revision+1,audit_event_id:audit}};
}

export async function coverage({env,url}: AuthContext): Promise<ApiResponse> {
  const result=await env.DB.prepare(`SELECT t.id AS target_id,t.name,
    COUNT(i.id) AS discovered,
    SUM(CASE WHEN i.enrichment_status='ready' THEN 1 ELSE 0 END) AS enriched,
    SUM(CASE WHEN i.enrichment_status='rejected' THEN 1 ELSE 0 END) AS rejected,
    SUM(CASE WHEN i.enrichment_status='failed' THEN 1 ELSE 0 END) AS failed,
    SUM(CASE WHEN i.id IS NOT NULL AND (i.enrichment_status IS NULL OR i.enrichment_status='failed') AND (
      COALESCE(json_extract(i.raw_metadata_json,'$.discovery_only'),0)=1 OR (
        (i.published_at IS NULL OR julianday(i.published_at)>=julianday('now','-72 hours'))
        AND (length(COALESCE(i.content_text,''))<400 OR a.summary LIKE '公开来源显示%')
      )) THEN 1 ELSE 0 END) AS pending_enrichment,
    COUNT(a.item_id) AS analyzed
    FROM targets t LEFT JOIN items i ON i.target_id=t.id AND julianday(i.fetched_at)>=julianday(?)
    LEFT JOIN analyses a ON a.item_id=i.id WHERE t.enabled=1 GROUP BY t.id ORDER BY t.id`).bind(since(url)).all();
  return {status:200,body:{targets:result.results ?? []}};
}
