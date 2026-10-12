(function(){
 'use strict';
 if(window.__ycheCompactNavigation)return;window.__ycheCompactNavigation=true;
 var panel,main,bar,links=[],headings=[],frame=0,native,nativeAvailable=false,nativeCollapsed=false,desktop=window.matchMedia('(min-width:801px)');
 function syncPanel(){
  var visible=nativeAvailable?!nativeCollapsed:!panel.hidden;
  document.body.classList.toggle('yche-compact-reserved',desktop.matches&&visible&&(!native||native.dataset.ycheNativeToc==='pure'));
  var button=bar.querySelector('.yche-toc-button');if(button){button.setAttribute('aria-controls',nativeAvailable?native.id:panel.id);button.setAttribute('aria-expanded',String(visible));}
 }
 function nativeMode(){
  nativeAvailable=!!native&&desktop.matches;
  if(nativeAvailable&&native.dataset.ycheNativeToc!=='pure'){
   var rect=native.getBoundingClientRect();nativeAvailable=rect.width>0&&rect.right>0&&rect.left<innerWidth&&getComputedStyle(native).visibility!=='hidden';
  }
  document.body.classList.toggle('yche-compact-native-active',nativeAvailable);
  document.body.classList.toggle('yche-compact-native-collapsed',nativeAvailable&&nativeCollapsed);
  if(nativeAvailable)panel.hidden=true;
  syncPanel();
 }
 function resizeContent(){requestAnimationFrame(function(){window.dispatchEvent(new Event('resize'));});}
 function toggleDirectory(event){
  var button=event.target.closest&&event.target.closest('.yche-toc-button');if(!button)return;
  event.preventDefault();event.stopImmediatePropagation();
  if(nativeAvailable){
   if(native.dataset.ycheNativeToc==='pure'){nativeCollapsed=!nativeCollapsed;nativeMode();}
   if(!nativeCollapsed){var link=native.querySelector('a[href^="#"]');if(link)link.focus({preventScroll:true});}
  }else{
   panel.hidden=!panel.hidden;syncPanel();if(!panel.hidden){var first=panel.querySelector('.yche-chapters a')||panel.querySelector('button');if(first)first.focus({preventScroll:true});}
  }
  resizeContent();
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
  nativeMode();scheduleActive();
 }
 function init(){
  panel=document.getElementById('yche-reader-panel');bar=document.getElementById('yche-reader-bar');
  if(!panel||!bar||!document.querySelector('.yche-fixed-header'))return false;
  native=Array.from(document.querySelectorAll('[data-yche-native-toc],.report-nav')).find(function(el){
   return Array.from(el.querySelectorAll('a[href^="#"]')).filter(function(a){try{return !!document.getElementById(decodeURIComponent(a.hash.slice(1)));}catch(_){return false;}}).length>=2;
  });
  main=document.querySelector('main')||document.querySelector('.main-content')||document.querySelector('article')||document.querySelector('.content-area,.main,.content,.container');
  document.body.style.setProperty('--yche-compact-original-left',getComputedStyle(document.body).paddingLeft);
  document.body.classList.add('yche-compact-page');
  if(main)main.classList.add('yche-compact-main');
  if(native){
   native.dataset.ycheNativeToc=Array.from(native.querySelectorAll('input,select,textarea,button,[role="button"]')).some(function(control){return !control.hidden&&!control.matches('[data-nav-toggle],.wr-hide,.te-hide-nav,.te-expand')&&getComputedStyle(control).display!=='none';})?'interactive':'pure';
   document.body.classList.add('yche-compact-native');
   if(native.dataset.ycheNativeToc==='pure'){
    native.setAttribute('data-yche-compact-directory','native');
    if(main){main.classList.add('yche-compact-native-main');var parent=native.parentElement;if(parent!==document.body&&parent.contains(main)&&/grid|flex/.test(getComputedStyle(parent).display))parent.classList.add('yche-compact-native-layout');}
   }
   if(!native.id)native.id='yche-native-chapters';
  }
  document.querySelectorAll('main h2,.content-area h2,.main h2,.content h2,article h2').forEach(function(h){
   var auto=h.querySelector(':scope > .yche-section-number'),first=Array.from(h.children).find(function(child){return child!==auto;});
   if(first&&first.tagName==='SPAN'&&/^\d+(?:\.\d+)*$/.test(first.textContent.trim())){
    if(auto)auto.remove();
    if(first.nextSibling&&(!first.nextSibling.textContent||!/^\s/.test(first.nextSibling.textContent)))first.after(document.createTextNode(' '));
   }
  });
  mode();labelLinks();
  var chapters=panel.querySelector('.yche-chapters');
  if(chapters)new MutationObserver(labelLinks).observe(chapters,{childList:true});
  document.addEventListener('click',toggleDirectory,true);
  panel.addEventListener('click',function(event){if(desktop.matches&&event.target.closest('a'))event.stopPropagation();},true);
  new MutationObserver(syncPanel).observe(panel,{attributes:true,attributeFilter:['hidden']});
  desktop.addEventListener('change',mode);window.addEventListener('scroll',scheduleActive,{passive:true});window.addEventListener('resize',function(){nativeMode();scheduleActive();},{passive:true});return true;
 }

 if(!/^(design|design-docs|perf|perf-reports|reliability|research|ops|codegraph|htmls|about|mooncake-ds-analysis|_archive|hermes|deployments|metrics|benchmarks)\//.test(location.pathname.replace(/^\//,'')))return;
 if(!init()){var observer=new MutationObserver(function(){if(init())observer.disconnect();});observer.observe(document.body||document.documentElement,{childList:true,subtree:true});}
})();
