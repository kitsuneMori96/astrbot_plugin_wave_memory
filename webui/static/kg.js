// ═══════════════════════════════════════════════════════════
// Wave Memory 神经云图 v3D — NeuroGalaxy Three.js Engine
// 保留 2D 业务能力：查询、人物、寻路、配置筛选、展开、时间线、事实编辑
// ═══════════════════════════════════════════════════════════

const TYPE_COLORS = {
    person:'#f472b6', topic:'#60a5fa', event:'#34d399',
    emotion:'#fbbf24', entity:'#fb923c', keyword:'#94a3b8',
    fact:'#a78bfa', location:'#2dd4bf', time:'#e879f9',
    memory:'#6366f1', source:'#ffd700', belief:'#c084fc',
    concern:'#38bdf8', jargon:'#fb7185', community:'#22d3ee',
};
const TYPE_LABELS = {
    person:'人物', topic:'话题', event:'事件',
    emotion:'情绪', entity:'实体', keyword:'关键词',
    fact:'事实', location:'地点', time:'时间',
    memory:'记忆', source:'查询源', belief:'信念',
    concern:'关切', jargon:'黑话', community:'社区',
};

let scene = null;
let camera = null;
let webglRenderer = null;
let controls = null;
let composer = null;
let raycaster = null;
let mouse = null;
let galaxyContainer = null;
let graphGroup = null;
let edgeGroup = null;
let labelGroup = null;
let starField = null;
let animationId = null;
let pointerHandlers = null;
let graphUnavailableReason = '';

let currentView = 'galaxy';
let selectedNode = null;
let activeFilter = null;
let hoveredNode = null;
let hoveredNeighbors = new Set();
let selectedFact = null;
let selectedFactEntity = null;
let _kgFullEdges = null;

const graphState = {
    nodes: new Map(),
    edges: new Map(),
    adjacency: new Map(),
    labelIndex: new Map(),
};

const NODE_GEOMETRY = typeof THREE !== 'undefined' ? new THREE.SphereGeometry(1, 24, 16) : null;
const DEG2RAD = Math.PI / 180;

// ─── 基础工具 ───
function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeJs(value) {
    return String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n');
}

function colorForType(type) {
    return TYPE_COLORS[type] || TYPE_COLORS.keyword;
}

function showGraphUnavailable(message) {
    graphUnavailableReason = message || '当前浏览器无法初始化 WebGL 3D 画布。';
    const container = galaxyContainer || document.getElementById('galaxy-container');
    if (!container) return;
    container.innerHTML = `
        <div class="absolute inset-0 flex items-center justify-center p-8 text-center">
            <div class="glass glass-accent rounded-2xl px-6 py-5 max-w-md">
                <div class="text-glow-purple text-sm font-semibold mb-2">3D 神经云图暂不可用</div>
                <div class="text-slate-400 text-xs leading-relaxed">${escapeHtml(graphUnavailableReason)}</div>
                <div class="text-slate-600 text-[10px] mt-3">请使用支持 WebGL 的浏览器/显卡环境，或开启硬件加速后刷新。</div>
            </div>
        </div>`;
}

function isWebGLAvailable() {
    try {
        const canvas = document.createElement('canvas');
        return !!(window.WebGLRenderingContext && (canvas.getContext('webgl') || canvas.getContext('experimental-webgl')));
    } catch (_) {
        return false;
    }
}

function normalizeNodeId(n, fallback) {
    const raw = n?.id ?? n?.tag_id ?? n?.tagId ?? n?.memId ?? n?.name ?? n?.label ?? fallback;
    return String(raw);
}

function normalizeEdgeEndpoint(value) {
    if (value === undefined || value === null) return '';
    const raw = String(value);
    if (graphState.nodes.has(raw)) return raw;
    if (graphState.labelIndex.has(raw)) return graphState.labelIndex.get(raw);
    return raw;
}

function edgeKey(source, target, label='') {
    const a = String(source);
    const b = String(target);
    return `${a}::${b}::${label}`;
}

function hashString(str) {
    let h = 2166136261;
    for (let i = 0; i < String(str).length; i++) {
        h ^= String(str).charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return h >>> 0;
}

function getNeighbors(nodeId) {
    return Array.from(graphState.adjacency.get(String(nodeId)) || []);
}

function getNodeRecord(nodeId) {
    return graphState.nodes.get(String(nodeId));
}

function clearGraphState() {
    graphState.nodes.clear();
    graphState.edges.clear();
    graphState.adjacency.clear();
    graphState.labelIndex.clear();
}

function ensureAdjacency(nodeId) {
    if (!graphState.adjacency.has(nodeId)) graphState.adjacency.set(nodeId, new Set());
}

function addNodeRecord(rawNode, index=0, options={}) {
    const id = normalizeNodeId(rawNode, index);
    if (graphState.nodes.has(id)) return graphState.nodes.get(id);

    const type = rawNode.type || rawNode.tag_type || rawNode.nodeType || 'keyword';
    const degree = rawNode.value || rawNode.degree || rawNode.weight || 1;
    const isSeed = rawNode.isSource || rawNode.isSeed || type === 'source';
    const label = rawNode.name || rawNode.label || id;
    const color = isSeed ? TYPE_COLORS.source : colorForType(type);
    const radius = isSeed ? 1.9 : Math.max(0.55, Math.min(1.65, Math.log2((degree || 1) + 1) * 0.22 + 0.62));
    const position = rawNode._position || computeNodePosition(rawNode, index, options);

    const record = {
        id, label, type, degree, color, radius,
        raw: { ...rawNode, type, degree },
        position,
        object: null,
        labelObject: null,
        visible: true,
    };
    graphState.nodes.set(id, record);
    graphState.labelIndex.set(id, id);
    graphState.labelIndex.set(label, id);
    if (rawNode.name) graphState.labelIndex.set(rawNode.name, id);
    if (rawNode.label) graphState.labelIndex.set(rawNode.label, id);
    ensureAdjacency(id);
    return record;
}

function addEdgeRecord(rawEdge) {
    const label = rawEdge.label || rawEdge.relation_type || rawEdge.l || '';
    const source = normalizeEdgeEndpoint(rawEdge.source ?? rawEdge.from ?? rawEdge.s);
    const target = normalizeEdgeEndpoint(rawEdge.target ?? rawEdge.to ?? rawEdge.t);
    if (!source || !target || source === target) return null;
    if (!graphState.nodes.has(source) || !graphState.nodes.has(target)) return null;

    const key = edgeKey(source, target, label);
    const reverseKey = edgeKey(target, source, label);
    if (graphState.edges.has(key) || graphState.edges.has(reverseKey)) return graphState.edges.get(key) || graphState.edges.get(reverseKey);

    const weight = rawEdge.value || rawEdge.weight || rawEdge.w || rawEdge.count || 1;
    const record = {
        key, source, target, label,
        weight,
        isPath: !!rawEdge.isPath,
        layer: rawEdge.layer || 'facts',
        raw: { ...rawEdge, source, target, label, weight },
        object: null,
        visible: true,
    };
    graphState.edges.set(key, record);
    ensureAdjacency(source);
    ensureAdjacency(target);
    graphState.adjacency.get(source).add(target);
    graphState.adjacency.get(target).add(source);
    return record;
}

function computeNodePosition(node, index, options={}) {
    if (node.x !== undefined && node.y !== undefined && node.z !== undefined) {
        return new THREE.Vector3(Number(node.x), Number(node.y), Number(node.z));
    }
    const total = Math.max(1, options.total || 1);
    const layout = options.layout || 'galaxy';
    const idSeed = hashString(node.name || node.label || node.id || index);

    if (layout === 'path') {
        const step = 12;
        return new THREE.Vector3((index - (total - 1) / 2) * step, Math.sin(index * 0.9) * 2.2, 0);
    }

    if (layout === 'query') {
        if (node.type === 'source' || node.isSource) return new THREE.Vector3(0, 0, 0);
        const angle = (index / Math.max(1, total - 1)) * Math.PI * 2;
        const ring = 13 + (idSeed % 5);
        return new THREE.Vector3(Math.cos(angle) * ring, Math.sin(angle * 1.7) * 4, Math.sin(angle) * ring);
    }

    if (options.anchor) {
        const angle = ((idSeed % 360) * DEG2RAD);
        const lift = (((idSeed >> 8) % 100) / 100 - 0.5) * 12;
        const dist = 8 + ((idSeed >> 16) % 100) / 16;
        return options.anchor.clone().add(new THREE.Vector3(Math.cos(angle) * dist, lift, Math.sin(angle) * dist));
    }

    // Fermat / golden sphere — 确定性 3D 星团布局
    const golden = Math.PI * (3 - Math.sqrt(5));
    const y = 1 - (index / Math.max(1, total - 1)) * 2;
    const radiusAtY = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * index + ((idSeed % 97) / 97) * 0.4;
    const degree = node.degree || node.value || node.weight || 1;
    const scale = 20 + Math.min(18, Math.log2(degree + 1) * 4) + Math.min(16, total / 16);
    return new THREE.Vector3(Math.cos(theta) * radiusAtY * scale, y * scale * 0.72, Math.sin(theta) * radiusAtY * scale);
}

// ─── Three.js 初始化 ───
function initGraph() {
    if (typeof THREE === 'undefined') {
        console.error('[WaveMemory] THREE.js 未加载，无法初始化 3D 神经云图');
        return;
    }
    const container = document.getElementById('galaxy-container');
    if (!container) return;

    disposeGraph();
    galaxyContainer = container;
    galaxyContainer.innerHTML = '';
    graphUnavailableReason = '';

    if (!isWebGLAvailable()) {
        showGraphUnavailable('当前运行环境没有可用的 WebGL context。');
        return;
    }

    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x06080d, 0.018);
    camera = new THREE.PerspectiveCamera(58, window.innerWidth / window.innerHeight, 0.1, 2000);
    camera.position.set(0, 24, 62);

    try {
        webglRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    } catch (err) {
        console.warn('[WaveMemory] WebGLRenderer 初始化失败', err);
        scene = null;
        camera = null;
        webglRenderer = null;
        showGraphUnavailable(err?.message || 'WebGLRenderer 初始化失败。');
        return;
    }
    webglRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    webglRenderer.setSize(window.innerWidth, window.innerHeight);
    webglRenderer.setClearColor(0x06080d, 1);
    galaxyContainer.appendChild(webglRenderer.domElement);

    controls = new THREE.OrbitControls(camera, webglRenderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.38;
    controls.zoomSpeed = 0.6;
    controls.minDistance = 10;
    controls.maxDistance = 260;

    raycaster = new THREE.Raycaster();
    mouse = new THREE.Vector2();

    graphGroup = new THREE.Group();
    edgeGroup = new THREE.Group();
    labelGroup = new THREE.Group();
    scene.add(edgeGroup);
    scene.add(graphGroup);
    scene.add(labelGroup);

    buildNebulaField();
    setupLights();
    setupBloom();
    setupPointerEvents();
    window.addEventListener('resize', onWindowResize);
    animate();
}

function setupLights() {
    scene.add(new THREE.AmbientLight(0x8b5cf6, 0.38));
    const key = new THREE.PointLight(0x8b5cf6, 1.4, 260);
    key.position.set(35, 45, 30);
    scene.add(key);
    const rim = new THREE.PointLight(0x3b82f6, 1.1, 220);
    rim.position.set(-50, -20, -40);
    scene.add(rim);
}

function setupBloom() {
    composer = null;
    try {
        if (THREE.EffectComposer && THREE.RenderPass && THREE.UnrealBloomPass) {
            composer = new THREE.EffectComposer(webglRenderer);
            composer.addPass(new THREE.RenderPass(scene, camera));
            const bloom = new THREE.UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0.9, 0.55, 0.12);
            composer.addPass(bloom);
        }
    } catch (e) {
        console.warn('[WaveMemory] Bloom 初始化失败，降级为普通 WebGL 渲染', e);
        composer = null;
    }
}

function buildNebulaField() {
    const count = 900;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const palette = [new THREE.Color('#8b5cf6'), new THREE.Color('#3b82f6'), new THREE.Color('#f472b6'), new THREE.Color('#94a3b8')];
    for (let i = 0; i < count; i++) {
        const r = 120 + Math.random() * 260;
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
        positions[i * 3 + 1] = r * Math.cos(phi) * 0.72;
        positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
        const c = palette[i % palette.length];
        colors[i * 3] = c.r;
        colors[i * 3 + 1] = c.g;
        colors[i * 3 + 2] = c.b;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const mat = new THREE.PointsMaterial({ size: 0.8, vertexColors: true, transparent: true, opacity: 0.42, depthWrite: false });
    starField = new THREE.Points(geo, mat);
    scene.add(starField);
}

function setupPointerEvents() {
    const tooltip = document.getElementById('node-tooltip');
    pointerHandlers = {
        mousemove(event) {
            updateMouse(event);
            const hit = pickNode();
            if (hit !== hoveredNode) setHoveredNode(hit);
            if (tooltip) moveTooltip(event, tooltip);
        },
        mouseleave() {
            setHoveredNode(null);
        },
        click() {
            if (hoveredNode) selectNodeById(hoveredNode);
            else {
                selectedNode = null;
                selectedFact = null;
                selectedFactEntity = null;
                hideDetail();
                applyVisibility();
            }
        },
        dblclick() {
            if (!hoveredNode) return;
            selectedNode = hoveredNode;
            expandNode();
        },
    };
    Object.entries(pointerHandlers).forEach(([event, handler]) => galaxyContainer.addEventListener(event, handler));
}

function updateMouse(event) {
    const rect = galaxyContainer.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
}

function pickNode() {
    if (!raycaster || !camera) return null;
    raycaster.setFromCamera(mouse, camera);
    const objects = Array.from(graphState.nodes.values()).map(n => n.object).filter(Boolean);
    const hits = raycaster.intersectObjects(objects, false);
    return hits.length ? hits[0].object.userData.nodeId : null;
}

function setHoveredNode(nodeId) {
    hoveredNode = nodeId;
    hoveredNeighbors = nodeId ? new Set(getNeighbors(nodeId)) : new Set();
    if (galaxyContainer) galaxyContainer.style.cursor = nodeId ? 'pointer' : 'grab';
    const tooltip = document.getElementById('node-tooltip');
    if (tooltip) {
        if (nodeId) showNodeTooltip(nodeId, tooltip);
        else tooltip.classList.remove('visible');
    }
    applyVisibility();
}

window.addEventListener('beforeunload', disposeGraph);

function moveTooltip(event, tooltip) {
    tooltip.style.left = (event.clientX + 16) + 'px';
    tooltip.style.top = (event.clientY - 10) + 'px';
    const rect = tooltip.getBoundingClientRect();
    if (rect.right > window.innerWidth - 10) tooltip.style.left = (event.clientX - rect.width - 16) + 'px';
    if (rect.bottom > window.innerHeight - 10) tooltip.style.top = (event.clientY - rect.height - 10) + 'px';
}

function showNodeTooltip(nodeId, tooltip) {
    const rec = getNodeRecord(nodeId);
    if (!rec) return;
    const neighbors = getNeighbors(nodeId);
    tooltip.innerHTML = `
        <div class="tt-name">${escapeHtml(rec.label)}</div>
        <span class="tt-type" style="background:${rec.color}20; color:${rec.color}; border: 1px solid ${rec.color}40">${escapeHtml(TYPE_LABELS[rec.type] || rec.type || '未知')}</span>
        <div class="tt-meta">
            ${rec.raw.score ? `相似度: ${Number(rec.raw.score).toFixed(3)}<br>` : ''}
            ${rec.raw.community !== undefined ? `社区: #${escapeHtml(rec.raw.community)}<br>` : ''}
            连接数: ${neighbors.length}
        </div>
        ${neighbors.length ? `<div class="tt-neighbors">邻居: ${neighbors.slice(0, 5).map(n => escapeHtml(getNodeRecord(n)?.label || n)).join(', ')}${neighbors.length > 5 ? ` +${neighbors.length - 5}` : ''}</div>` : ''}
    `;
    tooltip.classList.add('visible');
}

function onWindowResize() {
    if (!camera || !webglRenderer) return;
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    webglRenderer.setSize(window.innerWidth, window.innerHeight);
    if (composer) composer.setSize(window.innerWidth, window.innerHeight);
}

function animate() {
    animationId = requestAnimationFrame(animate);
    const t = performance.now() * 0.001;
    if (starField) starField.rotation.y = t * 0.012;
    if (graphGroup) graphGroup.rotation.y += 0.00045;
    if (edgeGroup) edgeGroup.rotation.y = graphGroup.rotation.y;
    if (labelGroup) {
        labelGroup.rotation.y = graphGroup.rotation.y;
        labelGroup.children.forEach(sprite => sprite.lookAt(camera.position));
    }
    if (controls) controls.update();
    if (composer) composer.render();
    else if (webglRenderer) webglRenderer.render(scene, camera);
}

// ─── 渲染图谱 ───
function renderGraph(nodes, edges, options={}) {
    if (!graphGroup || !edgeGroup || !labelGroup) return;
    clearGraph3D();
    clearGraphState();
    const renderOptions = { ...options, total: nodes.length || 1 };

    nodes.forEach((n, i) => addNodeRecord(n, i, renderOptions));
    edges.forEach(e => addEdgeRecord(e));

    graphState.nodes.forEach((record) => createNodeObject(record));
    graphState.edges.forEach((record) => createEdgeObject(record));
    createImportantLabels();
    updateStats();
    applyVisibility();
    flyToGraph();
}

function disposeSceneObject(child) {
    if (!child) return;
    if (child.geometry && child.geometry !== NODE_GEOMETRY) child.geometry.dispose?.();
    const materials = Array.isArray(child.material) ? child.material : (child.material ? [child.material] : []);
    materials.forEach(mat => {
        if (mat.map) mat.map.dispose?.();
        mat.dispose?.();
    });
}

function clearGraph3D() {
    if (!graphGroup || !edgeGroup || !labelGroup) return;
    [graphGroup, edgeGroup, labelGroup].forEach(group => {
        while (group.children.length) {
            const child = group.children.pop();
            disposeSceneObject(child);
        }
    });
}

function createNodeObject(record) {
    const material = new THREE.MeshStandardMaterial({
        color: new THREE.Color(record.color),
        emissive: new THREE.Color(record.color),
        emissiveIntensity: record.raw.isSource ? 1.3 : 0.72,
        roughness: 0.35,
        metalness: 0.25,
        transparent: true,
        opacity: 0.95,
    });
    const mesh = new THREE.Mesh(NODE_GEOMETRY, material);
    mesh.position.copy(record.position);
    mesh.scale.setScalar(record.radius);
    mesh.userData.nodeId = record.id;
    mesh.userData.baseScale = record.radius;
    graphGroup.add(mesh);
    record.object = mesh;
}

function createEdgeObject(record) {
    const a = getNodeRecord(record.source);
    const b = getNodeRecord(record.target);
    if (!a || !b) return;
    const geo = new THREE.BufferGeometry().setFromPoints([a.position, b.position]);
    const color = record.isPath ? '#fbbf24' : (a.color || '#8b5cf6');
    const mat = new THREE.LineBasicMaterial({
        color: new THREE.Color(color),
        transparent: true,
        opacity: record.isPath ? 0.78 : Math.max(0.16, Math.min(0.46, Number(record.weight || 1) / 4)),
        blending: THREE.AdditiveBlending,
        depthWrite: false,
    });
    const line = new THREE.Line(geo, mat);
    line.userData.edgeKey = record.key;
    edgeGroup.add(line);
    record.object = line;
}

function createImportantLabels() {
    const records = Array.from(graphState.nodes.values())
        .sort((a, b) => (b.degree || 0) - (a.degree || 0))
        .slice(0, Math.min(80, graphState.nodes.size));
    records.forEach(record => {
        const sprite = createTextSprite(record.label, record.color, record.radius);
        sprite.position.copy(record.position).add(new THREE.Vector3(record.radius * 1.7, record.radius * 0.7, 0));
        sprite.userData.nodeId = record.id;
        labelGroup.add(sprite);
        record.labelObject = sprite;
    });
}

function createTextSprite(text, color, radius) {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    const fontSize = 34;
    const label = String(text || '').slice(0, 18);
    ctx.font = `600 ${fontSize}px system-ui, -apple-system, sans-serif`;
    const width = Math.ceil(ctx.measureText(label).width + 38);
    canvas.width = Math.max(128, width);
    canvas.height = 64;
    ctx.font = `600 ${fontSize}px system-ui, -apple-system, sans-serif`;
    ctx.fillStyle = 'rgba(6, 8, 13, 0.72)';
    ctx.strokeStyle = color + '88';
    roundRect(ctx, 4, 8, canvas.width - 8, 48, 18);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#f8fafc';
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;
    ctx.fillText(label, 20, 43);
    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(material);
    const scale = Math.max(4.0, radius * 3.2);
    sprite.scale.set((canvas.width / canvas.height) * scale, scale, 1);
    return sprite;
}

function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
}

function applyVisibility() {
    graphState.nodes.forEach(record => {
        const filterHidden = activeFilter && record.type !== activeFilter;
        const hoverDim = hoveredNode && record.id !== hoveredNode && !hoveredNeighbors.has(record.id);
        const selectedDim = selectedNode && record.id !== selectedNode && !getNeighbors(selectedNode).includes(record.id) && record.id !== hoveredNode;
        const visible = !filterHidden;
        record.visible = visible;
        if (record.object) {
            record.object.visible = visible;
            record.object.material.opacity = visible ? (hoverDim || selectedDim ? 0.22 : 0.95) : 0;
            const pulse = record.id === hoveredNode || record.id === selectedNode ? 1.45 : 1;
            record.object.scale.setScalar(record.radius * pulse);
        }
        if (record.labelObject) {
            record.labelObject.visible = visible && !(hoverDim || selectedDim);
        }
    });
    graphState.edges.forEach(record => {
        const a = getNodeRecord(record.source);
        const b = getNodeRecord(record.target);
        const filterHidden = activeFilter && a?.type !== activeFilter && b?.type !== activeFilter;
        const hoverHit = hoveredNode && (record.source === hoveredNode || record.target === hoveredNode);
        const selectedHit = selectedNode && (record.source === selectedNode || record.target === selectedNode);
        const visible = !filterHidden && a?.visible !== false && b?.visible !== false;
        record.visible = visible;
        if (record.object) {
            record.object.visible = visible;
            record.object.material.opacity = visible ? (hoveredNode || selectedNode ? (hoverHit || selectedHit || record.isPath ? 0.9 : 0.04) : (record.isPath ? 0.82 : Math.max(0.12, Math.min(0.42, Number(record.weight || 1) / 4)))) : 0;
        }
    });
}

function flyToGraph() {
    if (!camera || !controls) return;
    const count = Math.max(1, graphState.nodes.size);
    const distance = Math.max(34, Math.min(150, 38 + count * 0.32));
    if (typeof gsap !== 'undefined') {
        gsap.to(camera.position, { x: 0, y: Math.min(60, distance * 0.34), z: distance, duration: 0.9, ease: 'power2.out' });
        gsap.to(controls.target, { x: 0, y: 0, z: 0, duration: 0.9, ease: 'power2.out' });
    } else {
        camera.position.set(0, distance * 0.34, distance);
        controls.target.set(0, 0, 0);
    }
}

function flyToNode(nodeId) {
    const record = getNodeRecord(nodeId);
    if (!record || !camera || !controls) return;
    const target = record.position.clone();
    const camTarget = target.clone().add(new THREE.Vector3(0, Math.max(4, record.radius * 4), Math.max(14, record.radius * 12)));
    if (typeof gsap !== 'undefined') {
        gsap.to(controls.target, { x: target.x, y: target.y, z: target.z, duration: 0.75, ease: 'sine.inOut' });
        gsap.to(camera.position, { x: camTarget.x, y: camTarget.y, z: camTarget.z, duration: 0.75, ease: 'sine.inOut' });
    } else {
        controls.target.copy(target);
        camera.position.copy(camTarget);
    }
    createScreenRipple(nodeId);
}

function createScreenRipple(nodeId) {
    const record = getNodeRecord(nodeId);
    if (!record || !record.object || !galaxyContainer) return;
    const p = record.object.position.clone();
    graphGroup.localToWorld(p);
    p.project(camera);
    const rect = galaxyContainer.getBoundingClientRect();
    const x = (p.x * 0.5 + 0.5) * rect.width + rect.left;
    const y = (-p.y * 0.5 + 0.5) * rect.height + rect.top;
    const ripple = document.createElement('div');
    ripple.style.position = 'fixed';
    ripple.style.left = x + 'px';
    ripple.style.top = y + 'px';
    ripple.style.width = '10px';
    ripple.style.height = '10px';
    ripple.style.transform = 'translate(-50%, -50%)';
    ripple.style.borderRadius = '50%';
    ripple.style.border = `2px solid ${record.color}`;
    ripple.style.boxShadow = `0 0 18px ${record.color}`;
    ripple.style.pointerEvents = 'none';
    ripple.style.zIndex = '99';
    document.body.appendChild(ripple);
    if (typeof gsap !== 'undefined') {
        gsap.fromTo(ripple, { width: '10px', height: '10px', opacity: 1 }, { width: '130px', height: '130px', opacity: 0, duration: 1.0, ease: 'power2.out', onComplete: () => ripple.remove() });
    } else {
        setTimeout(() => ripple.remove(), 1000);
    }
}

function appendGraphData(newNodes, newEdges) {
    if (!graphGroup || !edgeGroup || !labelGroup) return;
    const anchor = selectedNode && getNodeRecord(selectedNode) ? getNodeRecord(selectedNode).position : new THREE.Vector3(0, 0, 0);
    const startIndex = graphState.nodes.size;
    newNodes.forEach((n, idx) => addNodeRecord(n, startIndex + idx, { total: startIndex + newNodes.length, anchor }));
    newEdges.forEach(e => addEdgeRecord(e));

    graphState.nodes.forEach(record => {
        if (!record.object) createNodeObject(record);
    });
    graphState.edges.forEach(record => {
        if (!record.object) createEdgeObject(record);
    });
    while (labelGroup.children.length) {
        const child = labelGroup.children.pop();
        disposeSceneObject(child);
    }
    graphState.nodes.forEach(record => { record.labelObject = null; });
    createImportantLabels();
    updateStats();
    applyVisibility();
}

// ─── Legend ───
function initLegend() {
    const legend = document.getElementById('legend');
    const types = ['person','topic','event','emotion','entity','keyword','fact','memory'];
    legend.innerHTML = types.map(t => `
        <button class="legend-pill flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-[10px] cursor-pointer border border-transparent"
                data-type="${t}" style="background:${TYPE_COLORS[t]}15; color:${TYPE_COLORS[t]}; --pill-glow:${TYPE_COLORS[t]}40">
            <span class="w-2.5 h-2.5 rounded-full" style="background: radial-gradient(circle at 30% 30%, ${TYPE_COLORS[t]}, ${TYPE_COLORS[t]}80)"></span>
            ${TYPE_LABELS[t]}
        </button>
    `).join('');
    legend.querySelectorAll('.legend-pill').forEach(btn => {
        btn.addEventListener('click', () => toggleFilter(btn.dataset.type));
    });
}

function toggleFilter(type) {
    activeFilter = activeFilter === type ? null : type;
    document.querySelectorAll('.legend-pill').forEach(btn => {
        const isActive = btn.dataset.type === activeFilter;
        btn.classList.toggle('active', isActive);
        btn.style.borderColor = isActive ? TYPE_COLORS[type] + '60' : 'transparent';
    });
    applyVisibility();
}

// ─── Load Galaxy ───
async function loadGalaxy() {
    showLoading('正在加载 3D 知识星海...');
    try {
        const layers = [];
        document.querySelectorAll('#cfg-layers input[type=checkbox]').forEach(cb => {
            if (cb.checked) layers.push(cb.dataset.layer);
        });
        const layerParam = layers.length ? layers.join(',') : 'facts';
        const res = await fetch(`/api/kg/full?layers=${encodeURIComponent(layerParam)}`);
        const data = await res.json();
        _kgFullEdges = data.edges || [];
        showLoading(`已加载 ${_kgFullEdges.length} 条关系（图层: ${(data.layers || []).join(', ')}），投射到 3D 星海...`);
        if (!kgConfigLoaded) loadKgConfig();
        applyKgConfig();
    } catch(e) {
        console.error('Load KG failed:', e);
        showLoading('知识星海加载失败');
        setTimeout(hideLoading, 1400);
        return;
    }
    hideLoading();
}

function applyKgConfig() {
    if (!_kgFullEdges) { loadGalaxy(); return; }
    const maxNodes = parseInt(document.getElementById('cfg-max-nodes')?.value || '150');
    const minWeight = parseFloat(document.getElementById('cfg-min-weight')?.value || '0');
    const days = parseInt(document.getElementById('cfg-days')?.value || '0');
    const cutoff = days > 0 ? (Date.now()/1000 - days * 86400) : 0;

    let filtered = [..._kgFullEdges];
    if (minWeight > 0) filtered = filtered.filter(e => e.layer !== 'facts' || e.w >= minWeight);
    if (cutoff > 0) filtered = filtered.filter(e => e.layer !== 'facts' || e.ts >= cutoff);
    if (typeof selectedRelTypes !== 'undefined' && selectedRelTypes.size > 0) {
        filtered = filtered.filter(e => e.layer !== 'facts' || selectedRelTypes.has(e.l));
    }
    if (typeof selectedNodeTypes !== 'undefined' && selectedNodeTypes.size > 0) {
        filtered = filtered.filter(e => e.layer !== 'facts' || selectedNodeTypes.has(e.st) || selectedNodeTypes.has(e.tt));
    }

    filtered.sort((a, b) => (b.w || 0) - (a.w || 0));
    const maxEdges = maxNodes * 2;
    filtered = filtered.slice(0, maxEdges);

    const nodeDeg = {};
    const nodeType = {};
    for (const e of filtered) {
        nodeDeg[e.s] = (nodeDeg[e.s]||0) + 1;
        nodeDeg[e.t] = (nodeDeg[e.t]||0) + 1;
        nodeType[e.s] = nodeType[e.s] || e.st;
        nodeType[e.t] = nodeType[e.t] || e.tt;
    }

    let sortedNodes = Object.entries(nodeDeg).sort((a,b) => b[1]-a[1]);
    if (sortedNodes.length > maxNodes) sortedNodes = sortedNodes.slice(0, maxNodes);
    const topSet = new Set(sortedNodes.map(x => x[0]));

    const nodes = sortedNodes.map(([name, deg]) => ({ id: name, name, type: nodeType[name] || 'entity', degree: deg }));
    const edges = filtered
        .filter(e => topSet.has(e.s) && topSet.has(e.t))
        .map(e => ({ source: e.s, target: e.t, label: e.l, weight: e.w, layer: e.layer }));

    renderGraph(nodes, edges, { layout: 'galaxy' });
    const status = document.getElementById('cfg-status');
    if (status) status.textContent = `显示 ${nodes.length} 实体 / ${edges.length} 关系（总 ${_kgFullEdges.length} 条）`;
}

// ─── Query ───
async function doQuery() {
    const q = document.getElementById('search-input').value.trim();
    if (!q) return;
    showLoading('正在语义检索...');
    try {
        const res = await fetch('/api/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: q, top_k: 12, enable_spike: true, enable_pyramid: false, enable_epa: false, enable_geodesic: false }),
        });
        const data = await res.json();
        if (data.results && data.results.length) {
            const nodes = [{ id: 'query-source', name: q, type: 'source', degree: data.results.length, isSource: true }];
            const edges = [];
            data.results.forEach((m, i) => {
                const id = `mem-${m.id || i}`;
                nodes.push({ id, name: `${m.sender_name || '未知'}: ${(m.content || '').slice(0, 18)}`, type: 'memory', degree: Math.max(1, Math.round((m.score || 0.2) * 10)), content: m.content, sender: m.sender_name, ts: m.timestamp, score: m.score });
                edges.push({ source: 'query-source', target: id, label: '联想', weight: Math.max(0.3, m.score || 0.3) });
            });
            renderGraph(nodes, edges, { layout: 'query' });
            showQueryDetail(q, data);
        } else {
            showLoading(`「${q}」无相关记忆`);
            setTimeout(hideLoading, 1500);
            return;
        }
    } catch(e) { console.error('Query failed:', e); }
    hideLoading();
}

function showQueryDetail(q, data) {
    const panel = document.getElementById('detail-panel');
    document.getElementById('detail-title').textContent = `「${q}」语义检索`;
    document.getElementById('detail-meta').innerHTML = `<span class="text-purple-300">${data.results.length} 条相关记忆</span> · ${data.timing?.total_ms || '?'}ms`;
    document.getElementById('detail-neighbor-list').innerHTML = '';
    const memList = document.getElementById('detail-memory-list');
    memList.innerHTML = data.results.map(m => {
        const time = m.timestamp ? new Date(m.timestamp * 1000).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
        const score = m.score ? `<span class="text-purple-400/70 text-[9px]">${(m.score*100).toFixed(0)}%</span>` : '';
        return `<div class="p-2.5 rounded-lg bg-white/[.03] border border-white/5">
            <div class="flex items-center gap-2 mb-1">
                <span class="text-purple-300 text-[10px] font-medium">${escapeHtml(m.sender_name || '未知')}</span>
                ${score}
                <span class="text-slate-600 text-[9px] ml-auto">${escapeHtml(time)}</span>
            </div>
            <p class="text-slate-300 text-[11px] leading-relaxed">${escapeHtml((m.content||'').slice(0,180))}${(m.content||'').length>180?'...':''}</p>
        </div>`;
    }).join('');
    document.getElementById('btn-expand').style.display = 'none';
    panel.classList.remove('hidden');
    if (typeof gsap !== 'undefined') gsap.fromTo(panel, { autoAlpha: 0, x: 30 }, { autoAlpha: 1, x: 0, duration: 0.4, ease: 'power3.out' });
}

// ─── Person View ───
async function loadPersonList() {
    const list = document.getElementById('person-list');
    list.innerHTML = '<p class="text-slate-600 text-xs">加载中...</p>';
    try {
        const res = await fetch('/api/explore/persons?limit=50');
        const persons = await res.json();
        if (!Array.isArray(persons) || persons.length === 0) {
            list.innerHTML = '<p class="text-slate-600 text-xs">暂无人物</p>';
            return;
        }
        list.innerHTML = persons.map(p => `
            <div class="person-item" onclick="loadPersonGraph('${escapeJs(p.id)}', '${escapeJs(p.name || '')}')">
                <div class="text-slate-200 text-xs font-medium">${escapeHtml(p.name || p.id)}</div>
                <div class="text-slate-500 text-[10px] mt-0.5">${escapeHtml(p.count || 0)} 条记忆</div>
            </div>
        `).join('');
        if (typeof gsap !== 'undefined') {
            gsap.fromTo('#person-list .person-item', { x: -16, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.3, stagger: 0.03, ease: 'power2.out' });
        }
    } catch(e) {
        list.innerHTML = '<p class="text-red-400 text-xs">加载失败</p>';
    }
}

async function loadPersonGraph(qqId, name) {
    showLoading(`加载 ${name || qqId} 的 3D 关系网...`);
    try {
        const res = await fetch(`/api/explore/person/${encodeURIComponent(qqId)}?max_memories=80`);
        const data = await res.json();
        if (data.nodes && data.nodes.length) {
            renderGraph(data.nodes, data.edges || [], { layout: 'galaxy' });
            const selected = data.nodes.find(n => String(n.id) === `p${qqId}` || String(n.qq_id || '') === String(qqId)) || data.nodes.find(n => String(n.id || n.memId || n.tagId) === String(qqId)) || data.nodes.find(n => n.type === 'memory') || data.nodes[0];
            if (selected) {
                selectedNode = normalizeNodeId(selected, selected.id || selected.memId || selected.tagId || qqId);
                await showDetail(selectedNode);
                flyToNode(selectedNode);
            }
        } else {
            showLoading(`${name || qqId} 暂无记忆网络`);
            setTimeout(hideLoading, 1200);
            return;
        }
    } catch(e) { console.error(e); }
    hideLoading();
}

function focusNode(nodeId) {
    if (getNodeRecord(nodeId)) {
        selectedNode = String(nodeId);
        applyVisibility();
        return flyToNode(nodeId);
    }
    const byLabel = graphState.labelIndex.get(String(nodeId));
    if (byLabel) {
        selectedNode = byLabel;
        applyVisibility();
        return flyToNode(byLabel);
    }
    return null;
}

function focusPerson(qqId, name) {
    return loadPersonGraph(qqId, name);
}

function loadPerson(qqId, name) {
    return loadPersonGraph(qqId, name);
}

window.focusNode = focusNode;
window.focusPerson = focusPerson;
window.loadPerson = loadPerson;
window.loadPersonGraph = loadPersonGraph;

// ─── Path Finding ───
async function doPathFind() {
    const from = document.getElementById('path-from').value.trim();
    const to = document.getElementById('path-to').value.trim();
    if (!from || !to) return;
    showLoading('寻路中...');
    try {
        const res = await fetch('/api/kg/path', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ from, to, max_depth: 6 }),
        });
        const data = await res.json();
        if (data.nodes && data.nodes.length) {
            const pathEdges = (data.edges || []).map(e => ({ ...e, isPath: true, weight: e.weight || 2 }));
            renderGraph(data.nodes, pathEdges, { layout: 'path' });
            showPathDetail(from, to, data);
        } else {
            showLoading(`${from} → ${to} 之间无连通路径`);
            setTimeout(hideLoading, 1500);
            return;
        }
    } catch(e) { console.error(e); }
    hideLoading();
}

function showPathDetail(from, to, data) {
    const panel = document.getElementById('detail-panel');
    document.getElementById('detail-title').textContent = `${from} → ${to} 路径`;
    document.getElementById('detail-meta').innerHTML = `<span class="text-purple-300">${data.path.length} 跳</span>`;
    document.getElementById('detail-neighbor-list').innerHTML = '';
    const memList = document.getElementById('detail-memory-list');
    memList.innerHTML = '<p class="text-slate-500 text-[9px] uppercase tracking-wider mb-1.5">关系链</p>' +
        (data.edges || []).map(e => `<div class="px-2 py-1.5 rounded bg-white/[.02] text-[10px] text-slate-400 mb-1"><span class="text-purple-300">${escapeHtml(e.source)}</span> <span class="text-amber-400/70">→${escapeHtml(e.label)}→</span> <span class="text-blue-300">${escapeHtml(e.target)}</span></div>`).join('');
    document.getElementById('btn-expand').style.display = 'none';
    panel.classList.remove('hidden');
    if (typeof gsap !== 'undefined') gsap.fromTo(panel, {autoAlpha:0,x:30}, {autoAlpha:1,x:0,duration:0.4,ease:'power3.out'});
}

// ─── Expand Node ───
async function expandNode() {
    if (!selectedNode) return;
    const rec = getNodeRecord(selectedNode);
    const entityName = rec?.label || selectedNode;
    if (!entityName) return;
    showLoading('展开邻居...');
    try {
        const res = await fetch(`/api/kg/entity/${encodeURIComponent(entityName)}`);
        const d = await res.json();
        const { nodes, edges } = deriveExpansionFromEntity(entityName, d);
        if (nodes.length || edges.length) {
            appendGraphData(nodes, edges);
        } else {
            showLoading('该节点无可展开关系');
            setTimeout(hideLoading, 1200);
            return;
        }
    } catch(e) { console.error(e); }
    hideLoading();
}

function deriveExpansionFromEntity(entityName, d) {
    const nodes = [];
    const edges = [];
    const seen = new Set();
    function addNeighbor(name, type, label, weight) {
        if (!name || name === entityName || seen.has(name)) return;
        seen.add(name);
        nodes.push({ id: name, name, label: name, type: type || 'entity', degree: 1 });
        edges.push({ source: entityName, target: name, label: label || 'relates', weight: weight || 0.5, layer: 'facts' });
    }
    (d.facts || []).forEach(f => {
        if (f.subject === entityName) addNeighbor(f.object, 'entity', f.predicate, f.confidence);
        else if (f.object === entityName) addNeighbor(f.subject, 'entity', f.predicate, f.confidence);
        else {
            addNeighbor(f.subject, 'entity', f.predicate, f.confidence);
            addNeighbor(f.object, 'entity', f.predicate, f.confidence);
        }
    });
    (d.relations || []).forEach(r => {
        if (r.source === entityName) addNeighbor(r.target, 'topic', r.type, r.weight);
        else if (r.target === entityName) addNeighbor(r.source, 'topic', r.type, r.weight);
        else {
            addNeighbor(r.source, 'topic', r.type, r.weight);
            addNeighbor(r.target, 'topic', r.type, r.weight);
        }
    });
    return { nodes, edges };
}

// ─── Timeline View ───
async function loadTimeline() {
    if (!selectedNode) return;
    const rec = getNodeRecord(selectedNode);
    const entityName = rec?.label || '';
    if (!entityName) return;
    const memList = document.getElementById('detail-memory-list');
    memList.innerHTML = '<p class="text-slate-600 text-[10px]">加载时间线...</p>';
    try {
        const r = await fetch(`/api/kg/entity/${encodeURIComponent(entityName)}/timeline?limit=25`);
        const d = await r.json();
        if (!d.events || !d.events.length) {
            memList.innerHTML = '<p class="text-slate-600 text-[10px]">暂无时间线数据</p>';
            return;
        }
        document.getElementById('detail-title').textContent = `📅 ${entityName} 时间线`;
        document.getElementById('detail-meta').innerHTML = `<span class="text-blue-300">${d.events.length} 个事件</span>`;
        let html = '<div class="relative pl-4 border-l-2 border-purple-500/20 space-y-3">';
        for (const ev of d.events) {
            const time = ev.ts ? new Date(ev.ts * 1000).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '?';
            const dotColor = ev.type === 'fact' ? '#a78bfa' : '#60a5fa';
            if (ev.type === 'fact') {
                html += `<div class="relative"><div class="absolute -left-[21px] top-1.5 w-2.5 h-2.5 rounded-full" style="background:${dotColor}"></div><div class="text-[9px] text-slate-600 mb-0.5">${escapeHtml(time)}</div><div class="px-2 py-1.5 rounded bg-purple-500/[.06] text-[10px] text-slate-300"><span class="text-purple-300">${escapeHtml(ev.subject||'')}</span> <span class="text-slate-600">→${escapeHtml(ev.predicate||'')}→</span> <span class="text-blue-300">${escapeHtml((ev.object||'').slice(0,50))}</span></div></div>`;
            } else {
                html += `<div class="relative"><div class="absolute -left-[21px] top-1.5 w-2.5 h-2.5 rounded-full" style="background:${dotColor}"></div><div class="text-[9px] text-slate-600 mb-0.5">${escapeHtml(time)}</div><div class="px-2 py-1.5 rounded bg-blue-500/[.06] text-[10px]"><span class="text-blue-300 font-medium">${escapeHtml(ev.sender||'')}</span><span class="text-slate-400 ml-1">${escapeHtml((ev.content||'').slice(0,80))}</span></div></div>`;
            }
        }
        html += '</div>';
        memList.innerHTML = html;
    } catch(e) {
        memList.innerHTML = '<p class="text-red-400/60 text-[10px]">时间线加载失败</p>';
    }
}

// ─── Detail Panel ───
async function selectNodeById(nodeId) {
    selectedNode = nodeId;
    selectedFact = null;
    selectedFactEntity = null;
    await showDetail(nodeId);
    flyToNode(nodeId);
    applyVisibility();
}

async function showDetail(nodeId) {
    const rec = getNodeRecord(nodeId);
    if (!rec) return;
    const panel = document.getElementById('detail-panel');
    document.getElementById('detail-title').textContent = rec.label || nodeId;
    let meta = `<span style="color:${rec.color}">${TYPE_LABELS[rec.type] || rec.type}</span>`;
    if (rec.degree) meta += ` · 度数 ${rec.degree}`;
    if (rec.raw.community !== undefined) meta += ` · 社区 #${rec.raw.community}`;
    document.getElementById('detail-meta').innerHTML = meta;
    document.getElementById('btn-expand').style.display = rec.raw.isSource ? 'none' : '';

    const neighbors = getNeighbors(nodeId);
    const neighborList = document.getElementById('detail-neighbor-list');
    neighborList.innerHTML = neighbors.slice(0, 12).map(n => {
        const nr = getNodeRecord(n);
        const c = nr?.color || '#94a3b8';
        return `<span class="inline-block px-2 py-0.5 rounded text-[10px] cursor-pointer hover:opacity-80 transition" style="background:${c}15; color:${c}; border: 1px solid ${c}25" onclick="selectNodeById('${escapeJs(n)}')">${escapeHtml(nr?.label || n)}</span>`;
    }).join('') + (neighbors.length > 12 ? `<span class="text-slate-600 text-[10px] ml-1">+${neighbors.length - 12}</span>` : '');

    const memList = document.getElementById('detail-memory-list');
    const entityName = rec.label || '';
    if (entityName) {
        selectedFactEntity = nodeId;
        memList.innerHTML = '<p class="text-slate-600 text-[10px]">加载知识...</p>';
        try {
            const r = await fetch(`/api/kg/entity/${encodeURIComponent(entityName)}?limit=12`);
            const d = await r.json();
            memList.innerHTML = buildEntityKnowledgeHtml(d);
        } catch(e) {
            memList.innerHTML = '<p class="text-red-400/60 text-[10px]">加载失败</p>';
        }
    } else {
        memList.innerHTML = '<p class="text-slate-600 text-[10px]">-</p>';
    }

    panel.classList.remove('hidden');
    if (typeof gsap !== 'undefined') gsap.fromTo(panel, { autoAlpha: 0, x: 30 }, { autoAlpha: 1, x: 0, duration: 0.4, ease: 'power3.out' });
}

function buildEntityKnowledgeHtml(d) {
    let html = '';
    if (d.person) {
        const p = d.person;
        const affColor = p.affection > 50 ? '#34d399' : p.affection > 0 ? '#fbbf24' : '#f87171';
        html += `<div class="mb-3 p-3 rounded-xl border border-purple-500/20 bg-purple-500/[.04]"><div class="flex items-center gap-2 mb-2"><div class="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold" style="background:${affColor}20; color:${affColor}; border:2px solid ${affColor}">${escapeHtml((p.name||'?')[0])}</div><div><div class="text-white text-xs font-semibold">${escapeHtml(p.name)}</div><div class="text-slate-500 text-[9px]">QQ ${escapeHtml(p.qq_id)} · ${escapeHtml(p.msg_count)} 条消息</div></div><div class="ml-auto text-right"><div class="text-[10px] font-mono" style="color:${affColor}">好感 ${escapeHtml(p.affection)}</div></div></div>${p.aliases?.length ? `<div class="text-[9px] text-slate-500 mb-1.5">别名: ${p.aliases.map(escapeHtml).join(' / ')}</div>` : ''}${p.personality_tags?.length ? `<div class="flex flex-wrap gap-1">${p.personality_tags.slice(0,8).map(t => `<span class="px-1.5 py-0.5 rounded text-[9px] bg-purple-500/10 text-purple-300 border border-purple-500/20">${escapeHtml(t)}</span>`).join('')}</div>` : ''}</div>`;
    }
    if (d.facts && d.facts.length) {
        html += '<p class="text-slate-500 text-[9px] uppercase tracking-wider mb-1.5">事实 (点击卡片选中后可进行斩断或修正)</p>';
        html += d.facts.slice(0, 6).map(f => `<div class="fact-item px-2 py-1.5 rounded bg-white/[.02] text-[10px] text-slate-400 mb-1 border border-transparent hover:border-purple-500/30 cursor-pointer transition" data-id="${escapeHtml(f.id)}" data-sub="${escapeHtml(f.subject)}" data-pred="${escapeHtml(f.predicate)}" data-obj="${escapeHtml(f.object)}" data-conf="${escapeHtml(f.confidence)}" onclick="selectFact(this)"><span class="text-purple-300">${escapeHtml(f.subject)}</span> <span class="text-slate-600">→${escapeHtml(f.predicate)}→</span> <span class="text-blue-300">${escapeHtml(f.object)}</span>${f.confidence ? `<span class="text-[9px] text-slate-600 ml-1 font-mono">(${Math.round(f.confidence*100)}%)</span>` : ''}</div>`).join('');
    }
    if (d.relations && d.relations.length) {
        html += '<p class="text-slate-500 text-[9px] uppercase tracking-wider mb-1.5 mt-2">关系</p>';
        html += d.relations.slice(0, 6).map(r => `<div class="px-2 py-1.5 rounded bg-white/[.02] text-[10px] text-slate-400 mb-1"><span class="text-purple-300">${escapeHtml(r.source)}</span> <span class="text-amber-400/70">${escapeHtml(r.type)}</span> <span class="text-blue-300">${escapeHtml(r.target)}</span></div>`).join('');
    }
    if (d.memories && d.memories.length) {
        html += '<p class="text-slate-500 text-[9px] uppercase tracking-wider mb-1.5 mt-2">记忆</p>';
        html += d.memories.slice(0, 8).map(m => {
            const time = m.ts ? new Date(m.ts * 1000).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
            return `<div class="p-2.5 rounded-lg bg-white/[.03] border border-white/5 mb-1.5"><div class="flex items-center gap-2 mb-1"><span class="text-purple-300 text-[10px] font-medium">${escapeHtml(m.sender||'未知')}</span><span class="text-slate-600 text-[9px] ml-auto">${escapeHtml(time)}</span></div><p class="text-slate-300 text-[11px] leading-relaxed">${escapeHtml((m.content||'').slice(0,120))}${(m.content||'').length>120?'...':''}</p></div>`;
        }).join('');
    }
    return html || '<p class="text-slate-600 text-[10px]">暂无关联知识</p>';
}

function hideDetail() {
    const panel = document.getElementById('detail-panel');
    if (panel.classList.contains('hidden')) return;
    if (typeof gsap !== 'undefined') {
        gsap.to(panel, { autoAlpha: 0, x: 30, duration: 0.25, ease: 'power2.in', onComplete: () => panel.classList.add('hidden') });
    } else {
        panel.classList.add('hidden');
    }
}

// ─── 事实选择、斩断与弹窗编辑 ───
function selectFact(el) {
    document.querySelectorAll('.fact-item').forEach(item => {
        item.style.borderColor = 'transparent';
        item.style.boxShadow = 'none';
        item.style.background = 'rgba(255, 255, 255, 0.02)';
    });
    el.style.borderColor = 'rgba(139, 92, 246, 0.7)';
    el.style.boxShadow = '0 0 10px rgba(139, 92, 246, 0.35)';
    el.style.background = 'rgba(139, 92, 246, 0.06)';
    selectedFact = { id: el.dataset.id, subject: el.dataset.sub, predicate: el.dataset.pred, object: el.dataset.obj, confidence: el.dataset.conf };
}

async function severFactRelation() {
    if (!selectedFact) {
        alert('请先在上方的事实列表中，点击选择要斩断的那条事实。');
        return;
    }
    if (!confirm(`确认要斩断并彻底物理删除这一事实关联吗？\n【${selectedFact.subject} → ${selectedFact.predicate} → ${selectedFact.object}】\n此操作不可逆！`)) return;
    const btn = document.getElementById('btn-sever-fact');
    const oldText = btn.textContent;
    btn.textContent = '斩断中...';
    btn.disabled = true;
    try {
        const r = await fetch(`/api/kg/facts/${selectedFact.id}`, { method: 'DELETE' });
        const d = await r.json();
        if (d.ok) {
            _kgFullEdges = null;
            selectedFact = null;
            alert('✓ 事实已成功物理斩断！该认知已从灵魂中抹去。');
            if (selectedNode) await showDetail(selectedNode);
            if (currentView === 'galaxy') await loadGalaxy();
        } else alert('✗ 斩断失败: ' + (d.error || '未知错误'));
    } catch(e) {
        alert('✗ 网络错误，斩断失败');
    } finally {
        btn.textContent = oldText;
        btn.disabled = false;
    }
}

function editEntity() {
    const dialog = document.getElementById('fact-edit-dialog');
    const inputSub = document.getElementById('edit-fact-subject');
    const inputPred = document.getElementById('edit-fact-predicate');
    const inputObj = document.getElementById('edit-fact-object');
    const inputConf = document.getElementById('edit-fact-confidence');
    if (selectedFact) {
        inputSub.value = selectedFact.subject || '';
        inputPred.value = selectedFact.predicate || '';
        inputObj.value = selectedFact.object || '';
        inputConf.value = selectedFact.confidence || 0.8;
    } else {
        const rec = selectedNode ? getNodeRecord(selectedNode) : null;
        inputSub.value = rec?.label || '';
        inputPred.value = '';
        inputObj.value = '';
        inputConf.value = 0.8;
    }
    dialog.classList.remove('hidden');
    if (typeof gsap !== 'undefined') gsap.fromTo(dialog.querySelector('.glass'), { scale: 0.9, opacity: 0 }, { scale: 1, opacity: 1, duration: 0.35, ease: 'back.out(1.5)' });
}

function closeFactEdit() {
    const dialog = document.getElementById('fact-edit-dialog');
    if (typeof gsap !== 'undefined') gsap.to(dialog.querySelector('.glass'), { scale: 0.9, opacity: 0, duration: 0.2, ease: 'power2.in', onComplete: () => dialog.classList.add('hidden') });
    else dialog.classList.add('hidden');
}

async function saveFactEdit() {
    const subj = document.getElementById('edit-fact-subject').value.trim();
    const pred = document.getElementById('edit-fact-predicate').value.trim();
    const obj = document.getElementById('edit-fact-object').value.trim();
    const conf = parseFloat(document.getElementById('edit-fact-confidence').value) || 0.8;
    if (!subj || !pred || !obj) {
        alert('请填写完整的三元组内容');
        return;
    }
    try {
        let r;
        if (selectedFact) {
            r = await fetch(`/api/kg/facts/${selectedFact.id}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ subject: subj, predicate: pred, object: obj, confidence: conf }) });
        } else {
            r = await fetch('/api/kg/add-fact', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ subject: subj, predicate: pred, object: obj, confidence: conf }) });
        }
        const d = await r.json();
        if (d.ok) {
            _kgFullEdges = null;
            closeFactEdit();
            selectedFact = null;
            if (selectedNode) await showDetail(selectedNode);
            if (currentView === 'galaxy') await loadGalaxy();
        } else alert('保存失败: ' + (d.error || '未知错误'));
    } catch(e) {
        alert('网络错误，保存失败');
    }
}

// ─── Utils ───
function updateStats() {
    const badge = document.getElementById('stats-badge');
    if (!badge) return;
    badge.innerHTML = `<span class="text-purple-300 font-semibold">${graphState.nodes.size}</span> 节点 · <span class="text-blue-300 font-semibold">${graphState.edges.size}</span> 连线`;
}
function showLoading(text) {
    const el = document.getElementById('loading');
    const textEl = document.getElementById('loading-text');
    if (textEl) textEl.textContent = text;
    if (!el) return;
    el.classList.remove('hidden'); el.classList.add('flex');
    if (typeof gsap !== 'undefined') gsap.fromTo(el, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.25 });
}
function hideLoading() {
    const el = document.getElementById('loading');
    if (!el) return;
    if (typeof gsap !== 'undefined') gsap.to(el, { autoAlpha: 0, duration: 0.3, onComplete: () => { el.classList.add('hidden'); el.classList.remove('flex'); } });
    else { el.classList.add('hidden'); el.classList.remove('flex'); }
}

// ─── View Switching ───
function switchView(view) {
    currentView = view;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    document.getElementById('search-box').style.display = view === 'query' ? 'flex' : 'none';
    document.getElementById('path-input').style.display = view === 'path' ? 'flex' : 'none';
    document.getElementById('person-panel').classList.toggle('hidden', view !== 'person');
    if (typeof gsap !== 'undefined') {
        if (view === 'query') gsap.fromTo('#search-box', { y: -16, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.4, ease: 'power3.out' });
        if (view === 'path') gsap.fromTo('#path-input', { y: -16, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.4, ease: 'power3.out' });
        if (view === 'person') gsap.fromTo('#person-panel', { x: -40, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.45, ease: 'power3.out' });
    }
    hideDetail();
    selectedNode = null;
    selectedFact = null;
    selectedFactEntity = null;
    activeFilter = null;
    document.querySelectorAll('.legend-pill').forEach(b => { b.classList.remove('active'); b.style.borderColor = 'transparent'; });
    if (view === 'galaxy') loadGalaxy();
    else if (view === 'person') { loadPersonList(); renderGraph([], [], { layout: 'galaxy' }); }
    else if (view === 'query') { renderGraph([], [], { layout: 'query' }); }
    else if (view === 'path') { renderGraph([], [], { layout: 'path' }); }
}

function disposeGraph() {
    if (animationId) {
        cancelAnimationFrame(animationId);
        animationId = null;
    }
    window.removeEventListener('resize', onWindowResize);
    if (galaxyContainer) {
        if (pointerHandlers) {
            Object.entries(pointerHandlers).forEach(([event, handler]) => galaxyContainer.removeEventListener(event, handler));
        }
        pointerHandlers = null;
        galaxyContainer.replaceChildren();
        galaxyContainer.style.cursor = 'grab';
    }
    if (starField) disposeSceneObject(starField);
    if (graphGroup || edgeGroup || labelGroup) clearGraph3D();
    if (controls) {
        controls.dispose?.();
        controls = null;
    }
    if (composer) {
        composer.passes?.forEach?.(pass => pass.dispose?.());
        composer = null;
    }
    if (webglRenderer) {
        webglRenderer.dispose?.();
        webglRenderer.forceContextLoss?.();
        webglRenderer = null;
    }
    scene = null;
    camera = null;
    raycaster = null;
    mouse = null;
    galaxyContainer = null;
    graphGroup = null;
    edgeGroup = null;
    labelGroup = null;
    starField = null;
    hoveredNode = null;
    hoveredNeighbors.clear();
    clearGraphState();
}

// ─── Init ───
document.querySelectorAll('.tab-btn').forEach(btn => btn.addEventListener('click', () => switchView(btn.dataset.view)));
document.getElementById('search-input')?.addEventListener('keydown', e => { if (e.key === 'Enter') doQuery(); });
document.getElementById('path-from')?.addEventListener('keydown', e => { if (e.key === 'Enter') doPathFind(); });
document.getElementById('path-to')?.addEventListener('keydown', e => { if (e.key === 'Enter') doPathFind(); });
