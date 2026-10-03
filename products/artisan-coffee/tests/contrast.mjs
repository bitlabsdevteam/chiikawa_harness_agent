// Computed text and focus-ring checks. SVG art and disabled controls are exempt.
const helpers = `
 const rgb=value=>value.match(/[\\d.]+/g)?.map(Number);
 const composite=(front,back)=>{const a=front[3]??1;return front.slice(0,3).map((value,i)=>value*a+back[i]*(1-a));};
 const background=element=>{const layers=[];for(let el=element;el;el=el.parentElement){const value=rgb(getComputedStyle(el).backgroundColor);if(value)layers.unshift(value);}return layers.reduce((back,front)=>composite(front,back),[255,255,255]);};
 const luminance=values=>values.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
 const ratio=(a,b)=>(Math.max(luminance(a),luminance(b))+.05)/(Math.min(luminance(a),luminance(b))+.05);
`;
export const contrastExpression = `(() => {
 ${helpers}
 const failures=[],samples=[];
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let node;
 while(node=walker.nextNode()) {
  const el=node.parentElement;
  if(!node.textContent.trim()||el.closest('svg,script,style,noscript,.sr-only,[hidden],:disabled')||!el.checkVisibility())continue;
  const css=getComputedStyle(el),bg=background(el),fg=composite(rgb(css.color),bg);
  const contrast=ratio(fg,bg);
  const large=parseFloat(css.fontSize)>=24||(parseFloat(css.fontSize)>=18.66&&parseInt(css.fontWeight)>=700);
  const sample={text:node.textContent.trim().slice(0,70),ratio:+contrast.toFixed(2),required:large?3:4.5,color:css.color,background:bg.join(',')};
  samples.push(sample);if(contrast+.001<sample.required)failures.push(sample);
 }
 return {checked:samples.length,minimum:Math.min(...samples.map(x=>x.ratio)),failures};
})()`;
export const focusContrastExpression = `(() => {
 ${helpers}
 const samples=[];
 for(const selector of ['#billing-monthly','#billing-annual','[data-select-tier="drift"]','[data-select-tier="daily"]','[data-select-tier="house"]']) {
  const el=document.querySelector(selector);el.focus();const css=getComputedStyle(el);
  samples.push({selector,ratio:+ratio(rgb(css.outlineColor),background(el.parentElement)).toFixed(2),width:parseFloat(css.outlineWidth),style:css.outlineStyle});
 }
 return samples;
})()`;
