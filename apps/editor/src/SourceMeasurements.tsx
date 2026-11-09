import React from 'react';
import {Help} from './prototype/ui';
import {packageWeight,packageSize} from './factsHelpers';
export default function SourceMeasurements({snapshot,source,components=[]}:any){
 const hints=snapshot?.fact_hints||{},measurements=snapshot?.body?.measurements;
 const used=components.filter((r:any)=>(r.source_id===source.id||r.url===source.url)&&r.supplier_sku_id).map((r:any)=>r.supplier_sku_id);
 const ids=new Set(used.length?used:(source.selected_skus||[]).map((r:any)=>r.sku_id));
 const variants=(snapshot?.body?.variants||[]).filter((v:any)=>!ids.size||ids.has(v.sku_id));
 return <section className="source-measurements" aria-label="货源重量与尺寸">
  <div className="measurement-heading"><strong>重量与尺寸</strong><Help text="产品属性与包装信息分开显示。包装数据仅作物流参考，不自动成为本品尺寸或文案依据；缺值和未注明的尺寸单位需按货源核实。"/></div>
  <div className="measurement-overview"><div><span>产品重量 / 净重</span><strong>{hints.weight||'未提供'}</strong></div><div><span>产品尺寸</span><strong>{hints.dimensions||'未提供'}</strong></div>{hints.unspecified_weight&&<div><span>货源属性重量（口径未注明）</span><strong>{hints.unspecified_weight}</strong></div>}{hints.unspecified_dimensions&&<div><span>货源属性尺寸（口径未注明）</span><strong>{hints.unspecified_dimensions}</strong></div>}</div>
  {!measurements?<p className="subtext">此条历史采集尚无包装数据，可重新解析商品更新。</p>:<><div className="measurement-heading"><span>{ids.size?'已选规格的包装信息':'货源规格包装信息（尚未选定SKU）'}</span><small>长 × 宽 × 高</small></div><div className="measurement-table-scroll"><table><thead><tr><th>货源规格</th><th>包装重量</th><th>包装尺寸</th></tr></thead><tbody>{variants.map((v:any)=><tr key={v.sku_id}><td title={v.specification}>{v.specification}</td><td>{packageWeight(measurements.package_by_sku?.[v.sku_id])}</td><td>{packageSize(measurements.package_by_sku?.[v.sku_id])}</td></tr>)}</tbody></table></div>{measurements.default_shipping_weight_kg>0&&<p className="subtext">货源默认物流件重（未指定SKU）：{packageWeight({weight_kg:measurements.default_shipping_weight_kg})}</p>}</>}
 </section>
}
