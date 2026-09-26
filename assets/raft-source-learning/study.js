(() => {
  const article = document.querySelector('.raft-study');
  if (!article) return;

  for (const widget of article.querySelectorAll('[data-raft-stepper]')) {
    const panels = [...widget.querySelectorAll('.raft-step-panel')];
    const buttons = [...widget.querySelectorAll('[data-step]')];
    const previous = widget.querySelector('[data-step-prev]');
    const next = widget.querySelector('[data-step-next]');
    let current = 0;
    function show(index) {
      current = Math.max(0, Math.min(index, panels.length - 1));
      panels.forEach((panel, i) => { panel.hidden = i !== current; });
      buttons.forEach((button, i) => button.setAttribute('aria-pressed', String(i === current)));
      widget.querySelector('[data-step-count]').textContent = `${String(current + 1).padStart(2, '0')} / 06`;
      previous.disabled = current === 0;
      next.disabled = current === panels.length - 1;
    }
    buttons.forEach((button, i) => {
      button.addEventListener('click', () => show(i));
      button.addEventListener('keydown', (event) => {
        let target;
        if (event.key === 'ArrowRight') target = (i + 1) % buttons.length;
        if (event.key === 'ArrowLeft') target = (i + buttons.length - 1) % buttons.length;
        if (event.key === 'Home') target = 0;
        if (event.key === 'End') target = buttons.length - 1;
        if (target === undefined) return;
        event.preventDefault();
        show(target);
        buttons[target].focus();
      });
    });
    previous.addEventListener('click', () => show(current - 1));
    next.addEventListener('click', () => show(current + 1));
    show(0);
    widget.querySelector('.raft-step-buttons').hidden = false;
    widget.querySelector('.raft-step-footer').hidden = false;
  }

  for (const button of article.querySelectorAll('[data-copy-link]')) {
    const group = button.closest('.raft-share');
    const status = group.querySelector('.raft-share-status');
    const fallback = group.querySelector('.raft-share-fallback');
    button.hidden = false;
    button.addEventListener('click', async () => {
      try {
        if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(button.dataset.copyLink);
        fallback.hidden = true;
        status.textContent = '链接已复制';
      } catch {
        fallback.hidden = false;
        const input = fallback.querySelector('input');
        input.focus();
        input.select();
        status.textContent = '请复制下方链接';
      }
    });
  }

  const toc = article.querySelector('.raft-toc');
  const narrow = window.matchMedia('(max-width: 960px)');
  if (narrow.matches) toc.open = false;
  narrow.addEventListener('change', (event) => { toc.open = !event.matches; });
  const navLinks = [...article.querySelectorAll('.raft-toc nav a')];
  const sections = [...article.querySelectorAll('.raft-content > h2[id]')];
  const progress = article.querySelector('.raft-progress span');
  let scheduled = false;
  function updateReadingPosition() {
    scheduled = false;
    const content = article.querySelector('.raft-content');
    const rect = content.getBoundingClientRect();
    const distance = Math.max(1, content.offsetHeight - window.innerHeight);
    progress.style.width = `${Math.min(100, Math.max(0, -rect.top / distance * 100))}%`;
    let active = sections[0]?.id;
    sections.forEach((section) => { if (section.getBoundingClientRect().top <= 150) active = section.id; });
    navLinks.forEach((link) => {
      if (decodeURIComponent(link.hash.slice(1)) === active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }
  function queueUpdate() {
    if (!scheduled) { scheduled = true; requestAnimationFrame(updateReadingPosition); }
  }
  window.addEventListener('scroll', queueUpdate, { passive: true });
  window.addEventListener('resize', queueUpdate, { passive: true });
  updateReadingPosition();
})();
