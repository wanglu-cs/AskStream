(function () {
    'use strict';

    const API_URL = '/api/annotations';
    const STORAGE_KEY = 'askstream.videoFilterState.v1';
    const AUTOSAVE_DELAY_MS = 900;
    const DEFAULT_CRITERIA = [
        { id: 'scene', label: 'Multiple entities, few scene changes', defaultChecked: false },
        { id: 'duration', label: 'Duration under 4 minutes', defaultChecked: true },
        { id: 'resolution', label: 'Resolution at least 720p', defaultChecked: true },
    ];
    const state = {
        records: [], filtered: [], index: 0, filter: 'all', search: '',
        criteria: DEFAULT_CRITERIA.map(item => ({ ...item })), decisions: {},
        updatedAt: null, revision: 0, dirty: false, saveTimer: null,
        saveInFlight: false, saveAgain: false,
    };
    const $ = id => document.getElementById(id);
    const e = {
        summary: $('datasetSummary'), saveState: $('saveState'), frame: $('youtubeFrame'),
        key: $('videoKey'), open: $('openYoutube'), prev: $('prevButton'), next: $('nextButton'),
        pos: $('positionText'), search: $('searchBox'), title: $('videoTitle'),
        status: $('statusBadge'), channel: $('channelName'), duration: $('durationText'),
        views: $('viewCount'), date: $('publishedAt'), criteria: $('criteriaList'),
        keywords: $('matchedKeywords'), description: $('descriptionText'),
        accept: $('acceptButton'), reject: $('rejectButton'), saveButton: $('saveButton'), toast: $('toast'),
    };

    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
        }[char]));
    }

    function time(seconds) {
        seconds = Math.max(0, Math.round(Number(seconds) || 0));
        return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }

    function setSaveState(kind, label) {
        e.saveState.dataset.state = kind;
        e.saveState.textContent = label;
    }

    function snapshot() {
        return {
            schema_version: 'pvb.video_filter.v1', records: state.records,
            criteria: state.criteria, decisions: state.decisions,
            updated_at: state.updatedAt, revision: state.revision,
        };
    }

    function readCache(key) {
        try {
            return JSON.parse(localStorage.getItem(key) || 'null');
        } catch (error) {
            console.warn('Could not read browser backup', error);
            return null;
        }
    }

    function browserBackup(serialized) {
        try {
            localStorage.setItem(STORAGE_KEY, serialized);
            return true;
        } catch (error) {
            console.warn('Could not save browser backup', error);
            return false;
        }
    }

    function markDirty() {
        state.dirty = true;
        state.updatedAt = new Date().toISOString();
        setSaveState('dirty', 'Unsaved');
        clearTimeout(state.saveTimer);
        state.saveTimer = setTimeout(() => saveDataset(true), AUTOSAVE_DELAY_MS);
    }

    async function saveDataset(silent = false) {
        clearTimeout(state.saveTimer);
        if (state.saveInFlight) {
            state.saveAgain = true;
            return false;
        }
        state.saveInFlight = true;
        state.updatedAt = new Date().toISOString();
        state.revision += 1;
        const body = JSON.stringify(snapshot(), null, 2);
        const localSaved = browserBackup(body);
        setSaveState('loading', 'Saving');
        let serverSaved = false;
        try {
            const response = await fetch(API_URL, {
                method: 'PUT', headers: { 'Content-Type': 'application/json; charset=utf-8' }, body,
            });
            if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
            serverSaved = true;
        } catch (error) {
            console.warn('Server save unavailable', error);
        } finally {
            state.saveInFlight = false;
        }
        if (serverSaved || localSaved) {
            state.dirty = false;
            setSaveState('saved', serverSaved ? 'Saved' : 'Saved in Browser');
            if (!silent) toast(serverSaved ? 'Video Filter data saved in this web directory' : 'Video Filter data saved in the browser');
        } else {
            setSaveState('error', 'Save Failed');
            if (!silent) toast('Save failed. Export a JSON backup first.', 5000);
        }
        if (state.saveAgain) {
            state.saveAgain = false;
            return saveDataset(true);
        }
        return serverSaved || localSaved;
    }

    function dec(key) {
        const saved = Object.hasOwn(state.decisions, key) ? state.decisions[key] : {};
        return {
            status: saved.status || 'pending',
            criteria: Object.fromEntries(state.criteria.map(item => [
                item.id, typeof saved.criteria?.[item.id] === 'boolean'
                    ? saved.criteria[item.id] : !!item.defaultChecked,
            ])),
        };
    }

    function ready(decision) {
        return state.criteria.every(item => decision.criteria[item.id]);
    }

    function normalize(data) {
        if (!Array.isArray(data)) throw new Error('The video data must be a JSON array.');
        const records = new Map();
        for (const item of data) {
            const key = String(item?.id || item?.key || '').trim();
            if (!/^[A-Za-z0-9_-]+$/.test(key)) throw new Error('Every video must have a valid YouTube ID.');
            records.set(key, {
                key, title: String(item.title || key), description: String(item.description || ''),
                channel: String(item.channel_title || item.channel || '-'),
                publishedAt: String(item.published_at || item.publishedAt || ''),
                keywords: (Array.isArray(item.matched_keywords) ? item.matched_keywords
                    : Array.isArray(item.keywords) ? item.keywords : []).map(String),
                url: `https://www.youtube.com/watch?v=${encodeURIComponent(key)}`,
                duration: Number(item.duration_seconds ?? item.duration) || 0,
                views: Number(item.views) || 0,
            });
        }
        return [...records.values()];
    }

    function normalizeCriteria(items) {
        if (!Array.isArray(items)) throw new Error('Filtering criteria must be an array.');
        const ids = new Set();
        return items.map(item => {
            if (!item || typeof item.id !== 'string' || !/^[A-Za-z0-9_-]+$/.test(item.id)
                || typeof item.label !== 'string' || !item.label.trim() || ids.has(item.id)) {
                throw new Error('Each criterion needs a unique ID and a non-empty label.');
            }
            ids.add(item.id);
            return { id: item.id, label: item.label.trim(), defaultChecked: !!item.defaultChecked };
        });
    }

    function normalizeDecisions(items) {
        if (!items || typeof items !== 'object' || Array.isArray(items)) {
            throw new Error('Review decisions must be a JSON object.');
        }
        const result = Object.create(null);
        for (const [key, value] of Object.entries(items)) {
            if (!/^[A-Za-z0-9_-]+$/.test(key) || !value || !['pending', 'accepted', 'rejected'].includes(value.status)) {
                throw new Error('A review decision has an invalid video ID or status.');
            }
            result[key] = {
                status: value.status,
                criteria: Object.fromEntries(Object.entries(value.criteria || {}).filter(([, checked]) => typeof checked === 'boolean')),
            };
        }
        return result;
    }

    function restore(document) {
        const records = normalize(document.records);
        const criteria = normalizeCriteria(document.criteria ?? DEFAULT_CRITERIA);
        const decisions = normalizeDecisions(document.decisions || {});
        state.records = records;
        state.criteria = criteria;
        state.decisions = decisions;
        state.updatedAt = document.updated_at || document.updatedAt || null;
        state.revision = Number(document.revision) || 0;
    }

    function apply() {
        const key = state.filtered[state.index]?.key;
        state.filtered = state.records.filter(record =>
            (state.filter === 'all' || dec(record.key).status === state.filter)
            && (!state.search || [record.key, record.title, record.description, record.channel, ...record.keywords]
                .join(' ').toLowerCase().includes(state.search.toLowerCase())));
        const index = state.filtered.findIndex(record => record.key === key);
        state.index = index >= 0 ? index : 0;
        render();
    }

    function renderCriteria(decision) {
        e.criteria.innerHTML = state.criteria.map(item => `
            <div class="criterionRow">
                <label class="checkRow"><input type="checkbox" data-criterion="${esc(item.id)}"
                    ${decision?.criteria[item.id] ? 'checked' : ''} ${decision ? '' : 'disabled'}><span>${esc(item.label)}</span></label>
                <button class="removeCriterionButton" type="button" data-remove-criterion="${esc(item.id)}"
                    title="Remove criterion" aria-label="Remove criterion: ${esc(item.label)}">&times;</button>
            </div>`).join('');
    }

    function render() {
        const acceptedCount = state.records.filter(record => dec(record.key).status === 'accepted').length;
        const rejectedCount = state.records.filter(record => dec(record.key).status === 'rejected').length;
        e.summary.textContent = `${state.records.length} videos | Accepted ${acceptedCount} | Rejected ${rejectedCount} | Pending ${state.records.length - acceptedCount - rejectedCount}`;
        document.querySelectorAll('.filter').forEach(button => button.classList.toggle('active', button.dataset.filter === state.filter));
        const record = state.filtered[state.index];
        e.prev.disabled = !record || state.index <= 0;
        e.next.disabled = !record || state.index >= state.filtered.length - 1;
        e.reject.disabled = !record;
        if (!record) {
            e.frame.removeAttribute('src');
            e.open.removeAttribute('href');
            for (const element of [e.key, e.title, e.channel, e.duration, e.views, e.date, e.description]) element.textContent = '-';
            e.keywords.innerHTML = '';
            e.status.textContent = 'Pending';
            e.status.className = 'badge pending';
            e.pos.textContent = '0 / 0';
            e.accept.disabled = true;
            renderCriteria(null);
            return;
        }
        const decision = dec(record.key);
        const source = `https://www.youtube.com/embed/${encodeURIComponent(record.key)}?rel=0`;
        if (e.frame.getAttribute('src') !== source) e.frame.src = source;
        e.key.textContent = record.key;
        e.open.href = record.url;
        e.pos.textContent = `${state.index + 1} / ${state.filtered.length}`;
        e.title.textContent = record.title;
        e.status.textContent = { pending: 'Pending', accepted: 'Accepted', rejected: 'Rejected' }[decision.status];
        e.status.className = `badge ${decision.status}`;
        e.channel.textContent = record.channel;
        e.duration.textContent = `${time(record.duration)} (${record.duration}s)`;
        e.views.textContent = new Intl.NumberFormat('en-US').format(record.views);
        e.date.textContent = record.publishedAt || '-';
        e.accept.disabled = !ready(decision);
        renderCriteria(decision);
        e.keywords.innerHTML = record.keywords.map(keyword => `<span class="keywordTag">${esc(keyword)}</span>`).join('');
        e.description.textContent = record.description || 'No description';
    }

    function move(offset) {
        state.index = Math.max(0, Math.min(state.filtered.length - 1, state.index + offset));
        render();
    }

    function setCriterion(id, checked) {
        const record = state.filtered[state.index];
        if (!record) return;
        const decision = dec(record.key);
        decision.criteria[id] = checked;
        state.decisions[record.key] = decision;
        markDirty();
        render();
    }

    function addCriterion() {
        const label = window.prompt('Enter a filtering criterion:');
        if (!label?.trim()) return;
        state.criteria.push({ id: `custom_${crypto.randomUUID()}`, label: label.trim(), defaultChecked: false });
        markDirty();
        render();
    }

    function removeCriterion(id) {
        state.criteria = state.criteria.filter(item => item.id !== id);
        for (const decision of Object.values(state.decisions)) delete decision.criteria?.[id];
        markDirty();
        render();
    }

    function mark(status) {
        const record = state.filtered[state.index];
        if (!record) return;
        if (status === 'accepted' && !ready(dec(record.key))) {
            toast('Please confirm all filtering criteria first.');
            return;
        }
        state.decisions[record.key] = { ...dec(record.key), status };
        markDirty();
        toast(`${record.key}: ${status === 'accepted' ? 'Accepted' : 'Rejected'}`);
        state.filter === 'pending' ? apply() : move(1);
    }

    let toastTimer;
    function toast(message, duration = 2400) {
        clearTimeout(toastTimer);
        e.toast.textContent = message;
        e.toast.classList.add('show');
        toastTimer = setTimeout(() => e.toast.classList.remove('show'), duration);
    }

    function download(name, data, type) {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([data], { type }));
        link.download = name;
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }

    function accepted() {
        return state.records.filter(record => dec(record.key).status === 'accepted' && ready(dec(record.key)));
    }

    function textRecords(text) {
        return text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
            let id = line;
            if (/^https?:\/\//i.test(line)) {
                const url = new URL(line);
                const host = url.hostname.toLowerCase().replace(/^www\./, '');
                if (host === 'youtu.be') id = url.pathname.split('/')[1];
                else if (host === 'youtube.com' || host === 'm.youtube.com') {
                    id = url.searchParams.get('v') || (/^\/(shorts|embed)\//.test(url.pathname) ? url.pathname.split('/')[2] : '');
                } else throw new Error('TXT files must contain YouTube IDs or YouTube URLs.');
            }
            if (!/^[A-Za-z0-9_-]{11}$/.test(id || '')) throw new Error('Each TXT line must contain an 11-character YouTube ID or URL.');
            return { id };
        });
    }

    async function importFile(file) {
        if (!file) return;
        try {
            const text = await file.text();
            const payload = /\.txt$/i.test(file.name) ? textRecords(text) : JSON.parse(text);
            if (Array.isArray(payload)) {
                if (!payload.length) throw new Error('The imported file contains no videos.');
                const records = normalize(payload);
                const reviews = Object.fromEntries(payload.filter(item => item.review).map(item => [item.id || item.key, item.review]));
                const decisions = normalizeDecisions(reviews);
                const definitions = payload.find(item => item.review?.criteria_definitions)?.review.criteria_definitions;
                const criteria = definitions === undefined ? state.criteria : normalizeCriteria(definitions);
                const originals = new Map(payload.map(item => [item.id || item.key, item]));
                const merged = new Map(state.records.map(record => [record.key, record]));
                for (const record of records) {
                    // ID-only imports should not erase existing video metadata.
                    const original = originals.get(record.key);
                    if (!merged.has(record.key) || Object.keys(original).some(key => !['id', 'key', 'review'].includes(key))) merged.set(record.key, record);
                }
                state.records = [...merged.values()];
                state.decisions = Object.assign(Object.create(null), state.decisions, decisions);
                state.criteria = criteria;
            } else if (payload && Array.isArray(payload.records)) {
                restore(payload);
            } else {
                throw new Error('Import a video JSON array, a saved Video Filter JSON file, or a TXT list of YouTube IDs.');
            }
            state.filter = 'all';
            state.search = '';
            e.search.value = '';
            markDirty();
            apply();
            toast('Import complete. Changes will be saved automatically.');
        } catch (error) {
            toast(`Import failed: ${error.message}`);
        } finally {
            $('importFile').value = '';
        }
    }

    function bind() {
        e.prev.onclick = () => move(-1);
        e.next.onclick = () => move(1);
        e.accept.onclick = () => mark('accepted');
        e.reject.onclick = () => mark('rejected');
        $('addCriterionButton').onclick = addCriterion;
        e.criteria.onchange = event => {
            if (event.target.dataset.criterion) setCriterion(event.target.dataset.criterion, event.target.checked);
        };
        e.criteria.onclick = event => {
            const button = event.target.closest('[data-remove-criterion]');
            if (button) removeCriterion(button.dataset.removeCriterion);
        };
        e.search.oninput = event => { state.search = event.target.value.trim(); apply(); };
        document.querySelectorAll('.filter').forEach(button => {
            button.onclick = () => { state.filter = button.dataset.filter; apply(); };
        });
        $('importButton').onclick = () => $('importFile').click();
        $('importFile').onchange = event => importFile(event.target.files[0]);
        $('exportTxtButton').onclick = () => download('youtube_accepted_ids.txt', accepted().map(record => record.key).join('\n'), 'text/plain;charset=utf-8');
        $('exportJsonButton').onclick = () => download('youtube_accepted_videos.json', JSON.stringify(accepted().map(record => ({
            ...record, review: { ...dec(record.key), criteria_definitions: state.criteria },
        })), null, 2), 'application/json;charset=utf-8');
        $('copyButton').onclick = async () => {
            try {
                await navigator.clipboard.writeText(accepted().map(record => record.key).join('\n'));
                toast('Video IDs copied.');
            } catch (error) {
                toast('Could not copy video IDs. Please use Export TXT.');
            }
        };
        e.saveButton.onclick = () => saveDataset(false);
        window.addEventListener('beforeunload', event => {
            if (!state.dirty) return;
            event.preventDefault();
            event.returnValue = '';
        });
    }

    async function init() {
        setSaveState('loading', 'Loading');
        try {
            const response = await fetch(API_URL, { cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            let document = await response.json();
            // As in the annotation interface, server data is authoritative and browser storage is a save fallback.
            const isInitialData = !document.schema_version;
            if (isInitialData) {
                const cache = readCache(STORAGE_KEY);
                if (cache?.schema_version) document = cache;
            }
            restore(document);
            bind();
            apply();
            if (isInitialData) markDirty();
            else setSaveState('saved', 'Saved');
        } catch (error) {
            setSaveState('error', 'Load Failed');
            e.summary.textContent = `Data loading failed: ${error.message}`;
        }
    }

    init();
})();
