'use strict';
window.BackupCenterI18n=(()=>{
  const messages=window.BC_MESSAGES||{};
  let language='fr';
  try{language=localStorage.getItem('backupcenter-language')||(navigator.language?.toLowerCase().startsWith('fr')?'fr':'en');}catch{}
  if(!['fr','en'].includes(language))language='fr';
  const t=source=>language==='en'?(messages[source]??source):source;
  const replacements=Object.keys(messages).filter(k=>messages[k]!==k&&k.length>=4).sort((a,b)=>b.length-a.length);
  const expression=new RegExp(replacements.map(s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|'),'g');
  const fragment=source=>language==='en'?source.replace(expression,m=>messages[m]):source;
  const html=(strings,...values)=>strings.map((part,i)=>fragment(part)+(i<values.length?values[i]:'')).join('');
  const initialize=()=>{
    document.documentElement.lang=language;
    document.title=t('BackupCenter · Vos sites, à l’abri');
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
    for(const node of nodes){
      if(node.parentElement.closest('script,style,textarea,code,pre,[translate="no"]'))continue;
      const trimmed=node.nodeValue.trim();
      if(messages[trimmed])node.nodeValue=node.nodeValue.replace(trimmed,t(trimmed));
    }
    for(const attr of ['placeholder','aria-label'])for(const el of document.querySelectorAll('['+attr+']'))el.setAttribute(attr,t(el.getAttribute(attr)));
    for(const select of document.querySelectorAll('[data-language-select]')){
      select.value=language;
      select.addEventListener('change',()=>{
        if(document.querySelector('dialog[open]')){select.value=language;window.dispatchEvent(new CustomEvent('bc-language-blocked'));return;}
        try{localStorage.setItem('backupcenter-language',select.value);}catch{}
        location.reload();
      });
    }
  };
  initialize();
  return {t,html,markup:fragment,language,locale:language==='en'?'en-CA':'fr-CA'};
})();
