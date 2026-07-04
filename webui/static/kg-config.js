// ─── KG Config Panel ───
let kgConfigLoaded = false;
let selectedRelTypes = new Set();
let selectedNodeTypes = new Set();

function toggleKgConfig() {
    const panel = document.getElementById('kg-config');
    if (!panel) return;
    const hidden = panel.classList.contains('hidden');
    panel.classList.toggle('hidden');
    if (hidden && !kgConfigLoaded) loadKgConfig();
    if (hidden && typeof gsap !== 'undefined') {
        gsap.fromTo(panel, {autoAlpha:0,x:-20}, {autoAlpha:1,x:0,duration:0.3,ease:'power3.out'});
    }
}

async function loadKgConfig() {
    try {
        const r = await fetch('/api/kg/config');
        const d = await r.json();
        
        // 标准化节点类型白名单
        const nodeTypes = d.node_types || ['person', 'topic', 'event', 'emotion', 'entity', 'keyword', 'fact', 'location', 'time', 'memory', 'source', 'belief', 'concern', 'jargon', 'community'];
        selectedNodeTypes = new Set(nodeTypes);
        const nodeDiv = document.getElementById('cfg-node-types');
        if (nodeDiv) {
            nodeDiv.innerHTML = nodeTypes.map(t => {
                const color = TYPE_COLORS[t] || '#94a3b8';
                return `<button class="px-2 py-0.5 rounded text-[9px] border transition cfg-pill" style="border-color:${color}; color:${color}; background:${color}20" data-type="${t}" onclick="toggleCfgPill(this,'node')">${TYPE_LABELS[t]||t}</button>`;
            }).join('');
        }

        // 关系类型 pills
        const relTypes = d.relation_types || [];
        selectedRelTypes = new Set(relTypes); // 默认全选
        const relDiv = document.getElementById('cfg-rel-types');
        if (relDiv) {
            relDiv.innerHTML = relTypes.map(t =>
                `<button class="px-2 py-0.5 rounded text-[9px] border transition cfg-pill" data-type="${t}" onclick="toggleCfgPill(this,'rel')" style="background:rgba(139,92,246,0.25); border-color:#8b5cf6; color:#c4b5fd">${t}</button>`
            ).join('');
        }
        
        // 自愈还原最小强度和置信度热数值滑块默认值
        const savedWeight = d.defaults?.min_weight !== undefined ? d.defaults.min_weight : 0.5;
        const weightInput = document.getElementById('cfg-min-weight');
        if (weightInput) {
            weightInput.value = savedWeight;
            const weightVal = document.getElementById('cfg-weight-val');
            if (weightVal) weightVal.innerText = savedWeight;
        }

        const savedConf = d.defaults?.min_confidence !== undefined ? d.defaults.min_confidence : 0.0;
        const confInput = document.getElementById('cfg-min-confidence');
        if (confInput) {
            confInput.value = savedConf;
            const confVal = document.getElementById('cfg-conf-val');
            if (confVal) confVal.innerText = savedConf;
        }

        kgConfigLoaded = true;
    } catch(e) { 
        console.error('[WaveMemory] loadKgConfig failed:', e); 
    }
}

function toggleCfgPill(btn, kind) {
    const t = btn.dataset.type;
    const set = kind === 'rel' ? selectedRelTypes : selectedNodeTypes;
    if (set.has(t)) {
        // 取消选中（该类型将被隐藏）
        set.delete(t);
        btn.style.background = '';
        btn.style.borderColor = 'rgba(255,255,255,0.1)';
        btn.style.color = '#64748b';
        btn.style.opacity = '0.5';
    } else {
        // 恢复选中
        set.add(t);
        if (kind === 'rel') {
            btn.style.background = 'rgba(139,92,246,0.25)';
            btn.style.borderColor = '#8b5cf6';
            btn.style.color = '#c4b5fd';
        } else {
            const color = TYPE_COLORS[t] || '#94a3b8';
            btn.style.background = color + '20';
            btn.style.borderColor = color;
            btn.style.color = color;
        }
        btn.style.opacity = '1';
    }
}
