/* Progressive enhancement: every configured source remains visible without JS. */
(() => {
  const form = document.querySelector('.sources-filters');
  if (!form) return;
  const search = document.getElementById('source-search');
  const status = document.getElementById('source-status');
  const results = document.getElementById('source-results');
  const targets = [...document.querySelectorAll('.source-target')];
  function filter() {
    const terms = search.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let count = 0;
    let targetCount = 0;
    for (const target of targets) {
      let visible = 0;
      for (const channel of target.querySelectorAll('.source-channel')) {
        const haystack = `${target.dataset.search} ${channel.dataset.search}`.toLocaleLowerCase();
        const matches = terms.every(term => haystack.includes(term)) &&
          (status.value === 'all' || (channel.dataset.enabled === 'true') === (status.value === 'enabled'));
        channel.hidden = !matches;
        if (matches) visible++;
      }
      target.hidden = !visible;
      if (visible) targetCount++;
      count += visible;
    }
    results.textContent = results.dataset.zh === 'true'
      ? `显示 ${targetCount} 个目标 · ${count} 个频道`
      : `Showing ${targetCount} targets · ${count} channels`;
    document.querySelector('.sources-empty').hidden = count > 0;
  }
  form.hidden = false;
  form.addEventListener('submit', event => event.preventDefault());
  search.addEventListener('input', filter);
  status.addEventListener('change', filter);
  filter();
})();
