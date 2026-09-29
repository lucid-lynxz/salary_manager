// State
const state = {
    salary: { fileId: null, filename: null, config: null, parsed: null, preview: null },
    email: { fileId: null, filename: null, config: null, parsed: null, preview: null, previewRowIndices: [], mapping: null, overrides: {}, overrideLog: {}, validationState: null },
    currentStep: 1,
    unlockedSteps: { 1: true },
    detectedYear: null,
    detectedMonth: null,
    savedConfig: null,
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

async function uploadFile(file, fileType) {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('file_type', fileType);
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
            header_rows: $('#salary-header-rows').value,
            data_start: $('#salary-data-start').value,
            data_end: $('#salary-data-end').value,
            data_end_col: $('#salary-data-end-col').value.trim(),
        },
        email: {
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

function renderTable(tableEl, rows, startRow, headerRows, rowIndices) {
    tableEl.innerHTML = '';
    if (!rows || !rows.length) return;
    startRow = startRow || 1;
    headerRows = headerRows || [];
    rowIndices = rowIndices || [];
    const headerCount = headerRows.length ? Math.max(...headerRows) : 1;
    const colCount = rows[0].length;

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
        row.forEach(cell => {
            const td = document.createElement('td');
            td.textContent = formatPreviewCell(cell);
            tr.appendChild(td);
        });
        thead.appendChild(tr);
    }
    tableEl.appendChild(thead);

    // 数据行
    const tbody = document.createElement('tbody');
    for (let idx = headerCount; idx < rows.length; idx++) {
        const row = rows[idx];
        const excelRow = rowIndices[idx];
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
            continue;
        }
        const tr = document.createElement('tr');
        const rowTh = document.createElement('th');
        rowTh.className = 'row-header';
        rowTh.textContent = excelRow ?? startRow + idx;
        tr.appendChild(rowTh);
        row.forEach(cell => {
            const td = document.createElement('td');
            td.textContent = formatPreviewCell(cell);
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    }
    tableEl.appendChild(tbody);
}

function populateSelect(selectEl, headers, selectedIndex) {
    selectEl.innerHTML = '';
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
        if (tab === 'search') loadMonths();
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
        s.classList.toggle('clickable', state.unlockedSteps[step]);
    });
}

$('#wizard-steps').addEventListener('click', (e) => {
    const stepEl = e.target.closest('.step');
    if (!stepEl) return;
    const n = parseInt(stepEl.dataset.step);
    if (state.unlockedSteps[n]) goStep(n);
});

$('#to-step2').addEventListener('click', () => goStep(2));
$('#back-to-step1').addEventListener('click', () => goStep(1));
$('#to-step3').addEventListener('click', () => {
    buildSummary();
    goStep(3);
});
$('#back-to-step2').addEventListener('click', () => goStep(2));

// Salary upload
$('#salary-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
        const data = await uploadFile(file, 'salary');
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
    const sliced = slicePreviewByEndCol(data.preview, $('#salary-data-end-col').value, state.salary.previewRowIndices);
    renderTable($('#salary-preview-table'), sliced.rows, 1, parseHeaderRows($('#salary-header-rows').value), sliced.rowIndices);
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
        });
        if (data.error) { alert(data.error); return; }
        state.salary.sheetData = data;
        state.salary.preview = data.preview;
        state.salary.previewRowIndices = data.preview_row_indices || [];
        const sliced = slicePreviewByEndCol(data.preview, $('#salary-data-end-col').value, state.salary.previewRowIndices);
        renderTable($('#salary-preview-table'), sliced.rows, 1, parseHeaderRows($('#salary-header-rows').value), sliced.rowIndices);
        applySectionDefaults('salary', data.detected_header_row);
        $('#to-step2').disabled = true;
    } catch (err) {
        console.error(err);
        alert('切换 Sheet 失败：' + err.message);
    }
});

$('#salary-preview-btn').addEventListener('click', parseSalary);

function updateDataStartFromHeaders(headerInputId, dataStartId) {
    const headerRows = parseHeaderRows($(headerInputId).value);
    if (headerRows.length) {
        $(dataStartId).value = Math.max(...headerRows) + 1;
    }
}

$('#salary-header-rows').addEventListener('input', () => {
    updateDataStartFromHeaders('#salary-header-rows', '#salary-data-start');
    if (state.salary.preview) {
        const sliced = slicePreviewByEndCol(state.salary.preview, $('#salary-data-end-col').value, state.salary.previewRowIndices);
        renderTable($('#salary-preview-table'), sliced.rows, 1, parseHeaderRows($('#salary-header-rows').value), sliced.rowIndices);
    }
});

$('#salary-data-end-col').addEventListener('input', () => {
    if (state.salary.preview) {
        const sliced = slicePreviewByEndCol(state.salary.preview, $('#salary-data-end-col').value, state.salary.previewRowIndices);
        renderTable($('#salary-preview-table'), sliced.rows, 1, parseHeaderRows($('#salary-header-rows').value), sliced.rowIndices);
    }
});

async function parseSalary() {
    try {
        const headerRows = parseHeaderRows($('#salary-header-rows').value);
        const dataStart = parseInt($('#salary-data-start').value);
        const dataEndVal = $('#salary-data-end').value;
        const dataEnd = dataEndVal ? parseInt(dataEndVal) : null;
        const dataEndCol = $('#salary-data-end-col').value.trim() || null;

        const body = {
            file_id: state.salary.fileId,
            sheet_name: $('#salary-sheet').value,
            file_type: 'salary',
            header_rows: headerRows,
            data_start_row: dataStart,
            data_end_row: dataEnd,
            data_end_col: dataEndCol,
        };
        const res = await postJson('/api/parse-config', body);
        if (res.error) { alert(res.error); return; }
        state.salary.parsed = res;

        const headers = res.headers;
        const mapping = res.suggested_mapping || {};
        populateSelect($('#salary-name-col'), headers, mapping.name ?? -1);
        populateSelect($('#salary-community-col'), headers, mapping.community ?? -1);

        const resultEl = $('#salary-parse-result');
        resultEl.innerHTML = `
            <p>解析到 <strong>${res.record_count}</strong> 条有效工资记录</p>
            <p>建议列映射：姓名=${mapping.name !== undefined ? headers[mapping.name] : '未识别'}, 社区=${mapping.community !== undefined ? headers[mapping.community] : '未识别'}</p>
        `;
        show(resultEl);
        $('#to-step2').disabled = false;
        unlockStep(2);
    } catch (err) {
        console.error(err);
        alert('解析工资表失败：' + err.message);
    }
}

// Email upload
$('#email-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
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
        const sliced = slicePreviewByEndCol(data.preview, $('#email-data-end-col').value, state.email.previewRowIndices);
        renderTable($('#email-preview-table'), sliced.rows, 1, parseHeaderRows($('#email-header-rows').value), sliced.rowIndices);
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
        });
        if (data.error) { alert(data.error); return; }
        state.email.sheetData = data;
        state.email.preview = data.preview;
        state.email.previewRowIndices = data.preview_row_indices || [];
        const sliced = slicePreviewByEndCol(data.preview, $('#email-data-end-col').value, state.email.previewRowIndices);
        renderTable($('#email-preview-table'), sliced.rows, 1, parseHeaderRows($('#email-header-rows').value), sliced.rowIndices);
        applySectionDefaults('email', data.detected_header_row);
        $('#to-step3').disabled = true;
    } catch (err) {
        console.error(err);
        alert('切换 Sheet 失败：' + err.message);
    }
});

$('#email-preview-btn').addEventListener('click', parseEmail);

$('#email-header-rows').addEventListener('input', () => {
    updateDataStartFromHeaders('#email-header-rows', '#email-data-start');
    if (state.email.preview) {
        const sliced = slicePreviewByEndCol(state.email.preview, $('#email-data-end-col').value, state.email.previewRowIndices);
        renderTable($('#email-preview-table'), sliced.rows, 1, parseHeaderRows($('#email-header-rows').value), sliced.rowIndices);
    }
});

$('#email-data-end-col').addEventListener('input', () => {
    if (state.email.preview) {
        const sliced = slicePreviewByEndCol(state.email.preview, $('#email-data-end-col').value, state.email.previewRowIndices);
        renderTable($('#email-preview-table'), sliced.rows, 1, parseHeaderRows($('#email-header-rows').value), sliced.rowIndices);
    }
});

async function parseEmail() {
    try {
        const headerRows = parseHeaderRows($('#email-header-rows').value);
        const dataStart = parseInt($('#email-data-start').value);
        const dataEndVal = $('#email-data-end').value;
        const dataEnd = dataEndVal ? parseInt(dataEndVal) : null;
        const dataEndCol = $('#email-data-end-col').value.trim() || null;

        const body = {
            file_id: state.email.fileId,
            sheet_name: $('#email-sheet').value,
            file_type: 'email',
            header_rows: headerRows,
            data_start_row: dataStart,
            data_end_row: dataEnd,
            data_end_col: dataEndCol,
        };
        const res = await postJson('/api/parse-config', body);
        if (res.error) { alert(res.error); return; }
        state.email.parsed = res;
        state.email.overrides = {};
        state.email.overrideLog = {};
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
    const groups = (validation.duplicate_groups || []).map(g => ({
        email: g.email,
        rows: g.rows.map(r => ({
            ...r,
            confirmed: false,
            editing: false,
            editValue: '',
            current: r.cleaned,
        })),
    }));
    state.email.validationState = {
        total: validation.total,
        valid: validation.valid,
        invalid: validation.invalid,
        issues: validation.issues || [],
        duplicateGroups: groups,
    };
}

function validateEmailLocal(email) {
    const errors = [];
    if (!email || !email.trim()) {
        errors.push('邮箱为空');
        return { email: '', errors };
    }
    let cleaned = email.trim().replace(/[\s\n\r\t]+/g, '').replace(/＠/g, '@').replace(/\bat\b/gi, '@').replace(/＠/g, '@').toLowerCase();
    if (!cleaned.includes('@')) errors.push('缺少 @');
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleaned)) errors.push('格式错误');
    return { email: cleaned, errors };
}

function rebuildValidationState() {
    const vs = state.email.validationState;
    if (!vs) return;
    // 收集所有当前有效邮箱（已确认或不在重复组中）
    const allEmails = [];
    vs.duplicateGroups.forEach(g => {
        g.rows.forEach(r => {
            allEmails.push(r.current);
        });
    });
    // 重新计算重复
    const counts = {};
    allEmails.forEach(e => { counts[e] = (counts[e] || 0) + 1; });

    // 合并相同的重复组
    const groupsByEmail = {};
    vs.duplicateGroups.forEach(g => {
        g.rows.forEach(r => {
            const e = r.current;
            if (!groupsByEmail[e]) groupsByEmail[e] = [];
            groupsByEmail[e].push(r);
        });
    });

    const newGroups = [];
    Object.entries(groupsByEmail).forEach(([email, rows]) => {
        if (rows.length <= 1) return;
        // 若该邮箱存在基础格式错误，不归为重复组
        const hasFormatError = rows.some(r => {
            const v = validateEmailLocal(r.current);
            return v.errors.length > 0;
        });
        if (hasFormatError) return;
        newGroups.push({ email, rows });
    });

    // 非重复记录数 = 总记录 - 重复组记录数 - 其他 issues 记录数
    const duplicateCount = newGroups.reduce((sum, g) => sum + g.rows.length, 0);
    const otherCount = vs.issues.length;
    vs.duplicateGroups = newGroups;
    vs.invalid = duplicateCount + otherCount;
}

function confirmDuplicateRow(groupIdx, rowIdx) {
    const vs = state.email.validationState;
    const row = vs.duplicateGroups[groupIdx].rows[rowIdx];
    if (row.editing) return;
    row.confirmed = true;
    renderValidation();
    updateStep3Button();
}

function confirmDuplicateGroup(groupIdx) {
    const vs = state.email.validationState;
    vs.duplicateGroups[groupIdx].rows.forEach(r => {
        if (!r.editing) r.confirmed = true;
    });
    renderValidation();
    updateStep3Button();
}

function editDuplicateRow(groupIdx, rowIdx) {
    const vs = state.email.validationState;
    const row = vs.duplicateGroups[groupIdx].rows[rowIdx];
    row.editing = true;
    row.editValue = row.current;
    renderValidation();
}

function saveDuplicateEdit(groupIdx, rowIdx) {
    const vs = state.email.validationState;
    const row = vs.duplicateGroups[groupIdx].rows[rowIdx];
    const newEmailRaw = row.editValue;
    const v = validateEmailLocal(newEmailRaw);
    if (v.errors.length) {
        alert('邮箱格式不正确：' + v.errors.join('；'));
        return;
    }
    const key = `${row.community}|${row.name}`;
    state.email.overrides[key] = v.email;
    state.email.overrideLog[key] = {
        community: row.community,
        name: row.name,
        original: row.raw,
        current: v.email,
    };
    updateEmailPreviewForOverride(row.row, v.email);
    row.current = v.email;
    row.editing = false;
    row.confirmed = false; // 修改后需再次确认
    rebuildValidationState();
    renderValidation();
    updateStep3Button();
}

function cancelDuplicateEdit(groupIdx, rowIdx) {
    const vs = state.email.validationState;
    const row = vs.duplicateGroups[groupIdx].rows[rowIdx];
    row.editing = false;
    row.editValue = '';
    renderValidation();
}

function updateStep3Button() {
    const vs = state.email.validationState;
    if (!vs) {
        $('#to-step3').disabled = true;
        return;
    }
    const hasPendingDuplicates = vs.duplicateGroups.some(g => g.rows.some(r => !r.confirmed && !r.editing));
    const hasIssues = vs.issues.length > 0;
    $('#to-step3').disabled = hasPendingDuplicates || hasIssues;
}

function updateEmailPreviewForOverride(excelRow, newEmail) {
    const preview = state.email.preview;
    const rowIndices = state.email.previewRowIndices;
    const mapping = state.email.mapping;
    if (!preview || !rowIndices || !mapping || mapping.email == null) return;
    const idx = rowIndices.findIndex(ri => ri === excelRow);
    if (idx < 0 || idx >= preview.length) return;
    preview[idx][mapping.email] = newEmail;
    const sliced = slicePreviewByEndCol(preview, $('#email-data-end-col').value, rowIndices);
    renderTable($('#email-preview-table'), sliced.rows, 1, parseHeaderRows($('#email-header-rows').value), sliced.rowIndices);
}

function renderOverrideLog() {
    const log = state.email.overrideLog || {};
    const entries = Object.values(log);
    if (!entries.length) return '';
    let html = `<div class="override-log"><h4>邮箱修改记录</h4>
        <table><thead><tr><th>社区</th><th>姓名</th><th>原始邮箱</th><th>修改后邮箱</th></tr></thead><tbody>`;
    entries.forEach(entry => {
        html += `<tr>
            <td>${escapeHtml(entry.community)}</td>
            <td>${escapeHtml(entry.name)}</td>
            <td>${escapeHtml(entry.original)}</td>
            <td>${escapeHtml(entry.current)}</td>
        </tr>`;
    });
    html += '</tbody></table></div>';
    return html;
}

function renderValidation() {
    const el = $('#email-validation');
    const vs = state.email.validationState;
    if (!vs) { hide(el); return; }
    show(el);
    let html = `
        <div class="stats">
            <div class="stat valid">有效：${vs.valid} / ${vs.total}</div>
            <div class="stat invalid">无效：${vs.invalid} / ${vs.total}</div>
        </div>
    `;

    // 重复邮箱分组
    if (vs.duplicateGroups.length) {
        html += `<div class="duplicate-section"><h4>重复邮箱（请确认是否为正常共用邮箱，或修改）</h4>`;
        vs.duplicateGroups.forEach((g, gi) => {
            const allConfirmed = g.rows.every(r => r.confirmed);
            html += `<div class="duplicate-group ${allConfirmed ? 'confirmed' : ''}" data-group-idx="${gi}">
                <div class="group-header">
                    <span class="group-email">${escapeHtml(g.email)}</span>
                    <span class="group-count">共 ${g.rows.length} 人</span>
                    ${allConfirmed ? '<span class="group-status">已确认</span>' : `<button class="btn small" data-confirm-group="${gi}">全部确认</button>`}
                </div>
                <table>
                    <thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>原始邮箱</th><th>当前邮箱</th><th>操作</th></tr></thead>
                    <tbody>`;
            g.rows.forEach((r, ri) => {
                const key = `${r.community}|${r.name}`;
                const isOverridden = state.email.overrides[key] !== undefined;
                html += `<tr class="${r.confirmed ? 'confirmed-row' : ''}">
                    <td>${escapeHtml(r.row)}</td>
                    <td>${escapeHtml(r.community)}</td>
                    <td>${escapeHtml(r.name)}</td>
                    <td>${escapeHtml(r.raw)}${isOverridden ? ' <span class="override-tag">已修改</span>' : ''}</td>`;
                if (r.editing) {
                    html += `<td><input type="text" class="email-edit-input" data-group="${gi}" data-row="${ri}" value="${escapeHtml(r.editValue)}"></td>
                        <td>
                            <button class="btn small primary" data-save-edit="${gi}" data-row="${ri}">保存</button>
                            <button class="btn small text" data-cancel-edit="${gi}" data-row="${ri}">取消</button>
                        </td>`;
                } else {
                    html += `<td>${escapeHtml(r.current)}</td>
                        <td>
                            ${r.confirmed ? '<span class="confirmed-tag">已确认</span>' : `<button class="btn small" data-confirm-row="${gi}" data-row="${ri}">确认</button>`}
                            <button class="btn small text" data-edit-row="${gi}" data-row="${ri}">修改</button>
                        </td>`;
                }
                html += `</tr>`;
            });
            html += `</tbody></table></div>`;
        });
        html += `</div>`;
    }

    // 其他问题
    if (vs.issues.length) {
        html += `<div class="issues-section"><h4>其他邮箱问题</h4>
            <table><thead><tr><th>行号</th><th>社区</th><th>姓名</th><th>原始邮箱</th><th>清洗后</th><th>问题</th></tr></thead><tbody>`;
        vs.issues.forEach(issue => {
            html += `<tr>
                <td>${escapeHtml(issue.row)}</td>
                <td>${escapeHtml(issue.community)}</td>
                <td>${escapeHtml(issue.name)}</td>
                <td>${escapeHtml(issue.raw)}</td>
                <td>${escapeHtml(issue.cleaned)}</td>
                <td>${escapeHtml(issue.errors.join('；'))}</td>
            </tr>`;
        });
        html += `</tbody></table></div>`;
    }

    html += renderOverrideLog();

    if (!vs.duplicateGroups.length && !vs.issues.length && !Object.keys(state.email.overrideLog || {}).length) {
        html += `<p>所有邮箱均校验通过。</p>`;
    }

    el.innerHTML = html;

    // Bind events
    el.querySelectorAll('[data-confirm-row]').forEach(btn => {
        btn.addEventListener('click', () => confirmDuplicateRow(parseInt(btn.dataset.confirmRow), parseInt(btn.dataset.row)));
    });
    el.querySelectorAll('[data-confirm-group]').forEach(btn => {
        btn.addEventListener('click', () => confirmDuplicateGroup(parseInt(btn.dataset.confirmGroup)));
    });
    el.querySelectorAll('[data-edit-row]').forEach(btn => {
        btn.addEventListener('click', () => editDuplicateRow(parseInt(btn.dataset.editRow), parseInt(btn.dataset.row)));
    });
    el.querySelectorAll('[data-save-edit]').forEach(btn => {
        btn.addEventListener('click', () => saveDuplicateEdit(parseInt(btn.dataset.saveEdit), parseInt(btn.dataset.row)));
    });
    el.querySelectorAll('[data-cancel-edit]').forEach(btn => {
        btn.addEventListener('click', () => cancelDuplicateEdit(parseInt(btn.dataset.cancelEdit), parseInt(btn.dataset.row)));
    });
    el.querySelectorAll('.email-edit-input').forEach(input => {
        input.addEventListener('input', () => {
            const vs = state.email.validationState;
            const groupIdx = parseInt(input.dataset.group);
            const rowIdx = parseInt(input.dataset.row);
            if (vs && vs.duplicateGroups[groupIdx]) {
                vs.duplicateGroups[groupIdx].rows[rowIdx].editValue = input.value;
            }
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') saveDuplicateEdit(parseInt(input.dataset.group), parseInt(input.dataset.row));
            if (e.key === 'Escape') cancelDuplicateEdit(parseInt(input.dataset.group), parseInt(input.dataset.row));
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
        $('#salary-year').value = year;
        $('#salary-month').value = month;
        hide($('#date-warning'));
    } else {
        show($('#date-warning'));
    }
    $('#import-summary').innerHTML = `
        <p>工资表：<strong>${escapeHtml(state.salary.filename)}</strong>，共 ${s.record_count} 条记录</p>
        <p>邮箱表：<strong>${escapeHtml(state.email.filename)}</strong>，共 ${e.record_count} 条记录</p>
        <p>合并键：<strong>社区 + 姓名</strong></p>
    `;
}

$('#do-import').addEventListener('click', async () => {
    const salaryMapping = {
        name: parseInt($('#salary-name-col').value),
        community: parseInt($('#salary-community-col').value),
    };
    const emailMapping = {
        name: parseInt($('#email-name-col').value),
        community: parseInt($('#email-community-col').value),
        email: parseInt($('#email-col').value),
    };
    const body = {
        year: parseInt($('#salary-year').value),
        month: parseInt($('#salary-month').value),
        salary: {
            file_id: state.salary.fileId,
            filename: state.salary.filename,
            sheet_name: $('#salary-sheet').value,
            header_rows: parseHeaderRows($('#salary-header-rows').value),
            data_start_row: parseInt($('#salary-data-start').value),
            data_end_row: $('#salary-data-end').value ? parseInt($('#salary-data-end').value) : null,
            data_end_col: $('#salary-data-end-col').value.trim() || null,
            mapping: salaryMapping,
        },
        email: {
            file_id: state.email.fileId,
            filename: state.email.filename,
            sheet_name: $('#email-sheet').value,
            header_rows: parseHeaderRows($('#email-header-rows').value),
            data_start_row: parseInt($('#email-data-start').value),
            data_end_row: $('#email-data-end').value ? parseInt($('#email-data-end').value) : null,
            data_end_col: $('#email-data-end-col').value.trim() || null,
            mapping: emailMapping,
            overrides: state.email.overrides,
        },
    };
    const res = await postJson('/api/import', body);
    const box = $('#import-result');
    show(box);
    if (res.error) {
        box.className = 'result-box error';
        box.textContent = '导入失败：' + res.error;
        return;
    }
    box.className = 'result-box';
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

let searchSuggestTimer = null;

function buildSearchUrl(base, q, monthVal) {
    let url = `${base}?q=${encodeURIComponent(q)}`;
    if (monthVal && monthVal !== 'all') {
        const [year, month] = monthVal.split('-');
        url += `&year=${year}&month=${month}`;
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
    const res = await fetch(buildSearchUrl('/api/search', q, monthVal)).then(r => r.json());
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
                <th>邮箱</th>
                <th>年月</th>
                <th>应发工资</th>
                <th>代扣合计</th>
                <th>实发工资</th>
                <th>银行账号</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>`;
    results.forEach(r => {
        html += `<tr>
            <td>${escapeHtml(r.community)}</td>
            <td>${escapeHtml(r.name)}</td>
            <td>${escapeHtml(r.email || '-')}</td>
            <td>${r.year}年${r.month}月</td>
            <td>${fmtMoney(r.gross_salary)}</td>
            <td>${fmtMoney(r.total_deduction)}</td>
            <td>${fmtMoney(r.net_salary)}</td>
            <td>${escapeHtml(r.bank_account || '-')}</td>
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

async function showPersonDetail(id, year, month) {
    let url = `/api/person/${id}`;
    if (year && month) url += `?year=${year}&month=${month}`;
    const r = await fetch(url).then(r => r.json());
    if (r.error) return;
    const body = $('#modal-body');
    let html = `<h2>${escapeHtml(r.community)} · ${escapeHtml(r.name)}</h2>`;
    if (r.email_issues) html += `<p style="color:var(--danger)">邮箱问题：${escapeHtml(r.email_issues)}</p>`;

    if (!r.history.length) {
        html += '<p>该月份暂无记录。</p>';
    } else {
        const h = r.history[0];
        html += `<h3>${h.year}年${h.month}月导入详情</h3>`;
        html += `<table class="detail-table"><tbody>`;
        html += `<tr><th>邮箱</th><td>${escapeHtml(r.email || '-')}</td></tr>`;
        Object.entries(h.detail).forEach(([key, value]) => {
            html += `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(value == null ? '' : String(value))}</td></tr>`;
        });
        html += `</tbody></table>`;
    }
    body.innerHTML = html;
    show($('#modal'));
}

$('.close-btn').addEventListener('click', () => hide($('#modal')));
$('#modal').addEventListener('click', (e) => {
    if (e.target === $('#modal')) hide($('#modal'));
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
        div.innerHTML = `
            <div>
                <strong>${item.year}年${item.month}月</strong>
                <div style="font-size:12px;color:var(--muted);margin-top:4px;">
                    工资：${escapeHtml(item.salary_filename || '-')} | 邮箱：${escapeHtml(item.email_filename || '-')} | ${new Date(item.imported_at).toLocaleString()}
                </div>
            </div>
            <button class="btn danger" data-id="${item.id}">删除</button>
        `;
        div.querySelector('button').addEventListener('click', async () => {
            if (!confirm('确定删除这条导入记录及其所有人员工资数据吗？')) return;
            await fetch(`/api/import/${item.id}`, { method: 'DELETE' });
            loadHistory();
        });
        el.appendChild(div);
    });
}

// Init
updateStepClickable();
loadSavedConfig();

['salary', 'email'].forEach(kind => {
    ['header-rows', 'data-start', 'data-end', 'data-end-col'].forEach(field => {
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

applySavedTheme();