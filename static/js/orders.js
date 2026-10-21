(() => {
  'use strict';
  const form = document.querySelector('[data-order-form]');
  if (!form) return;
  const menu = JSON.parse(document.getElementById('order-menu').textContent);
  const byId = new Map(menu.map(item => [item.id, item]));
  const initial = JSON.parse(document.getElementById('order-initial').textContent);
  const cart = form.querySelector('[data-cart]');
  const error = form.querySelector('[data-cart-error]');
  const submit = form.querySelector('[data-submit-order]');
  const submitText = submit.textContent;
  const money = value => `${value.toLocaleString('ko-KR')}원`;
  const create = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  let lines = [], nextId = 0, dirty = false, submitting = false;
  const tabs = [...form.querySelectorAll('[data-category-tab]')];
  function selectCategory(tab) {
    tabs.forEach(button => {
      const active = button === tab;
      button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
      document.getElementById(button.getAttribute('aria-controls')).hidden = !active;
    });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectCategory(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
      else if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
      else if (event.key === 'Home') next = tabs[0];
      else if (event.key === 'End') next = tabs.at(-1);
      if (next) { event.preventDefault(); selectCategory(next); next.focus(); }
    });
  });
  function defaults(item) {
    return Object.fromEntries(item.groups.map(group => [String(group.id), group.choices.filter(choice => choice.is_default).map(choice => choice.id)]));
  }
  function unitPrice(line) {
    const item = byId.get(line.menu_id);
    return item.price + item.groups.reduce((sum, group) => sum + group.choices.filter(choice => line.selections[String(group.id)]?.includes(choice.id)).reduce((cost, choice) => cost + choice.extra_price * (group.mode === 'multiple' ? line.option_quantities[String(group.id)][String(choice.id)] ?? 0 : 1), 0), 0);
  }
  function sync() {
    form.querySelector('#id_items').value = JSON.stringify(lines.map(({menu_id, quantity, selections, option_quantities}) => ({
      menu_id, quantity, selections,
      option_quantities:Object.fromEntries(Object.entries(option_quantities).map(([group, counts]) => [group,Object.fromEntries(Object.entries(counts).filter(([choice, count]) => selections[group].includes(Number(choice)) && count !== 1))]).filter(([,counts]) => Object.keys(counts).length)),
    })));
    form.querySelector('[data-cart-empty]').hidden = lines.length > 0;
    let quantity = 0, total = 0;
    for (const line of lines) {
      const count = Number.isInteger(line.quantity) && line.quantity > 0 ? line.quantity : 0;
      quantity += count; total += unitPrice(line) * count;
      cart.querySelector(`[data-line="${line.key}"] [data-line-total]`).textContent = money(unitPrice(line) * count);
    }
    form.querySelector('[data-total-quantity]').textContent = `총 ${quantity}잔`;
    form.querySelector('[data-total-amount]').textContent = money(total);
    submit.disabled = lines.length === 0 || submitting;
  }
  function change() { dirty = true; error.hidden = true; sync(); }
  function renderLine(line) {
    const item = byId.get(line.menu_id);
    const article = create('article', undefined, 'cart-line'); article.dataset.line = line.key;
    const heading = create('div', undefined, 'cart-heading');
    heading.appendChild(create('h3', item.name));
    const quantityLabel = create('label', '음료', 'cart-drink-quantity');
    const quantity = create('input'); quantity.type = 'number'; quantity.min = '1'; quantity.max = '99'; quantity.required = true; quantity.step = '1'; quantity.inputMode = 'numeric'; quantity.value = line.quantity;
    quantity.setAttribute('aria-label', `${item.name} 수량`);
    quantity.addEventListener('input', () => { line.quantity = quantity.value === '' ? null : Number(quantity.value); change(); });
    quantityLabel.appendChild(quantity); heading.appendChild(quantityLabel);
    const remove = create('button', '빼기', 'text-button'); remove.type = 'button';
    remove.setAttribute('aria-label', `${item.name} 빼기`);
    remove.addEventListener('click', () => { lines = lines.filter(other => other !== line); article.remove(); change(); });
    heading.appendChild(remove); article.appendChild(heading);
    item.groups.forEach(group => {
      const fieldset = create('fieldset', undefined, 'cart-options');
      fieldset.appendChild(create('legend', `${group.name}${group.required ? ' · 필수' : ' · 선택'}`));
      const choices = group.mode === 'single' && !group.required ? [{id:null,name:'선택 안함',extra_price:0}, ...group.choices] : group.choices;
      choices.forEach(choice => {
        const row = create('div', undefined, 'cart-choice-row');
        const label = create('label', undefined, 'cart-choice');
        const input = create('input'); input.type = group.mode === 'single' ? 'radio' : 'checkbox';
        input.name = `choice-${line.key}-${group.id}`; input.value = choice.id ?? '';
        input.checked = choice.id === null ? line.selections[String(group.id)].length === 0 : line.selections[String(group.id)].includes(choice.id);
        if (group.mode === 'single' && group.required) input.required = true;
        let optionQuantity;
        input.addEventListener('change', () => {
          if (group.mode === 'single') line.selections[String(group.id)] = choice.id === null ? [] : [choice.id];
          else line.selections[String(group.id)] = [...fieldset.querySelectorAll('input:checked')].map(node => Number(node.value));
          if (optionQuantity) optionQuantity.disabled = !input.checked;
          change();
        });
        label.append(input, create('span', `${choice.name}${choice.extra_price ? ` (+${money(choice.extra_price)})` : ''}`));
        row.appendChild(label);
        if (group.mode === 'multiple') {
          optionQuantity = create('input', undefined, 'option-quantity'); optionQuantity.type = 'number';
          optionQuantity.min = '1'; optionQuantity.max = '99'; optionQuantity.step = '1'; optionQuantity.required = true; optionQuantity.inputMode = 'numeric';
          optionQuantity.value = line.option_quantities[String(group.id)][String(choice.id)];
          optionQuantity.disabled = !input.checked;
          optionQuantity.setAttribute('aria-label', `${group.name} · ${choice.name} 수량`);
          optionQuantity.addEventListener('input', () => {
            line.option_quantities[String(group.id)][String(choice.id)] = optionQuantity.value === '' ? null : Number(optionQuantity.value); change();
          });
          row.appendChild(optionQuantity);
        }
        fieldset.appendChild(row);
      });
      article.appendChild(fieldset);
    });
    const bottom = create('div', undefined, 'cart-bottom');
    const amount = create('strong'); amount.dataset.lineTotal = ''; bottom.appendChild(amount);
    article.appendChild(bottom); return article;
  }
  function append(item, raw) {
    const selections = defaults(item);
    const option_quantities = {};
    for (const group of item.groups.filter(group => group.mode === 'multiple')) {
      option_quantities[String(group.id)] = Object.fromEntries(group.choices.map(choice => [String(choice.id), raw?.option_quantities?.[String(group.id)]?.[String(choice.id)] ?? 1]));
    }
    if (raw?.selections && typeof raw.selections === 'object') {
      for (const group of item.groups) {
        const ids = raw.selections[String(group.id)];
        if (Array.isArray(ids)) selections[String(group.id)] = ids.filter(id => group.choices.some(choice => choice.id === id));
      }
    }
    const line = {key:++nextId,menu_id:item.id,quantity:raw?.quantity ?? 1,selections,option_quantities};
    lines.push(line); const article = renderLine(line); cart.appendChild(article); sync(); return article;
  }
  if (Array.isArray(initial)) initial.slice(0,50).forEach(raw => {
    const item = byId.get(raw?.menu_id); if (item) append(item, raw);
  });
  form.querySelectorAll('[data-add-menu]').forEach(button => button.addEventListener('click', () => {
    if (lines.length >= 50) { error.textContent = '한 번에 50개 항목까지 담을 수 있어요.'; error.hidden = false; error.scrollIntoView({block:'center'}); return; }
    const article = append(byId.get(Number(button.dataset.addMenu))); change();
    article.scrollIntoView({block:'start'}); (article.querySelector('.cart-options input') || article.querySelector('input'))?.focus({preventScroll:true});
  }));
  form.querySelector('#id_nickname').addEventListener('input', () => { dirty = true; });
  form.addEventListener('submit', event => {
    if (submitting) { event.preventDefault(); return; }
    const missing = lines.find(line => byId.get(line.menu_id).groups.some(group => group.required && !line.selections[String(group.id)].length));
    if (!lines.length || missing) {
      event.preventDefault(); error.textContent = missing ? '필수 옵션을 골라 주세요.' : '메뉴를 하나 이상 담아 주세요.'; error.hidden = false; error.scrollIntoView({block:'center'}); return;
    }
    sync(); submitting = true; submit.disabled = true; submit.textContent = '저장 중…';
  });
  window.addEventListener('beforeunload', event => { if (dirty && !submitting) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { submitting = false; submit.textContent = submitText; sync(); });
  sync();
})();
