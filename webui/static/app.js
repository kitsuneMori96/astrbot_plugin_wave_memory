function app() {
    return {
        // State
        dark: true,
        activeTab: 'memories',
        tabs: [
            { id: 'memories', label: '记忆浏览', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>' },
            { id: 'query', label: '查询测试', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>' },
            { id: 'graph', label: 'Tag 图谱', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>' },
            { id: 'import', label: '数据导入', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>' },
        ],

        // Jargon / Holyman State
        jargonSubTab: 'local',
        jargonItems: [],
        jargonFilter: '',
        jargonGroup: '',
        jargonTotal: 0,
        jargonPending: 0,
        jargonEdit: null,
        jargonEditForm: { word: '', meaning: '' },
        jargonCreate: false,
        jargonCreateForm: { word: '', meaning: '', group_id: '' },

        selectedJargonIds: [],
        jargonAllMatching: false,
        jargonPage: 1,
        jargonPageSize: 15,
        jargonSearch: '',
        jargonHasMore: false,
        jargonTotalPages: 1,

        holymanItems: [],
        holymanSearch: '',
        holymanUseProxy: true,
        holymanSyncing: false,
        holymanLocalVersion: 'Unknown',
        holymanRemoteVersion: 'Unknown',

        // Stats
        stats: {},

        // Memories
        memories: [],
        memTotal: 0,
        memPage: 1,
        memPages: 0,
        memFilter: { group_id: '', sender: '' },
        memDetail: null,

        // Query
        queryText: '',
        queryParams: { top_k: 5, group_id: '', enable_spike: true, enable_pyramid: true, enable_epa: false, enable_geodesic: false },
        queryResults: [],
        queryTiming: null,
        queryDebug: null,
        queryLoading: false,

        // Graph
        graphSearch: '',
        graphStats: { nodeCount: 0, edgeCount: 0 },
        topTags: [],
        selectedTagName: '',
        selectedTagMemories: [],
        _network: null,
        _graphData: null,

        // Import
        importSource: '',
        importPreview: null,
        importConfig: { re_embed: true, extract_tags: true, batch_size: 20 },
        importRunning: false,
        importProgress: 0,
        importLog: [],

        // Init
        async init() {
            await this.loadStats();
            await this.loadMemories();

                    // Watch tab changes
            this.$watch('activeTab', (tab) => {
                if (tab === 'graph') this.$nextTick(() => this.loadGraph());
                if (tab === 'jargon') {
                    this.jargonSubTab = 'local';
                    this.loadJargon();
                }
            });
        },

        // Theme
        toggleTheme() {
            this.dark = !this.dark;
            document.documentElement.classList.toggle('dark', this.dark);
        },

        // API helpers
        async api(path, options = {}) {
            const res = await fetch(path, {
                headers: { 'Content-Type': 'application/json' },
                ...options,
            });
            return res.json();
        },

        formatTime(ts) {
            if (!ts) return '-';
            const d = new Date(ts * 1000);
            return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
        },

        // ─── Stats ───
        async loadStats() {
            this.stats = await this.api('/api/stats');
        },

        // ─── Memories ───
        async loadMemories() {
            const params = new URLSearchParams({
                page: this.memPage,
                size: 20,
            });
            if (this.memFilter.group_id) params.set('group_id', this.memFilter.group_id);
            if (this.memFilter.sender) params.set('sender', this.memFilter.sender);

            const data = await this.api(`/api/memories?${params}`);
            this.memories = data.items || [];
            this.memTotal = data.total || 0;
            this.memPages = data.pages || 0;
        },

        async showMemoryDetail(id) {
            this.memDetail = await this.api(`/api/memories/${id}`);
        },

        // ─── Query ───
        async runQuery() {
            if (!this.queryText.trim()) return;
            this.queryLoading = true;
            this.queryResults = [];
            this.queryTiming = null;
            this.queryDebug = null;

            try {
                const data = await this.api('/api/query', {
                    method: 'POST',
                    body: JSON.stringify({
                        text: this.queryText,
                        ...this.queryParams,
                        group_id: this.queryParams.group_id || null,
                    }),
                });
                this.queryResults = data.results || [];
                this.queryTiming = data.timing || null;
                this.queryDebug = data.debug || null;
            } catch (e) {
                console.error('Query failed:', e);
            } finally {
                this.queryLoading = false;
            }
        },

        // ─── Graph ───
        async loadGraph() {
            const data = await this.api('/api/tags/graph');
            this._graphData = data;
            this.graphStats.nodeCount = (data.nodes || []).length;
            this.graphStats.edgeCount = (data.edges || []).length;

            // Top tags
            const sorted = [...(data.nodes || [])].sort((a, b) => b.value - a.value);
            this.topTags = sorted.slice(0, 10);

            // Render
            const container = document.getElementById('graph-container');
            if (!container) return;

            const nodes = new vis.DataSet((data.nodes || []).map(n => ({
                id: n.id,
                label: n.label,
                value: n.value,
                font: { color: '#e2e8f0', size: 12 },
                color: {
                    background: 'hsl(263, 70%, 50%)',
                    border: 'hsl(263, 70%, 40%)',
                    highlight: { background: 'hsl(263, 70%, 60%)', border: 'hsl(263, 70%, 70%)' },
                },
            })));

            const edges = new vis.DataSet((data.edges || []).map(e => ({
                from: e.from,
                to: e.to,
                value: e.value,
                color: { color: 'hsl(240, 3.7%, 25%)', highlight: 'hsl(263, 70%, 50%)' },
            })));

            const options = {
                physics: {
                    solver: 'forceAtlas2Based',
                    forceAtlas2Based: { gravitationalConstant: -20, centralGravity: 0.008, springLength: 80 },
                    stabilization: { iterations: 50, fit: true },
                    maxVelocity: 50,
                },
                nodes: { shape: 'dot', scaling: { min: 6, max: 25 }, font: { size: 10 } },
                edges: { smooth: { type: 'continuous' }, scaling: { min: 1, max: 4 }, color: { opacity: 0.6 } },
                interaction: { hover: true, tooltipDelay: 200, zoomView: true },
            };

            this._network = new vis.Network(container, { nodes, edges }, options);

            // Click handler
            this._network.on('click', async (params) => {
                if (params.nodes.length > 0) {
                    const nodeId = params.nodes[0];
                    const node = data.nodes.find(n => n.id === nodeId);
                    if (node) {
                        this.selectedTagName = node.label;
                        // Fetch memories for this tag
                        const memories = await this.api(`/api/tags/${nodeId}/memories`);
                        this.selectedTagMemories = memories || [];
                    }
                }
            });
        },

        highlightNode() {
            if (!this._network || !this._graphData) return;
            const search = this.graphSearch.toLowerCase();
            if (!search) return;

            const node = this._graphData.nodes.find(n => n.label.toLowerCase().includes(search));
            if (node) {
                this._network.selectNodes([node.id]);
                this._network.focus(node.id, { scale: 1.5, animation: true });
            }
        },

        // ─── Import ───
        async previewImport() {
            if (!this.importSource) return;
            this.importPreview = await this.api('/api/import/preview', {
                method: 'POST',
                body: JSON.stringify({ source: this.importSource }),
            });
        },

        async startImport() {
            if (!this.importSource || this.importRunning) return;
            this.importRunning = true;
            this.importProgress = 0;
            this.importLog = [];

            try {
                const response = await fetch('/api/import/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        source: this.importSource,
                        ...this.importConfig,
                    }),
                });

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const event = JSON.parse(line.slice(6));
                                this.importProgress = event.progress || 0;
                                if (event.message) this.importLog.push(event.message);
                                if (this.importLog.length > 100) this.importLog.shift();
                            } catch (e) {}
                        }
                    }
                }
            } catch (e) {
                this.importLog.push(`Error: ${e.message}`);
            } finally {
                this.importRunning = false;
                await this.loadStats();
            }
        },

        // ─── Jargon & Holyman ───
        async loadJargon() {
            try {
                this.selectedJargonIds = []; // 重置批量勾选
                const params = new URLSearchParams({
                    limit: this.jargonPageSize,
                    offset: (this.jargonPage - 1) * this.jargonPageSize
                });
                if (this.jargonFilter) params.set('status', this.jargonFilter);
                if (this.jargonGroup) params.set('group_id', this.jargonGroup.trim());
                if (this.jargonSearch) params.set('search', this.jargonSearch.trim());
                
                const r = await fetch('/api/jargon/?' + params);
                const d = await r.json();
                this.jargonItems = d.items || [];
                this.jargonTotal = d.total || 0;
                this.jargonPending = this.jargonItems.filter(j => (j.status || 'pending') === 'pending').length;
                
                // 重算页数
                this.jargonTotalPages = Math.ceil(this.jargonTotal / this.jargonPageSize) || 1;
                this.jargonHasMore = this.jargonItems.length >= this.jargonPageSize;
            } catch(e) { console.error('loadJargon failed:', e); }
        },

        async searchJargon() {
            this.jargonPage = 1;
            this.clearJargonSelection();
            await this.loadJargon();
        },

        async jargonPrev() {
            if (this.jargonPage <= 1) return;
            this.jargonPage--;
            this.clearJargonSelection();
            await this.loadJargon();
        },

        async jargonNext() {
            if (!this.jargonHasMore) return;
            this.jargonPage++;
            this.clearJargonSelection();
            await this.loadJargon();
        },

        clearJargonSelection() {
            this.selectedJargonIds = [];
            this.jargonAllMatching = false;
        },

        async reviewJargon(id, action) {
            await fetch(`/api/jargon/${id}/review`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ action }),
            });
            await this.loadJargon();
        },

        toggleSelectAllJargon(e) {
            if (e.target.checked) {
                this.selectedJargonIds = this.jargonItems.map(j => j.id);
            } else {
                this.selectedJargonIds = [];
            }
        },

        async batchReviewJargon(action) {
            if (!this.selectedJargonIds.length) return;
            try {
                const r = await fetch('/api/jargon/batch-review', {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({ 
                        ids: this.selectedJargonIds, 
                        action: action,
                        all_matching: this.jargonAllMatching,
                        group_id: this.jargonGroup || null,
                        status: this.jargonFilter || null,
                        search: this.jargonSearch || null
                    })
                });
                const d = await r.json();
                if (d.ok) {
                    alert(`✓ 成功批量将 ${d.reviewed_count} 条词条置为 ${action==='approve'?'已确认':'已否决'}`);
                    this.clearJargonSelection();
                    await this.loadJargon();
                } else {
                    alert('批量流转失败');
                }
            } catch(e) { alert('网络错误，批量流转失败'); }
        },

        async batchDeleteJargon() {
            if (!this.selectedJargonIds.length) return;
            if (!confirm(`确定要批量物理删除选中的黑话词条吗？此操作不可逆！`)) return;
            try {
                const r = await fetch('/api/jargon/batch-delete', {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({ 
                        ids: this.selectedJargonIds,
                        all_matching: this.jargonAllMatching,
                        group_id: this.jargonGroup || null,
                        status: this.jargonFilter || null,
                        search: this.jargonSearch || null
                    })
                });
                const d = await r.json();
                if (d.ok) {
                    alert(`✓ 成功批量删除 ${d.deleted_count} 条黑话。`);
                    this.clearJargonSelection();
                    await this.loadJargon();
                } else {
                    alert('批量删除失败');
                }
            } catch(e) { alert('网络错误，批量删除失败'); }
        },

        openJargonEdit(j) {
            this.jargonEdit = j;
            this.jargonEditForm = { word: j.word, meaning: j.meaning || '' };
        },

        async saveJargonEdit() {
            if (!this.jargonEdit) return;
            await fetch(`/api/jargon/${this.jargonEdit.id}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(this.jargonEditForm),
            });
            this.jargonEdit = null;
            await this.loadJargon();
        },

        async quickEditMeaning(id, newMeaning) {
            if (!newMeaning && newMeaning !== '') return;
            await fetch(`/api/jargon/${id}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ meaning: newMeaning.trim() }),
            });
            await this.loadJargon();
        },

        async toggleJargonGlobal(id) {
            await fetch(`/api/jargon/${id}/toggle_global`, { method: 'POST' });
            await this.loadJargon();
        },

        async deleteJargon(id) {
            if (!confirm('确认删除该黑话词条？')) return;
            await fetch(`/api/jargon/${id}`, { method: 'DELETE' });
            await this.loadJargon();
        },

        openJargonCreate() {
            this.jargonCreate = true;
            this.jargonCreateForm = { word: '', meaning: '', group_id: '' };
        },

        async saveJargonCreate() {
            if (!this.jargonCreateForm.word.trim()) { alert('词条不能为空'); return; }
            try {
                await fetch('/api/jargon/', {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({
                        word: this.jargonCreateForm.word.trim(),
                        meaning: this.jargonCreateForm.meaning.trim(),
                        group_id: this.jargonCreateForm.group_id.trim() || null,
                    }),
                });
                this.jargonCreate = false;
                await this.loadJargon();
            } catch(e) { console.error(e); alert('保存失败: '+e.message); }
        },

        async loadJargonHolyman() {
            try {
                const data = await this.api('/api/jargon/holyman');
                this.holymanItems = data.items || [];
                this.holymanLocalVersion = data.local_version || 'Unknown';
                this.holymanRemoteVersion = data.remote_version || 'Unknown';
            } catch (e) {
                console.error('Failed to load Holyman items:', e);
            }
        },

        async toggleJargonHolyman(h) {
            try {
                await this.api('/api/jargon/holyman/toggle', {
                    method: 'POST',
                    body: JSON.stringify({
                        word: h.word,
                        meaning: h.meaning,
                        activate: h.is_activated
                    })
                });
                await this.loadJargonHolyman();
            } catch (e) {
                console.error('Failed to toggle Holyman item:', e);
            }
        },

        async quickEditHolyman(h, newMeaning) {
            try {
                const payload = {
                    word: h.word,
                    meaning: newMeaning.trim(),
                    activate: true
                };
                await this.api('/api/jargon/holyman/toggle', {
                    method: 'POST',
                    body: JSON.stringify(payload)
                });
                await this.loadJargonHolyman();
            } catch (e) {
                console.error('Failed to quick edit Holyman item:', e);
            }
        },

        async syncJargonHolyman() {
            if (this.holymanSyncing) return;
            this.holymanSyncing = true;
            try {
                const data = await this.api('/api/jargon/holyman/sync', {
                    method: 'POST',
                    body: JSON.stringify({
                        use_proxy: this.holymanUseProxy
                    })
                });
                if (data.ok) {
                    alert(`同步完成，加载了 ${data.phrases_count || 0} 条词条，${data.corpus_count || 0} 条语料`);
                } else {
                    alert(`同步失败: ${data.error || '未知错误'}`);
                }
            } catch (e) {
                console.error('Failed to sync Holyman items:', e);
                alert('同步失败: ' + e.message);
            } finally {
                this.holymanSyncing = false;
                await this.loadJargonHolyman();
            }
        },

        filteredHolymanItems() {
            const search = (this.holymanSearch || '').trim().toLowerCase();
            if (!search) return this.holymanItems;
            return this.holymanItems.filter(item => {
                const word = (item.word || '').toLowerCase();
                const meaning = (item.meaning || '').toLowerCase();
                const custom_meaning = (item.custom_meaning || '').toLowerCase();
                return word.includes(search) || meaning.includes(search) || custom_meaning.includes(search);
            });
        },
    };
}
