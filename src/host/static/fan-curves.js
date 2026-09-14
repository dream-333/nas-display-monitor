'use strict';
function curvePreset(name, source, kind) {
  const p = fanState.curve_presets[name];
  return {preset:name,source,points:p[kind || 'cpu'].map(p => [...p]),interpolation:'linear',hysteresis:3,step_up:p.step_up,step_down:p.step_down,min_percent:40,critical_temp:kind === 'board' ? 60 : 85};
}
function curveSvg(tag, attrs, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,v);
  if (text != null) node.textContent=text;
  return node;
}
function drawCurve(form) {
  const c=form._curve, svg=form.querySelector('.curve-chart'); if (!c) return;
  svg.replaceChildren(); const x=t=>42+t*4.1, y=p=>190-p*1.6;
  for (const n of [0,25,50,75,100]) {
    svg.append(curveSvg('line',{x1:42,y1:y(n),x2:452,y2:y(n),class:'curve-grid'}));
    svg.append(curveSvg('text',{x:32,y:y(n)+4,'text-anchor':'end',class:'curve-axis'},n));
    svg.append(curveSvg('line',{x1:x(n),y1:30,x2:x(n),y2:190,class:'curve-grid'}));
    svg.append(curveSvg('text',{x:x(n),y:212,'text-anchor':'middle',class:'curve-axis'},n));
  }
  svg.append(curveSvg('text',{x:42,y:16,class:'curve-axis'},'输出 %'));
  svg.append(curveSvg('text',{x:452,y:229,'text-anchor':'end',class:'curve-axis'},'温度 °C'));
  const pairs=[];
  for(let t=0;t<=100;t+=.5) {
    let speed=c.points[0][1];
    for(let i=1;i<c.points.length;i++) {
      const a=c.points[i-1],b=c.points[i];
      if(t<a[0]) break;
      if(t>=b[0]) {speed=b[1];continue;}
      speed=c.interpolation==='step'?a[1]:a[1]+(b[1]-a[1])*(t-a[0])/(b[0]-a[0]);break;
    }
    speed=t>=c.critical_temp?100:Math.max(c.min_percent,speed);
    pairs.push([x(t),y(speed)]);
  }
  const path=pairs.map((p,i)=>(i?'L':'M')+p.join(',')).join(' ');
  svg.append(curveSvg('path',{d:path+' L452,190 L42,190 Z',class:'curve-area'}));
  svg.append(curveSvg('path',{d:path,class:'curve-line'}));
  svg.append(curveSvg('line',{x1:x(c.critical_temp),y1:30,x2:x(c.critical_temp),y2:190,class:'curve-critical'}));
  c.points.forEach((p,i)=>svg.append(curveSvg('circle',{cx:x(p[0]),cy:y(p[1]),r:7,class:'curve-point','data-point':i})));
  const temp=form._curveTemp, speed=form._curveSpeed;
  if(Number.isFinite(temp)&&Number.isFinite(speed)) svg.append(curveSvg('circle',{cx:x(Math.max(0,Math.min(100,temp))),cy:y(speed),r:5,class:'curve-live'}));
}
function curveRows(form) {
  const list=form.querySelector('.curve-points');list.replaceChildren();
  form._curve.points.forEach((p,i)=>{
    const row=document.createElement('div');row.className='curve-point-row';
    const label=document.createElement('span');label.textContent=String(i+1).padStart(2,'0');row.append(label);
    for (let j=0;j<2;j++) {
      const input=document.createElement('input');input.type='number';input.min=0;input.max=100;input.step=j?1:.5;input.required=true;input.value=p[j];
      input.setAttribute('aria-label',`节点 ${i+1} ${j?'速度百分比':'温度摄氏度'}`);
      input.addEventListener('input',()=>{form._curve.points[i][j]=Number(input.value);curveEdited(form);drawCurve(form);});row.append(input);
    }
    const del=document.createElement('button');del.type='button';del.className='text-button';del.textContent='×';del.setAttribute('aria-label',`删除节点 ${i+1}`);del.disabled=form._curve.points.length<=2;
    del.addEventListener('click',()=>{form._curve.points.splice(i,1);form._curve.points.at(-1)[1]=100;curveEdited(form);curveRows(form);drawCurve(form);});row.append(del);list.append(row);
  });
  form.querySelector('.curve-add').disabled=form._curve.points.length>=8;
}
function curveEdited(form) {
  form.dataset.edited='1';form._curve.preset='custom';form.querySelector('.curve-preset').value='custom';
}
function fillCurve(form) {
  const c=form._curve;
  for(const key of ['preset','source','interpolation','hysteresis','step_up','step_down','min_percent','critical_temp']) {
    const el=form.querySelector('[data-curve="'+key+'"]');
    if(key==='source' && !Array.from(el.options).some(o=>o.value===c.source)) el.add(new Option('所选温度源当前不可用',c.source));
    el.value=c[key];
  }
  curveRows(form);drawCurve(form);
}
function createCurveEditor(form) {
  const field=document.createElement('fieldset');field.className='curve-editor';
  field.innerHTML='<div class="curve-heading"><span class="eyebrow">THERMAL CONTROL</span><span class="curve-live-text"></span></div><div class="form-grid"><label>运行模式<select class="curve-preset" data-curve="preset"><option value="silent">静音</option><option value="standard">标准</option><option value="performance">性能</option><option value="full">全速</option><option value="custom">自定义</option></select></label><label>跟随温度源<select data-curve="source"></select></label></div><svg class="curve-chart" viewBox="0 0 480 240" role="img" aria-label="温度与风扇输出曲线；可拖动节点，也可在下方输入数值"></svg><p class="hint">拖动节点调整，或展开下方精确输入。白点表示当前温度与输出，虚线表示全速保护温度。</p><details class="curve-details"><summary>曲线节点与响应设置</summary><div class="curve-point-row curve-point-label"><span>节点</span><span>温度 °C</span><span>输出 %</span><span></span></div><div class="curve-points"></div><button type="button" class="text-button curve-add">＋ 添加节点</button><div class="form-grid curve-advanced"><label>曲线方式<select data-curve="interpolation"><option value="linear">线性平滑</option><option value="step">阶梯</option></select></label><label>降温回差 °C<input type="number" data-curve="hysteresis" min="0" max="10" step="0.5" required></label><label>升速延迟 秒<input type="number" data-curve="step_up" min="0" max="10" step="1" required></label><label>降速延迟 秒<input type="number" data-curve="step_down" min="0" max="60" step="1" required></label><label>最低输出 %<input type="number" data-curve="min_percent" min="0" max="100" step="1" required></label><label>全速保护温度 °C<input type="number" data-curve="critical_temp" min="40" max="100" step="1" required></label></div></details><p class="hint">静音等预设是可修改的起点，并非主板厂商原厂参数。最低输出可设 0–100%，默认 40%；0% 表示允许停转。修改仅预览，点击应用后生效。</p>';
  form.insertBefore(field,form.querySelector('.secondary'));
  const source=field.querySelector('[data-curve=source]');
  field.addEventListener('change',e=>{
    const key=e.target.dataset.curve;if(!key)return;
    if(key==='preset' && e.target.value!=='custom') {
      const kind=(fanState.curve_sources||[]).find(s=>s.id===source.value)?.kind || 'cpu';
      form._curve=curvePreset(e.target.value,source.value,kind);form.dataset.edited='1';fillCurve(form);
    } else if(key==='source') {
      form._curve.source=source.value;
      if(form._curve.preset!=='custom') form._curve=curvePreset(form._curve.preset,source.value,(fanState.curve_sources||[]).find(s=>s.id===source.value)?.kind || 'cpu');
      form.dataset.edited='1';fillCurve(form);
    } else {
      form._curve[key]=['preset','interpolation'].includes(key)?e.target.value:Number(e.target.value);
      curveEdited(form);drawCurve(form);
    }
  });
  field.querySelector('.curve-add').addEventListener('click',()=>{
    const p=form._curve.points;if(p.length>=8)return;
    let index=0;for(let i=1;i<p.length-1;i++)if(p[i+1][0]-p[i][0]>p[index+1][0]-p[index][0])index=i;
    if(p[index+1][0]-p[index][0]<1)return;
    p.splice(index+1,0,[Math.round(p[index][0]+p[index+1][0])/2,Math.round((p[index][1]+p[index+1][1])/2)]);
    curveEdited(form);curveRows(form);drawCurve(form);
  });
  const svg=field.querySelector('svg');let dragging=null;
  svg.addEventListener('pointerdown',e=>{if(field.disabled)return;const dot=e.target.closest('[data-point]');if(!dot)return;dragging=Number(dot.dataset.point);svg.setPointerCapture(e.pointerId);e.preventDefault();});
  svg.addEventListener('pointermove',e=>{
    if(dragging===null||field.disabled)return;
    const point=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse());
    const pts=form._curve.points,i=dragging;
    pts[i][0]=Math.max(i?pts[i-1][0]+.5:0,Math.min(i<pts.length-1?pts[i+1][0]-.5:100,Math.round((point.x-42)/4.1*2)/2));
    pts[i][1]=i===pts.length-1?100:Math.max(i?pts[i-1][1]:0,Math.min(pts[i+1][1],Math.round((190-point.y)/1.6)));
    curveEdited(form);drawCurve(form);curveRows(form);
  });
  for(const event of ['pointerup','pointercancel','lostpointercapture']) svg.addEventListener(event,()=>{dragging=null;});
}
function renderCurveEditor(form,item,data) {
  const supported=!!data.curve_presets;
  const mode=form.querySelector('[data-field=mode]');
  if(supported&&!mode.querySelector('[value=curve]'))mode.add(new Option('温度曲线','curve'));
  const choice=mode.querySelector('[value=curve]');if(choice)choice.disabled=!item.can_curve;
  if(!supported)return;
  if(!form.querySelector('.curve-editor'))createCurveEditor(form);
  const field=form.querySelector('.curve-editor'),source=field.querySelector('[data-curve=source]');
  const signature=JSON.stringify((data.curve_sources||[]).map(s=>[s.id,s.label]));
  if(source.dataset.signature!==signature) {
    source.replaceChildren();for(const s of data.curve_sources||[])source.add(new Option(s.label,s.id));source.dataset.signature=signature;
    if(form._curve)fillCurve(form);
  }
  if(!form._curve || !form.dataset.edited) {form._curve=structuredClone(item.curve||curvePreset('standard','cpu:auto','cpu'));fillCurve(form);}
  form._curveTemp=item.curve_temperature;form._curveSpeed=item.speed_percent;
  field.querySelector('.curve-live-text').textContent=item.curve_active?`${temperature(item.curve_temperature)} · ${item.speed_percent ?? '--'}% · ${item.curve_state}`:'预览 · 点击应用后运行';
  const chosen=mode.value==='curve';field.hidden=!chosen;field.disabled=!chosen||fanBusy||!item.can_curve;
  drawCurve(form);
}
