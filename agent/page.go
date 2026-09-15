package main

// 폴백 화면 — Safari 처럼 콘솔에서 못 붙는 브라우저를 위해.
//
// **콘솔 화면과 같은 API 를 부른다.** 화면 코드가 두 벌이면 숫자가 갈린다.
// (JS 에 백틱을 안 쓴다 — Go 원시 문자열이 백틱으로 끝나서 여기 못 들어온다.)
const pageHTML = `<!doctype html><html lang="ko"><meta charset="utf-8">
<title>PERSO 데이터</title>
<style>
 body{font:14px/1.6 -apple-system,'Segoe UI',sans-serif;margin:0;padding:28px 34px;
      background:#f5f6f6;color:#1a1a1a}
 h1{font-size:18px;margin:0 0 4px} .sub{color:#667;font-size:12.5px;margin-bottom:18px}
 .card{background:#fff;border:1px solid #e3e5e5;border-radius:10px;padding:18px 20px;margin-bottom:16px}
 table{border-collapse:collapse;width:100%;font-size:12.5px}
 th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #eee}
 th{color:#667;font-weight:600} td.n{text-align:right;font-variant-numeric:tabular-nums}
 .warn{background:#fff6e5;border-color:#f0d9a8}
</style>
<h1>PERSO 데이터 — 로컬</h1>
<div class="sub">이 화면과 계산은 <b>이 PC</b> 에서만 돕니다. 원본은 어디로도 안 나갑니다.</div>
<div class="card" id="status">불러오는 중…</div>
<div id="out"></div>
<script>
const T="__TOKEN__", B="http://127.0.0.1:__PORT__";
const get=(p)=>fetch(B+p,{headers:{Authorization:"Bearer "+T},cache:"no-store"}).then(r=>r.json());
const esc=(s)=>String(s).replace(/[&<>]/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
function table(d){
  const th=d.columns.map((c)=>"<th>"+esc(c)+"</th>").join("");
  const tr=d.rows.slice(0,200).map((r)=>"<tr>"+r.map((v)=>
     typeof v==="number" ? "<td class=\"n\">"+v.toLocaleString()+"</td>"
                         : "<td>"+esc(v??"")+"</td>").join("")+"</tr>").join("");
  return "<div class=\"card\"><b>"+esc(d.label)+"</b><div class=\"sub\">"
    + d.rows.length.toLocaleString() + "행 · " + d.computed_ms + "ms"
    + (d.suppressed_groups ? " · 작은 그룹 "+d.suppressed_groups+"개 제외" : "")
    + "</div><table>"+th+tr+"</table></div>";
}
(async()=>{
  const s=await get("/v1/status");
  const box=document.getElementById("status");
  box.className="card"+(s.as_of.stale?" warn":"");
  box.innerHTML = "데이터 기준 <b>"+esc(s.as_of.committed_at||"(알 수 없음)")+"</b> · 커밋 <code>"
    + esc(s.as_of.commit)+"</code> · pull: "+esc(s.as_of.pull)
    + " · 폴더 "+s.folder_mb.toLocaleString()+"MB"
    + (s.as_of.stale?" <b>⚠️ 낡은 데이터입니다</b>":"");
  const out=document.getElementById("out");
  for (const m of s.metrics) {
    const d=await get("/v1/metrics/"+m);
    out.insertAdjacentHTML("beforeend",
      d.error ? "<div class=\"card warn\">"+esc(m)+": "+esc(d.error)+"</div>" : table(d));
  }
})();
</script></html>`
