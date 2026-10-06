// State
const state = {
    salary: { fileId: null, filename: null, config: null, parsed: null, preview: null, previewRowIndices: [], mapping: null, validationState: null, overrides: {}, deletedRows: [], fullPreview: false, previewPage: 1, previewPageSize: 20 },
    email: { fileId: null, filename: null, config: null, parsed: null, preview: null, previewRowIndices: [], mapping: null, overrides: {}, overrideLog: {}, deletedRows: [], validationState: null, fullPreview: false, previewPage: 1, previewPageSize: 20 },
    currentStep: 1,
    unlockedSteps: { 1: true },
    detectedYear: null,
    detectedMonth: null,
    savedConfig: null,
    importPreview: { headers: null, rows: null, fullPreview: false, page: 1, pageSize: 20, total: 0, matched: 0, unmatched: 0, onlyUnmatched: false },
};

// Utility
function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }
function show(el) { el.classList.remove('hidden'); }
function hide(el) { el.classList.add('hidden'); }
function setText(el, text) { el.textContent = text; }

async function postJson(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`请求失败 (${res.status}): ${text || res.statusText}`);
    }
    return await res.json();
}

async function uploadFile(file, fileType, fullPreview = false) {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('file_type', fileType);
    if (fullPreview) fd.append('full_preview', 'true');
    const res = await fetch('/api/preview', { method: 'POST', body: fd });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`上传失败 (${res.status}): ${text || res.statusText}`);
    }
    return await res.json();
}

function colIndexToLetters(index) {
    // 0 -> A, 1 -> B, 25 -> Z, 26 -> AA, 701 -> ZZ
    let result = '';
    let n = index + 1; // Excel 列号从 1 开始
    while (n > 0) {
        n--;
        result = String.fromCharCode(65 + (n % 26)) + result;
        n = Math.floor(n / 26);
    }
    return result;
}

function colLettersToIndex(letters) {
    // Excel 列字母或 1-based 数字转 0-based 索引；空/非法返回 null
    if (!letters) return null;
    letters = String(letters).trim().toUpperCase();
    if (/^\d+$/.test(letters)) {
        const n = parseInt(letters);
        return n > 0 ? n - 1 : null;
    }
    let n = 0;
    for (const ch of letters) {
        if (ch < 'A' || ch > 'Z') return null;
        n = n * 26 + (ch.charCodeAt(0) - 64);
    }
    return n - 1;
}

function slicePreviewByEndCol(rows, endCol, rowIndices) {
    if (!rows || !rows.length || !endCol) return { rows, rowIndices };
    const idx = colLettersToIndex(endCol);
    if (idx === null || idx < 0) return { rows, rowIndices };
    return {
        rows: rows.map(row => row.slice(0, idx + 1)),
        rowIndices: rowIndices,
    };
}

function filterPreviewBySkipRows(rows, rowIndices, skipRows) {
    if (!rows || !rows.length || !skipRows || !skipRows.length) {
        return { rows, rowIndices };
    }
    const skipSet = new Set(skipRows);
    const keptRows = [];
    const keptIndices = [];
    rows.forEach((row, i) => {
        const excelRow = rowIndices[i] ?? (i + 1);
        if (!skipSet.has(excelRow)) {
            keptRows.push(row);
            keptIndices.push(excelRow);
        }
    });
    return { rows: keptRows, rowIndices: keptIndices };
}

function parseHeaderRows(value) {
    // 支持 "3,4"、"3-4"、"3,5-7" 等多种写法，返回去重后的整数数组
    const result = [];
    value.split(',').forEach(part => {
        part = part.trim();
        if (!part) return;
        if (part.includes('-')) {
            const [start, end] = part.split('-').map(s => parseInt(s.trim()));
            if (!isNaN(start) && !isNaN(end)) {
                const lo = Math.min(start, end);
                const hi = Math.max(start, end);
                for (let i = lo; i <= hi; i++) result.push(i);
            }
        } else {
            const n = parseInt(part);
            if (!isNaN(n)) result.push(n);
        }
    });
    // 去重并保持顺序
    return [...new Set(result)];
}

function formatPreviewCell(cell) {
    // 预览时把 YYYY-MM-DD 00:00:00 简化为 YYYY-MM-DD
    if (typeof cell === 'string' && /^\d{4}-\d{2}-\d{2} 00:00:00$/.test(cell)) {
        return cell.slice(0, 10);
    }
    return cell;
}

// ---------------------------------------------------------------------------
// Personalized user config (localStorage + backend)
// ---------------------------------------------------------------------------

const USER_CONFIG_LS_KEY = 'salary_manager_user_config';
let saveConfigTimer = null;

function loadSavedConfig() {
    try {
        const raw = localStorage.getItem(USER_CONFIG_LS_KEY);
        if (raw) state.savedConfig = JSON.parse(raw);
    } catch (e) { /* ignore */ }
    fetch('/api/user-config').then(r => r.json()).then(cfg => {
        state.savedConfig = cfg;
        localStorage.setItem(USER_CONFIG_LS_KEY, JSON.stringify(cfg));
    }).catch(() => {});
}

function getSectionConfig(kind) {
    const cfg = state.savedConfig || {};
    return cfg[kind] || {};
}

function applySectionDefaults(kind, detectedHeader) {
    const cfg = getSectionConfig(kind);
    const prefix = kind;
    // 优先用保存的配置
    if (cfg.skip_rows != null) {
        $(`#${prefix}-skip-rows`).value = cfg.skip_rows;
    }
    if (cfg.header_rows != null) {
        $(`#${prefix}-header-rows`).value = cfg.header_rows;
    } else if (detectedHeader !== null) {
        $(`#${prefix}-header-rows`).value = `${detectedHeader + 1}`;
    }
    if (cfg.data_start != null) {
        $(`#${prefix}-data-start`).value = cfg.data_start;
    } else if (detectedHeader !== null) {
        $(`#${prefix}-data-start`).value = `${detectedHeader + 2}`;
    }
    if (cfg.data_end != null) $(`#${prefix}-data-end`).value = cfg.data_end;
    if (cfg.data_end_col != null) $(`#${prefix}-data-end-col`).value = cfg.data_end_col;
}

function saveCurrentConfig() {
    const cfg = {
        salary: {
            skip_rows: $('#salary-skip-rows').value,
            header_rows: $('#salary-header-rows').value,
            data_start: $('#salary-data-start').value,
            data_end: $('#salary-data-end').value,
            data_end_col: $('#salary-data-end-col').value.trim(),
        },
        email: {
            skip_rows: $('#email-skip-rows').value,
            header_rows: $('#email-header-rows').value,
            data_start: $('#email-data-start').value,
            data_end: $('#email-data-end').value,
            data_end_col: $('#email-data-end-col').value.trim(),
        },
    };
    state.savedConfig = cfg;
    try { localStorage.setItem(USER_CONFIG_LS_KEY, JSON.stringify(cfg)); } catch (e) { /* ignore */ }
    clearTimeout(saveConfigTimer);
    saveConfigTimer = setTimeout(() => {
        fetch('/api/user-config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cfg),
        }).catch(() => {});
    }, 800);
}

// ---------------------------------------------------------------------------
// 列配置变更后自动重新触发解析与有效性校验（防抖）
// ---------------------------------------------------------------------------
let parseSalaryTimer = null;
let parseEmailTimer = null;

function triggerParseSalary() {
    if (!state.salary.fileId) return;
    clearTimeout(parseSalaryTimer);
    parseSalaryTimer = setTimeout(() => {
        parseSalary().catch(err => console.error('自动解析工资表失败:', err));
    }, 300);
}

function triggerParseEmail() {
    if (!state.email.fileId) return;
    clearTimeout(parseEmailTimer);
    parseEmailTimer = setTimeout(() => {
        parseEmail().catch(err => console.error('自动解析邮箱表失败:', err));
    }, 300);
}

function renderTable(tableEl, rows, startRow, headerRows, rowIndices, pagination, freezeCols) {
    tableEl.innerHTML = '';
    if (!rows || !rows.length) return;
    startRow = startRow || 1;
    headerRows = headerRows || [];
    rowIndices = rowIndices || [];
    pagination = pagination || null;
    freezeCols = freezeCols || []; // 0-based 数据列索引数组，按从左到右冻结顺序排列
    // 根据 rowIndices 定位最后一个表头行的位置，兼容跳过行后的行偏移
    const headerCount = (() => {
        if (!headerRows.length) return 1;
        const maxHeader = Math.max(...headerRows);
        const idx = rowIndices.indexOf(maxHeader);
        return idx >= 0 ? idx + 1 : maxHeader;
    })();
    const colCount = rows[0].length;

    // 数据行（表头之后的部分）
    let dataRows = rows.slice(headerCount);
    let dataRowIndices = rowIndices.slice(headerCount);
    let pageInfo = null;
    if (pagination) {
        const totalPages = Math.max(1, Math.ceil(dataRows.length / pagination.pageSize));
        let page = pagination.page;
        if (page > totalPages) page = totalPages;
        if (page < 1) page = 1;
        const start = (page - 1) * pagination.pageSize;
        const end = Math.min(start + pagination.pageSize, dataRows.length);
        pageInfo = { page, pageSize: pagination.pageSize, total: dataRows.length, totalPages, start, end };
        if (dataRows.length > pagination.pageSize) {
            dataRows = dataRows.slice(start, end);
            dataRowIndices = dataRowIndices.slice(start, end);
        }
    }

    const freezeSet = new Set(freezeCols);
    const freezeOrder = new Map(); // colIdx -> 1, 2, ...
    freezeCols.forEach((c, i) => freezeOrder.set(c, i + 1));

    const buildCell = (cell, isHeader, colIdx) => {
        const el = document.createElement(isHeader ? 'th' : 'td');
        el.textContent = formatPreviewCell(cell);
        if (freezeSet.has(colIdx)) {
            el.classList.add('freeze-col', `freeze-${freezeOrder.get(colIdx)}`);
            if (colIdx === freezeCols[freezeCols.length - 1]) el.classList.add('freeze-last');
        }
        return el;
    };

    const thead = document.createElement('thead');

    // 列字母行
    const letterTr = document.createElement('tr');
    const cornerTh = document.createElement('th');
    cornerTh.className = 'row-header corner';
    cornerTh.textContent = '#';
    letterTr.appendChild(cornerTh);
    for (let i = 0; i < colCount; i++) {
        const th = document.createElement('th');
        th.innerHTML = `${escapeHtml(colIndexToLetters(i))}<span class="col-number">列${i + 1}</span>`;
        if (freezeSet.has(i)) {
            th.classList.add('freeze-col', `freeze-${freezeOrder.get(i)}`);
            if (i === freezeCols[freezeCols.length - 1]) th.classList.add('freeze-last');
        }
        letterTr.appendChild(th);
    }
    thead.appendChild(letterTr);

    // 表头行（Excel 中的表头区域及以上）固定到 thead
    for (let idx = 0; idx < Math.min(headerCount, rows.length); idx++) {
        const row = rows[idx];
        const tr = document.createElement('tr');
        tr.className = 'frozen-header-row';
        const rowTh = document.createElement('th');
        rowTh.className = 'row-header';
        rowTh.textContent = rowIndices[idx] ?? startRow + idx;
        tr.appendChild(rowTh);
        row.forEach((cell, ci) => tr.appendChild(buildCell(cell, true, ci)));
        thead.appendChild(tr);
    }
    tableEl.appendChild(thead);

    // 数据行
    const tbody = document.createElement('tbody');
    dataRows.forEach((row, i) => {
        const excelRow = dataRowIndices[i];
        // 省略标记行：数组长度明显短于列数且内容以 ... 开头
        if (row.length === 1 && typeof row[0] === 'string' && row[0].startsWith('...')) {
            const tr = document.createElement('tr');
            tr.className = 'omitted-row';
            const td = document.createElement('td');
            td.className = 'omitted-cell';
            td.colSpan = colCount + 1;
            td.textContent = row[0];
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }
        const tr = document.createElement('tr');
        const rowTh = document.createElement('th');
        rowTh.className = 'row-header';
        rowTh.textContent = excelRow ?? startRow + headerCount + i;
        tr.appendChild(rowTh);
        row.forEach((cell, ci) => tr.appendChild(buildCell(cell, false, ci)));
        tbody.appendChild(tr);
    });
    tableEl.appendChild(tbody);

    // 设置表头行 top 偏移 + 冻结列的 left 偏移
    requestAnimationFrame(() => {
        const theadRows = tableEl.querySelectorAll('thead tr');
        let topAcc = 0;
        theadRows.forEach(tr => {
            tr.querySelectorAll('th, td').forEach(cell => { cell.style.top = topAcc + 'px'; });
            topAcc += tr.offsetHeight;
        });

        if (freezeCols.length) {
            const firstRowHeader = tableEl.querySelector('tbody th.row-header') || tableEl.querySelector('thead th.row-header');
            const rowHeaderW = firstRowHeader ? firstRowHeader.offsetWidth : 44;
            let accumulated = rowHeaderW;
            freezeCols.forEach((c, i) => {
                const sample = tableEl.querySelector(`tbody td.freeze-${i + 1}`) || tableEl.querySelector(`thead th.freeze-${i + 1}`);
                const w = sample ? sample.offsetWidth : 0;
                const left = accumulated;
                tableEl.querySelectorAll(`.freeze-${i + 1}`).forEach(el => { el.style.left = left + 'px'; });
                accumulated += w;
            });
        }
    });

    // 返回分页信息供外部渲染分页控件
    return pageInfo;
}

function getFreezeCols(mapping) {
    if (!mapping) return [];
    const cols = [mapping.community, mapping.name].filter(v => v != null && v !== '');
    return [...new Set(cols)].sort((a, b) => a - b);
}

function renderSalaryPreview() {
    if (!state.salary.preview) return;
    const skipRows = parseHeaderRows($('#salary-skip-rows').value);
    const filtered = filterPreviewBySkipRows(state.salary.preview, state.salary.previewRowIndices, skipRows);
    const sliced = slicePreviewByEndCol(filtered.rows, $('#salary-data-end-col').value, filtered.rowIndices);

    // 应用删除行和字段覆盖（community/name/account/gross/deduction/net）
    const mapping = state.salary.mapping || {};
    const deleted = new Set(state.salary.deletedRows);
    const ov = state.salary.overrides || {};
    const headerRows = parseHeaderRows($('#salary-header-rows').value);
    const headerRowSet = new Set(headerRows);
    const colMap = {
        community: mapping.community,
        name: mapping.name,
        account_clean: parseInt($('#salary-bank-account-col').value),
        gross: parseInt($('#salary-gross-col').value),
        deduction: parseInt($('#salary-deduction-col').value),
        net: parseInt($('#salary-net-col').value),
    };
    const finalRows = [];
    const finalRowIndices = [];
    for (let i = 0; i < sliced.rows.length; i++) {
        const excelRow = sliced.rowIndices[i];
        // 表头行不过滤，数据行若被删除则跳过
        if (!headerRowSet.has(excelRow) && deleted.has(excelRow)) continue;
        const row = sliced.rows[i].slice();
        if (ov[excelRow]) {
            for (const [key, colIdx] of Object.entries(colMap)) {
                if (colIdx == null || isNaN(colIdx)) continue;
                if (ov[excelRow][key] !== undefined) {
                    if (colIdx < row.length) row[colIdx] = ov[excelRow][key];
                }
            }
        }
        finalRows.push(row);
        finalRowIndices.push(excelRow);
    }

    const pagination = state.salary.fullPreview ? { page: state.salary.previewPage, pageSize: state.salary.previewPageSize } : null;
    const freezeCols = getFreezeCols(mapping);
    const pageInfo = renderTable($('#salary-preview-table'), finalRows, 1, parseHeaderRows($('#salary-header-rows').value), finalRowIndices, pagination, freezeCols);
    renderPreviewPagination('salary', pageInfo);
}

function renderEmailPreview() {
    if (!state.email.preview) return;
    const skipRows = parseHeaderRows($('#email-skip-rows').value);
    const filtered = filterPreviewBySkipRows(state.email.preview, state.email.previewRowIndices, skipRows);
    const sliced = slicePreviewByEndCol(filtered.rows, $('#email-data-end-col').value, filtered.rowIndices);
    // 应用删除行与用户修正的社区/姓名/邮箱覆盖
    const mapping = state.email.mapping || {};
    const deletedEmail = new Set(state.email.deletedRows || []);
    const ov = state.email.overrides || {};
    const headerRowSet = new Set(parseHeaderRows($('#email-header-rows').value));
    const colMap = {
        community: mapping.community,
        name: mapping.name,
        email: mapping.email,
    };
    const visible = { rows: [], rowIndices: [] };
    for (let i = 0; i < sliced.rows.length; i++) {
        const excelRow = sliced.rowIndices[i];
        if (!headerRowSet.has(excelRow) && deletedEmail.has(excelRow)) continue;
        const row = sliced.rows[i].slice();
        const o = ov[excelRow] || {};
        for (const [key, colIdx] of Object.entries(colMap)) {
            if (colIdx == null || isNaN(colIdx)) continue;
            if (o[key] !== undefined && colIdx < row.length) row[colIdx] = o[key];
        }
        visible.rows.push(row);
        visible.rowIndices.push(excelRow);
    }
    const pagination = state.email.fullPreview ? { page: state.email.previewPage, pageSize: state.email.previewPageSize } : null;
    const freezeCols = getFreezeCols(state.email.mapping);
    const pageInfo = renderTable($('#email-preview-table'), visible.rows, 1, parseHeaderRows($('#email-header-rows').value), visible.rowIndices, pagination, freezeCols);
    renderPreviewPagination('email', pageInfo);
}

// 预览表格分页控件（只在全量模式且数据量超过单页时显示）
const PREVIEW_PAGE_SIZES = [10, 20, 50, 100, 500];

// 固定 3 个页码按钮，按当前页居中（边界处自动贴边）
function getFixedPages(page, totalPages) {
    if (totalPages <= 3) return Array.from({ length: totalPages }, (_, i) => i + 1);
    let start = page - 1;
    if (start < 1) start = 1;
    if (start + 2 > totalPages) start = totalPages - 2;
    return [start, start + 1, start + 2];
}

function renderPreviewPagination(kind, pageInfo) {
    const wrap = $(`#${kind}-preview-table`).closest('.table-wrap');
    let bar = wrap.nextElementSibling;
    if (!bar || !bar.classList.contains('preview-pagination-bar')) {
        bar = document.createElement('div');
        bar.className = 'pagination-bar preview-pagination-bar';
        wrap.parentNode.insertBefore(bar, wrap.nextSibling);
    }
    if (!pageInfo) { bar.innerHTML = ''; return; }

    const { page, pageSize, total, totalPages } = pageInfo;
    const start = (page - 1) * pageSize;
    const end = Math.min(start + pageSize, total);

    const fixedPages = getFixedPages(page, totalPages);
    const pageBtns = fixedPages.map(p =>
        `<button class="page-btn page-num ${p === page ? 'active' : ''}" data-page="${p}">${p}</button>`
    ).join('');

    bar.innerHTML = `
        <div class="pagination-info">第 ${start + 1}-${end} 条 / 共 ${total} 条</div>
        <div class="pagination-controls">
            <button class="page-btn nav-btn" data-page="1" ${page === 1 ? 'disabled' : ''}>首页</button>
            <button class="page-btn nav-btn" data-page="${page - 1}" ${page === 1 ? 'disabled' : ''}>上一页</button>
            ${pageBtns}
            <button class="page-btn nav-btn" data-page="${page + 1}" ${page === totalPages ? 'disabled' : ''}>下一页</button>
            <button class="page-btn nav-btn" data-page="${totalPages}" ${page === totalPages ? 'disabled' : ''}>末页</button>
        </div>
        <div class="page-size-select">
            每页
            <select class="preview-page-size">
                ${PREVIEW_PAGE_SIZES.map(s => `<option value="${s}" ${s === pageSize ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
            条
        </div>`;

    bar.querySelectorAll('.page-btn[data-page]').forEach(btn => {
        btn.addEventListener('click', () => {
            const p = parseInt(btn.dataset.page);
            if (!isNaN(p) && p >= 1 && p <= totalPages) {
                if (kind === 'import') {
                    state.importPreview.page = p;
                    renderImportPreview();
                } else {
                    state[kind].previewPage = p;
                    if (kind === 'salary') renderSalaryPreview(); else renderEmailPreview();
                }
            }
        });
    });
    bar.querySelector('.preview-page-size').addEventListener('change', (e) => {
        const size = parseInt(e.target.value);
        if (kind === 'import') {
            state.importPreview.pageSize = size;
            state.importPreview.page = 1;
            renderImportPreview();
        } else {
            state[kind].previewPageSize = size;
            state[kind].previewPage = 1;
            if (kind === 'salary') renderSalaryPreview(); else renderEmailPreview();
        }
    });
}

function populateSelect(selectEl, headers, selectedIndex) {
    selectEl.innerHTML = '';
    // 添加空选项，支持"未选择"
    if (selectedIndex === -1 || selectedIndex === null || selectedIndex === undefined) {
        const emptyOpt = document.createElement('option');
        emptyOpt.value = '';
        emptyOpt.textContent = '未选择';
        emptyOpt.selected = true;
        selectEl.appendChild(emptyOpt);
    }
    headers.forEach((h, i) => {
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = `${i + 1}. ${h}`;
        if (i === selectedIndex) opt.selected = true;
        selectEl.appendChild(opt);
    });
}

// Tabs
$$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        $$('.tab-btn').forEach(b => b.classList.remove('active'));
        $$('.tab-panel').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        $(`#tab-${tab}`).classList.add('active');
        if (tab === 'search') { loadMonths(); loadCommunities(); }
        if (tab === 'history') loadHistory();
    });
});

// Wizard navigation
function goStep(n) {
    if (!state.unlockedSteps[n]) return;
    state.currentStep = n;
    $$('.step').forEach(s => s.classList.toggle('active', parseInt(s.dataset.step) === n));
    $$('.step-content').forEach(c => c.classList.toggle('active', parseInt(c.dataset.step) === n));
    updateStepClickable();
}

function unlockStep(n) {
    state.unlockedSteps[n] = true;
    updateStepClickable();
}

function updateStepClickable() {
    $$('.step').forEach(s => {
        const step = parseInt(s.dataset.step);
        let clickable = state.unlockedSteps[step];
        // 步骤2需工资表校验通过，步骤3需邮箱表校验通过
        if (step === 2 && $('#to-step2').disabled) clickable = false;
        if (step === 3 && $('#to-step3').disabled) clickable = false;
        s.classList.toggle('clickable', clickable);
    });
}

$('#wizard-steps').addEventListener('click', (e) => {
    const stepEl = e.target.closest('.step');
    if (!stepEl) return;
    const n = parseInt(stepEl.dataset.step);
    if (!state.unlockedSteps[n]) return;
    // 顶部步骤按钮也需遵守校验：步骤2需工资表校验通过，步骤3需邮箱表校验通过
    if (n === 2 && $('#to-step2').disabled) {
        confirmModal('请先处理工资表中的异常项后再进入下一步（可点击标题旁的红色感叹号定位到异常区域）').then((ok) => {
            if (ok) {
                const el = $('#salary-validation');
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
        return;
    }
    if (n === 3 && $('#to-step3').disabled) {
        confirmModal('请先处理邮箱表中的异常项后再进入下一步（可点击标题旁的红色感叹号定位到异常区域）').then((ok) => {
            if (ok) {
                const el = $('#email-validation');
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
        return;
    }
    goStep(n);
    if (n === 3) {
        buildSummary();
        loadImportPreview();
    }
});

$('#to-step2').addEventListener('click', () => goStep(2));
$('#back-to-step1').addEventListener('click', () => goStep(1));
$('#to-step3').addEventListener('click', () => {
    buildSummary();
    goStep(3);
    loadImportPreview();
});
$('#back-to-step2').addEventListener('click', () => goStep(2));

// Salary upload
$('#salary-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    state.salary.fullPreview = false;
    state.salary.previewPage = 1;
    const fullBtn = $('#salary-full-preview-btn');
    if (fullBtn) fullBtn.classList.remove('active');
    try {
        const data = await uploadFile(file, 'salary', state.salary.fullPreview);
        if (data.error) { alert(data.error); return; }
        state.salary.fileId = data.file_id;
        state.salary.filename = data.filename;
        state.salary.sheetData = data;

    const sheetSelect = $('#salary-sheet');
    sheetSelect.innerHTML = '';
    data.sheets.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s; opt.textContent = s;
        if (s === data.default_sheet) opt.selected = true;
        sheetSelect.appendChild(opt);
    });

    // defaults: 优先使用个性化配置，否则根据检测结果
    applySectionDefaults('salary', data.detected_header_row);

    // 记住从表格/文件名解析出的年月，供第三步预填
    if (data.detected_year !== null && data.detected_month !== null) {
        state.detectedYear = data.detected_year;
        state.detectedMonth = data.detected_month;
    }

    show($('#salary-config'));
    show($('#salary-preview-area'));
    state.salary.preview = data.preview;
    state.salary.previewRowIndices = data.preview_row_indices || [];
    renderSalaryPreview();
    $('#to-step2').disabled = true;
    await parseSalary();
    } catch (err) {
        console.error(err);
        alert('上传工资表失败，请确认服务是否已启动：' + err.message);
    }
});

$('#salary-sheet').addEventListener('change', async () => {
    try {
        const data = await postJson('/api/sheet-preview', {
            file_id: state.salary.fileId,
            sheet_name: $('#salary-sheet').value,
            file_type: 'salary',
            full_preview: state.salary.fullPreview,
        });
        if (data.error) { alert(data.error); return; }
        state.salary.sheetData = data;
        state.salary.preview = data.preview;
        state.salary.previewRowIndices = data.preview_row_indices || [];
        renderSalaryPreview();
        applySectionDefaults('salary', data.detected_header_row);
        $('#to-step2').disabled = true;
        triggerParseSalary();
    } catch (err) {
        console.error(err);
        alert('切换 Sheet 失败：' + err.message);
    }
});

$('#salary-preview-btn').addEventListener('click', parseSalary);
$('#salary-status-icon').addEventListener('click', () => {
    const el = $('#salary-validation');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
});
$('#email-status-icon').addEventListener('click', () => {
    const el = $('#email-validation');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

$('#salary-full-preview-btn').addEventListener('click', async () => {
    const btn = $('#salary-full-preview-btn');
    state.salary.fullPreview = !state.salary.fullPreview;
    state.salary.previewPage = 1;
    btn.classList.toggle('active', state.salary.fullPreview);
    if (!state.salary.fileId) return;
    try {
        const data = await postJson('/api/sheet-preview', {
            file_id: state.salary.fileId,
            sheet_name: $('#salary-sheet').value,
            file_type: 'salary',
            full_preview: state.salary.fullPreview,
        });
        if (data.error) { alert(data.error); return; }
        state.salary.preview = data.preview;
        state.salary.previewRowIndices = data.preview_row_indices || [];
        renderSalaryPreview();
    } catch (err) {
        console.error(err);
        alert('刷新预览失败：' + err.message);
    }
});

function updateDataStartFromHeaders(headerInputId, dataStartId) {
    const headerRows = parseHeaderRows($(headerInputId).value);
    if (headerRows.length) {
        $(dataStartId).value = Math.max(...headerRows) + 1;
    }
}

$('#salary-header-rows').addEventListener('input', () => {
    updateDataStartFromHeaders('#salary-header-rows', '#salary-data-start');
    if (state.salary.preview) renderSalaryPreview();
    triggerParseSalary();
});

$('#salary-skip-rows').addEventListener('input', () => {
    if (state.salary.preview) renderSalaryPreview();
    triggerParseSalary();
});

$('#salary-data-end-col').addEventListener('input', () => {
    if (state.salary.preview) renderSalaryPreview();
    triggerParseSalary();
});

// 数据起始行 / 结束行 修改后重新触发解析校验
['salary-data-start', 'salary-data-end'].forEach(id => {
    const el = $(`#${id}`);
    if (el) el.addEventListener('input', triggerParseSalary);
});

// 工资字段列（银行账号 / 应发 / 代扣 / 实发）下拉框变更后重新触发解析校验
// 注意：姓名列 / 社区列 不在此列 —— 它们未发送到后端，触发会被 suggested_mapping 覆盖
['salary-bank-account-col', 'salary-gross-col', 'salary-deduction-col', 'salary-net-col'].forEach(id => {
    const el = $(`#${id}`);
    if (el) el.addEventListener('change', triggerParseSalary);
});

async function parseSalary() {
    try {
        const headerRows = parseHeaderRows($('#salary-header-rows').value);
        const dataStart = parseInt($('#salary-data-start').value);
        const dataEndVal = $('#salary-data-end').value;
        const dataEnd = dataEndVal ? parseInt(dataEndVal) : null;
        const dataEndCol = $('#salary-data-end-col').value.trim() || null;
        const skipRows = parseHeaderRows($('#salary-skip-rows').value);

        const salaryFieldMapping = {
            bank_account: $('#salary-bank-account-col').value,
            gross_salary: $('#salary-gross-col').value,
            total_deduction: $('#salary-deduction-col').value,
            net_salary: $('#salary-net-col').value,
        };

        const body = {
            file_id: state.salary.fileId,
            sheet_name: $('#salary-sheet').value,
            file_type: 'salary',
            header_rows: headerRows,
            data_start_row: dataStart,
            data_end_row: dataEnd,
            data_end_col: dataEndCol,
            skip_rows: skipRows,
            salary_field_mapping: salaryFieldMapping,
        };
        const res = await postJson('/api/parse-config', body);
        if (res.error) { alert(res.error); return; }
        state.salary.parsed = res;

        const headers = res.headers;
        const mapping = res.suggested_mapping || {};
        state.salary.mapping = mapping;
        populateSelect($('#salary-name-col'), headers, mapping.name ?? -1);
        populateSelect($('#salary-community-col'), headers, mapping.community ?? -1);

        // 填充工资字段列下拉框，优先使用用户已选值，其次用后端自动检测值
        const detected = res.detected_salary_fields || {};
        const bankSel = $('#salary-bank-account-col').value;
        const grossSel = $('#salary-gross-col').value;
        const dedSel = $('#salary-deduction-col').value;
        const netSel = $('#salary-net-col').value;
        populateSelect($('#salary-bank-account-col'), headers, bankSel !== '' ? parseInt(bankSel) : (detected.bank_account ?? -1));
        populateSelect($('#salary-gross-col'), headers, grossSel !== '' ? parseInt(grossSel) : (detected.gross_salary ?? -1));
        populateSelect($('#salary-deduction-col'), headers, dedSel !== '' ? parseInt(dedSel) : (detected.total_deduction ?? -1));
        populateSelect($('#salary-net-col'), headers, netSel !== '' ? parseInt(netSel) : (detected.net_salary ?? -1));

        const resultEl = $('#salary-parse-result');
        let resultHtml = `
            <p>解析到 <strong>${res.record_count}</strong> 条记录`;
        if (res.validation) {
            resultHtml += `，其中有效工资记录 <strong id="salary-valid-count">${res.validation.total_valid}</strong> 条`;
            if (res.validation.skipped_empty > 0) {
                resultHtml += `，跳过 ${res.validation.skipped_empty} 条社区/姓名为空的行`;
            }
        }
        resultHtml += ` <button type="button" class="help-icon-btn" id="salary-validation-help" title="查看校验说明">?</button></p>
            <p>建议列映射：姓名=${mapping.name !== undefined ? headers[mapping.name] : '未识别'}, 社区=${mapping.community !== undefined ? headers[mapping.community] : '未识别'}</p>
        `;
        resultEl.innerHTML = resultHtml;
        show(resultEl);

        const helpBtn = $('#salary-validation-help');
        if (helpBtn) helpBtn.addEventListener('click', showValidationHelp);

        initSalaryValidationState(res.validation);
        renderSalaryValidation();

        const vs = state.salary.validationState;
        const hasBlocking = vs && (
            (vs.duplicateAccountGroups && vs.duplicateAccountGroups.length > 0) ||
            (vs.accountChangedGroups && vs.accountChangedGroups.length > 0) ||
            (vs.mathMismatchIssues && vs.mathMismatchIssues.length > 0) ||
            (vs.accountEmptyIssues && vs.accountEmptyIssues.length > 0) ||
            (vs.duplicateNameGroups && vs.duplicateNameGroups.length > 0)
        );
        // 有异常时不自动滚动，由用户点击红色感叹号图标触发滚动
        updateSalaryNextButton();
        unlockStep(2);
        renderSalaryPreview();
    } catch (err) {
        console.error(err);
        alert('解析工资表失败：' + err.message);
    }
}

function initSalaryValidationState(validation) {
    if (!validation) { state.salary.validationState = null; return; }
    state.salary.overrides = {};
    state.salary.deletedRows = [];
    rebuildSalaryValidationState(validation);
}

function rebuildSalaryValidationState(validation) {
    const vs = state.salary.validationState || {};
    if (validation) {
        vs.totalValid = validation.total_valid;
        vs.skippedEmpty = validation.skipped_empty;
        vs.hasAccountField = validation.has_account_field;
        vs.hasMathFields = validation.has_math_fields;
        vs.hasPreviousData = validation.has_previous_data;
        vs.allRows = (validation.all_rows || []).map(r => ({ ...r }));
    }
    state.salary.validationState = vs;

    const ov = state.salary.overrides;
    const deleted = new Set(state.salary.deletedRows);

    const applyOverride = (r) => {
        const o = ov[r.row] || {};
        const next = {
            ...r,
            community: o.community !== undefined ? o.community : r.community,
            name: o.name !== undefined ? o.name : r.name,
            account_clean: o.account_clean !== undefined ? o.account_clean : r.account_clean,
            gross: o.gross !== undefined ? o.gross : r.gross,
            deduction: o.deduction !== undefined ? o.deduction : r.deduction,
            net: o.net !== undefined ? o.net : r.net,
        };
        if (o.account_raw !== undefined) next.account_raw = o.account_raw;
        return next;
    };

    const allRows = vs.allRows
        .filter(r => !deleted.has(r.row))
        .map(r => applyOverride(r));

    const accountCounts = {};
    allRows.forEach(r => {
        if (r.account_clean) accountCounts[r.account_clean] = (accountCounts[r.account_clean] || 0) + 1;
    });

    const seenDup = new Set();
    const dupGroups = [];
    allRows.forEach(r => {
        const acc = r.account_clean;
        if (!acc || accountCounts[acc] <= 1 || seenDup.has(acc)) return;
        const groupRows = allRows.filter(x => x.account_clean === acc);
        if (groupRows.length <= 1) return;
        dupGroups.push({ account: acc, rows: groupRows.map(r => ({ ...r, confirmed: false, editing: false, editFields: {} })) });
        seenDup.add(acc);
    });

    const mathIssues = [];
    allRows.forEach(r => {
        if (r.gross == null || r.deduction == null || r.net == null) return;
        const diff = Math.round((r.gross - r.deduction - r.net) * 100) / 100;
        if (Math.abs(diff) > 0.02) {
            mathIssues.push({ ...r, diff, confirmed: false, editing: false, editFields: {} });
        }
    });

    const accountChanged = [];
    if (vs.hasPreviousData) {
        allRows.forEach(r => {
            const prev = r.previous || "";
            const cur = r.account_clean || "";
            if (prev && cur && prev !== cur) {
                accountChanged.push({ ...r, current: cur, confirmed: false, editing: false, editFields: {} });
            }
        });
    }

    const accountEmpty = [];
    allRows.forEach(r => {
        if (!r.account_clean) {
            accountEmpty.push({ ...r, confirmed: false, editing: false, editFields: {} });
        }
    });

    // 重新计算社区+姓名重复（基于当前 overrides 和 deletedRows）
    const nameCounts = {};
    allRows.forEach(r => {
        const key = `${r.community}|${r.name}`;
        nameCounts[key] = (nameCounts[key] || 0) + 1;
    });
    const seenNameDup = new Set();
    const nameDupGroups = [];
    allRows.forEach(r => {
        const key = `${r.community}|${r.name}`;
        if (nameCounts[key] <= 1 || seenNameDup.has(key)) return;
        const groupRows = allRows.filter(x => `${x.community}|${x.name}` === key);
        nameDupGroups.push({
            community: r.community,
            name: r.name,
            rows: groupRows.map(x => ({ ...x, editing: false, editFields: {} })),
        });
        seenNameDup.add(key);
    });

    vs.duplicateAccountGroups = dupGroups;
    vs.mathMismatchIssues = mathIssues;
    vs.accountChangedGroups = accountChanged;
    vs.accountEmptyIssues = accountEmpty;
    vs.duplicateNameGroups = nameDupGroups;

    // 有效数 = 非删除且无社区+姓名重复的行数
    const duplicateNameRowCount = nameDupGroups.reduce((s, g) => s + g.rows.length, 0);
    vs.totalValid = allRows.length - duplicateNameRowCount;
    vs.deletedCount = state.salary.deletedRows.length;
}

function updateSalaryNextButton() {
    const vs = state.salary.validationState;
    if (!vs) { $('#to-step2').disabled = true; return; }
    const dupPending = vs.duplicateAccountGroups.some(g => g.rows.some(r => !r.confirmed && !r.editing));
    const changedPending = vs.accountChangedGroups.some(r => !r.confirmed && !r.editing);
    const mathPending = vs.mathMismatchIssues.some(r => !r.confirmed && !r.editing);
    const emptyPending = vs.accountEmptyIssues.some(r => !r.confirmed && !r.editing);
    const nameDup = (vs.duplicateNameGroups || []).length > 0;
    const hasError = dupPending || changedPending || mathPending || emptyPending || nameDup;
    $('#to-step2').disabled = hasError;
    // 更新状态图标
    const icon = $('#salary-status-icon');
    if (icon) {
        icon.classList.remove('ok', 'error');
        if (hasError) {
            icon.classList.add('error');
            icon.title = '检测到异常，请查看下方校验结果';
        } else {
            icon.classList.add('ok');
            icon.title = '所有数据校验通过';
        }
    }
    updateStepClickable();
}

function startSalaryEdit(groupType, groupIdx, rowIdx) {
    const vs = state.salary.validationState;
    if (!vs) return;
    if (groupType === 'dup') {
        vs.duplicateAccountGroups[groupIdx].rows[rowIdx].editing = true;
        vs.duplicateAccountGroups[groupIdx].rows[rowIdx].editFields = {};
    } else if (groupType === 'changed') {
        vs.accountChangedGroups[groupIdx].editing = true;
        vs.accountChangedGroups[groupIdx].editFields = {};
    } else if (groupType === 'math') {
        vs.mathMismatchIssues[groupIdx].editing = true;
        vs.mathMismatchIssues[groupIdx].editFields = {};
    } else if (groupType === 'empty') {
        vs.accountEmptyIssues[groupIdx].editing = true;
        vs.accountEmptyIssues[groupIdx].editFields = {};
    }
    renderSalaryValidation();
    updateSalaryNextButton();
    // 渲染完成后自动聚焦到第一个输入框
    setTimeout(() => {
        const input = document.querySelector(`[data-edit^="${groupType}|${groupIdx}|"]`);
        if (input) input.focus();
    }, 0);
}

function cancelSalaryEdit(groupType, groupIdx, rowIdx) {
    const vs = state.salary.validationState;
    if (!vs) return;
    let target = null;
    if (groupType === 'dup') target = vs.duplicateAccountGroups[groupIdx].rows[rowIdx];
    else if (groupType === 'changed') target = vs.accountChangedGroups[groupIdx];
    else if (groupType === 'math') target = vs.mathMismatchIssues[groupIdx];
    else if (groupType === 'empty') target = vs.accountEmptyIssues[groupIdx];
    if (target) { target.editing = false; target.editFields = {}; }
    renderSalaryValidation();
    updateSalaryNextButton();
}

function saveSalaryEdit(groupType, groupIdx, rowIdx) {
    const vs = state.salary.validationState;
    const ov = state.salary.overrides;
    let row = null;
    if (groupType === 'dup') row = vs.duplicateAccountGroups[groupIdx].rows[rowIdx];
    else if (groupType === 'changed') row = vs.accountChangedGroups[groupIdx];
    else if (groupType === 'math') row = vs.mathMismatchIssues[groupIdx];
    else if (groupType === 'empty') row = vs.accountEmptyIssues[groupIdx];
    if (!row) return;

    const ef = row.editFields || {};
    if (!Object.keys(ef).length) {
        row.editing = false;
        renderSalaryValidation();
        updateSalaryNextButton();
        return;
    }

    const ovForRow = ov[row.row] || {};

    if (ef.account_clean !== undefined) {
        const rawInput = String(ef.account_clean);
        const cleaned = rawInput.replace(/\s+/g, '').replace(/\D/g, '');
        if (!cleaned) { alert('账号不能为空！'); return; }
        ovForRow.account_clean = cleaned;
        ovForRow.account_raw = ef.account_clean;
    }
    if (ef.gross !== undefined) ovForRow.gross = parseFloat(ef.gross) || 0;
    if (ef.deduction !== undefined) ovForRow.deduction = parseFloat(ef.deduction) || 0;
    if (ef.net !== undefined) ovForRow.net = parseFloat(ef.net) || 0;

    ov[row.row] = ovForRow;
    rebuildSalaryValidationState();
    renderSalaryValidation();
    renderSalaryPreview();
    updateSalaryNextButton();
}

function confirmSalaryRow(groupType, groupIdx, rowIdx) {
    const vs = state.salary.validationState;
    if (!vs) return;
    let row = null;
    if (groupType === 'dup') row = vs.duplicateAccountGroups[groupIdx].rows[rowIdx];
    else if (groupType === 'changed') row = vs.accountChangedGroups[groupIdx];
    else if (groupType === 'math') row = vs.mathMismatchIssues[groupIdx];
    else if (groupType === 'empty') row = vs.accountEmptyIssues[groupIdx];
    if (!row || row.editing) return;
    row.confirmed = true;
    renderSalaryValidation();
    updateSalaryNextButton();
}

function confirmSalaryGroup(groupType, groupIdx) {
    const vs = state.salary.validationState;
    if (!vs) return;
    if (groupType === 'dup') {
        vs.duplicateAccountGroups[groupIdx].rows.forEach(r => { if (!r.editing) r.confirmed = true; });
    } else if (groupType === 'changed') {
        if (!vs.accountChangedGroups[groupIdx].editing) vs.accountChangedGroups[groupIdx].confirmed = true;
    } else if (groupType === 'math') {
        if (!vs.mathMismatchIssues[groupIdx].editing) vs.mathMismatchIssues[groupIdx].confirmed = true;
    } else if (groupType === 'empty') {
        if (!vs.accountEmptyIssues[groupIdx].editing) vs.accountEmptyIssues[groupIdx].confirmed = true;
    }
    renderSalaryValidation();
    updateSalaryNextButton();
}

function confirmAllSalaryGroups(groupType) {
    const vs = state.salary.validationState;
    if (!vs) return;
    if (groupType === 'dup') {
        vs.duplicateAccountGroups.forEach(g => g.rows.forEach(r => { if (!r.editing) r.confirmed = true; }));
    } else if (groupType === 'changed') {
        vs.accountChangedGroups.forEach(r => { if (!r.editing) r.confirmed = true; });
    } else if (groupType === 'math') {
        vs.mathMismatchIssues.forEach(r => { if (!r.editing) r.confirmed = true; });
    } else if (groupType === 'empty') {
        vs.accountEmptyIssues.forEach(r => { if (!r.editing) r.confirmed = true; });
    }
    renderSalaryValidation();
    updateSalaryNextButton();
}

function renderSalaryValidation() {
    const el = $('#salary-validation');
    const vs = state.salary.validationState;
    if (!vs) { hide(el); return; }
    show(el);

    // 同步更新顶部摘要里的"有效工资记录"数字，与下方统计保持一致
    const validCountEl = $('#salary-valid-count');
    if (validCountEl) validCountEl.textContent = vs.totalValid;

    const pendingDup = vs.duplicateAccountGroups.reduce((s, g) => s + g.rows.filter(r => !r.confirmed && !r.editing).length, 0);
    const pendingChanged = vs.accountChangedGroups.filter(r => !r.confirmed && !r.editing).length;
    const pendingMath = vs.mathMismatchIssues.filter(r => !r.confirmed && !r.editing).length;
    const pendingEmpty = vs.accountEmptyIssues.filter(r => !r.confirmed && !r.editing).length;

    let html = '';
    // 社区+姓名重复：顶部仅保留简短警告，表格移至下方待处理异常区
    if (vs.duplicateNameGroups && vs.duplicateNameGroups.length > 0) {
        html += `<div class="duplicate-name-warning">
            <strong>⚠ 检测到 ${vs.duplicateNameGroups.length} 组「社区+姓名」重复</strong>
            <p>系统默认「社区+姓名」唯一，重复会导致数据合并错乱。请在下方修改社区/姓名，或删除其中一条数据。</p>
        </div>`;
    }

    html += `<div class="stats">
        <div class="stat valid">有效：${vs.totalValid}</div>
        ${vs.deletedCount > 0 ? `<div class="stat">已删除：${vs.deletedCount}</div>` : ''}
        ${(vs.duplicateNameGroups || []).length > 0 ? `<div class="stat invalid">社区+姓名重复：${vs.duplicateNameGroups.reduce((s,g)=>s+g.rows.length,0)}</div>` : ''}
        ${vs.skippedEmpty > 0 ? `<div class="stat">跳过空行：${vs.skippedEmpty}</div>` : ''}
    `;
    if (!vs.hasAccountField) {
        html += `<div class="stat invalid">未检测到"银行卡号"列</div>`;
    } else {
        if (pendingDup > 0) html += `<div class="stat invalid">待确认重复账号：${pendingDup}</div>`;
        if (pendingEmpty > 0) html += `<div class="stat invalid">账号为空：${pendingEmpty}</div>`;
    }
    if (vs.hasPreviousData && pendingChanged > 0) {
        html += `<div class="stat invalid">账号变化待确认：${pendingChanged}</div>`;
    }
    if (vs.hasMathFields && pendingMath > 0) {
        html += `<div class="stat invalid">实发工资对不上：${pendingMath}</div>`;
    }
    html += `</div>`;

    if (!vs.hasAccountField && !vs.hasMathFields && !vs.hasPreviousData) {
        html += `<p>（未检测到账号/工资计算字段，以上校验未执行）</p>`;
        el.innerHTML = html;
        return;
    }

    const confirmBtn = (groupType, gi, ri, confirmed) => {
        if (confirmed) return '<span class="confirmed-tag">已确认</span>';
        return `<button class="btn small" data-salary-confirm="${groupType}|${gi}|${ri}">确认</button>`;
    };

    if (vs.duplicateAccountGroups.length > 0) {
        html += `<div class="duplicate-section"><h4>重复银行卡号 ${pendingDup > 0 ? `<button class="btn small" data-salary-confirm-all="dup">全部确认</button>` : ''}</h4>`;
        vs.duplicateAccountGroups.forEach((g, gi) => {
            const allConfirmed = g.rows.every(r => r.confirmed);
            html += `<div class="duplicate-group ${allConfirmed ? 'confirmed' : ''}">
                <div class="group-header">
                    <span class="group-email">${escapeHtml(g.account)}</span>
                    <span class="group-count">共 ${g.rows.length} 人</span>
                    ${allConfirmed ? '<span class="group-status">已确认</span>' : `<button class="btn small" data-salary-group-confirm="dup|${gi}">全部确认</button>`}
                </div>
                <table class="salary-table"><thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>原始账号</th><th>账号（可编辑）</th><th>操作</th></tr></thead><tbody>`;
            g.rows.forEach((r, ri) => {
                if (r.editing) {
                    html += `<tr class="editing-row">
                        <td>${escapeHtml(r.row)}</td>
                        <td>${escapeHtml(r.community)}</td>
                        <td>${escapeHtml(r.name)}</td>
                        <td>${escapeHtml(r.account_raw || '')}</td>
                        <td><input class="salary-edit-input" type="text" data-edit="dup|${gi}|${ri}|account_clean" value="${escapeHtml(r.editFields.account_clean !== undefined ? r.editFields.account_clean : r.account_clean)}" style="width:140px"></td>
                        <td><button class="btn small primary" data-salary-save="dup|${gi}|${ri}">保存</button>
                            <button class="btn small" data-salary-cancel="dup|${gi}|${ri}">取消</button></td>
                    </tr>`;
                } else {
                    html += `<tr class="${r.confirmed ? 'confirmed-row' : ''}">
                        <td>${escapeHtml(r.row)}</td>
                        <td>${escapeHtml(r.community)}</td>
                        <td>${escapeHtml(r.name)}</td>
                        <td>${escapeHtml(r.account_raw || '')}</td>
                        <td>${escapeHtml(r.account_clean)}</td>
                        <td><button class="btn small" data-salary-edit="dup|${gi}|${ri}">编辑</button>
                            ${confirmBtn('dup', gi, ri, r.confirmed)}</td>
                    </tr>`;
                }
            });
            html += `</tbody></table></div>`;
        });
        html += `</div>`;
    }

    if (vs.accountChangedGroups.length > 0) {
        html += `<div class="duplicate-section"><h4>账号变化（与上次导入对比） ${pendingChanged > 0 ? `<button class="btn small" data-salary-confirm-all="changed">全部确认</button>` : ''}</h4>`;
        vs.accountChangedGroups.forEach((r, gi) => {
            const allConfirmed = r.confirmed;
            if (r.editing) {
                html += `<div class="duplicate-group editing">
                    <div class="group-header"><span class="group-email">${escapeHtml(r.community)} / ${escapeHtml(r.name)}</span></div>
                    <table class="salary-table" style="table-layout:fixed"><colgroup><col style="width:60px"><col style="width:180px"><col style="width:180px"><col style="width:160px"></colgroup><thead><tr><th>行号</th><th>上次账号</th><th>本次账号（可编辑）</th><th>操作</th></tr></thead><tbody>
                    <tr><td>${escapeHtml(r.row)}</td><td>${escapeHtml(r.previous || '')}</td>
                    <td><input class="salary-edit-input" type="text" data-edit="changed|${gi}|-|account_clean" value="${escapeHtml(r.editFields.account_clean !== undefined ? r.editFields.account_clean : r.account_clean)}" style="width:100%;box-sizing:border-box"></td>
                    <td><button class="btn small primary" data-salary-save="changed|${gi}|-">保存</button>
                        <button class="btn small text" data-salary-cancel="changed|${gi}|-">取消</button></td></tr>
                    </tbody></table></div>`;
            } else {
                html += `<div class="duplicate-group ${allConfirmed ? 'confirmed' : ''}">
                    <div class="group-header">
                        <span class="group-email">${escapeHtml(r.community)} / ${escapeHtml(r.name)}</span>
                    </div>
                    <table class="salary-table" style="table-layout:fixed"><colgroup><col style="width:60px"><col style="width:180px"><col style="width:180px"><col style="width:160px"></colgroup><thead><tr><th>行号</th><th>上次账号</th><th>本次账号</th><th>操作</th></tr></thead><tbody>
                    <tr class="${allConfirmed ? 'confirmed-row' : ''}"><td>${escapeHtml(r.row)}</td><td>${escapeHtml(r.previous || '')}</td><td>${escapeHtml(r.account_clean)}</td>
                    <td>${!allConfirmed ? `<button class="btn small" data-salary-confirm="changed|${gi}|-">确认</button>` : '<span class="confirmed-tag">已确认</span>'}
                        <button class="btn small text" data-salary-edit="changed|${gi}|-">修改</button>
                        <button class="btn small text danger" data-salary-delete="changed|${gi}|-">删除</button></td></tr>
                    </tbody></table></div>`;
            }
        });
        html += `</div>`;
    }

    if (vs.mathMismatchIssues.length > 0) {
        html += `<div class="duplicate-section"><h4>实发工资对不上（应发 - 代扣 ≠ 实发） ${pendingMath > 0 ? `<button class="btn small" data-salary-confirm-all="math">全部确认</button>` : ''}</h4>`;
        vs.mathMismatchIssues.forEach((r, gi) => {
            if (r.editing) {
                html += `<div class="duplicate-group editing">
                    <div class="group-header"><span class="group-email">${escapeHtml(r.community)} / ${escapeHtml(r.name)}</span></div>
                    <table class="salary-table"><thead><tr><th>行号</th><th>应发工资</th><th>代扣合计</th><th>实发工资</th><th>差额</th><th>操作</th></tr></thead><tbody>
                    <tr><td>${escapeHtml(r.row)}</td>
                    <td><input class="salary-edit-input" type="number" step="0.01" data-edit="math|${gi}|-|gross" value="${r.editFields.gross !== undefined ? r.editFields.gross : r.gross}" style="width:100px"></td>
                    <td><input class="salary-edit-input" type="number" step="0.01" data-edit="math|${gi}|-|deduction" value="${r.editFields.deduction !== undefined ? r.editFields.deduction : r.deduction}" style="width:100px"></td>
                    <td><input class="salary-edit-input" type="number" step="0.01" data-edit="math|${gi}|-|net" value="${r.editFields.net !== undefined ? r.editFields.net : r.net}" style="width:100px"></td>
                    <td style="color:var(--danger)">${r.diff > 0 ? '+' : ''}${r.diff}</td>
                    <td><button class="btn small primary" data-salary-save="math|${gi}|-">保存</button>
                        <button class="btn small" data-salary-cancel="math|${gi}|-">取消</button></td></tr>
                    </tbody></table></div>`;
            } else {
                html += `<div class="duplicate-group ${r.confirmed ? 'confirmed' : ''}">
                    <div class="group-header">
                        <span class="group-email">${escapeHtml(r.community)} / ${escapeHtml(r.name)}</span>
                        ${r.confirmed ? '<span class="group-status">已确认</span>' : `<button class="btn small" data-salary-confirm="math|${gi}|-">确认</button>`}
                    </div>
                    <table class="salary-table"><thead><tr><th>行号</th><th>应发工资</th><th>代扣合计</th><th>实发工资</th><th>差额</th><th>操作</th></tr></thead><tbody>
                    <tr class="${r.confirmed ? 'confirmed-row' : ''}"><td>${escapeHtml(r.row)}</td><td>${r.gross}</td><td>${r.deduction}</td><td>${r.net}</td>
                    <td style="color:var(--danger)">${r.diff > 0 ? '+' : ''}${r.diff}</td>
                    <td><button class="btn small" data-salary-edit="math|${gi}|-">编辑</button>${r.confirmed ? '<span class="confirmed-tag">已确认</span>' : ''}</td></tr>
                    </tbody></table></div>`;
            }
        });
        html += `</div>`;
    }

    if (vs.accountEmptyIssues.length > 0) {
        html += `<div class="duplicate-section"><h4>账号为空 ${pendingEmpty > 0 ? `<button class="btn small" data-salary-confirm-all="empty">全部确认</button>` : ''}</h4>`;
        vs.accountEmptyIssues.forEach((r, gi) => {
            if (r.editing) {
                html += `<div class="duplicate-group editing">
                    <div class="group-header"><span class="group-email">${escapeHtml(r.community)} / ${escapeHtml(r.name)}</span></div>
                    <table class="salary-table"><thead><tr><th>行号</th><th>原始值</th><th>账号（可编辑）</th><th>操作</th></tr></thead><tbody>
                    <tr><td>${escapeHtml(r.row)}</td><td>${escapeHtml(r.account_raw || '(空)')}</td>
                    <td><input class="salary-edit-input" type="text" data-edit="empty|${gi}|-|account_clean" value="${escapeHtml(r.editFields.account_clean !== undefined ? r.editFields.account_clean : '')}" style="width:140px"></td>
                    <td><button class="btn small primary" data-salary-save="empty|${gi}|-">保存</button>
                        <button class="btn small" data-salary-cancel="empty|${gi}|-">取消</button></td></tr>
                    </tbody></table></div>`;
            } else {
                html += `<div class="duplicate-group ${r.confirmed ? 'confirmed' : ''}">
                    <div class="group-header">
                        <span class="group-email">${escapeHtml(r.community)} / ${escapeHtml(r.name)}</span>
                        ${r.confirmed ? '<span class="group-status">已确认</span>' : `<button class="btn small" data-salary-confirm="empty|${gi}|-">确认</button>`}
                    </div>
                    <table class="salary-table"><thead><tr><th>行号</th><th>原始值</th><th>当前</th><th>操作</th></tr></thead><tbody>
                    <tr class="${r.confirmed ? 'confirmed-row' : ''}"><td>${escapeHtml(r.row)}</td><td>${escapeHtml(r.account_raw || '(空)')}</td><td>${escapeHtml(r.account_clean || '(空)')}</td>
                    <td><button class="btn small" data-salary-edit="empty|${gi}|-">编辑</button>${r.confirmed ? '<span class="confirmed-tag">已确认</span>' : ''}</td></tr>
                    </tbody></table></div>`;
            }
        });
        html += `</div>`;
    }

    // 社区+姓名重复（可修改/删除）
    if (vs.duplicateNameGroups && vs.duplicateNameGroups.length > 0) {
        html += `<div class="duplicate-section"><h4>社区+姓名重复</h4>`;
        vs.duplicateNameGroups.forEach((g, gi) => {
            html += `<div class="duplicate-group">
                <div class="group-header">
                    <span class="group-email">${escapeHtml(g.community)} - ${escapeHtml(g.name)}</span>
                    <span class="group-count">共 ${g.rows.length} 条</span>
                </div>
                <table class="salary-table" style="table-layout:fixed"><colgroup><col style="width:60px"><col style="width:180px"><col style="width:180px"><col style="width:160px"></colgroup><thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>操作</th></tr></thead><tbody>`;
            g.rows.forEach((r, ri) => {
                if (r.editing) {
                    html += `<tr class="editing-row">
                        <td>${escapeHtml(r.row)}</td>
                        <td><input class="salary-edit-input" type="text" data-name-edit-input="community|${gi}|${ri}" value="${escapeHtml(r.editFields.community !== undefined ? r.editFields.community : r.community)}" style="width:100%;box-sizing:border-box"></td>
                        <td><input class="salary-edit-input" type="text" data-name-edit-input="name|${gi}|${ri}" value="${escapeHtml(r.editFields.name !== undefined ? r.editFields.name : r.name)}" style="width:100%;box-sizing:border-box"></td>
                        <td>
                            <button class="btn small primary" data-name-save="${gi}|${ri}">保存</button>
                            <button class="btn small text" data-name-cancel="${gi}|${ri}">取消</button>
                        </td>
                    </tr>`;
                } else {
                    html += `<tr>
                        <td>${escapeHtml(r.row)}</td>
                        <td>${escapeHtml(r.community)}</td>
                        <td>${escapeHtml(r.name)}</td>
                        <td>
                            <button class="btn small" data-name-edit="${gi}|${ri}">修改</button>
                            <button class="btn small text danger" data-name-delete="${gi}|${ri}">删除</button>
                        </td>
                    </tr>`;
                }
            });
            html += `</tbody></table></div>`;
        });
        html += `</div>`;
    }

    if (vs.duplicateAccountGroups.length === 0 && vs.accountChangedGroups.length === 0 && vs.mathMismatchIssues.length === 0 && vs.accountEmptyIssues.length === 0 && (vs.duplicateNameGroups || []).length === 0) {
        html += `<p>所有数据均校验通过 ✅</p>`;
        
        // 检查是否有修改或删除操作，如果有则显示写回按钮
        const hasOverrides = Object.keys(state.salary.overrides).length > 0;
        const hasDeletedRows = state.salary.deletedRows.length > 0;
        
        console.log('写回按钮条件检查:', {
            hasOverrides,
            hasDeletedRows,
            overridesCount: Object.keys(state.salary.overrides).length,
            deletedRowsCount: state.salary.deletedRows.length
        });
        
        if (hasOverrides || hasDeletedRows) {
            html += `<div style="margin-top: 16px;">
                <button id="write-back-btn" class="btn primary">保存修改到源文件</button>
            </div>`;
            console.log('✅ 写回按钮已渲染');
        } else {
            console.log('❌ 写回按钮未渲染（无修改或删除）');
        }
    }

    el.innerHTML = html;
    
    // 绑定写回按钮事件
    const writeBackBtn = $('#write-back-btn');
    if (writeBackBtn) {
        writeBackBtn.addEventListener('click', async () => {
            console.log('点击写回按钮');
            console.log('overrides:', state.salary.overrides);
            console.log('deletedRows:', state.salary.deletedRows);
            console.log('fileId:', state.salary.fileId);
            console.log('sheetName:', $('#salary-sheet').value);
            
            const confirmed = await confirmModal(`此操作将直接修改源文件 "${state.salary.filename}"，是否继续？`);
            if (!confirmed) {
                console.log('用户取消');
                return;
            }
            
            try {
                // 获取当前的配置
                const salaryConfig = {
                    header_rows: $('#salary-header-rows').value,
                    data_start: $('#salary-data-start').value
                };
                
                console.log('发送请求:', {
                    file_id: state.salary.fileId,
                    sheet_name: $('#salary-sheet').value,
                    overrides: state.salary.overrides,
                    deleted_rows: state.salary.deletedRows,
                    salary_config: salaryConfig
                });
                
                const res = await postJson('/api/write-back-salary', {
                    file_id: state.salary.fileId,
                    sheet_name: $('#salary-sheet').value,
                    overrides: state.salary.overrides,
                    deleted_rows: state.salary.deletedRows,
                    salary_config: salaryConfig,
                    original_filename: state.salary.filename  // 传递原始文件名
                });
                
                console.log('响应:', res);
                
                if (res.error) {
                    alert('写回失败: ' + res.error);
                } else {
                    await infoModal('已成功写回源文件');
                    // 清除已应用的修改，避免重复写回
                    state.salary.overrides = {};
                    state.salary.deletedRows = [];
                    // 重新渲染验证报告
                    renderSalaryValidation();
                }
            } catch (err) {
                console.error('写回异常:', err);
                alert('写回失败: ' + err.message);
            }
        });
    }

    el.querySelectorAll('[data-salary-edit]').forEach(btn => {
        btn.addEventListener('click', () => {
            const parts = btn.dataset.salaryEdit.split('|');
            const rowIdx = parts[2] === '-' ? -1 : parseInt(parts[2]);
            startSalaryEdit(parts[0], parseInt(parts[1]), rowIdx);
        });
    });
    el.querySelectorAll('[data-salary-save]').forEach(btn => {
        btn.addEventListener('click', () => {
            const parts = btn.dataset.salarySave.split('|');
            const rowIdx = parts[2] === '-' ? -1 : parseInt(parts[2]);
            saveSalaryEdit(parts[0], parseInt(parts[1]), rowIdx);
        });
    });
    el.querySelectorAll('[data-salary-cancel]').forEach(btn => {
        btn.addEventListener('click', () => {
            const parts = btn.dataset.salaryCancel.split('|');
            const rowIdx = parts[2] === '-' ? -1 : parseInt(parts[2]);
            cancelSalaryEdit(parts[0], parseInt(parts[1]), rowIdx);
        });
    });
    el.querySelectorAll('[data-salary-confirm]').forEach(btn => {
        btn.addEventListener('click', () => {
            const parts = btn.dataset.salaryConfirm.split('|');
            const rowIdx = parts[2] === '-' ? -1 : parseInt(parts[2]);
            confirmSalaryRow(parts[0], parseInt(parts[1]), rowIdx);
        });
    });
    el.querySelectorAll('[data-salary-group-confirm]').forEach(btn => {
        btn.addEventListener('click', () => {
            const parts = btn.dataset.salaryGroupConfirm.split('|');
            confirmSalaryGroup(parts[0], parseInt(parts[1]));
        });
    });
    el.querySelectorAll('[data-salary-confirm-all]').forEach(btn => {
        btn.addEventListener('click', () => confirmAllSalaryGroups(btn.dataset.salaryConfirmAll));
    });
    el.querySelectorAll('[data-edit]').forEach(input => {
        input.addEventListener('input', () => {
            const parts = input.dataset.edit.split('|');
            const field = parts[3];
            const groupType = parts[0], groupIdx = parseInt(parts[1]), rowIdx = parts[2] === '-' ? -1 : parseInt(parts[2]);
            let row = null;
            if (groupType === 'dup') row = state.salary.validationState.duplicateAccountGroups[groupIdx].rows[rowIdx];
            else if (groupType === 'changed') row = state.salary.validationState.accountChangedGroups[groupIdx];
            else if (groupType === 'math') row = state.salary.validationState.mathMismatchIssues[groupIdx];
            else if (groupType === 'empty') row = state.salary.validationState.accountEmptyIssues[groupIdx];
            if (row) { row.editFields[field] = input.value; }
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const parts = input.dataset.edit.split('|');
                const groupType = parts[0], groupIdx = parseInt(parts[1]), rowIdx = parts[2] === '-' ? -1 : parseInt(parts[2]);
                saveSalaryEdit(groupType, groupIdx, rowIdx);
            }
            if (e.key === 'Escape') {
                const parts = input.dataset.edit.split('|');
                const groupType = parts[0], groupIdx = parseInt(parts[1]), rowIdx = parts[2] === '-' ? -1 : parseInt(parts[2]);
                cancelSalaryEdit(groupType, groupIdx, rowIdx);
            }
        });
    });

    // 社区+姓名重复：修改/删除
    el.querySelectorAll('[data-name-edit]').forEach(btn => {
        btn.addEventListener('click', () => {
            const [gi, ri] = btn.dataset.nameEdit.split('|').map(Number);
            const row = state.salary.validationState.duplicateNameGroups[gi].rows[ri];
            row.editing = true;
            row.editFields = { community: row.community, name: row.name };
            renderSalaryValidation();
            // 渲染完成后自动聚焦到社区输入框
            setTimeout(() => {
                const input = document.querySelector(`[data-name-edit-input="community|${gi}|${ri}"]`);
                if (input) input.focus();
            }, 0);
        });
    });
    el.querySelectorAll('[data-name-save]').forEach(btn => {
        btn.addEventListener('click', () => {
            const [gi, ri] = btn.dataset.nameSave.split('|').map(Number);
            const row = state.salary.validationState.duplicateNameGroups[gi].rows[ri];
            const excelRow = row.row;
            state.salary.overrides[excelRow] = state.salary.overrides[excelRow] || {};
            if (row.editFields.community !== undefined) state.salary.overrides[excelRow].community = row.editFields.community;
            if (row.editFields.name !== undefined) state.salary.overrides[excelRow].name = row.editFields.name;
            rebuildSalaryValidationState();
            renderSalaryValidation();
            renderSalaryPreview();
            updateSalaryNextButton();
        });
    });
    el.querySelectorAll('[data-name-cancel]').forEach(btn => {
        btn.addEventListener('click', () => {
            const [gi, ri] = btn.dataset.nameCancel.split('|').map(Number);
            state.salary.validationState.duplicateNameGroups[gi].rows[ri].editing = false;
            renderSalaryValidation();
            renderSalaryPreview();
        });
    });
    el.querySelectorAll('[data-name-delete]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const [gi, ri] = btn.dataset.nameDelete.split('|').map(Number);
            const row = state.salary.validationState.duplicateNameGroups[gi].rows[ri];
            if (!await confirmModal(`确定删除第 ${row.row} 行（${row.community} - ${row.name}）的数据吗？删除后该条工资记录不会导入。`)) return;
            state.salary.deletedRows.push(row.row);
            rebuildSalaryValidationState();
            renderSalaryValidation();
            renderSalaryPreview();
            updateSalaryNextButton();
        });
    });
    el.querySelectorAll('[data-name-edit-input]').forEach(input => {
        input.addEventListener('input', () => {
            const [field, gi, ri] = input.dataset.nameEditInput.split('|');
            const row = state.salary.validationState.duplicateNameGroups[parseInt(gi)].rows[parseInt(ri)];
            row.editFields[field] = input.value;
        });
    });

    // 账号变化：删除
    el.querySelectorAll('[data-salary-delete]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const parts = btn.dataset.salaryDelete.split('|');
            const groupType = parts[0], groupIdx = parseInt(parts[1]);
            if (groupType !== 'changed') return;
            const row = state.salary.validationState.accountChangedGroups[groupIdx];
            if (!await confirmModal(`确定删除第 ${row.row} 行（${row.community} - ${row.name}）的数据吗？删除后该条工资记录不会导入。`)) return;
            state.salary.deletedRows.push(row.row);
            rebuildSalaryValidationState();
            renderSalaryValidation();
            renderSalaryPreview();
            updateSalaryNextButton();
        });
    });
}

// Email upload
$('#email-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    state.email.fullPreview = false;
    state.email.previewPage = 1;
    const fullBtn = $('#email-full-preview-btn');
    if (fullBtn) fullBtn.classList.remove('active');
    try {
        const data = await uploadFile(file, 'email');
        if (data.error) { alert(data.error); return; }
        state.email.fileId = data.file_id;
        state.email.filename = data.filename;
        state.email.sheetData = data;

        const sheetSelect = $('#email-sheet');
        sheetSelect.innerHTML = '';
        data.sheets.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s; opt.textContent = s;
            if (s === data.default_sheet) opt.selected = true;
            sheetSelect.appendChild(opt);
        });

        applySectionDefaults('email', data.detected_header_row);

        show($('#email-config'));
        show($('#email-preview-area'));
        state.email.preview = data.preview;
        state.email.previewRowIndices = data.preview_row_indices || [];
        renderEmailPreview();
        $('#to-step3').disabled = true;
        await parseEmail();
    } catch (err) {
        console.error(err);
        alert('上传邮箱表失败，请确认服务是否已启动：' + err.message);
    }
});

$('#email-sheet').addEventListener('change', async () => {
    try {
        const data = await postJson('/api/sheet-preview', {
            file_id: state.email.fileId,
            sheet_name: $('#email-sheet').value,
            file_type: 'email',
            full_preview: state.email.fullPreview,
        });
        if (data.error) { alert(data.error); return; }
        state.email.sheetData = data;
        state.email.preview = data.preview;
        state.email.previewRowIndices = data.preview_row_indices || [];
        renderEmailPreview();
        applySectionDefaults('email', data.detected_header_row);
        $('#to-step3').disabled = true;
        triggerParseEmail();
    } catch (err) {
        console.error(err);
        alert('切换 Sheet 失败：' + err.message);
    }
});

$('#email-preview-btn').addEventListener('click', parseEmail);

$('#email-full-preview-btn').addEventListener('click', async () => {
    const btn = $('#email-full-preview-btn');
    state.email.fullPreview = !state.email.fullPreview;
    state.email.previewPage = 1;
    btn.classList.toggle('active', state.email.fullPreview);
    if (!state.email.fileId) return;
    try {
        const data = await postJson('/api/sheet-preview', {
            file_id: state.email.fileId,
            sheet_name: $('#email-sheet').value,
            file_type: 'email',
            full_preview: state.email.fullPreview,
        });
        if (data.error) { alert(data.error); return; }
        state.email.preview = data.preview;
        state.email.previewRowIndices = data.preview_row_indices || [];
        renderEmailPreview();
    } catch (err) {
        console.error(err);
        alert('刷新预览失败：' + err.message);
    }
});

$('#email-header-rows').addEventListener('input', () => {
    updateDataStartFromHeaders('#email-header-rows', '#email-data-start');
    if (state.email.preview) renderEmailPreview();
    triggerParseEmail();
});

$('#email-skip-rows').addEventListener('input', () => {
    if (state.email.preview) renderEmailPreview();
    triggerParseEmail();
});

$('#email-data-end-col').addEventListener('input', () => {
    if (state.email.preview) renderEmailPreview();
    triggerParseEmail();
});

// 数据起始行 / 结束行 修改后重新触发解析校验
['email-data-start', 'email-data-end'].forEach(id => {
    const el = $(`#${id}`);
    if (el) el.addEventListener('input', triggerParseEmail);
});

async function parseEmail() {
    try {
        const headerRows = parseHeaderRows($('#email-header-rows').value);
        const dataStart = parseInt($('#email-data-start').value);
        const dataEndVal = $('#email-data-end').value;
        const dataEnd = dataEndVal ? parseInt(dataEndVal) : null;
        const dataEndCol = $('#email-data-end-col').value.trim() || null;
        const skipRows = parseHeaderRows($('#email-skip-rows').value);

        const body = {
            file_id: state.email.fileId,
            sheet_name: $('#email-sheet').value,
            file_type: 'email',
            header_rows: headerRows,
            data_start_row: dataStart,
            data_end_row: dataEnd,
            data_end_col: dataEndCol,
            skip_rows: skipRows,
        };
        const res = await postJson('/api/parse-config', body);
        if (res.error) { alert(res.error); return; }
        state.email.parsed = res;
        state.email.overrides = {};
        state.email.overrideLog = {};
        state.email.deletedRows = [];
        state.email.mapping = res.suggested_mapping || {};

        const headers = res.headers;
        const mapping = state.email.mapping;
        populateSelect($('#email-name-col'), headers, mapping.name ?? -1);
        populateSelect($('#email-community-col'), headers, mapping.community ?? -1);
        populateSelect($('#email-col'), headers, mapping.email ?? -1);

        initValidationState(res.validation);
        renderValidation();
        updateStep3Button();
        unlockStep(3);
        renderEmailPreview();
    } catch (err) {
        console.error(err);
        alert('解析邮箱表失败：' + err.message);
    }
}

// Email validation state management
function initValidationState(validation) {
    if (!validation) {
        state.email.validationState = null;
        return;
    }
    state.email.confirmedRows = [];
    state.email.editing = null;
    state.email.validationState = {
        allRows: validation.all_rows || [],
        total: validation.total,
        valid: validation.valid,
        invalid: validation.invalid,
        issues: [],
        duplicateGroups: [],
        duplicateNameGroups: [],
    };
    rebuildValidationState();
}

// 清洗与报错文案与后端 clean_email / validate_email 保持一致
function validateEmailLocal(email) {
    const cleaned = String(email == null ? '' : email)
        .trim().replace(/[\s\n\r\t]+/g, '').replace(/＠/g, '@').toLowerCase();
    if (!cleaned) return { email: '', errors: ['邮箱为空'] };
    const errors = [];
    if (!cleaned.includes('@')) errors.push('缺少 @ 符号');
    if (!/^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(cleaned)) errors.push('邮箱格式无效');
    return { email: cleaned, errors };
}

// 依据原始清洗结果 + 本地覆盖/删除，重算三类异常分组
function rebuildValidationState() {
    const vs = state.email.validationState;
    if (!vs) return;
    const ov = state.email.overrides || {};
    const deleted = new Set(state.email.deletedRows || []);

    const resolve = r => {
        const o = ov[r.row] || {};
        const community = String(o.community !== undefined ? o.community : (r.community || '')).trim();
        const name = String(o.name !== undefined ? o.name : (r.name || '')).trim();
        const raw = o.email !== undefined ? o.email : r.raw;
        const v = validateEmailLocal(raw);
        return { ...r, community, name, raw, current: v.email, errors: v.errors };
    };
    const rows = vs.allRows.filter(r => !deleted.has(r.row)).map(resolve);

    const emailCounts = {};
    rows.forEach(r => { if (r.current) emailCounts[r.current] = (emailCounts[r.current] || 0) + 1; });
    const byEmail = {};
    rows.forEach(r => {
        if (!r.current || r.errors.length || emailCounts[r.current] <= 1) return;
        (byEmail[r.current] = byEmail[r.current] || []).push(r);
    });
    vs.duplicateGroups = Object.entries(byEmail).map(([email, groupRows]) => ({ email, rows: groupRows }));

    const dupEmailRows = new Set();
    vs.duplicateGroups.forEach(g => g.rows.forEach(r => dupEmailRows.add(r.row)));
    vs.issues = rows.filter(r => r.errors.length && !dupEmailRows.has(r.row));

    const nameCounts = {};
    rows.forEach(r => { if (r.community && r.name) { const k = `${r.community}|${r.name}`; nameCounts[k] = (nameCounts[k] || 0) + 1; } });
    const byName = {};
    rows.forEach(r => {
        if (!r.community || !r.name) return;
        const k = `${r.community}|${r.name}`;
        if (nameCounts[k] <= 1) return;
        (byName[k] = byName[k] || []).push(r);
    });
    vs.duplicateNameGroups = Object.entries(byName).map(([k, groupRows]) => ({
        community: groupRows[0].community,
        name: groupRows[0].name,
        count: groupRows.length,
        rows: groupRows,
    }));

    vs.deletedRecords = vs.allRows.filter(r => deleted.has(r.row)).map(resolve);
    
    // total 是总行数（不含已删除），不随确认变化
    vs.total = rows.length;
    
    // 计算有效/无效时，排除已确认的行
    const confirmedSet = new Set(state.email.confirmedRows || []);
    const unconfirmedDupRows = vs.duplicateGroups.reduce((acc, g) => 
        acc.concat(g.rows.filter(r => !confirmedSet.has(r.row))), []);
    const unconfirmedIssues = vs.issues.filter(r => !confirmedSet.has(r.row));
    
    vs.invalid = unconfirmedDupRows.length + unconfirmedIssues.length;
    vs.valid = vs.total - vs.invalid;
}

function findEmailRow(row) {
    const vs = state.email.validationState;
    if (!vs) return null;
    const all = vs.issues
        .concat(vs.duplicateGroups.reduce((acc, g) => acc.concat(g.rows), []))
        .concat(vs.duplicateNameGroups.reduce((acc, g) => acc.concat(g.rows), []));
    return all.find(r => r.row === row) || null;
}

function emailRowConfirmed(row) {
    return (state.email.confirmedRows || []).indexOf(row) >= 0;
}

function confirmEmailRows(rows) {
    rows.forEach(r => { if (!emailRowConfirmed(r)) state.email.confirmedRows.push(r); });
    renderValidation();
    updateStep3Button();
}

function confirmAllIssues() {
    const vs = state.email.validationState;
    confirmEmailRows(vs.issues.filter(r => !emailRowConfirmed(r.row)).map(r => r.row));
}

function confirmAllDuplicates() {
    const vs = state.email.validationState;
    confirmEmailRows(vs.duplicateGroups.reduce((acc, g) => acc.concat(g.rows.map(r => r.row)), []));
}

function startEmailEdit(row, kind) {
    const rec = findEmailRow(row);
    const editing = { row, kind, value: '', community: '', name: '' };
    if (rec) {
        editing.value = rec.current || '';
        editing.community = rec.community || '';
        editing.name = rec.name || '';
    }
    state.email.editing = editing;
    renderValidation();
    // 渲染完成后自动聚焦到第一个输入框
    setTimeout(() => {
        let input = null;
        if (kind === 'email') {
            input = document.querySelector(`[data-email-value="${row}"]`);
        } else if (kind === 'identity') {
            input = document.querySelector(`[data-email-community="${row}"]`);
        }
        if (input) input.focus();
    }, 0);
}

function cancelEmailEdit() {
    state.email.editing = null;
    renderValidation();
}

function saveEmailEdit(row) {
    const editing = state.email.editing;
    if (!editing || editing.row !== row) return;
    const rec = findEmailRow(row);
    const ov = { ...(state.email.overrides[row] || {}) };

    if (editing.kind === 'email') {
        const v = validateEmailLocal(editing.value);
        if (v.errors.length) { alert('邮箱格式不正确：' + v.errors.join('；')); return; }
        state.email.overrides[row] = { ...ov, email: v.email };
        state.email.overrideLog[row] = {
            row,
            community: rec ? rec.community : '',
            name: rec ? rec.name : '',
            original: rec ? rec.raw : '',
            current: v.email,
        };
    } else {
        const community = editing.community.trim();
        const name = editing.name.trim();
        if (!community || !name) { alert('社区和姓名不能为空'); return; }
        state.email.overrides[row] = { ...ov, community, name };
    }
    // 修改后需重新确认
    state.email.confirmedRows = state.email.confirmedRows.filter(r => r !== row);
    state.email.editing = null;
    rebuildValidationState();
    renderValidation();
    renderEmailPreview();
    updateStep3Button();
}

async function deleteEmailRow(row) {
    const rec = findEmailRow(row);
    const label = rec ? `${rec.community} - ${rec.name}` : '';
    if (!await confirmModal(`确定删除第 ${row} 行（${label}）的邮箱记录吗？删除后该人员在合并时会变成「未匹配」。`)) return;
    if (!state.email.deletedRows.includes(row)) state.email.deletedRows.push(row);
    state.email.confirmedRows = state.email.confirmedRows.filter(r => r !== row);
    state.email.editing = null;
    rebuildValidationState();
    renderValidation();
    renderEmailPreview();
    updateStep3Button();
}

function restoreEmailRow(row) {
    state.email.deletedRows = state.email.deletedRows.filter(r => r !== row);
    rebuildValidationState();
    renderValidation();
    renderEmailPreview();
    updateStep3Button();
}

function updateStep3Button() {
    const vs = state.email.validationState;
    if (!vs) {
        $('#to-step3').disabled = true;
        updateStepClickable();
        return;
    }
    const pending = vs.issues.some(r => !emailRowConfirmed(r.row))
        || vs.duplicateGroups.some(g => g.rows.some(r => !emailRowConfirmed(r.row)));
    const nameDup = vs.duplicateNameGroups.length > 0;
    const hasError = pending || nameDup;
    $('#to-step3').disabled = hasError;
    // 更新状态图标
    const icon = $('#email-status-icon');
    if (icon) {
        icon.classList.remove('ok', 'error');
        if (hasError) {
            icon.classList.add('error');
            icon.title = '检测到异常，请查看下方校验结果';
        } else {
            icon.classList.add('ok');
            icon.title = '所有数据校验通过';
        }
    }
    updateStepClickable();
}

function renderOverrideLog() {
    const log = state.email.overrideLog || {};
    const entries = Object.values(log);
    if (!entries.length) return '';
    let html = `<div class="override-log"><h4>邮箱修改记录</h4>
        <table><thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>原始邮箱</th><th>修改后邮箱</th></tr></thead><tbody>`;
    entries.forEach(entry => {
        html += `<tr>
            <td>${escapeHtml(entry.row)}</td>
            <td>${escapeHtml(entry.community)}</td>
            <td>${escapeHtml(entry.name)}</td>
            <td>${escapeHtml(entry.original)}</td>
            <td>${escapeHtml(entry.current)}</td>
        </tr>`;
    });
    html += '</tbody></table></div>';
    return html;
}

function emailOpsHtml(row, { canConfirm, editKind }) {
    const editing = state.email.editing;
    if (editing && editing.row === row && editing.kind === editKind) {
        return `<button class="btn small primary" data-email-save="${row}">保存</button>
            <button class="btn small text" data-email-cancel="${row}">取消</button>`;
    }
    let html = '';
    if (canConfirm) {
        html += emailRowConfirmed(row)
            ? '<span class="confirmed-tag">已确认</span>'
            : `<button class="btn small" data-email-confirm="${row}">确认</button>`;
    }
    html += `<button class="btn small text" data-email-edit="${row}|${editKind}">修改</button>
        <button class="btn small text danger" data-email-delete="${row}">删除</button>`;
    return html;
}

function emailCellHtml(row, value) {
    const editing = state.email.editing;
    if (editing && editing.row === row && editing.kind === 'email') {
        return `<td><input type="text" class="email-edit-input" data-email-value="${row}" value="${escapeHtml(editing.value)}" style="width:100%;box-sizing:border-box"></td>`;
    }
    return `<td>${escapeHtml(value)}</td>`;
}

function renderValidation() {
    const el = $('#email-validation');
    const vs = state.email.validationState;
    if (!vs) { hide(el); return; }
    
    // 重新计算有效/无效计数（基于当前 confirmedRows）
    rebuildValidationState();
    
    show(el);
    const ov = state.email.overrides || {};
    const editing = state.email.editing;
    let html = '';

    const invalidStat = vs.invalid > 0
        ? `<div class="stat invalid">无效：${vs.invalid} / ${vs.total}</div>`
        : '';
    const deletedStat = vs.deletedRecords && vs.deletedRecords.length
        ? `<div class="stat invalid">已删除：${vs.deletedRecords.length}</div>`
        : '';
    html += `
        <div class="stats">
            <div class="stat valid">有效：${vs.valid} / ${vs.total}</div>
            ${invalidStat}
            ${deletedStat}
            <button type="button" class="help-icon-btn" id="email-validation-help" title="查看校验说明">?</button>
        </div>
    `;

    if (vs.duplicateNameGroups.length) {
        const pendingNameDup = vs.duplicateNameGroups.reduce((s, g) => s + g.rows.length, 0);
        html += `<div class="duplicate-section"><h4>社区+姓名重复</h4>
            <p class="hint-text">系统以「社区 + 姓名」为唯一键，重复会导致数据合并错乱。请修改其中一条的社区/姓名，或删除多余的那条。当前 ${pendingNameDup} 条待处理。</p>`;
        vs.duplicateNameGroups.forEach(g => {
            html += `<div class="duplicate-group">
                <div class="group-header">
                    <span class="group-email">${escapeHtml(g.community)} - ${escapeHtml(g.name)}</span>
                    <span class="group-count">共 ${g.count} 条</span>
                </div>
                <table style="table-layout:fixed"><colgroup><col style="width:60px"><col style="width:120px"><col style="width:120px"><col style="width:180px"><col style="width:140px"></colgroup>
                    <thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>邮箱</th><th>操作</th></tr></thead>
                    <tbody>`;
            g.rows.forEach(r => {
                const isEditing = editing && editing.row === r.row && editing.kind === 'identity';
                const communityCell = isEditing
                    ? `<td><input type="text" class="email-edit-input" data-email-community="${r.row}" value="${escapeHtml(editing.community)}" style="width:100%;box-sizing:border-box"></td>`
                    : `<td>${escapeHtml(r.community)}${ov[r.row] ? ' <span class="override-tag">已修改</span>' : ''}</td>`;
                const nameCell = isEditing
                    ? `<td><input type="text" class="email-edit-input" data-email-name="${r.row}" value="${escapeHtml(editing.name)}" style="width:100%;box-sizing:border-box"></td>`
                    : `<td>${escapeHtml(r.name)}${ov[r.row] ? ' <span class="override-tag">已修改</span>' : ''}</td>`;
                html += `<tr>
                    <td>${escapeHtml(r.row)}</td>
                    ${communityCell}
                    ${nameCell}
                    <td>${escapeHtml(r.current)}</td>
                    <td>${emailOpsHtml(r.row, { canConfirm: false, editKind: 'identity' })}</td>
                </tr>`;
            });
            html += `</tbody></table></div>`;
        });
        html += `</div>`;
    }

    if (vs.duplicateGroups.length) {
        const pendingDup = vs.duplicateGroups.reduce((s, g) => s + g.rows.filter(r => !emailRowConfirmed(r.row)).length, 0);
        html += `<div class="duplicate-section"><h4>重复邮箱（请确认是否为正常共用邮箱，或修改）${pendingDup > 0 ? ' <button class="btn small" data-email-confirm-all="dup">全部确认</button>' : ''}</h4>`;
        vs.duplicateGroups.forEach(g => {
            const allConfirmed = g.rows.every(r => emailRowConfirmed(r.row));
            html += `<div class="duplicate-group ${allConfirmed ? 'confirmed' : ''}">
                <div class="group-header">
                    <span class="group-email">${escapeHtml(g.email)}</span>
                    <span class="group-count">共 ${g.rows.length} 人</span>
                    ${allConfirmed ? '<span class="group-status">已确认</span>' : `<button class="btn small" data-email-confirm-group="${g.rows[0].row}">全部确认</button>`}
                </div>
                <table>
                    <thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>原始邮箱</th><th>当前邮箱</th><th>操作</th></tr></thead>
                    <tbody>`;
            g.rows.forEach(r => {
                html += `<tr class="${emailRowConfirmed(r.row) ? 'confirmed-row' : ''}">
                    <td>${escapeHtml(r.row)}</td>
                    <td>${escapeHtml(r.community)}</td>
                    <td>${escapeHtml(r.name)}</td>
                    <td>${escapeHtml(r.raw)}${ov[r.row] ? ' <span class="override-tag">已修改</span>' : ''}</td>
                    ${emailCellHtml(r.row, r.current)}
                    <td>${emailOpsHtml(r.row, { canConfirm: true, editKind: 'email' })}</td>
                </tr>`;
            });
            html += `</tbody></table></div>`;
        });
        html += `</div>`;
    }

    if (vs.issues.length) {
        const pendingIssues = vs.issues.filter(r => !emailRowConfirmed(r.row)).length;
        html += `<div class="issues-section"><h4>其他邮箱问题${pendingIssues > 0 ? ' <button class="btn small" data-email-confirm-all="issues">全部确认</button>' : ''}</h4>
            <p class="hint-text">可修改为正确邮箱；或确认保留该邮箱继续导入；或删除该条邮箱记录，对应人员在合并时会变成「未匹配」。</p>
            <table style="table-layout:fixed"><colgroup><col style="width:60px"><col style="width:80px"><col style="width:80px"><col style="width:150px"><col style="width:180px"><col style="width:120px"><col style="width:160px"></colgroup>
                <thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>原始邮箱</th><th>当前邮箱</th><th>问题</th><th>操作</th></tr></thead>
                <tbody>`;
        vs.issues.forEach(r => {
            html += `<tr class="${emailRowConfirmed(r.row) ? 'confirmed-row' : ''}">
                <td>${escapeHtml(r.row)}</td>
                <td>${escapeHtml(r.community)}</td>
                <td>${escapeHtml(r.name)}</td>
                <td>${escapeHtml(r.raw)}${ov[r.row] ? ' <span class="override-tag">已修改</span>' : ''}</td>
                ${emailCellHtml(r.row, r.current)}
                <td>${escapeHtml(r.errors.join('；'))}</td>
                <td>${emailOpsHtml(r.row, { canConfirm: true, editKind: 'email' })}</td>
            </tr>`;
        });
        html += `</tbody></table></div>`;
    }

    if (vs.deletedRecords && vs.deletedRecords.length) {
        html += `<div class="issues-section"><h4>已删除的邮箱记录</h4>
            <table style="table-layout:fixed"><colgroup><col style="width:60px"><col style="width:80px"><col style="width:80px"><col style="width:150px"><col style="width:160px"></colgroup><thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>原始邮箱</th><th>操作</th></tr></thead><tbody>`;
        vs.deletedRecords.forEach(r => {
            html += `<tr class="deleted-row">
                <td>${escapeHtml(r.row)}</td>
                <td>${escapeHtml(r.community)}</td>
                <td>${escapeHtml(r.name)}</td>
                <td>${escapeHtml(r.raw)}</td>
                <td><button class="btn small text" data-email-restore="${r.row}">撤销删除</button></td>
            </tr>`;
        });
        html += `</tbody></table></div>`;
    }

    html += renderOverrideLog();

    // 检查是否有需要处理的异常（重复邮箱、格式错误、姓名重复）
    const hasUnresolvedIssues = vs.duplicateGroups.length || vs.issues.length || vs.duplicateNameGroups.length;
    const hasOverrides = Object.keys(state.email.overrides).length > 0;
    const hasDeletedRows = state.email.deletedRows.length > 0;
    
    if (!hasUnresolvedIssues) {
        html += `<p>所有邮箱均校验通过 ✅</p>`;

        // 有修改或删除操作时显示写回按钮
        if (hasOverrides || hasDeletedRows) {
            html += `<div style="margin-top: 16px;">
                <button id="write-back-email-btn" class="btn primary">保存修改到源文件</button>
            </div>`;
        }
    }

    el.innerHTML = html;

    // 绑定邮箱写回按钮事件
    const writeBackEmailBtn = $('#write-back-email-btn');
    if (writeBackEmailBtn) {
        writeBackEmailBtn.addEventListener('click', async () => {
            const confirmed = await confirmModal(`此操作将直接修改源文件 "${state.email.filename}"，是否继续？`);
            if (!confirmed) return;

            try {
                const emailConfig = {
                    header_rows: $('#email-header-rows').value,
                    data_start: $('#email-data-start').value,
                    skip_rows: $('#email-skip-rows').value,
                    data_end_col: $('#email-data-end-col').value.trim() || null,
                };

                const res = await postJson('/api/write-back-email', {
                    file_id: state.email.fileId,
                    sheet_name: $('#email-sheet').value,
                    overrides: state.email.overrides,
                    deleted_rows: state.email.deletedRows,
                    email_config: emailConfig,
                    original_filename: state.email.filename
                });

                if (res.error) {
                    alert('写回失败: ' + res.error);
                } else {
                    await infoModal('已成功写回源文件');
                    state.email.overrides = {};
                    state.email.deletedRows = [];
                    state.email.overrideLog = {};
                    renderValidation();
                }
            } catch (err) {
                console.error('写回异常:', err);
                alert('写回失败: ' + err.message);
            }
        });
    }

    const emailHelpBtn = $('#email-validation-help');
    if (emailHelpBtn) emailHelpBtn.addEventListener('click', showValidationHelp);

    el.querySelectorAll('[data-email-confirm]').forEach(btn => {
        btn.addEventListener('click', () => confirmEmailRows([parseInt(btn.dataset.emailConfirm)]));
    });
    el.querySelectorAll('[data-email-confirm-group]').forEach(btn => {
        const group = vs.duplicateGroups.find(g => g.rows.some(r => r.row === parseInt(btn.dataset.emailConfirmGroup)));
        btn.addEventListener('click', () => group && confirmEmailRows(group.rows.map(r => r.row)));
    });
    el.querySelectorAll('[data-email-confirm-all]').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.emailConfirmAll === 'issues') confirmAllIssues();
            else confirmAllDuplicates();
        });
    });
    el.querySelectorAll('[data-email-edit]').forEach(btn => {
        const [row, kind] = btn.dataset.emailEdit.split('|');
        btn.addEventListener('click', () => startEmailEdit(parseInt(row), kind));
    });
    el.querySelectorAll('[data-email-save]').forEach(btn => {
        btn.addEventListener('click', () => saveEmailEdit(parseInt(btn.dataset.emailSave)));
    });
    el.querySelectorAll('[data-email-cancel]').forEach(btn => {
        btn.addEventListener('click', () => cancelEmailEdit());
    });
    el.querySelectorAll('[data-email-delete]').forEach(btn => {
        btn.addEventListener('click', () => deleteEmailRow(parseInt(btn.dataset.emailDelete)));
    });
    el.querySelectorAll('[data-email-restore]').forEach(btn => {
        btn.addEventListener('click', () => restoreEmailRow(parseInt(btn.dataset.emailRestore)));
    });
    el.querySelectorAll('[data-email-value]').forEach(input => {
        input.addEventListener('input', () => {
            if (state.email.editing) state.email.editing.value = input.value;
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') saveEmailEdit(parseInt(input.dataset.emailValue));
            if (e.key === 'Escape') cancelEmailEdit();
        });
    });
    el.querySelectorAll('[data-email-community]').forEach(input => {
        input.addEventListener('input', () => {
            if (state.email.editing) state.email.editing.community = input.value;
        });
    });
    el.querySelectorAll('[data-email-name]').forEach(input => {
        input.addEventListener('input', () => {
            if (state.email.editing) state.email.editing.name = input.value;
        });
    });
}

// Step 3
function buildSummary() {
    const s = state.salary.parsed;
    const e = state.email.parsed;
    // 优先使用从文件名解析的年月，其次使用上传时表格解析的年月
    const salaryDetected = state.salary.sheetData || {};
    const year = state.detectedYear ?? salaryDetected.detected_year ?? null;
    const month = state.detectedMonth ?? salaryDetected.detected_month ?? null;
    if (year !== null && month !== null) {
        $('#salary-year-month').value = `${year}-${String(month).padStart(2, '0')}`;
        hide($('#date-warning'));
    } else {
        // 未解析到年月，默认填充今年/本月
        const now = new Date();
        $('#salary-year-month').value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
        show($('#date-warning'));
    }
    const salaryFileLink = state.salary.fileId
        ? `<a href="/api/download/${state.salary.fileId}?filename=${encodeURIComponent(state.salary.filename)}" target="_blank" class="file-link">${escapeHtml(state.salary.filename)}</a>`
        : escapeHtml(state.salary.filename);
    const emailFileLink = state.email.fileId
        ? `<a href="/api/download/${state.email.fileId}?filename=${encodeURIComponent(state.email.filename)}" target="_blank" class="file-link">${escapeHtml(state.email.filename)}</a>`
        : escapeHtml(state.email.filename);
    const salaryDeleted = (state.salary.deletedRows || []).length;
    const salaryActual = Math.max(0, s.record_count - salaryDeleted);
    const emailDeleted = (state.email.deletedRows || []).length;
    const emailActual = Math.max(0, e.record_count - emailDeleted);
    $('#import-summary').innerHTML = `
        <p>工资表：<strong>${salaryFileLink}</strong>，源文件 <strong>${s.record_count}</strong> 条${salaryDeleted > 0 ? `，删除 <strong class="text-danger">${salaryDeleted}</strong> 条，实际导入 <strong>${salaryActual}</strong> 条` : ''}</p>
        <p>邮箱表：<strong>${emailFileLink}</strong>，源文件 <strong>${e.record_count}</strong> 条${emailDeleted > 0 ? `，删除 <strong class="text-danger">${emailDeleted}</strong> 条，实际导入 <strong>${emailActual}</strong> 条` : ''}</p>
        <p>合并键：<strong>社区 + 姓名</strong>（以工资表为准进行合并）</p>
        <div id="summary-empty-email"></div>
    `;
}

// 构建导入/预览用的 salary + email 配置
function buildImportConfig() {
    const salaryMapping = {
        name: parseInt($('#salary-name-col').value),
        community: parseInt($('#salary-community-col').value),
    };
    const salaryFieldMapping = {
        bank_account: $('#salary-bank-account-col').value,
        gross_salary: $('#salary-gross-col').value,
        total_deduction: $('#salary-deduction-col').value,
        net_salary: $('#salary-net-col').value,
    };
    const emailMapping = {
        name: parseInt($('#email-name-col').value),
        community: parseInt($('#email-community-col').value),
        email: parseInt($('#email-col').value),
    };
    return {
        salary: {
            file_id: state.salary.fileId,
            filename: state.salary.filename,
            sheet_name: $('#salary-sheet').value,
            header_rows: parseHeaderRows($('#salary-header-rows').value),
            data_start_row: parseInt($('#salary-data-start').value),
            data_end_row: $('#salary-data-end').value ? parseInt($('#salary-data-end').value) : null,
            data_end_col: $('#salary-data-end-col').value.trim() || null,
            skip_rows: parseHeaderRows($('#salary-skip-rows').value),
            mapping: salaryMapping,
            salary_field_mapping: salaryFieldMapping,
            overrides: state.salary.overrides,
            deleted_rows: state.salary.deletedRows,
            email_overrides: state.importPreview.emailOverrides || {},
        },
        email: {
            file_id: state.email.fileId,
            filename: state.email.filename,
            sheet_name: $('#email-sheet').value,
            header_rows: parseHeaderRows($('#email-header-rows').value),
            data_start_row: parseInt($('#email-data-start').value),
            data_end_row: $('#email-data-end').value ? parseInt($('#email-data-end').value) : null,
            data_end_col: $('#email-data-end-col').value.trim() || null,
            skip_rows: parseHeaderRows($('#email-skip-rows').value),
            mapping: emailMapping,
            overrides: state.email.overrides,
            deleted_rows: state.email.deletedRows,
        },
    };
}

// 加载并渲染合并预览
async function loadImportPreview() {
    if (!state.salary.fileId || !state.email.fileId) return;
    const table = $('#import-preview-table');
    table.innerHTML = '<tr><td>正在加载合并预览...</td></tr>';
    try {
        const cfg = buildImportConfig();
        const res = await postJson('/api/import-preview', cfg);
        if (res.error) { table.innerHTML = `<tr><td>加载失败：${escapeHtml(res.error)}</td></tr>`; return; }
        state.importPreview.headers = res.headers;
        state.importPreview.rows = res.rows;
        state.importPreview.rowIndices = res.row_indices || res.rows.map((_, i) => i + 2);
        state.importPreview.emailOverrides = {};
        state.importPreview.total = res.total;
        state.importPreview.matched = res.matched;
        state.importPreview.unmatched = res.unmatched;
        // 存在未匹配记录时默认激活"仅看未匹配"
        if (res.unmatched > 0) {
            state.importPreview.onlyUnmatched = true;
            $('#import-preview-unmatched-btn').classList.add('active');
        } else {
            state.importPreview.onlyUnmatched = false;
            $('#import-preview-unmatched-btn').classList.remove('active');
        }
        const statsText = `共 ${res.total} 条（实际导入），已匹配邮箱 ${res.matched} 条，未匹配 ${res.unmatched} 条`;
        $('#import-preview-stats').textContent = statsText;
        // 更新顶部摘要：邮箱空白 + 实际匹配数
        const emptyEmailEl = $('#summary-empty-email');
        if (emptyEmailEl) {
            let summaryExtra = '';
            if (res.unmatched > 0) {
                summaryExtra += `<p>邮箱空白：<strong class="text-danger">${res.unmatched}</strong> 条（可手动填写或从历史邮箱中选择）</p>`;
            }
            summaryExtra += `<p class="hint-text">实际将导入 ${res.total} 条工资记录，其中 ${res.matched} 条已匹配邮箱</p>`;
            emptyEmailEl.innerHTML = summaryExtra;
        }
        renderImportPreview();
    } catch (err) {
        console.error(err);
        table.innerHTML = `<tr><td>加载失败：${escapeHtml(err.message)}</td></tr>`;
    }
}

function renderImportPreview() {
    const { headers, rows, rowIndices, fullPreview, page, pageSize, onlyUnmatched, emailOverrides } = state.importPreview;
    if (!headers || !rows) return;
    const emailIdx = headers.length - 1;
    // 未匹配筛选：邮箱列（最后一列）为空（考虑已编辑的邮箱）
    let sourceRows = rows;
    let sourceIndices = rowIndices;
    if (onlyUnmatched) {
        const filtered = [];
        const filteredIdx = [];
        rows.forEach((r, i) => {
            const curEmail = emailOverrides[rowIndices[i]] !== undefined ? emailOverrides[rowIndices[i]] : r[emailIdx];
            if (!curEmail) { filtered.push(r); filteredIdx.push(rowIndices[i]); }
        });
        sourceRows = filtered;
        sourceIndices = filteredIdx;
    }
    let displayRows = sourceRows;
    let displayIndices = sourceIndices;
    if (!fullPreview && sourceRows.length > 10) {
        const head = sourceRows.slice(0, 5);
        const tail = sourceRows.slice(-5);
        const headIdx = sourceIndices.slice(0, 5);
        const tailIdx = sourceIndices.slice(-5);
        displayRows = [...head, ['...'], ...tail];
        displayIndices = [...headIdx, 0, ...tailIdx];
    }
    const allRows = [headers, ...displayRows];
    const allIndices = [1, ...displayIndices];
    const pagination = fullPreview ? { page, pageSize } : null;
    const communityIdx = headers.indexOf('社区');
    const nameIdx = headers.indexOf('姓名');
    const freezeCols = [communityIdx, nameIdx].filter(i => i >= 0).sort((a, b) => a - b);
    const pageInfo = renderTable($('#import-preview-table'), allRows, 1, [1], allIndices, pagination, freezeCols);
    renderPreviewPagination('import', pageInfo);

    // 邮箱列编辑：仅对空邮箱行可编辑，并附带历史邮箱选择
    const table = $('#import-preview-table');
    table.querySelectorAll('tbody tr').forEach(tr => {
        const cells = tr.querySelectorAll('td');
        if (cells.length <= emailIdx) return;
        const excelRow = parseInt(tr.querySelector('.row-header')?.textContent);
        if (isNaN(excelRow)) return;
        const emailCell = cells[emailIdx];
        const origIdx = state.importPreview.rowIndices.indexOf(excelRow);
        const originalEmail = state.importPreview.rows[origIdx]?.[emailIdx] ?? '';
        const curEmail = emailOverrides[excelRow] !== undefined ? emailOverrides[excelRow] : originalEmail;
        if (!originalEmail) {
            const communityVal = origIdx >= 0 ? (state.importPreview.rows[origIdx]?.[communityIdx] ?? '') : '';
            const nameVal = origIdx >= 0 ? (state.importPreview.rows[origIdx]?.[nameIdx] ?? '') : '';
            emailCell.innerHTML = `<div class="email-edit-cell">
                <input class="email-edit-input" type="email" data-email-edit="${excelRow}" value="${escapeHtml(curEmail)}" placeholder="输入邮箱..." style="width:180px">
                <button class="btn small history-email-btn" data-history-community="${escapeHtml(communityVal)}" data-history-name="${escapeHtml(nameVal)}" title="从历史邮箱选择">历史</button>
            </div>`;
        }
    });
    table.querySelectorAll('[data-email-edit]').forEach(input => {
        input.addEventListener('input', () => {
            const excelRow = parseInt(input.dataset.emailEdit);
            state.importPreview.emailOverrides[excelRow] = input.value.trim();
            // 更新统计
            updateImportPreviewStats();
        });
    });
    // 历史邮箱按钮
    table.querySelectorAll('.history-email-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const community = btn.dataset.historyCommunity;
            const name = btn.dataset.historyName;
            if (!community || !name) return;
            btn.disabled = true;
            btn.textContent = '...';
            try {
                const res = await fetch(`/api/history-emails?community=${encodeURIComponent(community)}&name=${encodeURIComponent(name)}`);
                const data = await res.json();
                if (!data.emails || data.emails.length === 0) {
                    alert(`未找到「${community} - ${name}」的历史邮箱`);
                    return;
                }
                // 显示下拉列表
                const dropdown = document.createElement('div');
                dropdown.className = 'history-email-dropdown';
                dropdown.innerHTML = data.emails.map(e => `<div class="history-email-item" data-email="${escapeHtml(e)}">${escapeHtml(e)}</div>`).join('');
                const cell = btn.parentElement;
                // 移除已有的下拉
                cell.querySelector('.history-email-dropdown')?.remove();
                cell.appendChild(dropdown);
                dropdown.querySelectorAll('.history-email-item').forEach(item => {
                    item.addEventListener('click', () => {
                        const email = item.dataset.email;
                        const input = cell.querySelector('.email-edit-input');
                        if (input) {
                            input.value = email;
                            const excelRow = parseInt(input.dataset.emailEdit);
                            state.importPreview.emailOverrides[excelRow] = email;
                            updateImportPreviewStats();
                        }
                        dropdown.remove();
                    });
                });
            } catch (e) {
                alert('查询历史邮箱失败：' + e.message);
            } finally {
                btn.disabled = false;
                btn.textContent = '历史';
            }
        });
    });
}

function updateImportPreviewStats() {
    const { rows, rowIndices, emailOverrides } = state.importPreview;
    if (!rows || !rowIndices) return;
    const emailIdx = state.importPreview.headers.length - 1;
    let matched = 0, unmatched = 0;
    rows.forEach((r, i) => {
        const curEmail = emailOverrides[rowIndices[i]] !== undefined ? emailOverrides[rowIndices[i]] : r[emailIdx];
        if (curEmail) matched++; else unmatched++;
    });
    state.importPreview.matched = matched;
    state.importPreview.unmatched = unmatched;
    $('#import-preview-stats').textContent = `共 ${rows.length} 条，已匹配邮箱 ${matched} 条，未匹配 ${unmatched} 条`;
    const emptyEmailEl = $('#summary-empty-email');
    if (emptyEmailEl) {
        emptyEmailEl.innerHTML = unmatched > 0 ? `<p>邮箱空白：<strong class="text-danger">${unmatched}</strong> 条</p>` : '';
    }
}

// 导入预览全量按钮
$('#import-preview-full-btn').addEventListener('click', () => {
    state.importPreview.fullPreview = !state.importPreview.fullPreview;
    state.importPreview.page = 1;
    $('#import-preview-full-btn').classList.toggle('active', state.importPreview.fullPreview);
    renderImportPreview();
});

// 仅看未匹配按钮
$('#import-preview-unmatched-btn').addEventListener('click', () => {
    state.importPreview.onlyUnmatched = !state.importPreview.onlyUnmatched;
    state.importPreview.page = 1;
    $('#import-preview-unmatched-btn').classList.toggle('active', state.importPreview.onlyUnmatched);
    renderImportPreview();
});

$('#do-import').addEventListener('click', async () => {
    // 校验年份月份
    const ymVal = $('#salary-year-month').value;
    if (!ymVal) {
        alert('请选择年月');
        return;
    }
    const [yearStr, monthStr] = ymVal.split('-');
    const year = parseInt(yearStr);
    const month = parseInt(monthStr);
    if (isNaN(year) || isNaN(month) || month < 1 || month > 12) {
        alert('年月格式无效');
        return;
    }
    // 存在未匹配记录时，二次确认
    if (state.importPreview.unmatched && state.importPreview.unmatched > 0) {
        const msg = `检测到 ${state.importPreview.unmatched} 条工资记录未匹配到邮箱。<br><br>点击「确定」将导入全部工资数据，未匹配人员的邮箱留空；点击「取消」放弃导入。`;
        const confirmed = await confirmModal(msg, true);
        if (!confirmed) return;
    }
    const cfg = buildImportConfig();
    const body = {
        year,
        month,
        ...cfg,
    };
    const box = $('#import-result');
    show(box);
    box.className = 'result-box';
    box.textContent = '正在导入...';
    let res;
    try {
        res = await postJson('/api/import', body);
    } catch (err) {
        box.className = 'result-box error';
        box.textContent = '导入失败：' + err.message;
        return;
    }
    if (res.error) {
        box.className = 'result-box error';
        box.textContent = '导入失败：' + res.error;
        return;
    }
    let html = `<p>导入成功！年月：${res.year}年${res.month}月</p>`;
    html += `<p>工资记录：${res.stats.total_salary} 条，成功匹配邮箱：${res.stats.matched} 条，未匹配：${res.stats.unmatched} 条，邮箱异常：${res.stats.email_invalid} 条</p>`;
    if (res.unmatched && res.unmatched.length) {
        html += `<p><strong>未匹配人员（工资表中有，邮箱表中无）：</strong></p><ul>`;
        res.unmatched.forEach(u => html += `<li>${escapeHtml(u.community)} - ${escapeHtml(u.name)}</li>`);
        html += `</ul>`;
    }
    if (res.email_issues && res.email_issues.length) {
        html += `<p><strong>邮箱校验问题：</strong></p><ul>`;
        res.email_issues.forEach(i => html += `<li>${escapeHtml(i.community)} ${escapeHtml(i.name)}：${escapeHtml(i.errors.join('；'))}（原始：${escapeHtml(i.raw)}）</li>`);
        html += `</ul>`;
    }
    box.innerHTML = html;
    loadMonths();
    // 导入成功后跳转到「导入历史」标签页并刷新列表
    $$('.tab-btn').forEach(b => b.classList.remove('active'));
    $$('.tab-panel').forEach(p => p.classList.remove('active'));
    const historyBtn = document.querySelector('.tab-btn[data-tab="history"]');
    if (historyBtn) historyBtn.classList.add('active');
    $('#tab-history').classList.add('active');
    loadHistory();
});

// Search
async function loadMonths() {
    const res = await fetch('/api/months').then(r => r.json());
    const select = $('#search-month');
    select.innerHTML = '<option value="">最新数据</option><option value="all">全部数据</option>';
    res.forEach(m => {
        const opt = document.createElement('option');
        opt.value = `${m.year}-${m.month}`;
        opt.textContent = m.label;
        select.appendChild(opt);
    });
}

async function loadCommunities() {
    const res = await fetch('/api/communities').then(r => r.json());
    const select = $('#search-community');
    select.innerHTML = '<option value="">全部社区</option>';
    res.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c;
        opt.textContent = c;
        select.appendChild(opt);
    });
}

let searchSuggestTimer = null;

function buildSearchUrl(base, q, monthVal, communityVal) {
    let url = `${base}?q=${encodeURIComponent(q)}`;
    if (monthVal && monthVal !== 'all') {
        const [year, month] = monthVal.split('-');
        url += `&year=${year}&month=${month}`;
    }
    if (communityVal) {
        url += `&community=${encodeURIComponent(communityVal)}`;
    }
    return url;
}

async function fetchSearchSuggestions() {
    const q = $('#search-input').value.trim();
    const monthVal = $('#search-month').value;
    if (!q) {
        hide($('#search-suggestions'));
        return;
    }
    const res = await fetch(buildSearchUrl('/api/search-suggest', q, monthVal)).then(r => r.json());
    renderSearchSuggestions(res);
}

function selectSuggestion(item) {
    clearTimeout(searchSuggestTimer);
    $('#search-input').value = item.name;
    hide($('#search-suggestions'));
    $('#search-btn').click();
}

function renderSearchSuggestions(items) {
    const el = $('#search-suggestions');
    if (!items.length) {
        hide(el);
        return;
    }
    // 只有一条候选项时直接触发精确搜索
    if (items.length === 1) {
        hide(el);
        selectSuggestion(items[0]);
        return;
    }
    let html = '';
    items.forEach(item => {
        html += `<div class="suggestion-item" data-id="${item.id}">
            <span class="suggestion-name">${escapeHtml(item.community)} · ${escapeHtml(item.name)}</span>
            <span class="suggestion-meta">
                ${item.position ? `岗位：${escapeHtml(item.position)}` : ''}
                ${item.service_years ? `调资居龄：${escapeHtml(item.service_years)}` : ''}
                ${item.position_start_date ? `起算：${escapeHtml(item.position_start_date)}` : ''}
            </span>
        </div>`;
    });
    el.innerHTML = html;
    show(el);
    el.querySelectorAll('.suggestion-item').forEach(item => {
        item.addEventListener('click', () => {
            const id = parseInt(item.dataset.id);
            const selected = items.find(i => i.id === id);
            if (selected) selectSuggestion(selected);
        });
    });
}

$('#search-input').addEventListener('input', () => {
    clearTimeout(searchSuggestTimer);
    const q = $('#search-input').value.trim();
    const monthSelect = $('#search-month');
    if (q && monthSelect.value === '') {
        monthSelect.value = 'all';
    } else if (!q && monthSelect.value === 'all') {
        monthSelect.value = '';
    }
    searchSuggestTimer = setTimeout(fetchSearchSuggestions, 200);
});

$('#search-input').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hide($('#search-suggestions'));
});

$('#search-input').addEventListener('blur', () => {
    setTimeout(() => hide($('#search-suggestions')), 200);
});

$('#search-month').addEventListener('change', () => {
    const q = $('#search-input').value.trim();
    if (q) fetchSearchSuggestions();
    // 已展示详情/结果时，按新月份重新加载；空姓名时默认列出最新月份全部数据
    $('#search-btn').click();
});

$('#search-btn').addEventListener('click', async () => {
    const q = $('#search-input').value.trim();
    const monthVal = $('#search-month').value;
    const communityVal = $('#search-community').value;
    const res = await fetch(buildSearchUrl('/api/search', q, monthVal, communityVal)).then(r => r.json());
    renderSearchResults(res);
});

function renderSearchResults(results) {
    show($('#search-results'));
    const el = $('#search-results');
    if (!results.length) {
        el.innerHTML = '<div class="empty-tip">未找到匹配人员</div>';
        return;
    }
    let html = `<table class="search-results-table">
        <thead>
            <tr>
                <th>社区</th>
                <th>姓名</th>
                <th>年月</th>
                <th>应发工资</th>
                <th>代扣合计</th>
                <th>实发工资</th>
                <th>银行账号</th>
                <th>邮箱</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>`;
    results.forEach(r => {
        html += `<tr>
            <td>${escapeHtml(r.community)}</td>
            <td>${escapeHtml(r.name)}</td>
            <td>${r.year}年${r.month}月</td>
            <td>${fmtMoney(r.gross_salary)}</td>
            <td>${fmtMoney(r.total_deduction)}</td>
            <td>${fmtMoney(r.net_salary)}</td>
            <td>${escapeHtml(r.bank_account || '-')}</td>
            <td>${escapeHtml(r.email || '-')}</td>
            <td><button class="btn small secondary" data-id="${r.id}" data-year="${r.year}" data-month="${r.month}">查看明细</button></td>
        </tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
    el.querySelectorAll('button[data-id]').forEach(btn => {
        btn.addEventListener('click', () => showPersonDetail(
            parseInt(btn.dataset.id),
            parseInt(btn.dataset.year),
            parseInt(btn.dataset.month)
        ));
    });
}

function fmtMoney(v) {
    if (v === null || v === undefined) return '-';
    return parseFloat(v).toFixed(2);
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function showValidationHelp() {
    $('#modal').classList.remove('import-detail-modal');
    const body = $('#modal-body');
    let html = `<h2>校验说明</h2>
        <p style="color:var(--muted);margin-top:-8px">系统在解析工资表与邮箱表时会自动执行以下校验，存在问题的记录需确认或修改后才能进入下一步。</p>

        <h3 style="margin-top:20px">一、工资表校验</h3>
        <ul class="help-list">
            <li><strong>空行跳过</strong>：社区或姓名为空的行会被自动跳过，不计入有效工资记录。</li>
            <li><strong>重复银行卡号</strong>：同一银行卡号出现在多条记录中时，需确认是否为正常共用账号，或修改为正确账号。</li>
            <li><strong>账号变化</strong>：与上次导入的同社区 + 姓名人员银行卡号不一致时，需确认是否为正常变更。（仅在存在历史导入数据时执行）</li>
            <li><strong>实发工资对不上</strong>：当「应发工资 - 代扣合计 ≠ 实发工资」且差额超过 0.02 时，需确认数据是否正确，可直接修改。（需检测到应发工资、代扣合计、实发工资三列）</li>
            <li><strong>账号为空</strong>：银行卡号列为空时，需补充正确账号或确认留空。</li>
            <li><strong>未检测到银行卡号列</strong>：若系统未自动识别到银行卡号列，相关校验不会执行，请手动指定银行卡号列后重新解析。</li>
        </ul>

        <h3 style="margin-top:20px">二、邮箱表校验</h3>
        <ul class="help-list">
            <li><strong>邮箱为空</strong>：邮箱地址为空，属于无效邮箱。</li>
            <li><strong>缺少 @ 符号</strong>：邮箱地址中没有 @ 符号，格式不正确。</li>
            <li><strong>邮箱格式无效</strong>：邮箱地址不符合标准格式要求。</li>
            <li><strong>邮箱重复</strong>：同一邮箱被多人使用时，需确认是否为正常共用邮箱，或修改为正确邮箱。</li>
        </ul>

        <h3 style="margin-top:20px">三、说明</h3>
        <ul class="help-list">
            <li>所有待确认的问题均需逐条「确认」后才能进入下一步，防止错误数据被导入。</li>
            <li>对有问题的数据可点击「编辑/修改」直接修正，保存后系统会重新计算校验结果。</li>
            <li>银行卡号会自动去除空格、横线等非数字字符后再参与校验。</li>
            <li>邮箱会自动去除空白字符、将全角 @ 转为半角、并统一转为小写后再校验。</li>
        </ul>
    `;
    body.innerHTML = html;
    show($('#modal'));
}

async function showPersonDetail(id, year, month) {
    $('#modal').classList.add('import-detail-modal');
    // 不再按单月过滤，后端返回该人员所有历史导入记录
    const r = await fetch(`/api/person/${id}`).then(r => r.json());
    if (r.error) return;
    const body = $('#modal-body');
    let html = `<h2>${escapeHtml(r.community)} · ${escapeHtml(r.name)}</h2>`;
    if (r.email_issues) html += `<p style="color:var(--danger)">邮箱问题：${escapeHtml(r.email_issues)}</p>`;

    const history = r.history || [];
    if (!history.length) {
        html += '<p>暂无历史记录。</p>';
    } else {
        // 列顺序以最新一次导入的 detail keys 为准；补充其它历史记录中存在的列
        const detailCols = [...(r.detail_keys || [])];
        const seen = new Set(detailCols);
        history.forEach(h => {
            Object.keys(h.detail || {}).forEach(k => {
                if (!seen.has(k)) { seen.add(k); detailCols.push(k); }
            });
        });
        // 找出社区/姓名索引用于冻结列
        const communityIdx = detailCols.indexOf('社区');
        const nameIdx = detailCols.indexOf('姓名');
        const freezeClass = (i) => {
            if (i === communityIdx) return 'freeze-1';
            if (i === nameIdx) return 'freeze-2';
            return '';
        };
        html += `<table class="search-results-table"><thead><tr>`;
        detailCols.forEach((col, i) => {
            const cls = freezeClass(i);
            html += `<th${cls ? ` class="${cls}"` : ''}>${escapeHtml(col)}</th>`;
        });
        html += `<th>年月</th><th>邮箱</th></tr></thead><tbody>`;
        history.forEach(h => {
            html += `<tr>`;
            detailCols.forEach((col, i) => {
                const cls = freezeClass(i);
                const v = (h.detail || {})[col];
                html += `<td${cls ? ` class="${cls}"` : ''}>${escapeHtml(v == null ? '' : String(v))}</td>`;
            });
            html += `<td>${h.year}年${h.month}月</td><td>${escapeHtml(r.email || '-')}</td></tr>`;
        });
        html += `</tbody></table>`;
    }
    body.innerHTML = html;
    show($('#modal'));
}

$('.close-btn').addEventListener('click', () => { $('#modal').classList.remove('import-detail-modal'); hide($('#modal')); });
$('#modal').addEventListener('click', (e) => {
    if (e.target === $('#modal')) { $('#modal').classList.remove('import-detail-modal'); hide($('#modal')); }
});

// History
async function loadHistory() {
    const res = await fetch('/api/imports').then(r => r.json());
    const el = $('#history-list');
    if (!res.length) {
        el.innerHTML = '<div class="empty-tip">暂无导入记录</div>';
        return;
    }
    el.innerHTML = '';
    res.forEach(item => {
        const div = document.createElement('div');
        div.className = 'history-item';
        div.title = '双击查看本次导入的完整数据表';
        
        // 构建发送进度文本
        let sendProgressHtml = '';
        if (item.total > 0) {
            const progressText = `已发送: ${item.sent}/${item.total}`;
            let progressClass = 'send-progress';
            if (item.sent === item.total && item.total > 0) {
                progressClass += ' send-progress-complete';
            } else if (item.failed > 0) {
                progressClass += ' send-progress-partial';
            }
            sendProgressHtml = `<span class="${progressClass}">${progressText}</span>`;
        }
        
        div.innerHTML = `
            <div>
                <strong>${item.year}年${item.month}月</strong>
                ${sendProgressHtml}
                <div style="font-size:12px;color:var(--muted);margin-top:4px;">
                    工资：${escapeHtml(item.salary_filename || '-')} | 邮箱：${escapeHtml(item.email_filename || '-')} | ${new Date(item.imported_at).toLocaleString()}
                </div>
            </div>
            <div style="display:flex;gap:8px;">
                <button class="btn primary" data-send-id="${item.id}" data-year="${item.year}" data-month="${item.month}">发送</button>
                <button class="btn primary" data-export-id="${item.id}" data-year="${item.year}" data-month="${item.month}">导出</button>
                <button class="btn danger" data-delete-id="${item.id}">删除</button>
            </div>
        `;
        
        // 绑定发送按钮
        const sendBtn = div.querySelector('[data-send-id]');
        sendBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            await handleImportSend(item.id, item.year, item.month);
        });
        
        // 绑定导出按钮
        const exportBtn = div.querySelector('[data-export-id]');
        exportBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const year = exportBtn.dataset.year;
            const month = exportBtn.dataset.month;
            // 月份补零：1 -> 01, 12 -> 12
            const monthStr = String(month).padStart(2, '0');
            const filename = `${year}${monthStr}_工资邮箱表.xlsx`;
            
            try {
                const res = await fetch(`/api/export-import/${item.id}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename })
                });
                
                // 检查响应类型
                const contentType = res.headers.get('content-type');
                if (!contentType || !contentType.includes('application/json')) {
                    const text = await res.text();
                    console.error('导出接口返回非JSON:', text.substring(0, 200));
                    alert('导出失败: 服务器返回了非JSON响应');
                    return;
                }
                
                const data = await res.json();
                
                if (data.error) {
                    alert('导出失败: ' + data.error);
                } else {
                    // 触发下载
                    const link = document.createElement('a');
                    link.href = `/api/download-export/${data.export_id}?filename=${encodeURIComponent(filename)}`;
                    link.download = filename;
                    link.click();
                }
            } catch (err) {
                console.error('导出异常:', err);
                alert('导出失败: ' + err.message);
            }
        });
        
        // 绑定删除按钮
        const deleteBtn = div.querySelector('[data-delete-id]');
        deleteBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!await confirmModal('确定删除这条导入记录及其所有人员工资数据吗？')) return;
            await fetch(`/api/import/${item.id}`, { method: 'DELETE' });
            loadHistory();
        });
        
        div.addEventListener('dblclick', () => showImportDetail(item.id));
        el.appendChild(div);
    });
}

async function handleImportSend(importId, year, month) {
    // 1. 检查是否有已启用的发件人
    try {
        const senderRes = await fetch('/api/check-senders');
        const senderData = await senderRes.json();
        
        if (!senderData.has_senders) {
            const confirmed = await confirmModal(
                '还没有配置发件人邮箱，无法发送工资单。<br><br>是否前往「发件人管理」页面配置？',
                true
            );
            if (confirmed) {
                // 切换到发件人管理标签页
                document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
                document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.remove('active'));
                
                const sendersBtn = document.querySelector('[data-tab="senders"]');
                const sendersPanel = $('#tab-senders');
                if (sendersBtn && sendersPanel) {
                    sendersBtn.classList.add('active');
                    sendersPanel.classList.add('active');
                    loadSenders();
                }
            }
            return;
        }
    } catch (e) {
        infoModal(`检查发件人配置失败: ${e.message}`);
        return;
    }
    
    // 2. 获取发送详情
    try {
        const detailsRes = await fetch(`/api/import/${importId}/send-details`);
        const details = await detailsRes.json();
        
        if (details.error) {
            infoModal(details.error);
            return;
        }
        
        // 3. 判断是首次发送还是重发
        if (details.sent > 0 || details.failed > 0) {
            // 已有发送记录，显示重发确认对话框
            const message = `${year}年${month}月工资单已发送过（共 ${details.total} 条）：<br>` +
                           `成功: ${details.sent} 条<br>` +
                           `失败: ${details.failed} 条<br>` +
                           `待发送: ${details.pending} 条<br><br>` +
                           `请选择操作：`;
            
            // 使用自定义对话框（传入失败和待发送数量）
            const choice = await showResendConfirmDialog(message, details.failed, details.pending);
            
            if (choice === 'all') {
                // 全部重发
                await executeImportSend(importId, 'all', year, month, false);
            } else if (choice === 'failed') {
                // 重发失败+待发送
                await executeImportSend(importId, 'failed', year, month, false);
            }
            // choice === 'cancel' 则不做任何操作
            
        } else {
            // 首次发送
            const hasEmailCount = details.total - details.no_email;
            const message = `当前有工资单数据 ${details.total} 条，其中有邮箱 ${hasEmailCount} 条，无邮箱 ${details.no_email} 条。<br><br>是否发送工资单？`;
            
            const confirmed = await confirmModal(message, true);
            if (confirmed) {
                await executeImportSend(importId, 'all', year, month, true);
            }
        }
        
    } catch (e) {
        infoModal(`获取发送详情失败: ${e.message}`);
    }
}

function showResendConfirmDialog(message, failedCount, pendingCount) {
    const hasRetryable = (failedCount || 0) > 0 || (pendingCount || 0) > 0;
    
    return new Promise((resolve) => {
        const modal = document.createElement('div');
        modal.className = 'modal';
        
        // 根据是否有可重发记录，决定按钮布局
        let buttonsHtml;
        if (hasRetryable) {
            buttonsHtml = `
                <button class="btn" id="resend-cancel-btn">取消</button>
                <button class="btn primary" id="resend-failed-btn">失败重发</button>
                <button class="btn danger" id="resend-all-btn">全部重发</button>
            `;
        } else {
            buttonsHtml = `
                <button class="btn" id="resend-cancel-btn">取消</button>
                <button class="btn danger" id="resend-all-btn">全部重发</button>
            `;
        }
        
        modal.innerHTML = `
            <div class="modal-content" style="max-width: 450px;">
                <div class="modal-header">
                    <h3>工资单发送确认</h3>
                    <button class="close-btn" onclick="this.closest('.modal').remove()">×</button>
                </div>
                <div class="modal-body" style="padding: 20px;">
                    <p>${message}</p>
                </div>
                <div class="modal-footer" style="display: flex; gap: 8px; justify-content: flex-end; padding: 16px 20px; border-top: 1px solid var(--border);">
                    ${buttonsHtml}
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        
        modal.querySelector('#resend-cancel-btn').addEventListener('click', () => {
            modal.remove();
            resolve('cancel');
        });
        
        const failedBtn = modal.querySelector('#resend-failed-btn');
        if (failedBtn) {
            failedBtn.addEventListener('click', () => {
                modal.remove();
                resolve('failed');
            });
        }
        
        modal.querySelector('#resend-all-btn').addEventListener('click', () => {
            modal.remove();
            resolve('all');
        });
        
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
                resolve('cancel');
            }
        });
    });
}

async function executeImportSend(importId, mode, year, month, isFirstSend) {
    // 构建提示文案
    let actionText;
    if (isFirstSend) {
        actionText = `正在发送 ${year}年${month}月工资单`;
    } else if (mode === 'failed') {
        actionText = `正在重发失败的工资单 (${year}年${month}月)`;
    } else {
        actionText = `正在全部重发 ${year}年${month}月工资单`;
    }
    
    // 显示进度对话框（带进度条）
    const progressDialog = showProgressDialog(actionText);
    
    try {
        const res = await fetch(`/api/import/${importId}/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode })
        });
        
        const data = await res.json();
        
        progressDialog.close();
        
        if (data.error) {
            infoModal(`发送失败: ${data.error}`);
        } else {
            // 显示结果对话框
            await showSendResultDialog(data, year, month, importId);
            // 刷新历史列表以更新进度
            loadHistory();
        }
    } catch (e) {
        progressDialog.close();
        infoModal(`发送异常: ${e.message}`);
    }
}

function showProgressDialog(message) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 400px;">
            <div class="modal-body" style="padding: 30px; text-align: center;">
                <p style="margin-bottom: 20px; font-size: 15px;">${message}</p>
                <div class="progress-bar-container">
                    <div class="progress-bar" id="send-progress-bar" style="width: 0%"></div>
                </div>
                <p id="send-progress-text" style="margin-top: 10px; color: var(--muted); font-size: 13px;">请稍候...</p>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    
    return {
        close: () => modal.remove(),
        updateProgress: (current, total, personInfo) => {
            const bar = modal.querySelector('#send-progress-bar');
            const text = modal.querySelector('#send-progress-text');
            if (bar && total > 0) {
                const pct = Math.round((current / total) * 100);
                bar.style.width = `${pct}%`;
            }
            if (text && personInfo) {
                text.textContent = `${current}/${total} - ${personInfo}`;
            }
        }
    };
}

async function showSendResultDialog(data, year, month, importId) {
    const hasFailures = data.failed_count > 0;
    
    let message = `<strong>${year}年${month}月工资单发送完成！</strong><br><br>`;
    message += `成功: ${data.success_count} 条<br>`;
    if (hasFailures) {
        message += `失败: ${data.failed_count} 条`;
    }
    
    // 如果有失败记录，列出失败详情
    if (hasFailures && data.results) {
        const failedResults = data.results.filter(r => !r.success);
        if (failedResults.length > 0) {
            message += `<br><br><div style="text-align: left; max-height: 200px; overflow-y: auto; border: 1px solid var(--border); padding: 10px; border-radius: 4px;">`;
            message += `<strong style="color: var(--danger);">失败详情：</strong><br>`;
            failedResults.slice(0, 10).forEach((r, idx) => {
                const personInfo = r.person_name ? `${r.community || ''}${r.person_name}` : `人员#${r.person_id}`;
                message += `${idx + 1}. ${escapeHtml(personInfo)} - ${escapeHtml(r.error || '未知错误')}<br>`;
            });
            if (failedResults.length > 10) {
                message += `... 还有 ${failedResults.length - 10} 条失败记录`;
            }
            message += `</div>`;
        }
    }
    
    // 根据是否有失败记录，显示不同按钮
    if (hasFailures) {
        // 有失败：显示"确认"和"失败重发"按钮
        const choice = await new Promise((resolve) => {
            const modal = document.createElement('div');
            modal.className = 'modal';
            modal.innerHTML = `
                <div class="modal-content" style="max-width: 500px;">
                    <div class="modal-header">
                        <h3>发送结果</h3>
                        <button class="close-btn" onclick="this.closest('.modal').remove()">×</button>
                    </div>
                    <div class="modal-body" style="padding: 20px;">
                        <p>${message}</p>
                    </div>
                    <div class="modal-footer" style="display: flex; gap: 8px; justify-content: flex-end; padding: 16px 20px; border-top: 1px solid var(--border);">
                        <button class="btn" id="result-confirm-btn">确认</button>
                        <button class="btn danger" id="result-resend-failed-btn">失败重发</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            
            modal.querySelector('#result-confirm-btn').addEventListener('click', () => {
                modal.remove();
                resolve('confirm');
            });
            
            modal.querySelector('#result-resend-failed-btn').addEventListener('click', () => {
                modal.remove();
                resolve('resend_failed');
            });
            
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.remove();
                    resolve('confirm');
                }
            });
        });
        
        if (choice === 'resend_failed') {
            // 重新调用发送，仅重发失败的
            await executeImportSend(importId, 'failed', year, month, false);
        }
    } else {
        // 全部成功：只显示"确认"按钮
        await infoModal(message, true);
    }
}

async function showImportDetail(importId) {
    const body = $('#modal-body');
    body.innerHTML = '<p>加载中...</p>';
    show($('#modal'));
    try {
        const r = await fetch(`/api/import/${importId}/records`).then(r => r.json());
        if (r.error) { body.innerHTML = `<p style="color:var(--danger)">${escapeHtml(r.error)}</p>`; return; }

        const records = r.records || [];
        // 用后端返回的 detail_keys（工资表原始列顺序）代替从 detail 收集，
        // 避免 jsonify 的 sort_keys 打乱 dict key 顺序
        const detailCols = r.detail_keys || [];
        const seen = new Set(detailCols);
        // 补充 detail_keys 中缺失但某条记录有的列（兼容旧数据）
        records.forEach(rec => {
            Object.keys(rec.detail || {}).forEach(k => {
                if (!seen.has(k)) { seen.add(k); detailCols.push(k); }
            });
        });

        // 分页状态
        const PAGE_SIZES = [10, 20, 50, 100, 500];
        let pageSize = 20;
        let currentPage = 1;

        const metaHtml = `<h2>${r.year}年${r.month}月 导入数据</h2>
            <p style="color:var(--muted);margin-top:-8px;font-size:13px">
                工资表：${escapeHtml(r.salary_filename || '-')} | 邮箱表：${escapeHtml(r.email_filename || '-')} | 共 <strong>${records.length}</strong> 条记录
            </p>`;

        if (!records.length) {
            body.innerHTML = metaHtml + '<p>暂无记录。</p>';
            return;
        }

        // 列顺序完全跟工资表（detailCols）一致，最后加邮箱列
        // 找出社区/姓名在 detailCols 中的索引用于冻结
        const communityIdx = detailCols.indexOf('社区');
        const nameIdx = detailCols.indexOf('姓名');
        const freezeClass = (i) => {
            if (i === communityIdx) return 'freeze-1';
            if (i === nameIdx) return 'freeze-2';
            return '';
        };

        // 构建表头：工资表原始列 + 邮箱列
        const theadHtml = `<thead><tr>
            ${detailCols.map((col, i) => {
                const cls = freezeClass(i);
                return `<th${cls ? ` class="${cls}"` : ''}>${escapeHtml(col)}</th>`;
            }).join('')}
            <th>邮箱</th>
        </tr></thead>`;

        function rowHtml(rec) {
            return `<tr>
                ${detailCols.map((col, i) => {
                    const cls = freezeClass(i);
                    const v = (rec.detail || {})[col];
                    return `<td${cls ? ` class="${cls}"` : ''}>${escapeHtml(v == null ? '' : String(v))}</td>`;
                }).join('')}
                <td>${escapeHtml(rec.email || '-')}</td>
            </tr>`;
        }

        function pagerHtml() {
            const totalPages = Math.ceil(records.length / pageSize);
            if (currentPage > totalPages) currentPage = totalPages;
            const start = (currentPage - 1) * pageSize;
            const end = Math.min(start + pageSize, records.length);

            // 固定 3 个页码按钮，按当前页居中
            const fixedPages = getFixedPages(currentPage, totalPages);
            const pageBtns = fixedPages.map(p =>
                `<button class="page-btn page-num ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`
            ).join('');

            return `
            <div class="pagination-bar">
                <div class="pagination-info">第 ${start + 1}-${end} 条 / 共 ${records.length} 条</div>
                <div class="pagination-controls">
                    <button class="page-btn nav-btn" data-page="1" ${currentPage === 1 ? 'disabled' : ''}>首页</button>
                    <button class="page-btn nav-btn" data-page="${currentPage - 1}" ${currentPage === 1 ? 'disabled' : ''}>上一页</button>
                    ${pageBtns}
                    <button class="page-btn nav-btn" data-page="${currentPage + 1}" ${currentPage === totalPages ? 'disabled' : ''}>下一页</button>
                    <button class="page-btn nav-btn" data-page="${totalPages}" ${currentPage === totalPages ? 'disabled' : ''}>末页</button>
                </div>
                <div class="page-size-select">
                    每页
                    <select id="page-size-select">
                        ${PAGE_SIZES.map(s => `<option value="${s}" ${s === pageSize ? 'selected' : ''}>${s}</option>`).join('')}
                    </select>
                    条
                </div>
            </div>`;
        }

        function renderTable() {
            const totalPages = Math.ceil(records.length / pageSize);
            const start = (currentPage - 1) * pageSize;
            const pageRecords = records.slice(start, start + pageSize);

            let html = metaHtml;
            html += `<div class="import-detail-wrap" id="import-detail-wrap">
                <table class="import-detail-table">${theadHtml}<tbody>
                    ${pageRecords.map(rowHtml).join('')}
                </tbody></table></div>`;
            html += pagerHtml();
            body.innerHTML = html;

            // 绑定分页事件
            body.querySelectorAll('.page-btn[data-page]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const p = parseInt(btn.dataset.page);
                    if (!isNaN(p) && p >= 1 && p <= totalPages && p !== currentPage) {
                        currentPage = p;
                        renderTable();
                    }
                });
            });
            const sizeSel = $('#page-size-select');
            if (sizeSel) {
                sizeSel.addEventListener('change', () => {
                    pageSize = parseInt(sizeSel.value);
                    currentPage = 1;
                    renderTable();
                });
            }

            // 根据第一列实际宽度设置第二列的 left 偏移
            requestAnimationFrame(() => {
                const wrap = $('#import-detail-wrap');
                const firstTh = wrap && wrap.querySelector('thead th.freeze-1');
                if (firstTh) {
                    wrap.style.setProperty('--freeze-1-width', firstTh.offsetWidth + 'px');
                }
            });
        }

        $('#modal').classList.add('import-detail-modal');
        renderTable();
    } catch (err) {
        console.error(err);
        body.innerHTML = `<p style="color:var(--danger)">加载失败：${escapeHtml(err.message)}</p>`;
    }
}

// Init
updateStepClickable();
loadSavedConfig();

['salary', 'email'].forEach(kind => {
    ['skip-rows', 'header-rows', 'data-start', 'data-end', 'data-end-col'].forEach(field => {
        const el = $(`#${kind}-${field}`);
        if (el) el.addEventListener('input', saveCurrentConfig);
    });
});

// Theme
const THEME_KEY = 'salary_manager_theme';
function applySavedTheme() {
    const saved = localStorage.getItem(THEME_KEY) || 'system';
    let theme = saved;
    if (theme === 'system') {
        theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', theme);
    updateThemeDropdown();
}
function setTheme(theme) {
    localStorage.setItem(THEME_KEY, theme);
    applySavedTheme();
    const menu = $('#themeDropdownMenu');
    if (menu) menu.classList.remove('active');
}
function toggleThemeDropdown() {
    const menu = $('#themeDropdownMenu');
    if (menu) menu.classList.toggle('active');
}
function updateThemeDropdown() {
    const saved = localStorage.getItem(THEME_KEY) || 'system';
    document.querySelectorAll('.theme-option').forEach(el => {
        el.classList.toggle('active', el.dataset.theme === saved);
    });
}

// Package / open project
async function openProjectDir() {
    try {
        await postJson('/api/open-project-dir', {});
    } catch (e) {
        alert('打开项目目录失败：' + e.message);
    }
}
async function packageProject(mode) {
    hidePackageDropdown();
    try {
        await postJson('/api/package-project', { mode });
    } catch (e) {
        alert('打包项目失败：' + e.message);
    }
}
function togglePackageDropdown() {
    const menu = $('#packageDropdownMenu');
    if (menu) menu.classList.toggle('active');
}
function hidePackageDropdown() {
    const menu = $('#packageDropdownMenu');
    if (menu) menu.classList.remove('active');
}

// Stop service
async function stopServer() {
    if (!confirm('确定要停止服务吗？')) return;
    try {
        const res = await postJson('/api/stop-service', {});
        alert(res.message);
    } catch (e) {
        alert('停止服务失败：' + e.message);
    }
}

// Close dropdowns when clicking outside
document.addEventListener('click', (e) => {
    const themeDropdown = $('.theme-dropdown');
    if (themeDropdown && !themeDropdown.contains(e.target)) {
        const menu = $('#themeDropdownMenu');
        if (menu) menu.classList.remove('active');
    }
    const packageDropdown = $('#packageDropdown');
    if (packageDropdown && !packageDropdown.contains(e.target)) {
        hidePackageDropdown();
    }
});

// 自定义确认弹框（居中显示，替代原生 confirm）
function confirmModal(message, allowHtml = false) {
    return new Promise((resolve) => {
        // 防止重复弹出：已有弹框时直接返回取消
        if (document.querySelector('.confirm-modal-overlay')) {
            resolve(false);
            return;
        }
        const overlay = document.createElement('div');
        overlay.className = 'confirm-modal-overlay';
        const safeMessage = allowHtml ? message : escapeHtml(message);
        overlay.innerHTML = `<div class="confirm-modal-box">
            <p class="confirm-modal-message">${safeMessage}</p>
            <div class="confirm-modal-actions">
                <button class="btn" data-confirm="cancel">取消</button>
                <button class="btn danger" data-confirm="ok">确定</button>
            </div>
        </div>`;
        document.body.appendChild(overlay);
        const okBtn = overlay.querySelector('[data-confirm="ok"]');
        const cancelBtn = overlay.querySelector('[data-confirm="cancel"]');
        let settled = false;
        const close = (result) => {
            if (settled) return;
            settled = true;
            overlay.remove();
            resolve(result);
            document.removeEventListener('keydown', keyHandler, true);
        };
        okBtn.addEventListener('click', () => close(true));
        cancelBtn.addEventListener('click', () => close(false));
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(false); });
        // 键盘交互：ESC=取消, Enter/Space=确定（默认高亮按钮）
        // 用捕获阶段监听，stopPropagation+preventDefault 阻止事件到达底层"删除"按钮，避免重复弹出
        const keyHandler = (e) => {
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(false); }
            else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); close(true); }
        };
        document.addEventListener('keydown', keyHandler, true);
        // 打开后立即聚焦"确定"按钮，使其成为默认按钮
        okBtn.focus();
    });
}

// 自定义信息提示弹框（居中显示，替代原生 alert）
function infoModal(message, allowHtml = false) {
    return new Promise((resolve) => {
        if (document.querySelector('.confirm-modal-overlay')) {
            resolve(false);
            return;
        }
        const overlay = document.createElement('div');
        overlay.className = 'confirm-modal-overlay';
        const messageContent = allowHtml ? message : escapeHtml(message);
        overlay.innerHTML = `<div class="confirm-modal-box">
            <p class="confirm-modal-message">${messageContent}</p>
            <div class="confirm-modal-actions">
                <button class="btn primary" data-confirm="ok">确定</button>
            </div>
        </div>`;
        document.body.appendChild(overlay);
        const okBtn = overlay.querySelector('[data-confirm="ok"]');
        let settled = false;
        const close = () => {
            if (settled) return;
            settled = true;
            overlay.remove();
            resolve(true);
            document.removeEventListener('keydown', keyHandler, true);
        };
        okBtn.addEventListener('click', () => close());
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        const keyHandler = (e) => {
            if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') {
                e.preventDefault(); e.stopPropagation(); close();
            }
        };
        document.addEventListener('keydown', keyHandler, true);
        okBtn.focus();
    });
}

// 年份/月份输入限制：年份4位且首位1或2，月份1-12
(function() {
    // type="month" 原生日历选择器，无需手动校验
})();

// ---------------------------------------------------------------------------
// Send Salary Emails
// ---------------------------------------------------------------------------

let currentImportId = null;
let previewData = [];

async function loadSendImports() {
    try {
        const res = await fetch('/api/import-history');
        if (!res.ok) throw new Error('加载失败');
        const data = await res.json();
        
        const select = $('#send-import-select');
        select.innerHTML = '<option value="">请选择导入批次</option>';
        
        data.imports.forEach(imp => {
            const option = document.createElement('option');
            option.value = imp.id;
            option.textContent = `${imp.year}年${imp.month}月 - ${imp.salary_filename || '无工资表'}`;
            select.appendChild(option);
        });
        
    } catch (e) {
        infoModal(`加载导入历史失败: ${e.message}`);
    }
}

$('#send-import-select').addEventListener('change', async (e) => {
    const importId = e.target.value;
    if (!importId) {
        $('#send-preview-area').classList.add('hidden');
        return;
    }
    
    currentImportId = importId;
    await loadPreviewData(importId);
});

async function loadPreviewData(importId) {
    try {
        const res = await fetch(`/api/persons-by-import?import_id=${importId}`);
        if (!res.ok) throw new Error('加载失败');
        const data = await res.json();
        
        previewData = data.persons || [];
        
        // Update stats
        const validEmails = previewData.filter(p => p.email && isValidEmail(p.email));
        const invalidEmails = previewData.filter(p => p.email && !isValidEmail(p.email));
        
        $('#preview-total').textContent = previewData.length;
        $('#preview-valid').textContent = validEmails.length;
        $('#preview-invalid').textContent = invalidEmails.length;
        
        // Build preview table
        const tbody = $('#preview-tbody');
        tbody.innerHTML = '';
        
        previewData.forEach((person, index) => {
            const tr = document.createElement('tr');
            const hasValidEmail = person.email && isValidEmail(person.email);
            const statusClass = hasValidEmail ? 'status-valid' : 'status-invalid';
            const statusText = hasValidEmail ? '有效' : (person.email ? '格式错误' : '无邮箱');
            
            tr.innerHTML = `
                <td><input type="checkbox" class="row-checkbox" data-index="${index}" ${hasValidEmail ? 'checked' : ''} ${!hasValidEmail ? 'disabled' : ''}></td>
                <td>${escapeHtml(person.community)}</td>
                <td>${escapeHtml(person.name)}</td>
                <td>${escapeHtml(person.email || '-')}</td>
                <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            `;
            tbody.appendChild(tr);
        });
        
        $('#send-preview-area').classList.remove('hidden');
        
    } catch (e) {
        infoModal(`加载预览数据失败: ${e.message}`);
    }
}

function isValidEmail(email) {
    if (!email) return false;
    return /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(email);
}

// Select all rows checkbox
$('#select-all-rows').addEventListener('change', (e) => {
    document.querySelectorAll('.row-checkbox:not([disabled])').forEach(cb => {
        cb.checked = e.target.checked;
    });
});

async function startBatchSend() {
    // Get selected rows
    const selectedIndices = [];
    document.querySelectorAll('.row-checkbox:checked').forEach(cb => {
        selectedIndices.push(parseInt(cb.dataset.index));
    });
    
    if (selectedIndices.length === 0) {
        infoModal('请至少选择一行');
        return;
    }
    
    const confirmed = await confirmModal(`确定要发送 ${selectedIndices.length} 封邮件吗？这可能需要较长时间。`);
    if (!confirmed) return;
    
    // Prepare email data
    const emailsToSend = selectedIndices.map(idx => {
        const person = previewData[idx];
        return {
            person_id: person.id,
            email: person.email,
            subject: `${person.community} ${new Date().getFullYear()}年${new Date().getMonth() + 1}月工资单`,
            content: generateEmailContent(person)
        };
    });
    
    // Show progress area
    $('#send-progress-area').classList.remove('hidden');
    $('#start-send-btn').disabled = true;
    
    try {
        const res = await fetch('/api/send-salary-emails', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({emails: emailsToSend})
        });
        
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || '发送失败');
        
        // Show results
        showSendResults(result);
        
    } catch (e) {
        infoModal(`发送失败: ${e.message}`);
        $('#start-send-btn').disabled = false;
    }
}

function generateEmailContent(person) {
    // Simple HTML email template
    let html = `
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #333;">${person.community} 工资单</h2>
            <p>您好，${person.name}：</p>
            <p>以下是您本月的工资明细：</p>
            <div style="background: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
    `;
    
    // Parse data_json if available
    if (person.data_json) {
        try {
            const data = typeof person.data_json === 'string' ? JSON.parse(person.data_json) : person.data_json;
            for (const [key, value] of Object.entries(data)) {
                html += `<p><strong>${key}:</strong> ${value}</p>`;
            }
        } catch (e) {
            html += `<p>无法解析工资数据</p>`;
        }
    }
    
    html += `
            </div>
            <p style="color: #666; font-size: 12px;">此邮件由系统自动发送，请勿回复。</p>
        </body>
        </html>
    `;
    
    return html;
}

function showSendResults(result) {
    $('#send-results').classList.remove('hidden');
    
    const content = $('#send-results-content');
    content.innerHTML = `
        <div class="result-summary">
            <p><strong>总计:</strong> ${result.total} 封</p>
            <p><strong>成功:</strong> <span style="color: var(--success);">${result.success_count} 封</span></p>
            <p><strong>失败:</strong> <span style="color: var(--danger);">${result.failed_count} 封</span></p>
        </div>
    `;
    
    if (result.results && result.results.length > 0) {
        const failedResults = result.results.filter(r => !r.success);
        if (failedResults.length > 0) {
            content.innerHTML += `
                <h4>失败详情:</h4>
                <ul>
                    ${failedResults.map(r => `<li>${escapeHtml(r.error || '未知错误')}</li>`).join('')}
                </ul>
            `;
        }
    }
    
    $('#start-send-btn').disabled = false;
    
    // Reload status
    if (currentImportId) {
        loadEmailStatus(currentImportId);
    }
}

async function resetSendStatus() {
    if (!currentImportId) return;
    
    const confirmed = await confirmModal('确定要重置所有发送状态吗？这将允许重新发送。');
    if (!confirmed) return;
    
    try {
        const res = await fetch('/api/reset-email-send-status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({import_id: currentImportId})
        });
        
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || '重置失败');
        
        infoModal(result.message);
        
        // Reload preview
        await loadPreviewData(currentImportId);
        
    } catch (e) {
        infoModal(`重置失败: ${e.message}`);
    }
}

async function loadEmailStatus(importId) {
    try {
        const res = await fetch(`/api/email-send-status?import_id=${importId}`);
        if (!res.ok) return;
        
        const data = await res.json();
        
        // Update UI with status if needed
        console.log('Email send status:', data);
        
    } catch (e) {
        console.error('Failed to load email status:', e);
    }
}

// Load imports when tab is activated
document.querySelectorAll('.tab-btn[data-tab="send-emails"]').forEach(btn => {
    btn.addEventListener('click', () => {
        setTimeout(loadSendImports, 100);
    });
});

// ---------------------------------------------------------------------------
// Sender Email Management
// ---------------------------------------------------------------------------

let currentSenderId = null;

// ---------------------------------------------------------------------------
// Test Email
// ---------------------------------------------------------------------------

function testSender(id, email) {
    currentSenderId = id;
    $('#test-sender-id').value = id;
    $('#test-recipient').value = '';
    $('#test-subject').value = '发送邮箱测试';
    
    // 生成默认测试内容（包含当前时间）
    const now = new Date();
    const timeStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    $('#test-content').value = `这是一封测试邮件，发送时间：${timeStr}`;
    
    $('#test-email-modal').classList.remove('hidden');
}

function closeTestEmailModal() {
    $('#test-email-modal').classList.add('hidden');
    currentSenderId = null;
}

async function sendTestEmail(event) {
    event.preventDefault();
    
    const senderId = $('#test-sender-id').value;
    const recipientEmail = $('#test-recipient').value;
    const subject = $('#test-subject').value || '发送邮箱测试';
    const content = $('#test-content').value;
    
    const btn = $('#test-send-btn');
    btn.disabled = true;
    btn.textContent = '发送中...';
    
    try {
        const res = await fetch(`/api/sender-emails/${senderId}/test`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                recipient_email: recipientEmail,
                subject: subject,
                content: content
            })
        });
        
        const result = await res.json();
        
        if (res.ok) {
            infoModal(result.message || '测试邮件发送成功！');
            closeTestEmailModal();
        } else {
            infoModal(`发送失败：${result.error}`);
        }
        
    } catch (e) {
        infoModal(`请求失败：${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = '发送测试邮件';
    }
}

// ---------------------------------------------------------------------------
// Sender CRUD
// ---------------------------------------------------------------------------

async function loadSenders() {
    try {
        const res = await fetch('/api/sender-emails');
        if (!res.ok) throw new Error('加载失败');
        const data = await res.json();
        
        const container = $('#senders-list');
        if (!data.senders || data.senders.length === 0) {
            container.innerHTML = '<p class="empty-text">暂无发件人，请点击右上角添加</p>';
            return;
        }
        
        let html = '<div class="senders-grid">';
        data.senders.forEach(sender => {
            const statusClass = sender.is_enabled ? 'enabled' : 'disabled';
            const statusText = sender.is_enabled ? '启用' : '禁用';
            const lastSend = sender.last_send_time ? formatDateTime(sender.last_send_time) : '从未发送';
            const displayName = sender.alias ? `${escapeHtml(sender.alias)} (${escapeHtml(sender.email)})` : escapeHtml(sender.email);
            
            html += `
                <div class="sender-card ${statusClass}">
                    <div class="sender-card-header">
                        <div class="sender-email">${displayName}</div>
                        <button class="sender-status-badge ${statusClass}" onclick="toggleSender(${sender.id})" title="点击${sender.is_enabled ? '禁用' : '启用'}">${statusText}</button>
                    </div>
                    <div class="sender-card-body">
                        <div class="sender-info">
                            <span>SMTP:</span> ${escapeHtml(sender.smtp_server)}:${sender.smtp_port}
                        </div>
                        <div class="sender-info">
                            <span>IMAP:</span> ${escapeHtml(sender.imap_server)}:${sender.imap_port}
                        </div>
                        <div class="sender-info">
                            <span>间隔:</span> ${sender.send_interval}秒
                        </div>
                        <div class="sender-info">
                            <span>最后发送:</span> ${lastSend}
                        </div>
                    </div>
                    <div class="sender-card-actions">
                        <button class="btn small text" onclick="testSender(${sender.id}, '${escapeHtml(sender.email)}')">测试</button>
                        <button class="btn small text" onclick="editSender(${sender.id})">编辑</button>
                        <button class="btn small text danger" onclick="deleteSender(${sender.id}, '${escapeHtml(sender.email)}')">删除</button>
                    </div>
                </div>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
        
    } catch (e) {
        $('#senders-list').innerHTML = `<p class="error-text">加载失败: ${e.message}</p>`;
    }
}

function showAddSenderModal() {
    currentSenderId = null;
    $('#sender-modal-title').textContent = '添加发件人';
    $('#sender-form').reset();
    $('#sender-id').value = '';
    $('#sender-password').required = true;
    $('#sender-modal').classList.remove('hidden');
}

async function editSender(id) {
    try {
        const res = await fetch('/api/sender-emails');
        if (!res.ok) throw new Error('加载失败');
        const data = await res.json();
        
        const sender = data.senders.find(s => s.id === id);
        if (!sender) throw new Error('发件人不存在');
        
        currentSenderId = id;
        $('#sender-modal-title').textContent = '编辑发件人';
        $('#sender-id').value = id;
        $('#sender-email').value = sender.email;
        $('#sender-alias').value = sender.alias || '';
        $('#sender-smtp-server').value = sender.smtp_server;
        $('#sender-smtp-port').value = sender.smtp_port;
        $('#sender-imap-server').value = sender.imap_server;
        $('#sender-imap-port').value = sender.imap_port;
        $('#sender-interval').value = sender.send_interval;
        $('#sender-password').required = false;
        $('#sender-password').placeholder = '留空则不修改密码';
        
        $('#sender-modal').classList.remove('hidden');
        
    } catch (e) {
        infoModal(`加载发件人信息失败: ${e.message}`);
    }
}

function closeSenderModal() {
    $('#sender-modal').classList.add('hidden');
    currentSenderId = null;
}

async function saveSender(event) {
    event.preventDefault();
    
    const data = {
        email: $('#sender-email').value,
        alias: $('#sender-alias').value,
        smtp_server: $('#sender-smtp-server').value,
        smtp_port: parseInt($('#sender-smtp-port').value),
        imap_server: $('#sender-imap-server').value,
        imap_port: parseInt($('#sender-imap-port').value),
        send_interval: parseInt($('#sender-interval').value)
    };
    
    const password = $('#sender-password').value;
    if (password) {
        data.password = password;
    } else if (!currentSenderId) {
        infoModal('密码不能为空');
        return;
    }
    
    try {
        let res;
        if (currentSenderId) {
            // Update
            res = await fetch(`/api/sender-emails/${currentSenderId}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
        } else {
            // Create
            data.password = password;
            res = await fetch('/api/sender-emails', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
        }
        
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || '操作失败');
        
        infoModal(result.message || '保存成功');
        closeSenderModal();
        loadSenders();
        
    } catch (e) {
        infoModal(`保存失败: ${e.message}`);
    }
}

async function toggleSender(id) {
    try {
        const res = await fetch(`/api/sender-emails/${id}/toggle`, {
            method: 'PATCH'
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || '操作失败');
        
        loadSenders();
        
    } catch (e) {
        infoModal(`操作失败: ${e.message}`);
    }
}

async function deleteSender(id, email) {
    const confirmed = await confirmModal(`确定要删除发件人 "${email}" 吗？`);
    if (!confirmed) return;
    
    try {
        const res = await fetch(`/api/sender-emails/${id}`, {
            method: 'DELETE'
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.error || '删除失败');
        
        infoModal(result.message);
        loadSenders();
        
    } catch (e) {
        infoModal(`删除失败: ${e.message}`);
    }
}

function formatDateTime(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr.replace(' ', 'T'));
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes}`;
}

// Load senders when tab is activated
document.querySelectorAll('.tab-btn[data-tab="senders"]').forEach(btn => {
    btn.addEventListener('click', () => {
        setTimeout(loadSenders, 100);
    });
});

applySavedTheme();