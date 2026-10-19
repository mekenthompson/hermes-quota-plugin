const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let page, effects=[],slots=[],idx=0;
const forecast={label:'Shared',provider:'grok',account:'abc',window:'Weekly',used_percent:24,remaining_percent:76,status:'exhausts-before-reset',rate_percentage_points_per_hour:2,projected_used_percent_at_reset:116,estimated_exhaustion_at:'2026-01-02T16:00:00Z',reset_at:'2026-01-03T00:00:00Z',sample_count:3,observed_hours:2,fetched_at:'2026-01-02T00:00:00Z'};
const samples=[
  {fetched_at:'2026-01-01T00:00:00Z',provider:'grok',account:'abc',window:'Weekly',reset_at:'2026-01-03T00:00:00Z',used_percent:10,status:'ok'},
  {fetched_at:'2026-01-02T00:00:00Z',provider:'grok',account:'abc',window:'Weekly',reset_at:'2026-01-03T00:00:00Z',used_percent:24,status:'ok'},
  {fetched_at:'2026-01-02T00:00:00Z',provider:'grok',account:'abc',window:'Weekly',reset_at:'2026-01-03T00:00:00Z',used_percent:90,status:'auth-failed'}
];
const calls=[];
const React={createElement:(tag,props,...children)=>typeof tag==='function'?tag(props||{}):({tag,props,children}),useState:(initial)=>{const i=idx++;if(!(i in slots))slots[i]=initial;return [slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v]},useEffect:(fn)=>effects.push(fn)};
const context={window:{__HERMES_PLUGIN_SDK__:{React,fetchJSON:async(url,options)=>{calls.push([url,options]);if(url.endsWith('/forecast'))return {forecasts:[forecast]};if(url.endsWith('/history'))return {samples,daily_changes:[]};return {providers:{},managed_collection:true}}},__HERMES_PLUGINS__:{register:(name,fn)=>page=fn}},Date,Math,isFinite,isNaN,String};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../dashboard/dist/index.js'),'utf8'),context);
(async()=>{page();effects.forEach(fn=>fn());await new Promise(r=>setImmediate(r));idx=0;const rendered=JSON.stringify(page());assert.match(rendered,/116/);assert.match(rendered,/exhausts-before-reset/);assert.match(rendered,/76/);assert.match(rendered,/Below target/);assert.match(rendered,/svg/);assert.match(rendered,/even-pace target/);assert.match(rendered,/Reload/);assert.ok(calls.some(([u])=>u.endsWith('/forecast')));assert.ok(calls.every(([,o])=>!o||o.method!=='POST'));console.log('dashboard forecast renders real component and uses cached GETs: PASS');})().catch(e=>{console.error(e);process.exitCode=1});
