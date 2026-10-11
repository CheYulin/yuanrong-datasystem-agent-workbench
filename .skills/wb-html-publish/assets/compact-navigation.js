(function(){
 'use strict';
 if(window.__ycheCompactNavigation)return;window.__ycheCompactNavigation=true;
 var panel,main,bar,links=[],headings=[],frame=0,native,nativeAvailable=false,nativeCollapsed=false,desktop=window.matchMedia('(min-width:801px)');
 function syncPanel(){
  document.body.classList.toggle('yche-compact-collapsed',desktop.matches&&panel.hidden);
  var button=bar.querySelector('.yche-toc-button');if(button)button.setAttribute('aria-expanded',String(!panel.hidden));
 }
 function nativeMode(){
  document.body.classList.remove('yche-compact-native-collapsed');
  nativeAvailable=!!native.getClientRects().length&&getComputedStyle(native).display!=='none';
  document.body.classList.toggle('yche-compact-native-active',nativeAvailable);
  document.body.classList.toggle('yche-compact-native-collapsed',nativeAvailable&&nativeCollapsed);
  var shared=document.getElementById('yche-reader-panel'),button=document.querySelector('.yche-toc-button');
  if(nativeAvailable&&shared)shared.hidden=true;
  if(button){button.setAttribute('aria-controls',nativeAvailable?native.id:'yche-reader-panel');button.setAttribute('aria-expanded',String(nativeAvailable?!nativeCollapsed:!!shared&&!shared.hidden));}
 }
 function resizeContent(){requestAnimationFrame(function(){window.dispatchEvent(new Event('resize'));});}
 function toggleDirectory(event){
  var button=event.target.closest&&event.target.closest('.yche-toc-button');if(!button)return;
  event.preventDefault();event.stopImmediatePropagation();
  if(native&&nativeAvailable){nativeCollapsed=!nativeCollapsed;nativeMode();if(!nativeCollapsed){var link=native.querySelector('a[href^="#"]');if(link)link.focus({preventScroll:true});}resizeContent();return;}
  var current=document.getElementById('yche-reader-panel');if(!current)return;current.hidden=!current.hidden;button.setAttribute('aria-expanded',String(!current.hidden));if(!current.hidden){var first=current.querySelector('.yche-chapters a')||current.querySelector('button');if(first)first.focus({preventScroll:true});}resizeContent();
 }
 function updateActive(){
  frame=0;var threshold=(document.querySelector('.yche-fixed-header')||bar).getBoundingClientRect().bottom+24,index=0;
  headings.forEach(function(h,i){if(h&&h.getBoundingClientRect().top<=threshold)index=i;});
  links.forEach(function(a,i){if(i===index)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current');});
 }
 function scheduleActive(){if(!frame)frame=requestAnimationFrame(updateActive);}
 function labelLinks(){
  links=Array.from(panel.querySelectorAll('.yche-chapters a'));headings=[];
  links.forEach(function(a){
   if(!a.querySelector('.yche-compact-number')){
    var value=a.textContent.trim(),match=value.match(/^(\d+(?:\.\d+)*)(?:[.、\s]+)(.*)$/);
    if(match){var number=document.createElement('span');number.className='yche-compact-number';number.textContent=match[1];var title=document.createElement('span');title.textContent=match[2];a.replaceChildren(number,title);}
   }
   try{headings.push(document.getElementById(decodeURIComponent(a.hash.slice(1))));}catch(_){headings.push(null);}
  });scheduleActive();
 }
 function mode(){
  document.body.classList.toggle('yche-compact-pinned',desktop.matches);
  panel.hidden=!desktop.matches;
  syncPanel();
  scheduleActive();
 }
 function init(){
  panel=document.getElementById('yche-reader-panel');bar=document.getElementById('yche-reader-bar');
  native=document.querySelector('.report-nav,[data-yche-native-toc],[data-yche-sidebar]');
  if(native){
   document.body.classList.add('yche-compact-page','yche-compact-native');
   document.querySelectorAll('main h2,.content-area h2,.main h2,.content h2').forEach(function(h){
    var auto=h.querySelector(':scope > .yche-section-number'),first=Array.from(h.children).find(function(child){return child!==auto;});
    if(first&&first.tagName==='SPAN'&&/^\d+(?:\.\d+)*$/.test(first.textContent.trim())){
     if(auto)auto.remove();
     if(first.nextSibling&&(!first.nextSibling.textContent||!/^\s/.test(first.nextSibling.textContent)))first.after(document.createTextNode(' '));
    }
   });
   native.setAttribute('data-yche-compact-directory','native');
   if(!native.id)native.id='yche-native-chapters';
   document.addEventListener('click',toggleDirectory,true);
   nativeMode();window.addEventListener('resize',nativeMode,{passive:true});window.addEventListener('load',nativeMode,{once:true});return true;
  }
  if(!panel||!bar)return false;
  main=document.querySelector('main,.content-area,.main,.content');
  document.body.classList.add('yche-compact-page');
  if(!main||/flex|grid/.test(getComputedStyle(document.body).display))return true;
  main.classList.add('yche-compact-main');mode();labelLinks();
  new MutationObserver(function(records){if(records.some(function(r){return Array.from(r.addedNodes).some(function(n){return n.nodeType===1&&n.matches('a');});}))labelLinks();}).observe(panel.querySelector('.yche-chapters'),{childList:true});
  document.addEventListener('click',toggleDirectory,true);
  panel.addEventListener('click',function(event){if(desktop.matches&&event.target.closest('a'))event.stopPropagation();},true);
  new MutationObserver(syncPanel).observe(panel,{attributes:true,attributeFilter:['hidden']});
  desktop.addEventListener('change',mode);window.addEventListener('scroll',scheduleActive,{passive:true});window.addEventListener('resize',scheduleActive,{passive:true});return true;
 }
 if(!init()){var observer=new MutationObserver(function(){if(init())observer.disconnect();});observer.observe(document.body||document.documentElement,{childList:true,subtree:true});}
})();
