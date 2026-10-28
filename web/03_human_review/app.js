(function () {
    "use strict";

    const ANNOTATION_API_URL = "/api/annotations";
    const ASSETS_URL = "./assets.json";
    const ASSET_CACHE_KEY = "askstream.humanReview.assets.v1";
    const ANNOTATION_BACKUP_KEY = "askstream.humanReview.annotations.v1";
    const SCRUB_SEEK_INTERVAL_MS = 90;
    const AUTOSAVE_DELAY_MS = 800;

    const state = {
        dataset: null,
        assets: { videos: {} },
        assetBaseUrl: ASSETS_URL,
        videoIds: [],
        activeVideoId: null,
        activeEventId: null,
        dirty: false,
        saveInFlight: false,
        saveAgain: false,
        saveTimer: null,
        changeVersion: 0,
        pendingSeek: 0,
        animationFrame: null,
        scrubTimer: null,
        scrubTarget: 0,
        scrubLastSeekAt: 0,
        scrubWasPlaying: false,
        isScrubbing: false,
        toastTimer: null,
    };

    const ids = [
        "datasetSummary", "saveState", "importAssetsButton", "saveButton", "assetsFile",
        "videoSelect", "previousVideoButton", "nextVideoButton", "eventSelect",
        "previousEventButton", "nextEventButton", "eventPosition",
        "maskedVideo", "revealedVideo", "maskedMissing", "revealedMissing",
        "maskedSourceState", "revealedSourceState", "playButton", "previousFrameButton",
        "nextFrameButton", "timeText", "scrubber", "playbackRate", "jumpEventButton",
        "jumpAskingButton", "syncState", "eventTitle", "eventStatus", "youtubeVideoId", "questionText",
        "goldEntityList", "addActionButton", "actionOptionList", "addAnswerButton",
        "answerOptionList", "validationMessage", "toast",
    ];
    const els = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));

    function nowIso() {
        return new Date().toISOString();
    }

    function uid(prefix) {
        const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        return `${prefix}_${id}`;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function finite(value, fallback = 0) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    function formatTime(value) {
        const seconds = Math.max(finite(value), 0);
        const minutes = Math.floor(seconds / 60);
        return `${String(minutes).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}.${String(Math.floor((seconds % 1) * 1000)).padStart(3, "0")}`;
    }

    async function fetchJson(url, optional = false) {
        try {
            const response = await fetch(url, { cache: "no-store" });
            if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
            return await response.json();
        } catch (error) {
            if (optional) return null;
            throw new Error(`Could not load ${url}: ${error.message}`);
        }
    }

    function currentVideo() {
        return state.dataset?.videos?.[state.activeVideoId] || null;
    }

    function reviewableEvents(video = currentVideo()) {
        return (video?.segments || []).filter((segment) => segment.question?.text?.trim());
    }

    function currentEvent() {
        return reviewableEvents().find((event) => event.segment_id === state.activeEventId) || null;
    }

    function normalizeDataset(dataset) {
        Object.values(dataset?.videos || {}).forEach((video) => {
            video.entities = Array.isArray(video.entities) ? video.entities : [];
            video.segments = Array.isArray(video.segments) ? video.segments : [];
            video.segments.forEach((event) => {
                event.action_options = Array.isArray(event.action_options) ? event.action_options : [];
                event.answer_options = Array.isArray(event.answer_options) ? event.answer_options : [];
                event.gold = event.gold && typeof event.gold === "object" ? event.gold : {};
                event.gold.action_option_ids = Array.isArray(event.gold.action_option_ids) ? event.gold.action_option_ids : [];
                event.gold.answer_option_ids = Array.isArray(event.gold.answer_option_ids) ? event.gold.answer_option_ids : [];
                event.gold.entity_ids = Array.isArray(event.gold.entity_ids) ? event.gold.entity_ids : [];
            });
        });
        return dataset;
    }

    function normalizeAssets(payload) {
        return payload && typeof payload.videos === "object" ? payload : { videos: {} };
    }

    function rebuildReviewScope(preferredVideoId = state.activeVideoId) {
        const annotatedIds = Object.keys(state.dataset.videos).filter(
            (id) => reviewableEvents(state.dataset.videos[id]).length,
        );
        const assetIds = new Set(Object.keys(state.assets.videos || {}));
        state.videoIds = assetIds.size
            ? annotatedIds.filter((id) => assetIds.has(id))
            : annotatedIds;
        if (!state.videoIds.length) {
            state.activeVideoId = null;
            state.activeEventId = null;
            els.datasetSummary.textContent = "0 videos · 0 events";
            return;
        }
        state.activeVideoId = state.videoIds.includes(preferredVideoId)
            ? preferredVideoId
            : state.videoIds[0];
        if (!reviewableEvents().some((event) => event.segment_id === state.activeEventId)) {
            state.activeEventId = reviewableEvents()[0]?.segment_id || null;
        }
        const eventCount = state.videoIds.reduce(
            (sum, id) => sum + reviewableEvents(state.dataset.videos[id]).length,
            0,
        );
        els.datasetSummary.textContent = `${state.videoIds.length} videos · ${eventCount} events`;
    }

    function resolveAssetUri(uri) {
        const value = String(uri || "").trim();
        if (!value) return "";
        if (/^https?:\/\//i.test(value) || value.startsWith("data:") || value.startsWith("blob:")) return value;
        if (/^[A-Za-z]:[\\/]/.test(value)) {
            const normalized = value.replaceAll("\\", "/");
            const repositoryPrefix = "E:/AskStream/";
            return normalized.toLowerCase().startsWith(repositoryPrefix.toLowerCase())
                ? `/${normalized.slice(repositoryPrefix.length)}`
                : "";
        }
        return new URL(value, new URL(state.assetBaseUrl, window.location.href)).href;
    }

    function assetEntry() {
        const videoAssets = state.assets.videos?.[state.activeVideoId] || {};
        const eventAssets = videoAssets.events?.[state.activeEventId] || {};
        return {
            masked: resolveAssetUri(videoAssets.masked_video_uri || videoAssets.masked_uri || videoAssets.mask_video_uri),
            revealed: resolveAssetUri(eventAssets.revealed_video_uri || eventAssets.reveal_uri || eventAssets.correct_entity_video_uri),
            revealedPlaceholder: eventAssets.placeholder_text || "Correct video is not available yet.",
        };
    }

    function setSaveState(status, text) {
        els.saveState.dataset.state = status;
        els.saveState.textContent = text;
    }

    function showToast(message, duration = 2400) {
        els.toast.textContent = message;
        els.toast.classList.add("show");
        clearTimeout(state.toastTimer);
        state.toastTimer = setTimeout(() => els.toast.classList.remove("show"), duration);
    }

    function markDirty() {
        const video = currentVideo();
        if (video?.status === "complete") {
            video.status = "draft";
            delete video.completed_at;
        }
        if (video) video.updated_at = nowIso();
        state.dataset.updated_at = nowIso();
        state.dirty = true;
        state.changeVersion += 1;
        localStorage.setItem(ANNOTATION_BACKUP_KEY, JSON.stringify(state.dataset));
        setSaveState("dirty", "Unsaved");
        clearTimeout(state.saveTimer);
        state.saveTimer = setTimeout(saveDataset, AUTOSAVE_DELAY_MS);
        renderEventHeader();
    }

    function renderNavigation() {
        const videoIndex = state.videoIds.indexOf(state.activeVideoId);
        els.videoSelect.innerHTML = state.videoIds.map((id) => {
            const video = state.dataset.videos[id];
            return `<option value="${escapeHtml(id)}" ${id === state.activeVideoId ? "selected" : ""}>${escapeHtml(id)} · ${escapeHtml(video.coin_context?.class || "Unclassified")}</option>`;
        }).join("");
        els.previousVideoButton.disabled = videoIndex <= 0;
        els.nextVideoButton.disabled = videoIndex < 0 || videoIndex >= state.videoIds.length - 1;
        const events = reviewableEvents();
        els.eventSelect.innerHTML = events.map((event, index) => `
            <option value="${escapeHtml(event.segment_id)}" ${event.segment_id === state.activeEventId ? "selected" : ""}>${index + 1}. ${escapeHtml(event.label?.text || "Untitled Event")}</option>
        `).join("");
        const index = events.findIndex((event) => event.segment_id === state.activeEventId);
        els.eventPosition.textContent = `${index + 1} / ${events.length}`;
        els.previousEventButton.disabled = index <= 0;
        els.nextEventButton.disabled = index < 0 || index >= events.length - 1;
    }

    function renderEventHeader() {
        const video = currentVideo();
        const event = currentEvent();
        els.eventTitle.textContent = event?.label?.text || "No Event Selected";
        els.youtubeVideoId.textContent = state.activeVideoId || "-";
        els.eventStatus.textContent = video?.status === "complete" ? "Complete" : "In Progress";
        els.questionText.textContent = event?.question?.text || "-";
        const entities = new Map((video?.entities || []).map((entity) => [entity.entity_id, entity]));
        const goldIds = event?.gold?.entity_ids || [];
        els.goldEntityList.innerHTML = goldIds.length
            ? goldIds.map((id) => {
                const entity = entities.get(id);
                return `<span class="entityChip"><span class="entityDot" style="background:${escapeHtml(entity?.color || "#88949c")}"></span>${escapeHtml(entity?.display_name || id)}</span>`;
            }).join("")
            : '<span class="emptyText">No correct entity selected</span>';
    }

    function optionLetter(index) {
        return index < 26 ? String.fromCharCode(65 + index) : String(index + 1);
    }

    function renderOptions(kind) {
        const event = currentEvent();
        const list = kind === "action" ? els.actionOptionList : els.answerOptionList;
        if (!event) {
            list.innerHTML = "";
            return;
        }
        const options = kind === "action" ? event.action_options : event.answer_options;
        const goldKey = kind === "action" ? "action_option_ids" : "answer_option_ids";
        list.innerHTML = options.map((option, index) => `
            <div class="optionRow" data-kind="${kind}" data-option-id="${escapeHtml(option.option_id)}">
                <input type="radio" name="${kind}-gold" value="${escapeHtml(option.option_id)}" ${event.gold[goldKey].includes(option.option_id) ? "checked" : ""} aria-label="Set as the correct ${kind}">
                <span class="optionLetter">${optionLetter(index)}</span>
                <textarea rows="2" data-option-text placeholder="Option text">${escapeHtml(option.text || "")}</textarea>
                <button class="removeButton" type="button" data-remove-option title="Remove option" aria-label="Remove option">×</button>
            </div>
        `).join("");
    }

    function renderValidation() {
        const event = currentEvent();
        if (!event) {
            els.validationMessage.hidden = true;
            return;
        }
        const issues = [];
        if (event.action_options.filter((item) => item.text?.trim()).length < 2) issues.push("Fewer than two action options");
        if (event.answer_options.filter((item) => item.text?.trim()).length < 2) issues.push("Fewer than two answer options");
        if (!event.gold.action_option_ids.length) issues.push("No correct action selected");
        if (!event.gold.answer_option_ids.length) issues.push("No correct answer selected");
        if (!event.gold.entity_ids.length) issues.push("No correct entity selected");
        els.validationMessage.hidden = !issues.length;
        els.validationMessage.textContent = issues.join(" · ");
    }

    function renderInspector() {
        renderEventHeader();
        renderOptions("action");
        renderOptions("answer");
        renderValidation();
    }

    function configuredVideos() {
        return [els.maskedVideo, els.revealedVideo].filter((video) => Boolean(video.dataset.configured));
    }

    function masterVideo() {
        return els.maskedVideo.dataset.configured ? els.maskedVideo : els.revealedVideo;
    }

    function duration() {
        return finite(masterVideo().duration, finite(currentVideo()?.media?.duration_s, 0));
    }

    function frameStep() {
        const rate = currentVideo()?.media?.frame_rate || {};
        return finite(rate.den, 1) / Math.max(finite(rate.num, 30), 1);
    }

    function seekBoth(time) {
        const target = clamp(finite(time), 0, Math.max(duration(), 0));
        state.pendingSeek = target;
        configuredVideos().forEach((video) => {
            if (video.readyState >= 1) video.currentTime = clamp(target, 0, finite(video.duration, target));
        });
        updateTransport(target);
    }

    function seekPreviewVideo(time) {
        const video = masterVideo();
        if (!video || video.readyState < 1) return;
        const target = clamp(time, 0, finite(video.duration, time));
        if (typeof video.fastSeek === "function") {
            video.fastSeek(target);
        } else {
            video.currentTime = target;
        }
    }

    function beginScrub() {
        if (state.isScrubbing) return;
        const videos = configuredVideos();
        state.isScrubbing = true;
        state.scrubWasPlaying = videos.some((video) => !video.paused);
        videos.forEach((video) => video.pause());
        cancelAnimationFrame(state.animationFrame);
    }

    function previewScrub(time) {
        if (!state.isScrubbing) beginScrub();
        const target = clamp(finite(time), 0, Math.max(duration(), 0));
        const now = performance.now();
        state.scrubTarget = target;
        state.pendingSeek = target;
        updateTransport(target);

        const elapsed = now - state.scrubLastSeekAt;
        clearTimeout(state.scrubTimer);
        if (elapsed >= SCRUB_SEEK_INTERVAL_MS) {
            seekPreviewVideo(target);
            state.scrubLastSeekAt = now;
            return;
        }
        state.scrubTimer = setTimeout(() => {
            seekPreviewVideo(state.scrubTarget);
            state.scrubLastSeekAt = performance.now();
        }, SCRUB_SEEK_INTERVAL_MS - elapsed);
    }

    async function commitScrub(time = state.scrubTarget) {
        if (!state.isScrubbing) return;
        clearTimeout(state.scrubTimer);
        state.scrubTimer = null;
        state.isScrubbing = false;
        seekBoth(time);
        synchronize(true);
        if (state.scrubWasPlaying) {
            await Promise.allSettled(configuredVideos().map((video) => video.play()));
            monitorSync();
        }
        state.scrubWasPlaying = false;
    }

    function synchronize(force = false) {
        const videos = configuredVideos();
        if (videos.length < 2) {
            els.syncState.textContent = videos.length ? "Single Video" : "Sync Ready";
            els.syncState.classList.toggle("ready", Boolean(videos.length));
            return;
        }
        const master = masterVideo();
        const follower = master === els.maskedVideo ? els.revealedVideo : els.maskedVideo;
        const drift = Math.abs(master.currentTime - follower.currentTime);
        if ((force || drift > 0.08) && follower.readyState >= 1) follower.currentTime = master.currentTime;
        follower.playbackRate = master.playbackRate;
        els.syncState.textContent = `Drift ${Math.round(drift * 1000)} ms`;
        els.syncState.classList.toggle("ready", drift <= 0.08);
    }

    function updateTransport(time = masterVideo().currentTime) {
        const total = duration();
        els.timeText.textContent = `${formatTime(time)} / ${formatTime(total)}`;
        els.scrubber.max = String(Math.max(total, 0.001));
        els.scrubber.value = String(clamp(finite(time), 0, Math.max(total, 0.001)));
        els.playButton.textContent = configuredVideos().some((video) => !video.paused) ? "❚❚" : "▶";
    }

    async function togglePlayback() {
        const videos = configuredVideos();
        if (!videos.length) {
            showToast("Configure a video source first.");
            return;
        }
        const playing = videos.some((video) => !video.paused);
        if (playing) {
            videos.forEach((video) => video.pause());
            cancelAnimationFrame(state.animationFrame);
        } else {
            synchronize(true);
            await Promise.allSettled(videos.map((video) => video.play()));
            monitorSync();
        }
        updateTransport();
    }

    function monitorSync() {
        cancelAnimationFrame(state.animationFrame);
        if (configuredVideos().every((video) => video.paused)) return;
        synchronize();
        updateTransport();
        state.animationFrame = requestAnimationFrame(monitorSync);
    }

    function configureVideo(video, missing, badge, uri, placeholderText) {
        const sourceUri = uri || "";
        missing.hidden = Boolean(sourceUri);
        if (!uri && placeholderText) missing.textContent = placeholderText;
        badge.textContent = uri ? "Configured" : "Not Configured";
        badge.classList.toggle("ready", Boolean(uri));
        if (video.dataset.sourceUri === sourceUri) return false;

        video.pause();
        video.removeAttribute("src");
        video.dataset.configured = "";
        video.dataset.sourceUri = sourceUri;
        if (uri) {
            video.src = uri;
            video.dataset.configured = "true";
            video.load();
        } else {
            video.load();
        }
        return true;
    }

    function loadMedia() {
        clearTimeout(state.scrubTimer);
        state.scrubTimer = null;
        state.isScrubbing = false;
        state.scrubWasPlaying = false;
        configuredVideos().forEach((video) => video.pause());
        const sources = assetEntry();
        const event = currentEvent();
        state.pendingSeek = finite(event?.temporal?.start_s, 0);
        const maskedChanged = configureVideo(els.maskedVideo, els.maskedMissing, els.maskedSourceState, sources.masked);
        const revealedChanged = configureVideo(els.revealedVideo, els.revealedMissing, els.revealedSourceState, sources.revealed, sources.revealedPlaceholder);
        els.playbackRate.value = "1";
        if (!maskedChanged && !revealedChanged) seekBoth(state.pendingSeek);
        updateTransport(state.pendingSeek);
        synchronize(true);
    }

    function selectEvent(eventId, load = true) {
        if (!reviewableEvents().some((event) => event.segment_id === eventId)) return;
        state.activeEventId = eventId;
        renderNavigation();
        renderInspector();
        if (load) loadMedia();
    }

    function selectVideo(videoId) {
        if (!state.videoIds.includes(videoId)) return;
        state.activeVideoId = videoId;
        state.activeEventId = reviewableEvents()[0]?.segment_id || null;
        renderNavigation();
        renderInspector();
        loadMedia();
    }

    function moveEvent(delta) {
        const events = reviewableEvents();
        const index = events.findIndex((event) => event.segment_id === state.activeEventId);
        const next = events[index + delta];
        if (next) selectEvent(next.segment_id);
    }

    function moveVideo(delta) {
        const index = state.videoIds.indexOf(state.activeVideoId);
        const nextVideoId = state.videoIds[index + delta];
        if (nextVideoId) selectVideo(nextVideoId);
    }

    function addOption(kind) {
        const event = currentEvent();
        if (!event) return;
        const option = { option_id: uid(kind === "action" ? "act" : "ans"), text: "", entity_ids: [] };
        (kind === "action" ? event.action_options : event.answer_options).push(option);
        renderOptions(kind);
        renderValidation();
        markDirty();
        const list = kind === "action" ? els.actionOptionList : els.answerOptionList;
        list.querySelector(".optionRow:last-child textarea")?.focus();
    }

    function bindOptionList(list) {
        list.addEventListener("input", (event) => {
            if (!event.target.matches("textarea[data-option-text]")) return;
            const row = event.target.closest("[data-option-id]");
            const current = currentEvent();
            const options = row.dataset.kind === "action" ? current.action_options : current.answer_options;
            const option = options.find((item) => item.option_id === row.dataset.optionId);
            if (option) {
                option.text = event.target.value;
                renderValidation();
                markDirty();
            }
        });
        list.addEventListener("change", (event) => {
            if (!event.target.matches("input[type=radio]")) return;
            const row = event.target.closest("[data-option-id]");
            const current = currentEvent();
            const key = row.dataset.kind === "action" ? "action_option_ids" : "answer_option_ids";
            current.gold[key] = [row.dataset.optionId];
            renderValidation();
            markDirty();
        });
        list.addEventListener("click", (event) => {
            const button = event.target.closest("[data-remove-option]");
            if (!button) return;
            const row = button.closest("[data-option-id]");
            const current = currentEvent();
            const optionsKey = row.dataset.kind === "action" ? "action_options" : "answer_options";
            const goldKey = row.dataset.kind === "action" ? "action_option_ids" : "answer_option_ids";
            current[optionsKey] = current[optionsKey].filter((item) => item.option_id !== row.dataset.optionId);
            current.gold[goldKey] = current.gold[goldKey].filter((id) => id !== row.dataset.optionId);
            renderOptions(row.dataset.kind);
            renderValidation();
            markDirty();
        });
    }

    async function saveDataset() {
        clearTimeout(state.saveTimer);
        if (!state.dataset) return;
        if (state.saveInFlight) {
            state.saveAgain = true;
            return;
        }
        state.saveInFlight = true;
        const version = state.changeVersion;
        setSaveState("loading", "Saving");
        state.dataset.updated_at = nowIso();
        state.dataset.revision = finite(state.dataset.revision) + 1;
        const video = currentVideo();
        if (video) video.revision = finite(video.revision) + 1;
        try {
            const response = await fetch(ANNOTATION_API_URL, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(state.dataset),
            });
            if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
            if (state.changeVersion === version) {
                state.dirty = false;
                localStorage.removeItem(ANNOTATION_BACKUP_KEY);
                setSaveState("saved", "Saved");
            }
        } catch (error) {
            setSaveState("error", "Save Failed");
            showToast(`Save failed: ${error.message}`, 5000);
        } finally {
            state.saveInFlight = false;
        }
        if (state.saveAgain || state.changeVersion !== version) {
            state.saveAgain = false;
            return saveDataset();
        }
    }

    async function importAssets(file) {
        const previousAssets = state.assets;
        const previousBaseUrl = state.assetBaseUrl;
        try {
            const payload = normalizeAssets(JSON.parse(await file.text()));
            state.assets = payload;
            state.assetBaseUrl = window.location.href;
            rebuildReviewScope();
            localStorage.setItem(ASSET_CACHE_KEY, JSON.stringify(payload));
            renderNavigation();
            renderInspector();
            loadMedia();
            showToast(`Imported video sources for ${Object.keys(payload.videos).length} videos.`);
        } catch (error) {
            state.assets = previousAssets;
            state.assetBaseUrl = previousBaseUrl;
            rebuildReviewScope();
            showToast(`Invalid video manifest: ${error.message}`, 5000);
        } finally {
            els.assetsFile.value = "";
        }
    }

    function bindEvents() {
        els.videoSelect.addEventListener("change", (event) => selectVideo(event.target.value));
        els.previousVideoButton.addEventListener("click", () => moveVideo(-1));
        els.nextVideoButton.addEventListener("click", () => moveVideo(1));
        els.eventSelect.addEventListener("change", (event) => selectEvent(event.target.value));
        els.previousEventButton.addEventListener("click", () => moveEvent(-1));
        els.nextEventButton.addEventListener("click", () => moveEvent(1));
        els.playButton.addEventListener("click", togglePlayback);
        els.maskedVideo.addEventListener("click", togglePlayback);
        els.revealedVideo.addEventListener("click", togglePlayback);
        els.previousFrameButton.addEventListener("click", () => seekBoth(masterVideo().currentTime - frameStep()));
        els.nextFrameButton.addEventListener("click", () => seekBoth(masterVideo().currentTime + frameStep()));
        els.scrubber.addEventListener("pointerdown", beginScrub);
        els.scrubber.addEventListener("pointerup", (event) => commitScrub(event.currentTarget.value));
        els.scrubber.addEventListener("input", (event) => previewScrub(event.target.value));
        els.scrubber.addEventListener("change", (event) => commitScrub(event.target.value));
        els.scrubber.addEventListener("pointercancel", () => commitScrub());
        els.scrubber.addEventListener("blur", () => commitScrub());
        els.playbackRate.addEventListener("change", (event) => configuredVideos().forEach((video) => { video.playbackRate = Number(event.target.value); }));
        els.jumpEventButton.addEventListener("click", () => seekBoth(currentEvent()?.temporal?.start_s));
        els.jumpAskingButton.addEventListener("click", () => seekBoth(currentEvent()?.windows?.asking?.start_s));
        [els.maskedVideo, els.revealedVideo].forEach((video) => {
            video.addEventListener("loadedmetadata", () => {
                video.currentTime = clamp(state.pendingSeek, 0, finite(video.duration, state.pendingSeek));
                video.playbackRate = Number(els.playbackRate.value);
                synchronize(true);
                updateTransport();
            });
            video.addEventListener("timeupdate", () => {
                if (video === masterVideo()) updateTransport();
            });
            video.addEventListener("ended", () => {
                configuredVideos().forEach((item) => item.pause());
                updateTransport();
            });
            video.addEventListener("error", () => {
                const isMasked = video === els.maskedVideo;
                (isMasked ? els.maskedSourceState : els.revealedSourceState).textContent = "Load Failed";
            });
        });
        els.addActionButton.addEventListener("click", () => addOption("action"));
        els.addAnswerButton.addEventListener("click", () => addOption("answer"));
        bindOptionList(els.actionOptionList);
        bindOptionList(els.answerOptionList);
        els.saveButton.addEventListener("click", saveDataset);
        els.importAssetsButton.addEventListener("click", () => els.assetsFile.click());
        els.assetsFile.addEventListener("change", (event) => {
            const file = event.target.files?.[0];
            if (file) importAssets(file);
        });
        window.addEventListener("beforeunload", (event) => {
            if (!state.dirty) return;
            event.preventDefault();
            event.returnValue = "";
        });
    }

    async function initialize() {
        bindEvents();
        try {
            const [annotations, defaultAssets] = await Promise.all([
                fetchJson(ANNOTATION_API_URL),
                fetchJson(ASSETS_URL, true),
            ]);
            state.dataset = normalizeDataset(annotations);
            let cachedAssets = null;
            try { cachedAssets = JSON.parse(localStorage.getItem(ASSET_CACHE_KEY)); } catch (error) { console.warn(error); }
            state.assets = normalizeAssets(cachedAssets || defaultAssets);
            state.assetBaseUrl = cachedAssets ? window.location.href : ASSETS_URL;
            rebuildReviewScope();
            renderNavigation();
            renderInspector();
            loadMedia();
            els.saveState.title = "Automatically saved to web/03_human_review/human_review_annotations.json";
            setSaveState("saved", "Loaded");
        } catch (error) {
            setSaveState("error", "Load Failed");
            els.datasetSummary.textContent = error.message;
            showToast(error.message, 6000);
        }
    }

    initialize();
}());
