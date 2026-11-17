/** Keep sidebar content at its expanded width while the outer grid clips it during folding. */
(()=>{
  const motions=new WeakMap();
  /** Prepare a transition without changing the application's collapsed state or saved widths. */
  window.FlowCanvasSidebarMotion=function(container,pane,motionClass,collapsedClass,closing){
    motions.get(pane)?.();
    if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)return;
    const transition=container.style.transition;container.style.transition='none';
    let width=pane.getBoundingClientRect().width;
    if(!closing){
      const collapsed=container.classList.contains(collapsedClass);container.classList.remove(collapsedClass);
      width=pane.getBoundingClientRect().width;
      if(collapsed)container.classList.add(collapsedClass);
    }
    if(width>0){pane.style.setProperty('--sidebar-content-width',width+'px');pane.classList.add('sidebar-content-fixed');}
    // Commit the starting geometry before restoring transitions and applying the real state change.
    container.getBoundingClientRect();container.style.transition=transition;container.classList.add(motionClass);
    const timer=setTimeout(finish,500);
    /** Release temporary geometry after animation or before a rapid reverse toggle. */
    function finish(){clearTimeout(timer);container.classList.remove(motionClass);pane.classList.remove('sidebar-content-fixed');pane.style.removeProperty('--sidebar-content-width');motions.delete(pane);}
    motions.set(pane,finish);
  };
})();
