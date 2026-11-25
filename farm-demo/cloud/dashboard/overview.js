// Tenant-owned native ThingsBoard widget. All API requests use ctx.http,
 // which attaches the current native user JWT. No credential persistence.
self.onInit = function () {
  const root=self.ctx.$container[0].querySelector('.fw-shell');
  const el=id=>root.querySelector('#fw-'+id);
  const stations=self.ctx.settings.stations || [];
  const fields=[['temperature','Nhiệt độ','°C',1],['humidity','Độ ẩm','% RH',1],['pressure','Áp suất','hPa',1],
    ['wind_speed','Gió trung bình','m/s',1],['wind_gust','Gió giật 3 giây','m/s',1],
    ['rainfall','Mưa trong kỳ','mm',4],['battery_voltage','Điện áp pin','V',2],['wind_direction','Hướng gió','°',0]];
  const roles={admin:'Quản trị',technician:'Kỹ thuật',operator:'Vận hành',viewer:'Chỉ xem'};
  const states={PENDING:'Chờ thiết bị',QUEUED:'Đã xếp hàng',APPLIED:'Đã áp dụng',REJECTED:'Bị từ chối',EXPIRED:'Hết hạn',UNKNOWN:'Chưa xác minh'};
  let dead=false, generation=0, busy=false, refreshing=false, snapshot=null, command=null, timer;
  const subs=new Set();
  const text=(id,v)=>{el(id).textContent=v;};
  const node=(tag,content)=>{let e=document.createElement(tag);if(content!==undefined)e.textContent=content;return e;};
  const num=(v,d=0)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('vi-VN',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
  const time=v=>typeof v==='number'&&v>0?new Intl.DateTimeFormat('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',dateStyle:'short',timeStyle:'medium'}).format(v):'—';
  const call=(station,action,body)=>new Promise((resolve,reject)=>{
    if(dead){reject(new Error('closed'));return;}
    let sub=self.ctx.http.post('/api/farm/stations/'+encodeURIComponent(station)+'/'+action,body)
      .subscribe({next:resolve,error:reject});
    subs.add(sub);sub.add(()=>subs.delete(sub));
  });
  function pairs(id,items){const box=el(id);box.replaceChildren();for(const [k,v] of items){const d=node('div');d.append(node('span',k),node('strong',v));box.append(d);}}
  function fail(msg){el('error').hidden=false;text('error',msg);}
  function permissions(caps){el('controls').hidden=!caps.some(x=>['test_buzzer','request_stop_irrigation','set_config'].includes(x));
    el('stop').hidden=!caps.includes('request_stop_irrigation');el('test').hidden=!caps.includes('test_buzzer');el('config').hidden=!caps.includes('set_config');}
  function chart(points,now){
    const svg=el('chart'), ns='http://www.w3.org/2000/svg';svg.replaceChildren();
    const add=(tag,attrs,label)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(label)n.textContent=label;svg.append(n);return n;};
    const valid=points.filter(p=>typeof p.value==='number'&&Number.isFinite(p.value));
    if(!valid.length){add('text',{x:300,y:95,'text-anchor':'middle',fill:'#52685e'},'Chưa có dữ liệu nhiệt độ');return;}
    const low=Math.floor(Math.min(...valid.map(p=>p.value))-1),high=Math.ceil(Math.max(...valid.map(p=>p.value))+1);
    const x=t=>46+(t-(now-86400000))/86400000*536,y=v=>150-(v-low)/(high-low)*132;
    for(let i=0;i<=3;i++){let v=low+(high-low)*i/3;add('line',{x1:46,y1:y(v),x2:582,y2:y(v),stroke:'#dce6df'});add('text',{x:40,y:y(v)+4,'text-anchor':'end','font-size':10,fill:'#52685e'},num(v,1));}
    let prev=null;
    for(const p of points){if(typeof p.value!=='number'||!Number.isFinite(p.value)){prev=null;continue;}
      if(prev&&p.ts-prev.ts<=p.period_s*1000+1000)add('line',{x1:x(prev.ts),y1:y(prev.value),x2:x(p.ts),y2:y(p.value),stroke:'#176f53','stroke-width':2});
      add('circle',{cx:x(p.ts),cy:y(p.value),r:2.3,fill:'#176f53'});prev=p;}
    for(let i=0;i<=4;i++){const ts=now-86400000+i*21600000;add('text',{x:x(ts),y:172,'font-size':10,'text-anchor':'middle',fill:'#52685e'},new Intl.DateTimeFormat('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit'}).format(ts));}
  }
  function render(s){
    snapshot=s;el('error').hidden=true;
    text('fresh',({FRESH:'Dữ liệu hiện hành',STALE:'Dữ liệu cũ',UNKNOWN:'Chưa có dữ liệu'})[s.freshness]||'Chưa xác định');
    text('time',time(s.weather_ts));text('period',num(s.expected_period_s/60)+' phút');text('role',roles[s.role]||'—');
    const grid=el('metrics');grid.replaceChildren();
    for(const [key,label,unit,dec] of fields){const v=s.values[key],card=node('article');card.className='fw-metric';card.dataset.key=key;card.dataset.quality=v===null?'invalid':s.freshness==='FRESH'?'valid':'stale';
      card.append(node('span',label),node('strong',num(v,dec)+(v===null?'':' '+unit)),node('small',v===null?(s.weather_ts?'Không hợp lệ / thiếu':'Chưa có dữ liệu'):s.freshness==='FRESH'?'Thời điểm đo như trên':'Giá trị cũ'));grid.append(card);}
    const d=s.values.wind_direction,arrow=el('arrow');arrow.hidden=d===null||s.freshness!=='FRESH';if(!arrow.hidden)arrow.style.transform='rotate('+d+'deg)';
    text('direction',d===null?'Không xác định':num(d)+'° · '+['Bắc','Đông Bắc','Đông','Đông Nam','Nam','Tây Nam','Tây','Tây Bắc'][Math.round(d/45)%8]+(s.freshness==='FRESH'?'':' (cũ)'));
    const rain=s.rain_today;text('rain','Mưa ngày đã ghi nhận: '+num(rain.total,4)+(rain.total===null?'':' mm')+' · '+Math.floor(rain.coverage_s/60)+' / '+Math.floor(rain.elapsed_s/60)+' phút có dữ liệu hợp lệ (không nội suy)');
    chart(s.trend,s.now);text('trend-note',s.trend.length+' bản ghi · khoảng thiếu không nối đường');
    const g=s.gateway,r=g.radio||{},rf=r.simulated===true?' (mô phỏng)':'';
    pairs('health',[['Gateway / Cloud',g.fresh?(g.link_state||'Chưa rõ'):'Chưa xác nhận / dữ liệu cũ'],['Cập nhật Gateway',time(g.ts)],
      ['Bản ghi chờ',num(g.pending)],['Bản ghi cũ nhất',g.oldest_age_ms===null?'—':num(g.oldest_age_ms/1000)+' s'],
      ['Bộ đệm',g.budget_bytes>0&&g.disk_bytes!==null?num(100*g.disk_bytes/g.budget_bytes,1)+'%':'—'],
      ['RSSI'+rf,num(r.rssi,1)+(r.rssi==null?'':' dBm')],['SNR'+rf,num(r.snr,1)+(r.snr==null?'':' dB')],['Nhận LoRaWAN',time(r.received_at)],['Khu vực',s.zone||'—'],['Firmware',s.applied.firmware_version||'—']]);
    const box=el('alarms');box.replaceChildren();if(!s.alarms.length)box.append(node('p','Không có cảnh báo hoạt động trên Cloud.'));
    for(const a of s.alarms){const row=node('article');row.dataset.severity=a.severity;const desc=node('div');desc.append(node('strong',a.type+' · '+a.severity),node('p',time(a.start)+(a.acknowledged?' · Đã xác nhận':' · Chưa xác nhận')));row.append(desc);
      if(!a.acknowledged&&s.capabilities.includes('ack')){const b=node('button','Xác nhận đã đọc');b.type='button';b.disabled=busy;b.onclick=()=>operate('ack',{alarm_id:a.id});row.append(b);}box.append(row);}
    const output=v=>v===null?'Chưa có xác nhận hiện hành':v?'Đang kích hoạt':'Không kích hoạt';
    pairs('outputs',[['Còi',output(s.buzzer)],['Yêu cầu ngừng tưới',output(s.stop_request)]]);
    pairs('config-state',[['Phiên bản mong muốn',num(s.desired.config_version)],['Phiên bản đã áp dụng',num(s.applied.applied_config_version)],['Ngưỡng mong muốn',num(s.desired.wind_alarm_on)+' / '+num(s.desired.wind_alarm_off)+' m/s'],['Ngưỡng đã áp dụng',num(s.applied.applied_wind_alarm_on)+' / '+num(s.applied.applied_wind_alarm_off)+' m/s'],['Chu kỳ đã áp dụng',s.applied.applied_period_s?num(s.applied.applied_period_s/60)+' phút':'—']]);
    if(!el('config').dataset.initialized){for(const k of ['wind_alarm_on','wind_alarm_off','report_period_s'])if(s.desired[k]!=null)el('config').elements[k].value=s.desired[k];el('config').dataset.initialized='1';}
    permissions(s.capabilities);el('export').hidden=!historyResult||!s.capabilities.includes('export');
  }
  async function refresh(){
    if(dead||refreshing||!el('station').value)return;refreshing=true;const g=generation,station=el('station').value;
    try{const s=await call(station,'overview',{});if(dead||g!==generation)return;render(s);
      if(command){const c=await call(station,'rpc_status',{command_id:command});if(!dead&&g===generation){text('command',(states[c.state]||c.state)+' · '+command);if(['APPLIED','REJECTED','EXPIRED','UNKNOWN'].includes(c.state))command=null;}}
    }catch(e){if(!dead&&g===generation){snapshot=null;permissions([]);el('export').hidden=true;fail('Không thể cập nhật. Dữ liệu đang hiển thị là lần đọc trước, không dùng để quyết định hiện tại.');text('fresh','Mất kết nối ứng dụng');el('arrow').hidden=true;}}
    finally{refreshing=false;if(!dead&&g!==generation)refresh();}
  }
  async function operate(action,body){
    if(dead||busy||!snapshot)return;busy=true;const g=generation,station=el('station').value;
    el('station').disabled=true;for(const b of root.querySelectorAll('button'))b.disabled=true;
    try{const r=await call(station,action,body);if(dead||g!==generation)return;if(action==='rpc'){command=r.command_id;text('command','Chờ thiết bị · '+command);}else text('command','Đã xác nhận đọc cảnh báo. Không xóa điều kiện nguy hiểm.');}
    catch(e){if(!dead&&g===generation)text('command','Yêu cầu không thành công hoặc chưa xác minh. Không tự động gửi lại.');}
    finally{busy=false;if(!dead){el('station').disabled=false;for(const b of root.querySelectorAll('button'))b.disabled=false;refresh();}}
  }
  for(const s of stations){const o=node('option',s.name);o.value=s.id;el('station').append(o);}
  el('station').onchange=()=>{historyReset('Đã đổi trạm. Chọn Xem thống kê để tải lịch sử.');generation++;snapshot=null;command=null;permissions([]);el('metrics').replaceChildren();for(const id of ['health','outputs','config-state','alarms','chart'])el(id).replaceChildren();text('fresh','Đang tải…');text('time','—');text('direction','—');text('rain','—');text('trend-note','');el('arrow').hidden=true;text('command','Chưa gửi lệnh trong phiên này.');delete el('config').dataset.initialized;refresh();};
  el('refresh').onclick=refresh;
  el('stop').onclick=()=>{if(window.confirm('Gửi yêu cầu ngừng tưới? Không tự bật lại bơm.'))operate('rpc',{method:'request_stop_irrigation',params:{}});};
  el('test').onclick=()=>operate('rpc',{method:'test_buzzer',params:{}});
  el('config').onsubmit=e=>{e.preventDefault();if(!snapshot)return;const f=el('config').elements;
    const on=Number(f.wind_alarm_on.value),off=Number(f.wind_alarm_off.value),period=Number(f.report_period_s.value),version=snapshot.desired.config_version;
    if(!Number.isInteger(version)||!Number.isInteger(on)||!Number.isInteger(off)||off<=0||off>=on||on>10||![300,900].includes(period)){text('command','Cấu hình không hợp lệ.');return;}
    operate('rpc',{method:'set_config',params:{config_version:version+1,wind_alarm_on:on,wind_alarm_off:off,report_period_s:period}});};

  // History requests are independent of the live polling loop. A generation
  // invalidates in-flight results on station/range changes, including exports.
  let historyResult=null,historyRange=null,historyPage=0,historyGeneration=0;
  const hf=el('history-form'), hr=hf.elements;
  const hfields=fields.filter(f=>f[0]!=='wind_direction').concat([fields.find(f=>f[0]==='wind_direction')]);
  const localInput=ms=>new Date(ms+7*3600000).toISOString().slice(0,16);
  function parseLocal(v){return Date.parse(v+':00+07:00');}
  function preset(){if(hr.range.value==='custom')return;const end=Math.floor(Date.now()/60000)*60000;hr.end.value=localInput(end);hr.start.value=localInput(end-Number(hr.range.value)*86400000);}
  function historyReset(message){historyGeneration++;historyResult=null;historyRange=null;el('history-results').hidden=true;el('export').hidden=true;text('history-message',message||'');}
  function table(id,headers,rows){const t=el(id),head=node('thead'),body=node('tbody'),tr=node('tr');for(const h of headers)tr.append(node('th',h));head.append(tr);for(const row of rows){const r=node('tr');for(const v of row)r.append(node('td',v));body.append(r);}t.replaceChildren(head,body);}
  function rawPage(){if(!historyResult)return;const rows=historyResult.records,first=historyPage*50;
    table('history-rows',['Thời điểm đo','Mã bản ghi','Kỳ (phút)','Chất lượng',...hfields.map(f=>f[1]+' ('+f[2]+')')],
      rows.slice(first,first+50).map(r=>[time(r.ts),r.record_id,num(r.period_s/60),num(r.valid_mask),...hfields.map(f=>num(r.values[f[0]],f[3]))]));
    text('history-page',rows.length?((first+1)+'–'+Math.min(first+50,rows.length)+' / '+rows.length):'0 bản ghi');
    el('history-prev').disabled=historyPage===0;el('history-next').disabled=first+50>=rows.length;
  }
  function historyChart(target,label,unit,buckets,key,metric,dec){
    const article=node('article'),heading=node('h3',label+' · '+unit),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 360 160');svg.setAttribute('role','img');svg.setAttribute('aria-label',label);
    const add=(tag,a,t)=>{const n=document.createElementNS(svg.namespaceURI,tag);for(const [k,v] of Object.entries(a))n.setAttribute(k,String(v));if(t!==undefined)n.textContent=t;svg.append(n);return n;};
    const vals=buckets.map(b=>b.metrics[key][metric]),valid=vals.filter(v=>typeof v==='number'&&Number.isFinite(v));
    article.append(heading,svg);target.append(article);
    if(!valid.length){add('text',{x:180,y:80,'text-anchor':'middle',fill:'#52685e','font-size':12},'Không có kỳ hợp lệ');return;}
    const lo=key==='rainfall'?0:key==='wind_direction'?0:Math.min(...valid)-Math.max(0.1,(Math.max(...valid)-Math.min(...valid))*0.1);
    const hi=key==='wind_direction'?360:Math.max(...valid)+Math.max(0.1,(Math.max(...valid)-lo)*0.1);
    const x=i=>48+(i+.5)*300/buckets.length,y=v=>125-(v-lo)/(hi-lo)*108;
    for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3;add('line',{x1:48,x2:348,y1:y(v),y2:y(v),stroke:'#dce6df'});add('text',{x:43,y:y(v)+3,'text-anchor':'end','font-size':9,fill:'#52685e'},num(v,dec));}
    let prev=null;
    vals.forEach((v,i)=>{const b=buckets[i],s=b.metrics[key],complete=s.coverage_s*1000===b.endTs-b.startTs;
      if(v===null||!Number.isFinite(v)){prev=null;return;}
      const color=complete?'#176f53':'#a16c25';
      if(key==='rainfall'){const w=Math.max(1,300/buckets.length*.72);add('rect',{x:x(i)-w/2,y:y(v),width:w,height:Math.max(1,y(0)-y(v)),fill:color});}
      else{if(prev&&prev.complete&&complete&&key!=='wind_direction')add('line',{x1:x(i-1),y1:y(prev.v),x2:x(i),y2:y(v),stroke:color,'stroke-width':1.8});add('circle',{cx:x(i),cy:y(v),r:2.6,fill:color});}
      const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=time(b.endTs)+' · '+num(v,dec)+' '+unit+' · '+num(s.coverage_pct,1)+'%';svg.lastChild.append(title);prev={v,complete};
    });
    for(const i of [...new Set([0,Math.floor((buckets.length-1)/2),buckets.length-1])])add('text',{x:x(i),y:145,'text-anchor':'middle','font-size':8,fill:'#52685e'},new Intl.DateTimeFormat('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',...(historyResult.bucket_s===3600?{hour:'2-digit',minute:'2-digit'}:{day:'2-digit',month:'2-digit'})}).format(buckets[i].startTs));
  }
  function renderHistory(r){historyResult=r;historyPage=0;const s=r.summary,m=s.metrics;
    const cards=[['Bản ghi trong khoảng',num(r.records.length)],['Độ phủ bản ghi',num(s.coverage_pct,1)+'%'],['Đủ 7 nhóm hợp lệ',num(s.valid_coverage_pct,1)+'%'],['Tổng mưa đã ghi nhận',num(m.rainfall.total,4)+(m.rainfall.total===null?'':' mm')],['Gió giật cực đại',num(m.wind_gust.max,1)+(m.wind_gust.max===null?'':' m/s')],['Cảnh báo bắt đầu',num(r.alarm_count)]];
    const grid=el('history-summary');grid.replaceChildren();for(const [k,v] of cards){const a=node('article');a.className='fw-metric';a.append(node('span',k),node('strong',v));grid.append(a);}
    const target=el('history-charts');target.replaceChildren();
    for(const [k,label,unit,dec] of hfields)historyChart(target,label,unit,r.buckets,k,k==='rainfall'?'total':k==='wind_gust'?'max':k==='wind_direction'?'vector_deg':k==='battery_voltage'?'last':'avg',dec);
    text('history-bucket',(r.bucket_s===3600?'Theo giờ':'Theo ngày')+' · xanh: đủ kỳ hợp lệ; vàng: thiếu một phần; khoảng trống: không có dữ liệu.');
    table('history-stats',['Thông số','Min','Max','Trung bình / tổng / hướng','Cuối kỳ','Độ phủ hợp lệ'],hfields.map(([k,label,unit,d])=>{const a=m[k];return [label+' ('+unit+')',num(a.min,d),num(a.max,d),num(k==='rainfall'?a.total:k==='wind_direction'?a.vector_deg:a.avg,d),num(a.last,d),num(a.coverage_pct,1)+'%'];}));
    text('history-gaps',s.gaps.length+' khoảng thiếu · '+s.boundary_periods+' kỳ cắt ranh giới · '+s.overlapping_periods+' kỳ chồng lấn bị loại. '+s.gaps.slice(0,5).map(g=>time(g.startTs)+' → '+time(g.endTs)).join(' | ')+(s.gaps.length>5?' …':''));
    rawPage();el('history-results').hidden=false;el('export').hidden=!snapshot||!snapshot.capabilities.includes('export');
  }
  async function loadHistory(){const start=parseLocal(hr.start.value),end=parseLocal(hr.end.value);
    historyReset('Đang truy vấn lịch sử…');const g=historyGeneration,station=el('station').value;
    if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end||end-start>31*86400000){text('history-message','Khoảng thời gian không hợp lệ (tối đa 31 ngày).');return;}
    try{const r=await call(station,'history',{startTs:start,endTs:end});if(dead||g!==historyGeneration)return;historyRange={startTs:start,endTs:end};renderHistory(r);text('history-message','Đã tải · dữ liệu theo thời điểm đo. Làm mới để nhận bản ghi gửi bù.');}
    catch(e){if(!dead&&g===historyGeneration)historyReset('Không thể tải lịch sử. Kiểm tra kết nối hoặc quyền truy cập.');}
  }
  async function downloadHistory(){if(!historyRange||!snapshot||!snapshot.capabilities.includes('export'))return;
    const g=historyGeneration,station=el('station').value;el('export').disabled=true;
    try{const r=await call(station,'export',historyRange);if(dead||g!==historyGeneration)return;
      const url=URL.createObjectURL(new Blob(['\ufeff',r.csv],{type:'text/csv;charset=utf-8'})),a=node('a');a.href=url;a.download='farm-weather-'+historyRange.startTs+'-'+historyRange.endTs+'.csv';root.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);text('history-message','Đã xuất '+r.record_count+' bản ghi gốc.');
    }catch(e){if(!dead&&g===historyGeneration)text('history-message','Không thể xuất CSV. Kiểm tra quyền và kết nối.');}
    finally{if(!dead)el('export').disabled=false;}
  }
  function historyTab(show){el('overview-view').hidden=show;el('history-view').hidden=!show;el('overview-tab').setAttribute('aria-pressed',String(!show));el('history-tab').setAttribute('aria-pressed',String(show));if(show&&!historyResult)loadHistory();}
  preset();hr.range.onchange=()=>{preset();historyReset('Chọn Xem thống kê để tải khoảng mới.');};
  for(const i of [hr.start,hr.end])i.onchange=()=>{hr.range.value='custom';historyReset('Chọn Xem thống kê để tải khoảng mới.');};
  hf.onsubmit=e=>{e.preventDefault();loadHistory();};
  el('overview-tab').onclick=()=>historyTab(false);el('history-tab').onclick=()=>historyTab(true);
  el('export').onclick=downloadHistory;el('history-prev').onclick=()=>{if(historyPage>0){historyPage--;rawPage();}};
  el('history-next').onclick=()=>{if(historyResult&&(historyPage+1)*50<historyResult.records.length){historyPage++;rawPage();}};

  timer=setInterval(refresh,10000);refresh();
  self.cleanup=()=>{dead=true;clearInterval(timer);for(const s of subs)s.unsubscribe();subs.clear();};
};
self.onDestroy=function(){if(self.cleanup)self.cleanup();};
