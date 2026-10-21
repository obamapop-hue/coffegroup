(() => {
  'use strict';
  const root = document.querySelector('[data-sheet-app]');
  if (!root) return;
  const clone = value => JSON.parse(JSON.stringify(value));
  const uid = prefix => `${prefix}-${crypto.randomUUID()}`;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const columns = {
    rows: [
      {key:'name', label:'카페명'}, {key:'phone', label:'전화번호'},
      {key:'address', label:'주소', css:'wide-cell'},
      {key:'seller_code', label:'판매자 연결코드', css:'code-cell'},
      {key:'seller_name', label:'판매자 닉네임', readonly:true},
      {key:'is_open', label:'영업상태', readonly:true},
    ],
    menus: [
      {key:'category', label:'분류'}, {key:'name', label:'메뉴명'},
      {key:'price', label:'가격(원)', money:true},
      {key:'is_active', label:'메뉴판 표시', check:true},
      {key:'is_available', label:'품절상태', readonly:true},
      {key:'option_summary', label:'옵션 · 기본값', readonly:true, css:'summary-cell'},
    ],
    options: [
      {key:'menu_key', label:'메뉴', select:'menus'},
      {key:'group_name', label:'옵션 그룹'},
      {key:'mode', label:'선택 방식', select:[['single','하나 선택'],['multiple','여러 개 선택']]},
      {key:'required', label:'필수 선택', check:true},
      {key:'name', label:'선택 항목'},
      {key:'extra_price', label:'추가금액(원)', money:true},
      {key:'is_default', label:'기본 선택', check:true},
    ],
  };

  // Excel-compatible TSV, including quoted tabs/newlines and doubled quotes.
  function parseTSV(source) {
    const result = []; let row = [], cell = '', quoted = false;
    const text = source.replace(/\r\n?/g, '\n');
    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      if (char === '"' && (quoted || cell === '')) {
        if (quoted && text[i + 1] === '"') { cell += '"'; i++; }
        else quoted = !quoted;
      } else if (!quoted && (char === '\t' || char === '\n')) {
        row.push(cell); cell = '';
        if (char === '\n') { result.push(row); row = []; }
      } else cell += char;
    }
    row.push(cell); result.push(row);
    while (result.length && result.at(-1).every(value => value === '')) result.pop();
    return result;
  }

  class Sheet {
    constructor() {
      this.load(JSON.parse(document.getElementById('sheet-initial-data').textContent));
      this.active = root.dataset.kind === 'cafes' ? 'rows' : 'menus';
      this.filters = {rows:{search:'',status:'all'}, menus:{search:'',status:'all'}, options:{search:'',status:'all',menu:''}};
      this.errors = []; this.saving = false; this.sellerCache = new Map();
      this.bind(); this.render();
    }
    $(selector) { return root.querySelector(selector); }
    load(data) { this.data = clone(data); this.original = clone(data); }
    list(sheet = this.active) { return this.data[sheet]; }
    fields(sheet = this.active) { return columns[sheet].filter(col => !col.readonly); }
    blank(row, sheet = this.active) {
      const keys = sheet === 'rows' ? ['name','phone','address','seller_code'] : sheet === 'menus' ? ['name','category','price'] : ['name','group_name'];
      if (sheet === 'options' && (!['','0'].includes(String(row.extra_price ?? '').replace(/[,\s원₩]/g,'')) || row.is_default || row.mode !== 'single')) return false;
      return !row.id && keys.every(key => String(row[key] ?? '').trim() === '');
    }
    status(row, sheet = this.active) {
      if (row._delete) return 'deleted';
      if (!row.id) return this.blank(row, sheet) ? 'blank' : 'new';
      const old = this.original[sheet].find(item => item.key === row.key);
      return this.fields(sheet).some(col => String(row[col.key]) !== String(old?.[col.key])) ? 'edited' : 'saved';
    }
    counts() {
      const counts = {new:0, edited:0, deleted:0};
      for (const sheet of root.dataset.kind === 'cafes' ? ['rows'] : ['menus','options']) {
        for (const row of this.list(sheet)) {
          const status = this.status(row, sheet);
          if (status in counts) counts[status]++;
        }
      }
      return counts;
    }
    dirty() { return Object.values(this.counts()).some(Boolean); }
    bind() {
      root.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
        this.active = button.dataset.tab; this.clearFilters(); this.render();
      }));
      root.querySelectorAll('[data-add]').forEach(button => button.addEventListener('click', () => {
        for (let i = 0; i < Number(button.dataset.add); i++) this.list().push(this.newRow());
        this.filters[this.active].search = ''; this.filters[this.active].status = 'all';
        this.render();
        this.$('[data-body] tr:last-child input, [data-body] tr:last-child select')?.focus();
      }));
      this.$('[data-search]').addEventListener('input', event => { this.filters[this.active].search = event.target.value; this.renderRows(); });
      this.$('[data-filter]').addEventListener('change', event => { this.filters[this.active].status = event.target.value; this.renderRows(); });
      this.$('[data-menu-filter]').addEventListener('change', event => { this.filters.options.menu = event.target.value; this.renderRows(); });
      this.$('[data-show-all]').addEventListener('click', () => { this.clearFilters(); this.render(); });
      this.$('[data-save]').addEventListener('click', () => this.save());
      this.$('[data-reset]').addEventListener('click', () => {
        if (!this.dirty() || window.confirm('저장하지 않은 변경사항을 취소할까요?')) {
          this.data = clone(this.original); this.errors = []; this.render(); this.result('변경사항을 취소했어요.');
        }
      });
      this.$('[data-reload]').addEventListener('click', async () => {
        if (this.dirty() && !window.confirm('저장하지 않은 변경사항을 버리고 최신 내용을 불러올까요?')) return;
        try { this.load(await this.request(root.dataset.url)); this.errors = []; this.render(); this.result('최신 내용을 불러왔어요.'); }
        catch (error) { this.showErrors([{message:error.message}]); }
      });
      window.addEventListener('beforeunload', event => { if (this.dirty()) { event.preventDefault(); event.returnValue = ''; } });
      document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
          event.preventDefault(); if (this.dirty() && !this.saving) this.save();
        }
      });
    }
    newRow(sheet = this.active) {
      const key = uid('new');
      if (sheet === 'rows') return {key,id:null,name:'',phone:'',address:'',seller_code:'',seller_name:'',is_open:false};
      if (sheet === 'menus') return {key,id:null,name:'',category:'',price:'',is_active:true,is_available:true};
      const available = this.data.menus.filter(row => !row._delete && !this.blank(row,'menus'));
      return {key,id:null,menu_key:this.filters.options.menu || (available.length === 1 ? available[0].key : ''),group_key:uid('new-group'),group_name:'',mode:'single',required:true,name:'',extra_price:0,is_default:false};
    }
    clearFilters() {
      this.filters[this.active] = {search:'',status:'all',...(this.active === 'options' ? {menu:''} : {})};
    }
    basicOptions(menu) {
      if (menu.id || !menu.name.trim() || this.data.options.some(row => row.menu_key === menu.key)) return;
      const temperature = uid('new-group'), shots = uid('new-group');
      for (const [group_key, group_name, name, is_default] of [[temperature,'아이스 / 핫','아이스',true],[temperature,'아이스 / 핫','핫',false],[shots,'샷 추가','샷 +0',true]]) {
        this.data.options.push({...this.newRow('options'),menu_key:menu.key,group_key,group_name,name,is_default});
      }
    }
    menuChoices() {
      return [['','메뉴 선택'], ...this.data.menus.filter(row => !row._delete && !this.blank(row,'menus')).map(row => [row.key, row.category ? `${row.name} · ${row.category}` : row.name])];
    }
    optionsForMenu(key) { return this.data.options.filter(row => row.menu_key === key && !row._delete && !this.blank(row,'options')); }
    visibleRows() {
      const filter = this.filters[this.active];
      const search = filter.search.trim().toLocaleLowerCase();
      return this.list().filter(row => {
        if (this.active === 'options' && this.data.menus.some(menu => menu.key === row.menu_key && menu._delete)) return false;
        if (search && !Object.values(row).join(' ').toLocaleLowerCase().includes(search)) return false;
        if (filter.menu && this.active === 'options' && row.menu_key !== filter.menu) return false;
        const status = filter.status;
        if (status === 'changed' && ['saved','blank'].includes(this.status(row))) return false;
        if (this.active === 'rows') {
          if (status === 'linked' && !row.seller_code) return false;
          if (status === 'unlinked' && row.seller_code) return false;
          if (status === 'open' && !row.is_open) return false;
          if (status === 'closed' && row.is_open) return false;
        } else if (this.active === 'menus') {
          if (status === 'shown' && (!row.is_active || row._delete)) return false;
          if (status === 'hidden' && row.is_active && !row._delete) return false;
          if (status === 'soldout' && row.is_available) return false;
        }
        return true;
      });
    }
    fillSelect(select, items, selected) {
      select.replaceChildren(...items.map(([value,label]) => {
        const option = el('option',label); option.value = value; return option;
      }));
      if (!items.some(([value]) => value === selected)) {
        const option = el('option',`확인 필요: ${selected}`); option.value = selected; select.appendChild(option);
      }
      select.value = selected;
    }
    render() {
      root.querySelectorAll('[data-tab]').forEach(button => button.setAttribute('aria-selected',String(button.dataset.tab === this.active)));
      if (this.active !== 'rows') {
        this.$('#sheet-panel').setAttribute('role','tabpanel');
        this.$('#sheet-panel').setAttribute('aria-labelledby',`${this.active}-tab`);
      }
      this.$('[data-search]').value = this.filters[this.active].search;
      const statuses = [['all','전체'],['changed','변경된 행']];
      if (this.active === 'rows') statuses.push(['linked','판매자 연결됨'],['unlinked','판매자 미연결'],['open','영업중'],['closed','영업종료']);
      if (this.active === 'menus') statuses.push(['shown','표시 중'],['hidden','숨김'],['soldout','품절']);
      this.fillSelect(this.$('[data-filter]'), statuses, this.filters[this.active].status);
      this.$('[data-menu-filter-label]').hidden = this.active !== 'options';
      if (this.active === 'options') this.fillSelect(this.$('[data-menu-filter]'), [['','전체 메뉴'],...this.menuChoices().slice(1)],this.filters.options.menu);
      this.$('[data-help]').textContent = this.active === 'rows'
        ? '카페명 → 전화번호 → 주소 → 연결코드 순서로 엑셀 내용을 붙여넣으세요. 연결코드를 비우고 저장하면 판매자 연결이 해제돼요. 영업상태는 판매자 로그인·로그아웃으로 바뀌어요.'
        : this.active === 'menus'
        ? '분류 → 메뉴명 → 가격 → 메뉴판 표시 순서로 붙여넣으세요. 새 메뉴에는 아이스/핫·샷 +0 옵션이 생겨요. 옵션 셀을 누르면 같은 화면에서 설정할 수 있어요. 삭제는 메뉴판 숨김으로 처리돼요.'
        : '메뉴 → 옵션 그룹 → 선택 방식 → 필수 선택 → 선택 항목 → 추가금액 → 기본 선택 순서예요. 같은 그룹의 이름·선택 방식·필수 여부는 함께 변경돼요. 체크 값은 예/아니오 또는 O/X로 붙여넣을 수 있어요.';
      const header = el('tr'); header.appendChild(el('th','행 / 상태'));
      columns[this.active].forEach((col,index) => header.appendChild(el('th',col.label,index === 0 && this.active !== 'menus' ? 'sheet-freeze' : '')));
      header.appendChild(el('th','관리')); this.$('[data-head]').replaceChildren(header);
      this.$('[data-caption]').textContent = this.active === 'rows' ? '카페·판매자 관리 표' : this.active === 'menus' ? '메뉴 관리 표' : '옵션 관리 표';
      this.renderRows(); this.renderHistory();
    }
    renderRows() {
      const body = this.$('[data-body]'); body.replaceChildren();
      const rows = this.visibleRows();
      rows.forEach(row => body.appendChild(this.renderRow(row, this.list().indexOf(row))));
      this.$('[data-empty]').hidden = rows.length > 0;
      this.$('[data-empty]').textContent = this.list().length ? '검색 조건에 맞는 항목이 없어요.' : '아직 등록된 항목이 없어요. 행을 추가하거나 엑셀 내용을 붙여넣어 보세요.';
      const filter = this.filters[this.active];
      const filtered = Boolean(filter.search.trim() || filter.status !== 'all' || filter.menu);
      this.$('[data-show-all]').hidden = !filtered;
      this.$('[data-row-count]').textContent = filtered
        ? `필터 적용: ${rows.length}행 표시 · 전체 ${this.list().length}행`
        : `전체 ${rows.length}행`;
      this.updateCounts(); this.paintErrors();
    }
    renderRow(row, index) {
      const tr = el('tr'); tr.dataset.key = row.key;
      const state = el('td'); state.append(el('span',String(index + 1),'sheet-row-number'),el('span','', 'sheet-row-status')); tr.append(state);
      for (const [columnIndex,col] of columns[this.active].entries()) {
        const td = el('td'); td.dataset.field = col.key;
        if (columnIndex === 0 && this.active !== 'menus') td.classList.add('sheet-freeze');
        if (col.css) td.classList.add(col.css);
        if (col.check) td.classList.add('check-cell');
        if (col.readonly) {
          if (col.key === 'option_summary') {
            const options = this.optionsForMenu(row.key), groups = new Set(options.map(item => item.group_key));
            const button = el('button',`옵션 ${groups.size}개`, 'option-button'); button.type = 'button';
            button.addEventListener('click', () => {
              this.active = 'options'; this.clearFilters(); this.render();
              const firstOption = this.optionsForMenu(row.key)[0];
              if (firstOption) [...this.$('[data-body]').children].find(tr => tr.dataset.key === firstOption.key)?.querySelector('select')?.focus();
            });
            td.append(button);
            const defaults = options.filter(item => item.is_default).map(item => `${item.group_name}: ${item.name}`).join(' · ');
            td.appendChild(el('span',defaults || '기본값 없음', 'sheet-option-summary'));
          } else td.appendChild(el('span',this.readonlyValue(row,col.key), 'sheet-readonly'));
        } else {
          const input = el(col.select ? 'select' : 'input'); input.dataset.field = col.key;
          input.setAttribute('aria-label',`${index + 1}행 ${col.label}`);
          if (col.select) this.fillSelect(input,col.select === 'menus' ? this.menuChoices() : col.select,String(row[col.key] ?? ''));
          else if (col.check) { input.type = 'checkbox'; input.checked = row[col.key] === true; }
          else { input.type = 'text'; input.value = row[col.key] ?? ''; input.autocomplete = 'off'; input.spellcheck = false; if (col.money) input.inputMode = 'numeric'; }
          input.disabled = Boolean(row._delete);
          input.addEventListener(col.check || col.select ? 'change' : 'input', () => {
            this.setValue(row,col,col.check ? input.checked : input.value);
            this.errors = this.errors.filter(error => error.key !== row.key || error.field !== col.key);
            this.updateCounts(); this.paintErrors();
          });
          input.addEventListener('blur', () => {
            if (col.key === 'seller_code') this.resolveSeller(row);
            if (this.active === 'options' && ['menu_key','group_name'].includes(col.key)) { this.reconcile(row); this.updateCounts(); }
          });
          input.addEventListener('paste', event => this.paste(event,row,col));
          input.addEventListener('keydown', event => {
            if (input.tagName === 'SELECT' || !['Enter','ArrowUp','ArrowDown'].includes(event.key)) return;
            const rows = [...this.$('[data-body]').children], next = rows[rows.indexOf(tr) + (event.key === 'ArrowUp' ? -1 : 1)];
            const target = next?.querySelector(`[data-field="${col.key}"] input, [data-field="${col.key}"] select`);
            if (target) { event.preventDefault(); target.focus(); }
          });
          td.appendChild(input);
          if (this.active === 'rows' && col.key === 'name' && row.manage_url) {
            const link = el('a','메뉴·옵션 관리','sheet-cafe-link'); link.href = row.manage_url; td.appendChild(link);
          }
        }
        tr.appendChild(td);
      }
      const actions = el('td',undefined,'action-cell');
      const button = el('button',this.active === 'rows' && row.id ? '연결 해제' : row._delete ? '삭제 취소' : row.id ? '삭제' : '등록 취소', 'text-button'); button.type = 'button';
      if (this.active === 'rows' && row.id) {
        button.disabled = !row.seller_code;
        button.addEventListener('click', () => { row.seller_code = ''; row.seller_name = ''; this.renderRows(); });
      } else button.addEventListener('click', () => {
        if (!row.id) {
          this.data[this.active] = this.list().filter(item => item !== row);
          if (this.active === 'menus') this.data.options = this.data.options.filter(item => item.menu_key !== row.key);
        } else row._delete = !row._delete;
        this.renderRows();
      });
      actions.appendChild(button); tr.appendChild(actions); return tr;
    }
    readonlyValue(row,key) {
      if (key === 'seller_name') return row.seller_code ? row.seller_name || '코드 확인 대기' : '미연결';
      if (key === 'is_open') return row.is_open ? '영업중' : '영업종료';
      if (key === 'is_available') return row.is_available ? '판매중' : '품절';
      return row[key] ?? '';
    }
    setValue(row,col,value) {
      const previousGroup = row.group_key;
      row[col.key] = value;
      if (this.active === 'menus' && col.key === 'name') this.basicOptions(row);
      if (this.active === 'rows' && col.key === 'seller_code') row.seller_name = '';
      if (this.active === 'options') {
        if (col.key === 'menu_key') row.group_key = uid('new-group');
        if (['group_name','mode','required'].includes(col.key)) {
          for (const peer of this.data.options.filter(item => item.group_key === previousGroup && item !== row)) {
            peer[col.key] = value;
            const control = [...this.$('[data-body]').children].find(tr => tr.dataset.key === peer.key)?.querySelector(`[data-field="${col.key}"] input, [data-field="${col.key}"] select`);
            if (control) { if (col.check) control.checked = value === true; else control.value = value; }
          }
        }
      }
    }
    reconcile(row) {
      const target = this.data.options.find(peer => peer !== row && !peer._delete && peer.menu_key === row.menu_key && peer.group_name === row.group_name);
      if (target) row.group_key = target.group_key;
    }
    updateCounts() {
      const counts = this.counts();
      this.$('[data-changes]').textContent = this.dirty() ? `추가 ${counts.new} · 수정 ${counts.edited} · 삭제 예정 ${counts.deleted}` : '변경사항 없음';
      this.$('[data-save]').disabled = !this.dirty() || this.saving;
      this.$('[data-reset]').disabled = this.saving;
      for (const tr of this.$('[data-body]').children) {
        const row = this.list().find(item => item.key === tr.dataset.key); if (!row) continue;
        const status = this.status(row);
        tr.className = `row-${status}`;
        tr.querySelector('.sheet-row-status').textContent = {saved:'저장됨',new:'추가',edited:'수정',deleted:'삭제 예정',blank:'입력 대기'}[status];
        if (this.active === 'menus') {
          const options = this.optionsForMenu(row.key);
          tr.querySelector('.option-button').textContent = `옵션 ${new Set(options.map(item => item.group_key)).size}개`;
          tr.querySelector('.sheet-option-summary').textContent = options.filter(item => item.is_default).map(item => `${item.group_name}: ${item.name}`).join(' · ') || '기본값 없음';
        }
        if (this.active === 'rows') {
          tr.querySelector('[data-field="seller_name"] .sheet-readonly').textContent = this.readonlyValue(row,'seller_name');
          if (row.id) tr.querySelector('.action-cell button').disabled = !row.seller_code;
        }
        const old = this.original[this.active].find(item => item.key === row.key);
        for (const col of this.fields()) {
          const cell = [...tr.children].find(td => td.dataset.field === col.key);
          if (cell) {
            cell.classList.toggle('changed-cell',Boolean(old) && String(old[col.key]) !== String(row[col.key]));
            const previous = !old ? '' : col.check ? (old[col.key] ? '예' : '아니오') : col.key === 'menu_key' ? this.original.menus.find(menu => menu.key === old.menu_key)?.name || '' : old[col.key];
            cell.title = old ? `저장된 값: ${previous}` : '';
          }
        }
      }
      root.querySelectorAll('[data-count]').forEach(span => { span.textContent = this.data[span.dataset.count].filter(row => !row._delete && !this.blank(row,span.dataset.count)).length; });
    }
    async resolveSeller(row) {
      const code = row.seller_code.trim(); row.seller_code = code;
      if (!code) return;
      try {
        if (!this.sellerCache.has(code)) this.sellerCache.set(code,this.request(`${root.dataset.sellerUrl}?code=${encodeURIComponent(code)}`));
        const result = await this.sellerCache.get(code);
        if (row.seller_code !== code) return;
        row.seller_name = result.nickname;
        const tr = [...this.$('[data-body]').children].find(node => node.dataset.key === row.key);
        if (tr) tr.querySelector('[data-field="seller_name"] .sheet-readonly').textContent = result.nickname + (result.cafe && result.cafe_id !== row.id ? ` · ${result.cafe} 연결됨` : '');
      } catch {
        if (row.seller_code !== code) return;
        this.sellerCache.delete(code);
        this.errors = this.errors.filter(error => error.key !== row.key || error.field !== 'seller_code');
        this.errors.push({sheet:'rows',key:row.key,field:'seller_code',message:'가입한 계정의 연결코드를 확인해 주세요.'}); this.paintErrors();
      }
    }
    fromPaste(value,col) {
      value = value.trim();
      if (col.check) {
        if (['true','1','o','예','네','필수','표시','기본','사용','y'].includes(value.toLowerCase())) return true;
        if (['false','0','x','아니오','아니요','선택','숨김','미사용','n',''].includes(value.toLowerCase())) return false;
        return value;
      }
      if (col.key === 'mode') return {'하나 선택':'single','하나선택':'single','단일':'single','여러 개 선택':'multiple','여러개선택':'multiple','복수':'multiple'}[value] || value;
      if (col.key === 'menu_key') {
        const found = this.menuChoices().filter(([key,label]) => key === value || label === value || this.data.menus.find(row => row.key === key)?.name === value);
        return found.length === 1 ? found[0][0] : value;
      }
      return value;
    }
    paste(event,row,col) {
      const text = event.clipboardData?.getData('text/plain');
      if (!text || !/[\t\n\r]/.test(text)) return;
      event.preventDefault();
      const grid = parseTSV(text), fields = this.fields(), offset = fields.indexOf(col), targets = this.visibleRows(), start = targets.indexOf(row);
      if (grid[0]?.every((value,i) => fields[offset + i]?.label === value.trim())) grid.shift();
      const affected = [];
      grid.forEach((cells,index) => {
        let target = targets[start + index];
        if (!target) { target = this.newRow(); this.list().push(target); }
        if (target._delete) return;
        cells.slice(0,fields.length - offset).forEach((value,column) => {
          const field = fields[offset + column]; this.setValue(target,field,this.fromPaste(value,field));
        });
        if (this.active === 'options') this.reconcile(target);
        affected.push(target);
      });
      this.filters[this.active].search = ''; this.filters[this.active].status = 'all'; this.render();
      if (this.active === 'rows') affected.forEach(item => this.resolveSeller(item));
      this.result(`${affected.length}행을 붙여넣었어요. 내용을 확인한 뒤 변경사항 저장을 눌러 주세요.`);
    }
    paintErrors() {
      for (const cell of this.$('[data-body]').querySelectorAll('td')) {
        cell.classList.remove('invalid-cell'); cell.querySelector('.sheet-cell-error')?.remove();
        cell.querySelector('input,select')?.removeAttribute('aria-invalid');
      }
      for (const error of this.errors.filter(item => !item.sheet || item.sheet === this.active)) {
        const tr = [...this.$('[data-body]').children].find(node => node.dataset.key === error.key);
        const td = tr && [...tr.children].find(cell => cell.dataset.field === error.field);
        if (td) { td.classList.add('invalid-cell'); td.appendChild(el('span',error.message,'sheet-cell-error')); td.querySelector('input,select')?.setAttribute('aria-invalid','true'); }
      }
      const summary = this.$('[data-errors]'); summary.hidden = !this.errors.length;
      summary.textContent = this.errors.length ? `${this.errors.length}개 항목을 확인해 주세요. ${this.errors[0].message} 입력한 내용은 유지돼요.` : '';
    }
    showErrors(errors) {
      this.errors = errors;
      const first = errors.find(error => error.sheet && error.key);
      if (first) { this.active = first.sheet; this.filters[this.active] = {search:'',status:'all',menu:''}; this.render(); }
      else this.paintErrors();
      const input = this.$('.invalid-cell input, .invalid-cell select');
      if (input) { input.focus({preventScroll:true}); input.scrollIntoView({block:'nearest',inline:'nearest'}); }
      this.result('저장하지 못했어요. 표시된 내용을 확인해 주세요.');
    }
    renderHistory() {
      const items = (this.data.history || []).map(item => el('li',`${item.time} · ${item.summary}`));
      this.$('[data-history]').replaceChildren(...(items.length ? items : [el('li','아직 표에서 저장한 이력이 없어요.')]));
    }
    result(text) { this.$('[data-result]').textContent = text; }
    async request(url, data) {
      const options = {credentials:'same-origin',headers:{Accept:'application/json'}};
      if (data) {
        options.method = 'POST'; options.headers['Content-Type'] = 'application/json';
        options.headers['X-CSRFToken'] = this.$('[name="csrfmiddlewaretoken"]').value;
        options.body = JSON.stringify(data);
      }
      const response = await fetch(url,options);
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('로그인 상태와 접근 권한을 확인해 주세요.');
      const result = await response.json();
      if (!response.ok) { const error = new Error(result.message || '저장 내용을 확인해 주세요.'); error.cells = result.errors; throw error; }
      return result;
    }
    async save() {
      if (this.saving || !this.dirty()) return;
      this.saving = true; this.$('[data-sheet-controls]').disabled = true; this.updateCounts(); this.result('변경사항을 저장하고 있어요…');
      const payload = clone(this.data);
      for (const key of root.dataset.kind === 'cafes' ? ['rows'] : ['menus','options']) payload[key] = payload[key].filter(row => !this.blank(row,key));
      try {
        const result = await this.request(root.dataset.url,payload);
        if (result.menu_keys && this.filters.options.menu) this.filters.options.menu = result.menu_keys[this.filters.options.menu] || this.filters.options.menu;
        this.load(result); this.errors = []; this.render(); this.result('변경사항을 모두 저장했어요. 표에 표시된 내용이 현재 등록 상태예요.');
      } catch (error) { this.showErrors(error.cells || [{message:error.message}]); }
      finally { this.saving = false; this.$('[data-sheet-controls]').disabled = false; this.updateCounts(); }
    }
  }
  new Sheet();
})();
