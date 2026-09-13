(() => {
  const page = document.querySelector('.feed-page');
  if (!page) return;
  const list = document.querySelector('#feed-list');
  const status = document.querySelector('#feed-status');
  const empty = document.querySelector('#feed-empty');
  const more = document.querySelector('#feed-more');
  const reset = document.querySelector('#feed-reset');
  const from = document.querySelector('#feed-from');
  const to = document.querySelector('#feed-to');
  const target = document.querySelector('#feed-target');
  const importance = document.querySelector('#feed-importance');
  const pageSize = 30;
  let visible = pageSize;
  let items = [];
  let archives = [];
  let archivesLoaded = false;

  const text = (tag, value, className) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value || '';
    return node;
  };
  const safeUrl = value => {
    try {
      const url = new URL(value);
      return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
    } catch (_) { return ''; }
  };
  const dateLabel = value => new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value));

  function detailSection(title, value) {
    if (!value || (Array.isArray(value) && !value.length)) return null;
    const section = document.createElement('section');
    section.append(text('h3', title));
    if (Array.isArray(value)) {
      const ul = document.createElement('ul');
      value.forEach(entry => ul.append(text('li', typeof entry === 'string' ? entry : entry.claim)));
      section.append(ul);
    } else section.append(text('p', value));
    return section;
  }

  function card(item) {
    const article = document.createElement('article');
    article.className = `feed-card importance-${item.importance}`;
    article.dataset.url = safeUrl(item.url);
    article.tabIndex = 0;
    const head = document.createElement('header');
    const meta = document.createElement('div');
    meta.className = 'feed-card-meta';
    meta.append(text('span', dateLabel(item.published_at)));
    const sourceCount = item.sources?.length || 1;
    meta.append(text('span', `${item.target_name} / ${item.channel_name}${sourceCount > 1 ? ` · ${sourceCount} 个来源` : ''}`));
    const score = text('span', `重要性 ${item.importance}/5`, 'feed-score');
    score.title = '综合影响、可信度与可行动性评分；1 低，5 高';
    meta.append(score);
    const link = document.createElement('a');
    link.href = article.dataset.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = item.headline;
    head.append(meta, link);
    article.append(head, text('p', item.summary, 'feed-summary'));

    if (item.topics?.length) {
      const topics = document.createElement('div');
      topics.className = 'feed-topics';
      item.topics.forEach(value => topics.append(text('span', value)));
      article.append(topics);
    }
    const details = document.createElement('details');
    details.className = 'feed-analysis';
    details.append(text('summary', '展开分析'));
    [
      detailSection('具体变化', item.key_change),
      detailSection('为什么值得看', item.why_it_matters),
      detailSection('可以怎么用', item.company_impact),
      detailSection('后续观察', item.watch_next),
    ].filter(Boolean).forEach(section => details.append(section));
    if (item.evidence?.length) {
      const section = document.createElement('section');
      section.append(text('h3', '证据'));
      const links = document.createElement('ul');
      item.evidence.forEach(entry => {
        const href = safeUrl(entry.url);
        if (!href) return;
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.textContent = entry.claim || '查看来源';
        li.append(a); links.append(li);
      });
      section.append(links); details.append(section);
    }
    if (item.sources?.length) {
      const section = document.createElement('section');
      section.append(text('h3', '数据来源'));
      const links = document.createElement('ul');
      item.sources.forEach(entry => {
        const href = safeUrl(entry.url);
        if (!href) return;
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.textContent = `${entry.target_name} / ${entry.channel_name}`;
        li.append(a); links.append(li);
      });
      section.append(links); details.append(section);
    }
    article.append(details);
    const open = event => {
      if (!article.dataset.url || event.target.closest('a,button,summary,details,input,select')) return;
      window.open(article.dataset.url, '_blank', 'noopener');
    };
    article.addEventListener('click', open);
    article.addEventListener('keydown', event => {
      if ((event.key === 'Enter' || event.key === ' ') && event.target === article) {
        event.preventDefault(); window.open(article.dataset.url, '_blank', 'noopener');
      }
    });
    return article;
  }

  async function loadArchivesIfNeeded() {
    if (archivesLoaded || !from.value || !archives.length || !items.length) return;
    const oldest = items.at(-1).published_at.slice(0, 10);
    if (from.value >= oldest) return;
    const base = page.dataset.feedArchive;
    const pages = await Promise.all(archives.map(month => fetch(`${base}${month}.json`).then(response => {
      if (!response.ok) throw new Error(`archive ${month}`);
      return response.json();
    })));
    pages.forEach(data => items.push(...(data.items || [])));
    items.sort((a, b) => b.published_at.localeCompare(a.published_at) || b.id.localeCompare(a.id));
    archivesLoaded = true;
  }

  function filteredItems() {
    const minimum = Number(importance.value || 2);
    return items.filter(item => {
      const day = item.published_at.slice(0, 10);
      return item.importance >= minimum && (!target.value || item.target_name === target.value)
        && (!from.value || day >= from.value) && (!to.value || day <= to.value);
    });
  }

  function render() {
    const selected = filteredItems();
    list.replaceChildren(...selected.slice(0, visible).map(card));
    list.setAttribute('aria-busy', 'false');
    empty.hidden = selected.length !== 0;
    more.hidden = selected.length <= visible;
    status.textContent = `显示 ${Math.min(visible, selected.length)} 条，共 ${selected.length} 条 · 从新到旧`;
  }

  async function applyFilters() {
    visible = pageSize;
    status.textContent = '正在筛选…';
    try { await loadArchivesIfNeeded(); } catch (_) { status.textContent = '历史归档加载失败，请稍后重试。'; }
    render();
  }

  [from, to, target, importance].forEach(control => control.addEventListener('change', applyFilters));
  more.addEventListener('click', () => { visible += pageSize; render(); });
  reset.addEventListener('click', () => {
    from.value = ''; to.value = ''; target.value = ''; importance.value = '2'; applyFilters();
  });

  fetch(page.dataset.feedIndex)
    .then(response => { if (!response.ok) throw new Error('feed'); return response.json(); })
    .then(data => {
      items = data.items || []; archives = data.archives || [];
      (data.targets || []).forEach(name => {
        const option = document.createElement('option'); option.value = name; option.textContent = name; target.append(option);
      });
      render();
    })
    .catch(() => {
      list.setAttribute('aria-busy', 'false'); status.textContent = '信息流暂时无法加载，请稍后重试。';
    });
})();
