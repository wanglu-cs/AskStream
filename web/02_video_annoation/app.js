(function () {
    "use strict";

    const TRACKED_MANIFEST_URL = "/api/tracked-videos";
    const ANNOTATION_API_URL = "/api/annotations";
    const STORAGE_KEY = "askstream.videoAnnotations.v1";
    const SCHEMA_VERSION = "pvb.video_uncertainty.v6";
    const AUTOSAVE_DELAY_MS = 900;

    const SEGMENT_COLORS = [
        "#167d9a", "#d36a2d", "#3b8557", "#bd4153", "#8a6b18",
        "#4e72b8", "#a64f82", "#31847e", "#7958a6", "#687a31",
    ];

    const ENTITY_COLORS = [
        "#e84d3d", "#168b82", "#e08a21", "#3e70c9",
        "#b94c88", "#56a044", "#7958b6", "#b6612e",
    ];

    const state = {
        manifest: [],
        trackingVideos: {},
        coinDatabase: {},
        mediaMetadata: {},
        dataset: null,
        filteredVideoIds: [],
        activeVideoId: null,
        activeSegmentId: null,
        activeEntityId: null,
        selectedPromptId: null,
        activePanel: "segment",
        videoFilter: "all",
        videoSearch: "",
        tool: "select",
        boxDraft: null,
        polygonDraft: [],
        polygonHover: null,
        selectedVertex: null,
        vertexDrag: null,
        timelineZoom: 1,
        presentedMediaTime: null,
        saveTimer: null,
        saveInFlight: false,
        saveAgain: false,
        dirty: false,
        serverAvailable: false,
        toastTimer: null,
        pointerScrubbingTimeline: false,
        frameCallbackId: null,
    };

    const els = {
        datasetSummary: document.getElementById("datasetSummary"),
        saveState: document.getElementById("saveState"),
        importButton: document.getElementById("importButton"),
        exportButton: document.getElementById("exportButton"),
        saveButton: document.getElementById("saveButton"),
        importFile: document.getElementById("importFile"),
        completeCounter: document.getElementById("completeCounter"),
        progressMeterFill: document.getElementById("progressMeterFill"),
        videoSearch: document.getElementById("videoSearch"),
        videoList: document.getElementById("videoList"),
        videoOrdinal: document.getElementById("videoOrdinal"),
        videoTitle: document.getElementById("videoTitle"),
        videoSubtitle: document.getElementById("videoSubtitle"),
        previousVideoButton: document.getElementById("previousVideoButton"),
        nextVideoButton: document.getElementById("nextVideoButton"),
        completeButton: document.getElementById("completeButton"),
        videoStage: document.getElementById("videoStage"),
        videoPlayer: document.getElementById("videoPlayer"),
        drawCanvas: document.getElementById("drawCanvas"),
        videoLoading: document.getElementById("videoLoading"),
        videoError: document.getElementById("videoError"),
        videoErrorText: document.getElementById("videoErrorText"),
        playButton: document.getElementById("playButton"),
        previousFrameButton: document.getElementById("previousFrameButton"),
        nextFrameButton: document.getElementById("nextFrameButton"),
        currentTimeText: document.getElementById("currentTimeText"),
        frameText: document.getElementById("frameText"),
        fineScrubber: document.getElementById("fineScrubber"),
        playbackRate: document.getElementById("playbackRate"),
        timelineViewport: document.getElementById("timelineViewport"),
        timeline: document.getElementById("timeline"),
        timelineTicks: document.getElementById("timelineTicks"),
        segmentTrack: document.getElementById("segmentTrack"),
        windowTrack: document.getElementById("windowTrack"),
        timelinePlayhead: document.getElementById("timelinePlayhead"),
        segmentLabels: document.getElementById("segmentLabels"),
        zoomOutButton: document.getElementById("zoomOutButton"),
        zoomInButton: document.getElementById("zoomInButton"),
        zoomText: document.getElementById("zoomText"),
        undoPolygonPointButton: document.getElementById("undoPolygonPointButton"),
        deletePolygonPointButton: document.getElementById("deletePolygonPointButton"),
        finishPolygonButton: document.getElementById("finishPolygonButton"),
        cancelDrawingButton: document.getElementById("cancelDrawingButton"),
        activeEntitySelect: document.getElementById("activeEntitySelect"),
        quickAddEntityButton: document.getElementById("quickAddEntityButton"),
        currentFramePromptCount: document.getElementById("currentFramePromptCount"),
        entityTabCount: document.getElementById("entityTabCount"),
        promptTabCount: document.getElementById("promptTabCount"),
        addSegmentButton: document.getElementById("addSegmentButton"),
        deleteSegmentButton: document.getElementById("deleteSegmentButton"),
        segmentSelect: document.getElementById("segmentSelect"),
        segmentLabel: document.getElementById("segmentLabel"),
        segmentStart: document.getElementById("segmentStart"),
        segmentEnd: document.getElementById("segmentEnd"),
        waitingStart: document.getElementById("waitingStart"),
        waitingEnd: document.getElementById("waitingEnd"),
        waitingStartFrame: document.getElementById("waitingStartFrame"),
        waitingEndFrame: document.getElementById("waitingEndFrame"),
        askingStart: document.getElementById("askingStart"),
        askingEnd: document.getElementById("askingEnd"),
        askingStartFrame: document.getElementById("askingStartFrame"),
        askingEndFrame: document.getElementById("askingEndFrame"),
        questionText: document.getElementById("questionText"),
        correctEntityList: document.getElementById("correctEntityList"),
        correctEntityEmptyState: document.getElementById("correctEntityEmptyState"),
        addActionOptionButton: document.getElementById("addActionOptionButton"),
        actionOptionList: document.getElementById("actionOptionList"),
        addAnswerOptionButton: document.getElementById("addAnswerOptionButton"),
        answerOptionList: document.getElementById("answerOptionList"),
        addEntityButton: document.getElementById("addEntityButton"),
        entityList: document.getElementById("entityList"),
        entityEmptyState: document.getElementById("entityEmptyState"),
        deletePromptButton: document.getElementById("deletePromptButton"),
        promptEntityFilter: document.getElementById("promptEntityFilter"),
        promptSegmentFilter: document.getElementById("promptSegmentFilter"),
        promptList: document.getElementById("promptList"),
        promptEmptyState: document.getElementById("promptEmptyState"),
        runValidationButton: document.getElementById("runValidationButton"),
        reviewSummary: document.getElementById("reviewSummary"),
        validationList: document.getElementById("validationList"),
        entityDialog: document.getElementById("entityDialog"),
        entityForm: document.getElementById("entityForm"),
        entityDialogTitle: document.getElementById("entityDialogTitle"),
        entityId: document.getElementById("entityId"),
        entityName: document.getElementById("entityName"),
        entityGroundingText: document.getElementById("entityGroundingText"),
        entityDescription: document.getElementById("entityDescription"),
        entityColorSwatches: document.getElementById("entityColorSwatches"),
        closeEntityDialogButton: document.getElementById("closeEntityDialogButton"),
        cancelEntityButton: document.getElementById("cancelEntityButton"),
        toast: document.getElementById("toast"),
    };

    function nowIso() {
        return new Date().toISOString();
    }

    function uid(prefix) {
        const raw = typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
        return `${prefix}_${raw}`;
    }

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    function finiteNumber(value, fallback = null) {
        if (value === "" || value === null || value === undefined) {
            return fallback;
        }
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function rounded(value, places = 6) {
        const factor = 10 ** places;
        return Math.round(Number(value) * factor) / factor;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function formatTime(seconds, milliseconds = false) {
        const value = Math.max(0, Number(seconds) || 0);
        const whole = Math.floor(value);
        const hours = Math.floor(whole / 3600);
        const minutes = Math.floor((whole % 3600) / 60);
        const secs = whole % 60;
        const base = hours > 0
            ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
            : `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
        if (!milliseconds) {
            return base;
        }
        const ms = Math.floor((value - whole) * 1000);
        return `${base}.${String(ms).padStart(3, "0")}`;
    }

    function parseRate(value, fallback = { num: 30, den: 1 }) {
        if (value && typeof value === "object") {
            const num = finiteNumber(value.num);
            const den = finiteNumber(value.den);
            if (num > 0 && den > 0) {
                return { num, den };
            }
        }
        if (typeof value === "string" && value.includes("/")) {
            const [num, den] = value.split("/").map(Number);
            if (num > 0 && den > 0) {
                return { num, den };
            }
        }
        const number = finiteNumber(value);
        return number > 0 ? { num: number, den: 1 } : fallback;
    }

    function frameRateValue(media) {
        const rate = parseRate(media?.frame_rate, parseRate(media?.display_fps || 30));
        return rate.num / rate.den;
    }

    function secondsToFrame(seconds, media) {
        const value = finiteNumber(seconds);
        const fps = frameRateValue(media);
        if (value === null || !Number.isFinite(fps) || fps <= 0) {
            return null;
        }
        const startTime = finiteNumber(media?.start_time_s, 0);
        return Math.max(0, Math.round((value - startTime) * fps));
    }

    function frameToSeconds(frame, media) {
        const value = finiteNumber(frame);
        const fps = frameRateValue(media);
        if (value === null || !Number.isInteger(value) || value < 0 || !Number.isFinite(fps) || fps <= 0) {
            return null;
        }
        const startTime = finiteNumber(media?.start_time_s, 0);
        return rounded(startTime + value / fps, 6);
    }

    function createWindow(start, end, media) {
        return {
            start_s: finiteNumber(start),
            end_s: finiteNumber(end),
            start_frame: secondsToFrame(start, media),
            end_frame: secondsToFrame(end, media),
        };
    }

    function mediaDuration(videoRecord = currentVideo()) {
        const playerDuration = finiteNumber(els.videoPlayer.duration);
        const recordDuration = finiteNumber(videoRecord?.media?.duration_s, 0);
        return playerDuration > 0 ? playerDuration : Math.max(recordDuration, 0);
    }

    function playbackUri(videoRecord = currentVideo()) {
        return state.trackingVideos[videoRecord?.video_id]?.uri || videoRecord?.media?.uri || "";
    }

    async function fetchJson(url, optional = false) {
        try {
            const response = await fetch(url, { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`${response.status} ${response.statusText}`);
            }
            return await response.json();
        } catch (error) {
            if (optional) {
                return null;
            }
            throw new Error(`Failed to read ${url}: ${error.message}`);
        }
    }

    function mediaUrlFor(item) {
        const id = encodeURIComponent(item.id);
        const filename = encodeURIComponent(item.filename);
        return `/data/COIN/processed/video/${id}/${filename}`;
    }

    function createSegmentFromCoin(videoId, annotation, index, start, end, waitingStart = null, waitingEnd = null, media = null) {
        const segmentId = `${videoId}:seg:${String(index).padStart(3, "0")}`;
        return {
            segment_id: segmentId,
            order: index,
            label: {
                text: annotation.label || "",
                source: "COIN",
                coin_action_id: String(annotation.id ?? ""),
            },
            temporal: { start_s: start, end_s: end },
            entity_ids: [],
            visual_prompt_ids: [],
            windows: {
                waiting: createWindow(waitingStart, waitingEnd, media),
                asking: createWindow(null, null, media),
            },
            question: { text: "", language: "zh-CN" },
            action_options: [],
            answer_options: [],
            gold: { action_option_ids: [], answer_option_ids: [], entity_ids: [] },
            status: "draft",
            created_at: nowIso(),
            updated_at: nowIso(),
        };
    }

    function createBlankSegment(videoId, start, end, order) {
        const segmentId = `${videoId}:seg:manual:${uid("s")}`;
        return {
            segment_id: segmentId,
            order,
            label: { text: "", source: "manual", coin_action_id: null },
            temporal: { start_s: rounded(start), end_s: rounded(end) },
            entity_ids: [],
            visual_prompt_ids: [],
            windows: {
                waiting: createWindow(null, null, null),
                asking: createWindow(null, null, null),
            },
            question: { text: "", language: "zh-CN" },
            action_options: [],
            answer_options: [],
            gold: { action_option_ids: [], answer_option_ids: [], entity_ids: [] },
            status: "draft",
            created_at: nowIso(),
            updated_at: nowIso(),
        };
    }

    function normalizeMediaMetadata(item, coinRecord, metadata) {
        const precise = metadata || {};
        const rate = parseRate(precise.avg_frame_rate || precise.frame_rate || item.fps);
        const realRate = parseRate(precise.r_frame_rate || precise.frame_rate || item.fps);
        const isCfr = precise.is_cfr === true || (
            precise.is_cfr !== false &&
            Math.abs(rate.num / rate.den - realRate.num / realRate.den) < 0.00001
        );
        return {
            uri: mediaUrlFor(item),
            filename: item.filename,
            format_id: String(item.format_id ?? item.formatId ?? ""),
            container: String(item.filename || "").split(".").pop().toLowerCase(),
            size_bytes: finiteNumber(item.size_bytes, finiteNumber(item.sizeBytes, null)),
            duration_s: finiteNumber(precise.duration_s, finiteNumber(item.duration_s, finiteNumber(coinRecord.duration, null))),
            width: finiteNumber(precise.width, finiteNumber(item.width, null)),
            height: finiteNumber(precise.height, finiteNumber(item.height, null)),
            rotation_deg: finiteNumber(precise.rotation_deg, 0),
            display_fps: rate.num / rate.den,
            frame_rate: { num: rate.num, den: rate.den },
            real_frame_rate: { num: realRate.num, den: realRate.den },
            is_cfr: isCfr,
            start_time_s: finiteNumber(precise.start_time_s, 0),
            frame_count: finiteNumber(precise.frame_count, finiteNumber(item.frame_count, null)),
            codec: precise.codec || item.codec || null,
            audio_streams: finiteNumber(precise.audio_streams, finiteNumber(item.audio_streams, 0)),
        };
    }

    function createDataset(manifestData, coinData, metadataData) {
        const createdAt = nowIso();
        const videos = {};
        const database = coinData.database || {};
        const metadataById = metadataData?.videos || metadataData || {};

        for (const item of manifestData.videos || []) {
            const coinRecord = database[item.id] || {};
            const annotations = Array.isArray(coinRecord.annotation) ? coinRecord.annotation : [];
            const media = normalizeMediaMetadata(item, coinRecord, metadataById[item.id]);
            const duration = Math.max(finiteNumber(media.duration_s, finiteNumber(coinRecord.duration, 0)), 0);
            const segments = annotations.map((annotation, index) => {
                const originalStart = finiteNumber(annotation.segment?.[0], 0);
                const nextStart = finiteNumber(annotations[index + 1]?.segment?.[0], duration);
                const start = index === 0 ? 0 : clamp(originalStart, 0, duration);
                const originalEnd = finiteNumber(annotation.segment?.[1], start);
                const end = index === annotations.length - 1
                    ? clamp(originalEnd, start, duration)
                    : clamp(nextStart, start, duration);
                const hasLeadingWait = originalStart > start;
                const waitingStart = hasLeadingWait ? start : null;
                const waitingEnd = hasLeadingWait ? clamp(originalStart, start, end) : null;
                return createSegmentFromCoin(
                    item.id,
                    annotation,
                    index,
                    start,
                    end,
                    waitingStart,
                    waitingEnd,
                    media,
                );
            });
            videos[item.id] = {
                video_id: item.id,
                status: "draft",
                media,
                coin_context: {
                    recipe_type: coinRecord.recipe_type ?? null,
                    class: coinRecord.class || "",
                    domain: coinRecord.domainkey || "",
                    subset: coinRecord.subset || "",
                    source_video_url: coinRecord.video_url || "",
                    review_range_s: {
                        start_s: finiteNumber(coinRecord.start, null),
                        end_s: finiteNumber(coinRecord.end, null),
                    },
                },
                entities: [],
                segments,
                visual_prompts: [],
                revision: 0,
                created_at: createdAt,
                updated_at: createdAt,
                ui_state: { active_segment_id: annotations.length ? `${item.id}:seg:000` : null },
            };
        }

        return {
            schema_version: SCHEMA_VERSION,
            dataset_id: "COIN_local_56_video_information_uncertainty",
            benchmark: "ASK-Stream",
            task: "video_information_uncertainty",
            source_manifest: TRACKED_MANIFEST_URL,
            conventions: {
                time_unit: "seconds",
                time_interval: "[start,end)",
                coordinate_space: "decoded_source_frame_pixels_after_rotation",
                coordinate_origin: "top_left",
                bbox_order: "xyxy",
                normalized_coordinate_range: [0, 1],
                frame_index_base: 0,
                polygon_sam2_conversion: "rasterize_to_2d_boolean_mask",
            },
            videos,
            revision: 0,
            created_at: createdAt,
            updated_at: createdAt,
            ui_state: { last_video_id: manifestData.videos?.[0]?.id || null },
        };
    }

    function normalizeWindow(windowValue, media) {
        let startSeconds = finiteNumber(windowValue?.start_s, null);
        let endSeconds = finiteNumber(windowValue?.end_s, null);
        let startFrame = finiteNumber(windowValue?.start_frame, null);
        let endFrame = finiteNumber(windowValue?.end_frame, null);
        if (startSeconds === null && startFrame !== null) {
            startSeconds = frameToSeconds(startFrame, media);
        }
        if (endSeconds === null && endFrame !== null) {
            endSeconds = frameToSeconds(endFrame, media);
        }
        if (startFrame === null && startSeconds !== null) {
            startFrame = secondsToFrame(startSeconds, media);
        }
        if (endFrame === null && endSeconds !== null) {
            endFrame = secondsToFrame(endSeconds, media);
        }
        return {
            start_s: startSeconds,
            end_s: endSeconds,
            start_frame: startFrame,
            end_frame: endFrame,
        };
    }

    function normalizeSavedSegment(saved, fallback, index, useFreshDefaults, media) {
        const segment = { ...fallback, ...saved };
        segment.order = finiteNumber(saved?.order, index);
        segment.label = { ...fallback.label, ...(saved?.label || {}) };
        segment.temporal = useFreshDefaults
            ? { ...fallback.temporal }
            : { ...fallback.temporal, ...(saved?.temporal || {}) };
        delete segment.provenance_sources;
        segment.entity_ids = Array.isArray(saved?.entity_ids) ? saved.entity_ids : [];
        segment.visual_prompt_ids = Array.isArray(saved?.visual_prompt_ids) ? saved.visual_prompt_ids : [];
        segment.windows = {
            waiting: useFreshDefaults
                ? normalizeWindow(fallback.windows.waiting, media)
                : normalizeWindow(saved?.windows?.waiting, media),
            asking: normalizeWindow(saved?.windows?.asking, media),
        };
        segment.question = { text: "", language: "zh-CN", ...(saved?.question || {}) };
        segment.action_options = Array.isArray(saved?.action_options) ? saved.action_options : [];
        segment.answer_options = Array.isArray(saved?.answer_options) ? saved.answer_options : [];
        segment.gold = {
            action_option_ids: Array.isArray(saved?.gold?.action_option_ids) ? saved.gold.action_option_ids : [],
            answer_option_ids: Array.isArray(saved?.gold?.answer_option_ids) ? saved.gold.answer_option_ids : [],
            entity_ids: Array.isArray(saved?.gold?.entity_ids) ? saved.gold.entity_ids : [],
        };
        return segment;
    }

    function normalizeGoldEntitySelections(dataset) {
        Object.values(dataset?.videos || {}).forEach((video) => {
            (video.segments || []).forEach((segment) => {
                segment.gold = segment.gold && typeof segment.gold === "object" ? segment.gold : {};
                segment.gold.action_option_ids = Array.isArray(segment.gold.action_option_ids) ? segment.gold.action_option_ids : [];
                segment.gold.answer_option_ids = Array.isArray(segment.gold.answer_option_ids) ? segment.gold.answer_option_ids : [];
                segment.gold.entity_ids = Array.isArray(segment.gold.entity_ids) ? segment.gold.entity_ids : [];
            });
        });
        return dataset;
    }

    function reconcileDataset(saved, fresh) {
        if (!saved || typeof saved !== "object" || !saved.videos) {
            return fresh;
        }
        const result = { ...fresh, ...saved, schema_version: SCHEMA_VERSION, videos: {} };
        const migrateLegacyRanges = saved.schema_version !== SCHEMA_VERSION;
        result.conventions = { ...fresh.conventions, ...(saved.conventions || {}) };
        result.ui_state = { ...fresh.ui_state, ...(saved.ui_state || {}) };

        for (const item of state.manifest) {
            const id = item.id;
            const baseVideo = fresh.videos[id];
            const savedVideo = saved.videos[id];
            if (!savedVideo) {
                result.videos[id] = baseVideo;
                continue;
            }
            const video = { ...baseVideo, ...savedVideo };
            video.media = { ...baseVideo.media, ...(savedVideo.media || {}), uri: baseVideo.media.uri };
            video.coin_context = {
                ...baseVideo.coin_context,
                ...(savedVideo.coin_context || {}),
                review_range_s: {
                    ...baseVideo.coin_context.review_range_s,
                    ...(savedVideo.coin_context?.review_range_s || {}),
                },
            };
            video.entities = Array.isArray(savedVideo.entities) ? savedVideo.entities : [];
            video.visual_prompts = Array.isArray(savedVideo.visual_prompts) ? savedVideo.visual_prompts : [];
            if (Array.isArray(savedVideo.segments) && savedVideo.segments.length) {
                video.segments = savedVideo.segments.map((segment, index) => {
                    const baseCandidate = baseVideo.segments.find((candidate) => candidate.segment_id === segment.segment_id);
                    const fallback = baseCandidate || createBlankSegment(id, 0, 1, index);
                    return normalizeSavedSegment(
                        segment,
                        fallback,
                        index,
                        Boolean(migrateLegacyRanges && baseCandidate),
                        video.media,
                    );
                });
            } else {
                video.segments = baseVideo.segments;
            }
            video.ui_state = { ...baseVideo.ui_state, ...(savedVideo.ui_state || {}) };
            result.videos[id] = video;
        }
        return result;
    }

    function readLocalDataset() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (error) {
            console.warn("Failed to read browser annotation cache", error);
            return null;
        }
    }

    function latestDataset(serverDataset, localDataset) {
        const candidates = [serverDataset, localDataset].filter((candidate) => candidate?.videos);
        if (!candidates.length) {
            return null;
        }
        return candidates.sort((a, b) => {
            const aTime = Date.parse(a.updated_at || a.created_at || 0) || 0;
            const bTime = Date.parse(b.updated_at || b.created_at || 0) || 0;
            return bTime - aTime;
        })[0];
    }

    async function initialize() {
        setSaveState("loading", "Loading");
        try {
            const [trackedManifest, serverDataset] = await Promise.all([
                fetchJson(TRACKED_MANIFEST_URL),
                fetchJson(ANNOTATION_API_URL),
            ]);

            if (!serverDataset?.videos || typeof serverDataset.videos !== "object") {
                throw new Error("The annotation JSON does not contain valid videos data.");
            }
            // The shared server JSON is authoritative; browser storage is only a save fallback.
            const saved = serverDataset;
            const sourceDataset = createDataset(trackedManifest, { database: {} }, {});
            const mergedVideos = { ...saved.videos };
            for (const item of trackedManifest.videos || []) {
                const freshVideo = sourceDataset.videos[item.id];
                if (!mergedVideos[item.id]) {
                    mergedVideos[item.id] = freshVideo;
                }
            }
            state.dataset = normalizeGoldEntitySelections({ ...saved, videos: mergedVideos });
            state.serverAvailable = true;
            state.trackingVideos = Object.fromEntries(
                (trackedManifest.videos || []).map((item) => [item.id, item]),
            );
            state.manifest = trackedManifest.videos || [];
            if (!state.manifest.length) {
                state.activeVideoId = null;
                state.activeSegmentId = null;
                state.activeEntityId = null;
                applyVideoFilter(false);
                renderDatasetSummary();
                renderVideoList();
                setSaveState("saved", "Ready");
                return;
            }

            const requestedVideo = state.dataset.ui_state?.last_video_id;
            state.activeVideoId = state.manifest.some((item) => item.id === requestedVideo)
                ? requestedVideo
                : state.manifest[0].id;
            const video = currentVideo();
            state.activeSegmentId = validActiveSegmentId(video, video.ui_state?.active_segment_id);
            state.activeEntityId = video.entities[0]?.entity_id || null;

            applyVideoFilter(false);
            renderDatasetSummary();
            renderVideoList();
            renderCurrentVideo({ loadMedia: true, seekToSegment: true });
            setSaveState("saved", state.serverAvailable ? "Saved" : "Saved in Browser");
        } catch (error) {
            console.error(error);
            setSaveState("error", "Load Failed");
            els.datasetSummary.textContent = error.message;
            showToast(error.message, 6000);
        }
    }

    function currentVideo() {
        return state.dataset?.videos?.[state.activeVideoId] || null;
    }

    function currentSegment() {
        return currentVideo()?.segments?.find((segment) => segment.segment_id === state.activeSegmentId) || null;
    }

    function currentEntity() {
        return currentVideo()?.entities?.find((entity) => entity.entity_id === state.activeEntityId) || null;
    }

    function validActiveSegmentId(video, preferred) {
        if (!video?.segments?.length) {
            return null;
        }
        return video.segments.some((segment) => segment.segment_id === preferred)
            ? preferred
            : video.segments[0].segment_id;
    }

    function setSaveState(saveState, label) {
        els.saveState.dataset.state = saveState;
        els.saveState.textContent = label;
    }

    function markDirty({ renderSummary = true, annotationChange = true } = {}) {
        if (!state.dataset) {
            return;
        }
        const timestamp = nowIso();
        state.dirty = true;
        state.dataset.updated_at = timestamp;
        const video = currentVideo();
        if (video) {
            video.updated_at = timestamp;
            if (annotationChange && video.status === "complete") {
                video.status = "draft";
                delete video.completed_at;
                els.completeButton.classList.remove("completed");
                els.completeButton.textContent = "Mark Complete";
                renderVideoList();
            }
        }
        setSaveState("dirty", "Unsaved");
        if (renderSummary) {
            renderDatasetSummary();
        }
        clearTimeout(state.saveTimer);
        state.saveTimer = setTimeout(() => saveDataset(true), AUTOSAVE_DELAY_MS);
    }

    function browserBackup(snapshot) {
        try {
            localStorage.setItem(STORAGE_KEY, snapshot);
            return true;
        } catch (error) {
            console.warn("Failed to persist browser annotation cache", error);
            return false;
        }
    }

    async function saveDataset(silent = false) {
        if (!state.dataset) {
            return false;
        }
        clearTimeout(state.saveTimer);
        if (state.saveInFlight) {
            state.saveAgain = true;
            return false;
        }

        state.saveInFlight = true;
        state.dataset.updated_at = nowIso();
        state.dataset.revision = finiteNumber(state.dataset.revision, 0) + 1;
        const video = currentVideo();
        if (video) {
            video.revision = finiteNumber(video.revision, 0) + 1;
        }
        const snapshot = JSON.stringify(state.dataset, null, 2);
        const localSaved = browserBackup(snapshot);
        setSaveState("loading", "Saving");

        let serverSaved = false;
        try {
            const response = await fetch(ANNOTATION_API_URL, {
                method: "PUT",
                headers: { "Content-Type": "application/json; charset=utf-8" },
                body: snapshot,
            });
            if (!response.ok) {
                throw new Error(`${response.status} ${response.statusText}`);
            }
            serverSaved = true;
            state.serverAvailable = true;
        } catch (error) {
            console.warn("Server save unavailable", error);
        } finally {
            state.saveInFlight = false;
        }

        if (serverSaved || localSaved) {
            state.dirty = false;
            setSaveState("saved", serverSaved ? "Saved" : "Saved in Browser");
            if (!silent) {
                showToast(serverSaved ? "Annotation data saved in this web directory" : "Annotation data saved in the browser");
            }
        } else {
            setSaveState("error", "Save Failed");
            if (!silent) {
                showToast("Save failed. Export a dataset backup first.", 5000);
            }
        }

        if (state.saveAgain) {
            state.saveAgain = false;
            return saveDataset(true);
        }
        return serverSaved || localSaved;
    }

    function renderDatasetSummary() {
        if (!state.dataset) {
            return;
        }
        const videos = state.manifest.map((item) => state.dataset.videos[item.id]);
        const complete = videos.filter((video) => video.status === "complete").length;
        const segmentCount = videos.reduce((sum, video) => sum + video.segments.length, 0);
        const promptCount = videos.reduce((sum, video) => sum + video.visual_prompts.length, 0);
        els.datasetSummary.textContent = `${videos.length} videos · ${segmentCount} events · ${promptCount} keyframe prompts`;
        els.completeCounter.textContent = `${complete} / ${videos.length}`;
        els.progressMeterFill.style.width = `${videos.length ? (complete / videos.length) * 100 : 0}%`;
    }

    function applyVideoFilter(render = true) {
        const search = state.videoSearch.trim().toLowerCase();
        state.filteredVideoIds = state.manifest
            .map((item) => item.id)
            .filter((id) => {
                const video = state.dataset.videos[id];
                const matchesStatus = state.videoFilter === "all"
                    || (state.videoFilter === "complete" && video.status === "complete")
                    || (state.videoFilter === "draft" && video.status !== "complete");
                if (!matchesStatus) {
                    return false;
                }
                if (!search) {
                    return true;
                }
                const text = [id, video.coin_context.class, video.coin_context.domain]
                    .join(" ")
                    .toLowerCase();
                return text.includes(search);
            });

        if (render) {
            renderVideoList();
        }
    }

    function renderVideoList() {
        if (!state.dataset) {
            return;
        }
        els.videoList.innerHTML = state.filteredVideoIds.map((id) => {
            const video = state.dataset.videos[id];
            const active = id === state.activeVideoId ? " active" : "";
            const complete = video.status === "complete" ? " complete" : "";
            return `
                <button class="videoListItem${active}${complete}" type="button" data-video-id="${escapeHtml(id)}">
                    <strong>${escapeHtml(id)}</strong>
                    <span>${escapeHtml(video.coin_context.class || "Uncategorized")} · ${video.segments.length} segments</span>
                </button>
            `;
        }).join("");
    }

    function renderCurrentVideo({ loadMedia = false, seekToSegment = false } = {}) {
        const video = currentVideo();
        if (!video) {
            return;
        }
        const manifestIndex = state.manifest.findIndex((item) => item.id === video.video_id);
        els.videoOrdinal.textContent = `Video ${manifestIndex + 1} / ${state.manifest.length}`;
        els.videoTitle.textContent = video.video_id;
        els.videoSubtitle.textContent = [video.coin_context.class, video.coin_context.domain]
            .filter(Boolean)
            .join(" · ") || "-";
        els.completeButton.classList.toggle("completed", video.status === "complete");
        els.completeButton.textContent = video.status === "complete" ? "Complete" : "Mark Complete";
        els.previousVideoButton.disabled = manifestIndex <= 0;
        els.nextVideoButton.disabled = manifestIndex >= state.manifest.length - 1;

        renderTimeline();
        renderEntityPicker();
        renderSegmentPanel();
        renderEntityPanel();
        renderPromptPanel();
        renderReviewPanel();
        renderCounts();
        updateTransport();
        renderCanvas();

        if (loadMedia) {
            loadCurrentMedia(seekToSegment);
        }
    }

    function loadCurrentMedia(seekToSegment) {
        const video = currentVideo();
        if (!video) {
            return;
        }
        state.presentedMediaTime = null;
        state.boxDraft = null;
        state.polygonDraft = [];
        state.polygonHover = null;
        state.selectedVertex = null;
        state.vertexDrag = null;
        updatePolygonControls();
        els.videoLoading.hidden = false;
        els.videoError.hidden = true;
        els.videoPlayer.pause();
        els.videoPlayer.src = playbackUri(video);
        els.videoPlayer.load();
        els.videoPlayer.dataset.seekToSegment = seekToSegment ? "true" : "false";
    }

    function switchVideo(videoId, seekToSegment = true) {
        if (!state.dataset?.videos?.[videoId] || videoId === state.activeVideoId) {
            return;
        }
        state.activeVideoId = videoId;
        state.dataset.ui_state.last_video_id = videoId;
        const video = currentVideo();
        state.activeSegmentId = validActiveSegmentId(video, video.ui_state?.active_segment_id);
        state.activeEntityId = video.entities[0]?.entity_id || null;
        state.selectedPromptId = null;
        state.selectedVertex = null;
        state.vertexDrag = null;
        state.timelineZoom = 1;
        state.polygonDraft = [];
        state.polygonHover = null;
        state.boxDraft = null;
        renderVideoList();
        renderCurrentVideo({ loadMedia: true, seekToSegment });
        markDirty({ renderSummary: false, annotationChange: false });
    }

    function moveVideo(delta) {
        const index = state.manifest.findIndex((item) => item.id === state.activeVideoId);
        const next = state.manifest[index + delta];
        if (next) {
            switchVideo(next.id);
        }
    }

    function handleLoadedMetadata() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        els.videoLoading.hidden = true;
        els.videoError.hidden = true;
        const changed = video.media.width !== els.videoPlayer.videoWidth
            || video.media.height !== els.videoPlayer.videoHeight;
        video.media.width = els.videoPlayer.videoWidth;
        video.media.height = els.videoPlayer.videoHeight;
        video.media.duration_s = finiteNumber(els.videoPlayer.duration, video.media.duration_s);
        els.fineScrubber.max = String(mediaDuration(video));
        els.timeline.setAttribute("aria-valuemax", String(mediaDuration(video)));
        resizeCanvas();
        renderTimeline();

        if (els.videoPlayer.dataset.seekToSegment === "true") {
            const segment = currentSegment();
            if (segment) {
                seekVideo(segment.temporal.start_s);
            }
        }
        els.videoPlayer.dataset.seekToSegment = "false";
        if (changed) {
            markDirty({ renderSummary: false, annotationChange: false });
        }
        requestPresentedFrame();
    }

    function requestPresentedFrame() {
        if (typeof els.videoPlayer.requestVideoFrameCallback !== "function") {
            return;
        }
        if (state.frameCallbackId !== null && typeof els.videoPlayer.cancelVideoFrameCallback === "function") {
            els.videoPlayer.cancelVideoFrameCallback(state.frameCallbackId);
        }
        state.frameCallbackId = els.videoPlayer.requestVideoFrameCallback((_now, metadata) => {
            state.frameCallbackId = null;
            state.presentedMediaTime = finiteNumber(metadata.mediaTime, els.videoPlayer.currentTime);
            updateDynamicVideoUI();
            requestPresentedFrame();
        });
    }

    function handleVideoError() {
        els.videoLoading.hidden = true;
        els.videoError.hidden = false;
        const mediaError = els.videoPlayer.error;
        const messages = {
            1: "Playback was aborted.",
            2: "A network error occurred while reading the local video.",
            3: "The browser could not decode this video.",
            4: "The browser does not support this video format.",
        };
        els.videoErrorText.textContent = messages[mediaError?.code] || "Check the local server and video file.";
    }

    function seekVideo(time) {
        const duration = mediaDuration();
        const target = clamp(finiteNumber(time, 0), 0, Math.max(duration - 0.0001, 0));
        if (typeof els.videoPlayer.fastSeek === "function" && Math.abs(target - els.videoPlayer.currentTime) > 2) {
            els.videoPlayer.fastSeek(target);
        } else {
            els.videoPlayer.currentTime = target;
        }
        state.presentedMediaTime = target;
        updateDynamicVideoUI();
    }

    function togglePlayback() {
        if (els.videoPlayer.paused) {
            els.videoPlayer.play().catch((error) => showToast(`Unable to play: ${error.message}`));
        } else {
            els.videoPlayer.pause();
        }
    }

    function stepFrame(direction) {
        els.videoPlayer.pause();
        const rate = frameRateValue(currentVideo()?.media);
        const delta = rate > 0 ? 1 / rate : 1 / 30;
        seekVideo(els.videoPlayer.currentTime + direction * delta);
    }

    function currentFrameInfo() {
        const video = currentVideo();
        const media = video?.media || {};
        const pts = finiteNumber(state.presentedMediaTime, finiteNumber(els.videoPlayer.currentTime, 0));
        const fps = frameRateValue(media);
        const startTime = finiteNumber(media.start_time_s, 0);
        const estimate = Math.max(0, Math.round((pts - startTime) * fps));
        const frameIndex = media.is_cfr && media.frame_rate?.num && media.frame_rate?.den
            ? estimate
            : null;
        return {
            pts_time_s: rounded(pts),
            frame_index: frameIndex,
            frame_index_estimate: estimate,
            frame_index_status: frameIndex === null ? "requires_decode_alignment" : "cfr_pts_derived",
            frame_rate: { ...parseRate(media.frame_rate, parseRate(media.display_fps || 30)) },
        };
    }

    function updateTransport() {
        const frame = currentFrameInfo();
        const current = finiteNumber(els.videoPlayer.currentTime, 0);
        els.currentTimeText.textContent = formatTime(current, true);
        els.frameText.textContent = frame.frame_index === null
            ? `Frame ~${frame.frame_index_estimate}`
            : `Frame ${frame.frame_index}`;
        els.fineScrubber.value = String(current);
        els.playButton.textContent = els.videoPlayer.paused ? "▶" : "Ⅱ";
    }

    function updateDynamicVideoUI() {
        updateTransport();
        updatePlayhead();
        renderCanvas();
        updateCurrentFramePromptCount();
    }

    function renderTimeline() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        const duration = Math.max(mediaDuration(video), 0.001);
        els.timeline.style.width = `${state.timelineZoom * 100}%`;
        els.zoomText.textContent = `${Math.round(state.timelineZoom * 100)}%`;
        els.zoomOutButton.disabled = state.timelineZoom <= 1;
        els.zoomInButton.disabled = state.timelineZoom >= 8;

        const tickCount = state.timelineZoom >= 4 ? 20 : state.timelineZoom >= 2 ? 14 : 9;
        els.timelineTicks.innerHTML = Array.from({ length: tickCount + 1 }, (_, index) => {
            const ratio = index / tickCount;
            return `<div class="timelineTick" style="left:${ratio * 100}%"><span>${formatTime(duration * ratio)}</span></div>`;
        }).join("");

        els.segmentTrack.innerHTML = video.segments.map((segment, index) => {
            const start = clamp(finiteNumber(segment.temporal.start_s, 0), 0, duration);
            const end = clamp(finiteNumber(segment.temporal.end_s, start), start, duration);
            const left = (start / duration) * 100;
            const width = Math.max(((end - start) / duration) * 100, 0.08);
            const color = SEGMENT_COLORS[index % SEGMENT_COLORS.length];
            const active = segment.segment_id === state.activeSegmentId ? " active" : "";
            return `
                <button class="segmentRange${active}" type="button" data-segment-id="${escapeHtml(segment.segment_id)}"
                    style="left:${left}%;width:${width}%;background:${color}" title="${escapeHtml(segment.label.text)}"></button>
                <span class="segmentEndpoint" style="left:${left}%;background:${color}"></span>
                <span class="segmentEndpoint" style="left:${left + width}%;background:${color}"></span>
            `;
        }).join("");

        const segment = currentSegment();
        const windowRows = segment ? [
            ["waiting", segment.windows.waiting],
            ["asking", segment.windows.asking],
        ] : [];
        els.windowTrack.innerHTML = windowRows.map(([name, windowValue]) => {
            const start = finiteNumber(windowValue?.start_s);
            const end = finiteNumber(windowValue?.end_s);
            if (start === null || end === null || end <= start) {
                return "";
            }
            return `<span class="windowRange ${name}" style="left:${clamp(start / duration, 0, 1) * 100}%;width:${clamp((end - start) / duration, 0, 1) * 100}%"></span>`;
        }).join("");

        els.segmentLabels.innerHTML = video.segments.map((item, index) => {
            const active = item.segment_id === state.activeSegmentId ? " active" : "";
            const color = SEGMENT_COLORS[index % SEGMENT_COLORS.length];
            return `
                <button class="segmentLabelButton${active}" type="button" data-segment-id="${escapeHtml(item.segment_id)}">
                    <span class="segmentLabelColor" style="background:${color}"></span>
                    <span class="segmentLabelText">
                        <strong>${escapeHtml(item.label.text || `Segment ${index + 1}`)}</strong>
                        <span>${formatTime(item.temporal.start_s, true)} - ${formatTime(item.temporal.end_s, true)}</span>
                    </span>
                </button>
            `;
        }).join("");
        updatePlayhead();
    }

    function updatePlayhead() {
        const duration = Math.max(mediaDuration(), 0.001);
        const current = clamp(finiteNumber(els.videoPlayer.currentTime, 0), 0, duration);
        els.timelinePlayhead.style.left = `${(current / duration) * 100}%`;
        els.timeline.setAttribute("aria-valuenow", String(current));
    }

    function seekFromTimelineEvent(event) {
        if (event.target.closest("[data-segment-id]")) {
            return;
        }
        const rect = els.timeline.getBoundingClientRect();
        const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
        seekVideo(ratio * mediaDuration());
    }

    function setTimelineZoom(nextZoom) {
        const oldWidth = els.timeline.scrollWidth || 1;
        const oldCenter = els.timelineViewport.scrollLeft + els.timelineViewport.clientWidth / 2;
        const centerRatio = oldCenter / oldWidth;
        state.timelineZoom = clamp(nextZoom, 1, 8);
        renderTimeline();
        requestAnimationFrame(() => {
            els.timelineViewport.scrollLeft = centerRatio * els.timeline.scrollWidth - els.timelineViewport.clientWidth / 2;
        });
    }

    function selectSegment(segmentId, seek = false) {
        const video = currentVideo();
        if (!video?.segments.some((segment) => segment.segment_id === segmentId)) {
            return;
        }
        state.activeSegmentId = segmentId;
        video.ui_state.active_segment_id = segmentId;
        state.selectedPromptId = null;
        state.selectedVertex = null;
        state.vertexDrag = null;
        state.polygonDraft = [];
        state.polygonHover = null;
        state.boxDraft = null;
        renderTimeline();
        renderSegmentPanel();
        renderPromptPanel();
        renderReviewPanel();
        renderCanvas();
        if (seek) {
            seekVideo(currentSegment().temporal.start_s);
        }
        markDirty({ renderSummary: false, annotationChange: false });
    }

    function setInputValue(element, value) {
        element.value = value === null || value === undefined ? "" : String(value);
    }

    function renderSegmentPanel() {
        const video = currentVideo();
        const segment = currentSegment();
        const disabled = !segment;
        [
            els.segmentLabel, els.segmentStart, els.segmentEnd,
            els.waitingStart, els.waitingEnd, els.askingStart, els.askingEnd,
            els.waitingStartFrame, els.waitingEndFrame, els.askingStartFrame, els.askingEndFrame,
            els.questionText,
        ].forEach((element) => { element.disabled = disabled; });
        els.deleteSegmentButton.disabled = disabled;
        els.addActionOptionButton.disabled = disabled;
        els.addAnswerOptionButton.disabled = disabled;

        els.segmentSelect.innerHTML = video.segments.map((item, index) => `
            <option value="${escapeHtml(item.segment_id)}" ${item.segment_id === state.activeSegmentId ? "selected" : ""}>
                ${index + 1}. ${escapeHtml(item.label.text || "Unnamed Event")}
            </option>
        `).join("");

        if (!segment) {
            els.segmentLabel.value = "";
            [
                els.segmentStart, els.segmentEnd,
                els.waitingStart, els.waitingEnd, els.waitingStartFrame, els.waitingEndFrame,
                els.askingStart, els.askingEnd, els.askingStartFrame, els.askingEndFrame,
            ].forEach((element) => setInputValue(element, null));
            els.questionText.value = "";
            els.correctEntityList.innerHTML = "";
            els.correctEntityEmptyState.hidden = false;
            els.actionOptionList.innerHTML = "";
            els.answerOptionList.innerHTML = "";
            return;
        }

        els.segmentLabel.value = segment.label.text || "";
        setInputValue(els.segmentStart, segment.temporal.start_s);
        setInputValue(els.segmentEnd, segment.temporal.end_s);
        setInputValue(els.waitingStart, segment.windows.waiting.start_s);
        setInputValue(els.waitingEnd, segment.windows.waiting.end_s);
        setInputValue(els.waitingStartFrame, segment.windows.waiting.start_frame);
        setInputValue(els.waitingEndFrame, segment.windows.waiting.end_frame);
        setInputValue(els.askingStart, segment.windows.asking.start_s);
        setInputValue(els.askingEnd, segment.windows.asking.end_s);
        setInputValue(els.askingStartFrame, segment.windows.asking.start_frame);
        setInputValue(els.askingEndFrame, segment.windows.asking.end_frame);
        els.questionText.value = segment.question.text || "";
        renderCorrectEntitySelector();
        renderOptions("action");
        renderOptions("answer");
    }

    function renderCorrectEntitySelector() {
        const video = currentVideo();
        const segment = currentSegment();
        const entities = video?.entities || [];
        els.correctEntityEmptyState.hidden = Boolean(entities.length);
        if (!segment || !entities.length) {
            els.correctEntityList.innerHTML = "";
            return;
        }
        const selectedIds = new Set(segment.gold?.entity_ids || []);
        els.correctEntityList.innerHTML = entities.map((entity) => `
            <label class="correctEntityChoice">
                <input type="checkbox" value="${escapeHtml(entity.entity_id)}" ${selectedIds.has(entity.entity_id) ? "checked" : ""}>
                <span class="entityColor" style="background:${escapeHtml(entity.color)}"></span>
                <span>${escapeHtml(entity.display_name)}</span>
            </label>
        `).join("");
    }

    function optionLetter(index) {
        return index < 26 ? String.fromCharCode(65 + index) : String(index + 1);
    }

    function renderOptions(kind) {
        const segment = currentSegment();
        const list = kind === "action" ? els.actionOptionList : els.answerOptionList;
        if (!segment) {
            list.innerHTML = "";
            return;
        }
        const options = kind === "action" ? segment.action_options : segment.answer_options;
        const goldKey = kind === "action" ? "action_option_ids" : "answer_option_ids";
        list.innerHTML = options.map((option, index) => `
            <div class="optionItem" data-option-id="${escapeHtml(option.option_id)}" data-option-kind="${kind}">
                <input type="radio" name="${kind}-correct-option" value="${escapeHtml(option.option_id)}"
                    ${segment.gold[goldKey].includes(option.option_id) ? "checked" : ""} aria-label="Mark as correct option">
                <span class="optionLetter">${optionLetter(index)}</span>
                <textarea rows="2" data-option-text="${escapeHtml(option.option_id)}" placeholder="Option text">${escapeHtml(option.text || "")}</textarea>
                <button class="removeRowButton" type="button" data-remove-option="${escapeHtml(option.option_id)}" data-option-kind="${kind}" title="Delete option" aria-label="Delete option">×</button>
            </div>
        `).join("");
    }

    function addOption(kind) {
        const segment = currentSegment();
        if (!segment) {
            return;
        }
        const option = { option_id: uid(kind === "action" ? "act" : "ans"), text: "", entity_ids: [] };
        if (kind === "action") {
            segment.action_options.push(option);
        } else {
            segment.answer_options.push(option);
        }
        renderOptions(kind);
        markDirty();
        const list = kind === "action" ? els.actionOptionList : els.answerOptionList;
        list.querySelector(".optionItem:last-child textarea")?.focus();
    }

    function addSegment() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        const start = finiteNumber(els.videoPlayer.currentTime, 0);
        const end = Math.min(start + 5, Math.max(mediaDuration(video), start + 0.1));
        const segment = createBlankSegment(video.video_id, start, end, video.segments.length);
        video.segments.push(segment);
        selectSegment(segment.segment_id, false);
        els.segmentLabel.focus();
        renderDatasetSummary();
        showToast("Event added");
    }

    function deleteSegment() {
        const video = currentVideo();
        const segment = currentSegment();
        if (!video || !segment) {
            return;
        }
        const linkedCount = video.visual_prompts.filter((prompt) => prompt.segment_id === segment.segment_id).length;
        const message = linkedCount
            ? `Deleting this event will also delete ${linkedCount} keyframe prompts. Continue?`
            : "Delete the current event?";
        if (!window.confirm(message)) {
            return;
        }
        video.visual_prompts = video.visual_prompts.filter((prompt) => prompt.segment_id !== segment.segment_id);
        video.segments = video.segments.filter((item) => item.segment_id !== segment.segment_id);
        video.segments.forEach((item, index) => { item.order = index; });
        state.activeSegmentId = video.segments[0]?.segment_id || null;
        video.ui_state.active_segment_id = state.activeSegmentId;
        state.selectedPromptId = null;
        state.selectedVertex = null;
        renderCurrentVideo();
        renderDatasetSummary();
        markDirty();
        showToast("Event deleted");
    }

    function renderEntityPicker() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        if (!video.entities.some((entity) => entity.entity_id === state.activeEntityId)) {
            state.activeEntityId = video.entities[0]?.entity_id || null;
        }
        els.activeEntitySelect.innerHTML = video.entities.length
            ? video.entities.map((entity) => `<option value="${escapeHtml(entity.entity_id)}" ${entity.entity_id === state.activeEntityId ? "selected" : ""}>${escapeHtml(entity.display_name)}</option>`).join("")
            : `<option value="">No registered entities</option>`;
        els.activeEntitySelect.disabled = !video.entities.length;
    }

    function renderEntityPanel() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        els.entityEmptyState.hidden = video.entities.length > 0;
        els.entityList.innerHTML = video.entities.map((entity) => {
            const promptCount = video.visual_prompts.filter((prompt) => prompt.entity_id === entity.entity_id).length;
            const selected = entity.entity_id === state.activeEntityId ? " selected" : "";
            return `
                <div class="entityItem${selected}" data-entity-id="${escapeHtml(entity.entity_id)}">
                    <span class="entityColor" style="background:${escapeHtml(entity.color)}"></span>
                    <div class="entityDetails">
                        <strong>${escapeHtml(entity.display_name)}</strong>
                        <span>SAM2 object ${entity.sam2_object_id} · ${promptCount} keyframes${entity.grounding_text ? ` · ${escapeHtml(entity.grounding_text)}` : ""}</span>
                    </div>
                    <div class="entityActions">
                        <button type="button" data-use-entity="${escapeHtml(entity.entity_id)}">Select</button>
                        <button type="button" data-edit-entity="${escapeHtml(entity.entity_id)}">Edit</button>
                        <button type="button" data-delete-entity="${escapeHtml(entity.entity_id)}">Delete</button>
                    </div>
                </div>
            `;
        }).join("");
    }

    function renderEntityColorSwatches(selectedColor) {
        els.entityColorSwatches.innerHTML = ENTITY_COLORS.map((color, index) => `
            <label class="colorSwatchInput" title="${color}">
                <input type="radio" name="entityColor" value="${color}" ${(selectedColor || ENTITY_COLORS[0]) === color ? "checked" : ""}>
                <span style="background:${color}"></span>
            </label>
        `).join("");
    }

    function openEntityDialog(entityId = null) {
        const video = currentVideo();
        const entity = video?.entities.find((item) => item.entity_id === entityId) || null;
        els.entityDialogTitle.textContent = entity ? "Edit Entity" : "Register Entity";
        els.entityId.value = entity?.entity_id || "";
        els.entityName.value = entity?.display_name || "";
        els.entityGroundingText.value = entity?.grounding_text || "";
        els.entityDescription.value = entity?.description || "";
        const defaultColor = entity?.color || ENTITY_COLORS[video?.entities.length % ENTITY_COLORS.length] || ENTITY_COLORS[0];
        renderEntityColorSwatches(defaultColor);
        els.entityDialog.showModal();
        requestAnimationFrame(() => els.entityName.focus());
    }

    function saveEntityFromDialog() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        const name = els.entityName.value.trim();
        if (!name) {
            els.entityName.focus();
            return;
        }
        const chosenColor = els.entityColorSwatches.querySelector("input:checked")?.value || ENTITY_COLORS[0];
        const existing = video.entities.find((entity) => entity.entity_id === els.entityId.value);
        if (existing) {
            existing.display_name = name;
            existing.grounding_text = els.entityGroundingText.value.trim() || name;
            existing.description = els.entityDescription.value.trim();
            existing.color = chosenColor;
            existing.updated_at = nowIso();
            state.activeEntityId = existing.entity_id;
        } else {
            const usedIds = new Set(video.entities.map((entity) => finiteNumber(entity.sam2_object_id, 0)));
            let objectId = 1;
            while (usedIds.has(objectId)) {
                objectId += 1;
            }
            const entity = {
                entity_id: uid("ent"),
                display_name: name,
                grounding_text: els.entityGroundingText.value.trim() || name,
                description: els.entityDescription.value.trim(),
                color: chosenColor,
                sam2_object_id: objectId,
                created_at: nowIso(),
                updated_at: nowIso(),
            };
            video.entities.push(entity);
            state.activeEntityId = entity.entity_id;
        }
        els.entityDialog.close();
        renderEntityPicker();
        renderEntityPanel();
        renderSegmentPanel();
        renderPromptPanel();
        renderCanvas();
        renderCounts();
        markDirty();
    }

    function deleteEntity(entityId) {
        const video = currentVideo();
        const entity = video?.entities.find((item) => item.entity_id === entityId);
        if (!video || !entity) {
            return;
        }
        const count = video.visual_prompts.filter((prompt) => prompt.entity_id === entityId).length;
        const message = count
            ? `Deleting "${entity.display_name}" will also delete ${count} keyframe prompts. Continue?`
            : `Delete entity "${entity.display_name}"?`;
        if (!window.confirm(message)) {
            return;
        }
        const removedPromptIds = new Set(video.visual_prompts.filter((prompt) => prompt.entity_id === entityId).map((prompt) => prompt.prompt_id));
        video.entities = video.entities.filter((item) => item.entity_id !== entityId);
        video.visual_prompts = video.visual_prompts.filter((prompt) => prompt.entity_id !== entityId);
        video.segments.forEach((segment) => {
            segment.entity_ids = segment.entity_ids.filter((id) => id !== entityId);
            segment.gold.entity_ids = segment.gold.entity_ids.filter((id) => id !== entityId);
            segment.visual_prompt_ids = segment.visual_prompt_ids.filter((id) => !removedPromptIds.has(id));
        });
        state.activeEntityId = video.entities[0]?.entity_id || null;
        state.selectedPromptId = null;
        state.selectedVertex = null;
        renderCurrentVideo();
        markDirty();
    }

    function selectEntity(entityId) {
        if (!currentVideo()?.entities.some((entity) => entity.entity_id === entityId)) {
            return;
        }
        state.activeEntityId = entityId;
        els.activeEntitySelect.value = entityId;
        renderEntityPanel();
        renderCanvas();
    }

    function renderCounts() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        els.entityTabCount.textContent = String(video.entities.length);
        els.promptTabCount.textContent = String(video.visual_prompts.length);
        updateCurrentFramePromptCount();
    }

    function promptIsAtCurrentFrame(prompt) {
        const frame = currentFrameInfo();
        if (prompt.frame_index !== null && prompt.frame_index !== undefined && frame.frame_index !== null) {
            return prompt.frame_index === frame.frame_index;
        }
        const tolerance = 0.55 / Math.max(frameRateValue(currentVideo()?.media), 1);
        return Math.abs(finiteNumber(prompt.pts_time_s, -9999) - frame.pts_time_s) <= tolerance;
    }

    function updateCurrentFramePromptCount() {
        const video = currentVideo();
        const count = video ? video.visual_prompts.filter(promptIsAtCurrentFrame).length : 0;
        els.currentFramePromptCount.textContent = String(count);
    }

    function renderPromptPanel() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        const previousEntityFilter = els.promptEntityFilter.value || "all";
        const previousSegmentFilter = els.promptSegmentFilter.value || "all";
        els.promptEntityFilter.innerHTML = `<option value="all">All Entities</option>${video.entities.map((entity) => `<option value="${escapeHtml(entity.entity_id)}">${escapeHtml(entity.display_name)}</option>`).join("")}`;
        els.promptSegmentFilter.innerHTML = `<option value="all">All Events</option>${video.segments.map((segment, index) => `<option value="${escapeHtml(segment.segment_id)}">${index + 1}. ${escapeHtml(segment.label.text || "Unnamed Event")}</option>`).join("")}`;
        els.promptEntityFilter.value = video.entities.some((entity) => entity.entity_id === previousEntityFilter) ? previousEntityFilter : "all";
        els.promptSegmentFilter.value = video.segments.some((segment) => segment.segment_id === previousSegmentFilter) ? previousSegmentFilter : "all";

        const prompts = video.visual_prompts
            .filter((prompt) => els.promptEntityFilter.value === "all" || prompt.entity_id === els.promptEntityFilter.value)
            .filter((prompt) => els.promptSegmentFilter.value === "all" || prompt.segment_id === els.promptSegmentFilter.value)
            .sort((a, b) => finiteNumber(a.pts_time_s, 0) - finiteNumber(b.pts_time_s, 0));
        els.promptEmptyState.hidden = prompts.length > 0;
        els.deletePromptButton.disabled = !state.selectedPromptId;
        els.promptList.innerHTML = prompts.map((prompt) => {
            const entity = video.entities.find((item) => item.entity_id === prompt.entity_id);
            const segmentIndex = video.segments.findIndex((item) => item.segment_id === prompt.segment_id);
            const active = prompt.prompt_id === state.selectedPromptId ? " active" : "";
            const shapeLabel = prompt.geometry?.kind === "polygon" ? "Polygon" : "BBox";
            const frameLabel = prompt.frame_index === null || prompt.frame_index === undefined
                ? `~${prompt.frame_index_estimate}`
                : String(prompt.frame_index);
            return `
                <button class="promptItem${active}" type="button" data-prompt-id="${escapeHtml(prompt.prompt_id)}">
                    <span class="promptItemColor" style="background:${escapeHtml(entity?.color || "#64717a")}"></span>
                    <span class="promptTimestamp">
                        <strong>${formatTime(prompt.pts_time_s, true)}</strong>
                        <span>Frame ${frameLabel}</span>
                    </span>
                    <span class="promptItemMeta">
                        <strong>${escapeHtml(entity?.display_name || "Unknown Entity")}</strong>
                        <span>${shapeLabel} · Segment ${segmentIndex + 1}</span>
                    </span>
                </button>
            `;
        }).join("");
    }

    function selectPrompt(promptId, seek = true) {
        const prompt = currentVideo()?.visual_prompts.find((item) => item.prompt_id === promptId);
        if (!prompt) {
            return;
        }
        state.selectedPromptId = promptId;
        state.selectedVertex = null;
        state.activeEntityId = prompt.entity_id;
        if (currentVideo().segments.some((segment) => segment.segment_id === prompt.segment_id)) {
            state.activeSegmentId = prompt.segment_id;
        }
        if (seek) {
            seekVideo(prompt.pts_time_s);
        }
        renderEntityPicker();
        renderEntityPanel();
        renderTimeline();
        renderSegmentPanel();
        renderPromptPanel();
        renderCanvas();
    }

    function deletePrompt(promptId = state.selectedPromptId) {
        const video = currentVideo();
        if (!video || !promptId) {
            return;
        }
        const exists = video.visual_prompts.some((prompt) => prompt.prompt_id === promptId);
        if (!exists) {
            return;
        }
        video.visual_prompts = video.visual_prompts.filter((prompt) => prompt.prompt_id !== promptId);
        video.segments.forEach((segment) => {
            segment.visual_prompt_ids = segment.visual_prompt_ids.filter((id) => id !== promptId);
            const linkedEntities = new Set(video.visual_prompts.filter((prompt) => prompt.segment_id === segment.segment_id).map((prompt) => prompt.entity_id));
            segment.entity_ids = segment.entity_ids.filter((id) => linkedEntities.has(id));
        });
        state.selectedPromptId = null;
        state.selectedVertex = null;
        renderPromptPanel();
        renderEntityPanel();
        renderCounts();
        renderReviewPanel();
        renderCanvas();
        renderDatasetSummary();
        markDirty();
        showToast("Keyframe prompt deleted");
    }

    function projection() {
        const videoWidth = finiteNumber(els.videoPlayer.videoWidth, finiteNumber(currentVideo()?.media?.width, 0));
        const videoHeight = finiteNumber(els.videoPlayer.videoHeight, finiteNumber(currentVideo()?.media?.height, 0));
        const canvasWidth = els.drawCanvas.clientWidth;
        const canvasHeight = els.drawCanvas.clientHeight;
        if (!videoWidth || !videoHeight || !canvasWidth || !canvasHeight) {
            return null;
        }
        const scale = Math.min(canvasWidth / videoWidth, canvasHeight / videoHeight);
        const width = videoWidth * scale;
        const height = videoHeight * scale;
        return {
            videoWidth,
            videoHeight,
            canvasWidth,
            canvasHeight,
            scale,
            offsetX: (canvasWidth - width) / 2,
            offsetY: (canvasHeight - height) / 2,
            width,
            height,
        };
    }

    function resizeCanvas() {
        const dpr = Math.max(window.devicePixelRatio || 1, 1);
        const width = Math.max(1, Math.round(els.drawCanvas.clientWidth * dpr));
        const height = Math.max(1, Math.round(els.drawCanvas.clientHeight * dpr));
        if (els.drawCanvas.width !== width || els.drawCanvas.height !== height) {
            els.drawCanvas.width = width;
            els.drawCanvas.height = height;
        }
        renderCanvas();
    }

    function canvasPointFromEvent(event) {
        const rect = els.drawCanvas.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    function canvasToMedia(point) {
        const map = projection();
        if (!map) {
            return null;
        }
        const x = (point.x - map.offsetX) / map.scale;
        const y = (point.y - map.offsetY) / map.scale;
        if (x < 0 || y < 0 || x > map.videoWidth || y > map.videoHeight) {
            return null;
        }
        return { x: clamp(x, 0, map.videoWidth), y: clamp(y, 0, map.videoHeight) };
    }

    function mediaToCanvas(point, map = projection()) {
        if (!map) {
            return { x: 0, y: 0 };
        }
        return {
            x: map.offsetX + point.x * map.scale,
            y: map.offsetY + point.y * map.scale,
        };
    }

    function rgba(hex, alpha) {
        const value = hex.replace("#", "");
        const full = value.length === 3 ? value.split("").map((char) => char + char).join("") : value;
        const number = Number.parseInt(full, 16);
        const red = (number >> 16) & 255;
        const green = (number >> 8) & 255;
        const blue = number & 255;
        return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
    }

    function renderCanvas() {
        const canvas = els.drawCanvas;
        const context = canvas.getContext("2d");
        const dpr = Math.max(window.devicePixelRatio || 1, 1);
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
        context.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
        const map = projection();
        const video = currentVideo();
        if (!map || !video) {
            return;
        }

        const prompts = video.visual_prompts.filter(promptIsAtCurrentFrame);
        if (state.selectedVertex && !prompts.some((prompt) => prompt.prompt_id === state.selectedVertex.promptId)) {
            state.selectedVertex = null;
        }
        updatePolygonControls();
        for (const prompt of prompts) {
            const entity = video.entities.find((item) => item.entity_id === prompt.entity_id);
            drawGeometry(context, prompt.geometry, entity?.color || "#e84d3d", entity?.display_name || "Entity", prompt.prompt_id === state.selectedPromptId, map, prompt.prompt_id);
        }

        const entity = currentEntity();
        const draftColor = entity?.color || ENTITY_COLORS[0];
        if (state.boxDraft?.start && state.boxDraft?.end) {
            const x1 = Math.min(state.boxDraft.start.x, state.boxDraft.end.x);
            const y1 = Math.min(state.boxDraft.start.y, state.boxDraft.end.y);
            const x2 = Math.max(state.boxDraft.start.x, state.boxDraft.end.x);
            const y2 = Math.max(state.boxDraft.start.y, state.boxDraft.end.y);
            drawGeometry(context, { kind: "bbox", bbox_xyxy_px: [x1, y1, x2, y2] }, draftColor, entity?.display_name || "Entity", true, map);
        }
        if (state.polygonDraft.length) {
            drawPolygonDraft(context, state.polygonDraft, draftColor, map, state.polygonHover);
        }
    }

    function drawGeometry(context, geometry, color, label, selected, map, promptId = null) {
        const bbox = geometry?.bbox_xyxy_px;
        if (!Array.isArray(bbox) || bbox.length !== 4) {
            return;
        }
        context.save();
        context.lineJoin = "round";
        context.lineCap = "round";
        context.lineWidth = selected ? 3 : 2;
        context.strokeStyle = color;
        context.fillStyle = rgba(color, selected ? 0.18 : 0.11);

        if (geometry.kind === "polygon" && Array.isArray(geometry.polygon_xy_px)) {
            const points = geometry.polygon_xy_px.map(([x, y]) => mediaToCanvas({ x, y }, map));
            if (points.length >= 3) {
                context.beginPath();
                context.moveTo(points[0].x, points[0].y);
                points.slice(1).forEach((point) => context.lineTo(point.x, point.y));
                context.closePath();
                context.fill();
                context.stroke();
                points.forEach((point, index) => {
                    const vertexSelected = state.selectedVertex?.promptId === promptId
                        && state.selectedVertex.vertexIndex === index;
                    context.beginPath();
                    context.arc(point.x, point.y, vertexSelected ? 7 : selected ? 5 : 3, 0, Math.PI * 2);
                    context.fillStyle = vertexSelected ? "#b33a32" : "#fff";
                    context.fill();
                    context.strokeStyle = vertexSelected ? "#fff" : color;
                    context.lineWidth = vertexSelected ? 3 : 2;
                    context.stroke();
                });
            }
        } else {
            const topLeft = mediaToCanvas({ x: bbox[0], y: bbox[1] }, map);
            const bottomRight = mediaToCanvas({ x: bbox[2], y: bbox[3] }, map);
            context.fillRect(topLeft.x, topLeft.y, bottomRight.x - topLeft.x, bottomRight.y - topLeft.y);
            context.strokeRect(topLeft.x, topLeft.y, bottomRight.x - topLeft.x, bottomRight.y - topLeft.y);
        }

        const anchor = mediaToCanvas({ x: bbox[0], y: bbox[1] }, map);
        context.font = "700 12px Segoe UI, Microsoft YaHei, sans-serif";
        const textWidth = Math.min(context.measureText(label).width + 12, 220);
        const labelY = Math.max(anchor.y - 23, map.offsetY + 2);
        context.fillStyle = color;
        context.fillRect(anchor.x, labelY, textWidth, 21);
        context.fillStyle = "#fff";
        context.fillText(label, anchor.x + 6, labelY + 15, textWidth - 10);
        context.restore();
    }

    function drawPolygonDraft(context, points, color, map, hoverPoint) {
        const canvasPoints = points.map((point) => mediaToCanvas(point, map));
        const hover = hoverPoint ? mediaToCanvas(hoverPoint, map) : null;
        context.save();
        context.lineWidth = 2;
        context.strokeStyle = color;
        context.fillStyle = rgba(color, 0.13);
        context.beginPath();
        context.moveTo(canvasPoints[0].x, canvasPoints[0].y);
        canvasPoints.slice(1).forEach((point) => context.lineTo(point.x, point.y));
        if (hover) {
            context.lineTo(hover.x, hover.y);
        }
        context.stroke();
        canvasPoints.forEach((point, index) => {
            context.beginPath();
            context.arc(point.x, point.y, index === 0 ? 7 : 4, 0, Math.PI * 2);
            context.fillStyle = index === 0 ? color : "#fff";
            context.fill();
            context.stroke();
        });
        context.restore();
    }

    function geometryFromBBox(bbox) {
        const map = projection();
        if (!map) {
            return null;
        }
        const normalized = [
            bbox[0] / map.videoWidth,
            bbox[1] / map.videoHeight,
            bbox[2] / map.videoWidth,
            bbox[3] / map.videoHeight,
        ].map((value) => rounded(value, 8));
        return {
            kind: "bbox",
            bbox_xyxy_px: bbox.map((value) => rounded(value, 3)),
            bbox_xyxy_norm: normalized,
        };
    }

    function geometryFromPolygon(points) {
        const map = projection();
        if (!map || points.length < 3) {
            return null;
        }
        const xs = points.map((point) => point.x);
        const ys = points.map((point) => point.y);
        const bbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
        return {
            kind: "polygon",
            polygon_xy_px: points.map((point) => [rounded(point.x, 3), rounded(point.y, 3)]),
            polygon_xy_norm: points.map((point) => [rounded(point.x / map.videoWidth, 8), rounded(point.y / map.videoHeight, 8)]),
            bbox_xyxy_px: bbox.map((value) => rounded(value, 3)),
            bbox_xyxy_norm: [
                bbox[0] / map.videoWidth,
                bbox[1] / map.videoHeight,
                bbox[2] / map.videoWidth,
                bbox[3] / map.videoHeight,
            ].map((value) => rounded(value, 8)),
            bbox_source: "polygon_envelope",
        };
    }

    function saveVisualPrompt(geometry) {
        const video = currentVideo();
        const segment = currentSegment();
        const entity = currentEntity();
        const map = projection();
        if (!video || !segment) {
            showToast("Select or add an event first");
            return;
        }
        if (!entity) {
            showToast("Register and select an entity first");
            openEntityDialog();
            return;
        }
        if (!geometry || !map) {
            return;
        }
        const frame = currentFrameInfo();
        const sameFrame = video.visual_prompts.find((prompt) =>
            prompt.segment_id === segment.segment_id
            && prompt.entity_id === entity.entity_id
            && ((frame.frame_index !== null && prompt.frame_index === frame.frame_index)
                || (frame.frame_index === null && Math.abs(prompt.pts_time_s - frame.pts_time_s) < 0.5 / frameRateValue(video.media)))
        );
        const promptId = sameFrame?.prompt_id || uid("vp");
        const prompt = {
            prompt_id: promptId,
            segment_id: segment.segment_id,
            entity_id: entity.entity_id,
            sam2_object_id: entity.sam2_object_id,
            frame_index: frame.frame_index,
            frame_index_estimate: frame.frame_index_estimate,
            frame_index_status: frame.frame_index_status,
            pts_time_s: frame.pts_time_s,
            frame_rate: frame.frame_rate,
            frame_size: { width: map.videoWidth, height: map.videoHeight },
            geometry,
            created_at: sameFrame?.created_at || nowIso(),
            updated_at: nowIso(),
        };
        if (sameFrame) {
            Object.assign(sameFrame, prompt);
        } else {
            video.visual_prompts.push(prompt);
            segment.visual_prompt_ids.push(promptId);
            if (!segment.entity_ids.includes(entity.entity_id)) {
                segment.entity_ids.push(entity.entity_id);
            }
        }
        state.selectedPromptId = promptId;
        state.selectedVertex = null;
        renderPromptPanel();
        renderEntityPanel();
        renderCounts();
        renderReviewPanel();
        renderCanvas();
        renderDatasetSummary();
        markDirty();
        showToast(sameFrame ? "Updated the current entity's keyframe prompt" : "Keyframe prompt saved");
    }

    function setTool(tool) {
        state.tool = tool;
        state.boxDraft = null;
        state.polygonDraft = [];
        state.polygonHover = null;
        state.vertexDrag = null;
        if (tool !== "select") {
            state.selectedVertex = null;
        }
        updatePolygonControls();
        els.drawCanvas.dataset.tool = tool;
        document.querySelectorAll(".toolButton[data-tool]").forEach((button) => {
            button.classList.toggle("active", button.dataset.tool === tool);
        });
        renderCanvas();
    }

    function cancelDrawing(deleteSelection = false) {
        const hasDraft = Boolean(state.boxDraft || state.polygonDraft.length);
        state.boxDraft = null;
        state.polygonDraft = [];
        state.polygonHover = null;
        state.vertexDrag = null;
        updatePolygonControls();
        renderCanvas();
        if (deleteSelection && !hasDraft && state.selectedPromptId) {
            deletePrompt(state.selectedPromptId);
        }
    }

    function updatePolygonControls() {
        els.undoPolygonPointButton.disabled = state.polygonDraft.length === 0;
        els.finishPolygonButton.disabled = state.polygonDraft.length < 3;
        els.deletePolygonPointButton.disabled = !state.selectedVertex;
    }

    function mediaPointDistanceInCanvas(first, second) {
        const map = projection();
        if (!map) {
            return Number.POSITIVE_INFINITY;
        }
        return Math.hypot(first.x - second.x, first.y - second.y) * map.scale;
    }

    function undoPolygonPoint() {
        if (!state.polygonDraft.length) {
            return;
        }
        state.polygonDraft.pop();
        updatePolygonControls();
        renderCanvas();
    }

    function finishPolygon() {
        if (state.polygonDraft.length < 3) {
            showToast("A polygon requires at least 3 vertices");
            return;
        }
        const points = state.polygonDraft.filter((point, index, all) =>
            index === 0 || mediaPointDistanceInCanvas(point, all[index - 1]) > 2
        );
        if (points.length < 3) {
            showToast("A polygon requires at least 3 distinct vertices");
            return;
        }
        const geometry = geometryFromPolygon(points);
        state.polygonDraft = [];
        state.polygonHover = null;
        updatePolygonControls();
        saveVisualPrompt(geometry);
    }

    function polygonVertexHit(mediaPoint) {
        const prompts = currentVideo()?.visual_prompts.filter(promptIsAtCurrentFrame) || [];
        for (const prompt of prompts.slice().reverse()) {
            const points = prompt.geometry?.polygon_xy_px;
            if (!Array.isArray(points)) {
                continue;
            }
            for (let index = points.length - 1; index >= 0; index -= 1) {
                const point = { x: points[index][0], y: points[index][1] };
                if (mediaPointDistanceInCanvas(mediaPoint, point) <= 11) {
                    return { prompt, vertexIndex: index };
                }
            }
        }
        return null;
    }

    function applyPromptSelection(prompt) {
        state.selectedPromptId = prompt?.prompt_id || null;
        if (!prompt) {
            state.selectedVertex = null;
            renderPromptPanel();
            return;
        }
        state.activeEntityId = prompt.entity_id;
        state.activeSegmentId = prompt.segment_id;
        renderEntityPicker();
        renderEntityPanel();
        renderPromptPanel();
        renderTimeline();
        renderSegmentPanel();
    }

    function deleteSelectedPolygonPoint() {
        const selection = state.selectedVertex;
        const prompt = currentVideo()?.visual_prompts.find((item) => item.prompt_id === selection?.promptId);
        const points = prompt?.geometry?.polygon_xy_px;
        if (!selection || !prompt || !Array.isArray(points)) {
            state.selectedVertex = null;
            updatePolygonControls();
            return;
        }
        if (points.length <= 3) {
            showToast("A polygon must retain at least 3 vertices");
            return;
        }
        const nextPoints = points
            .filter((_point, index) => index !== selection.vertexIndex)
            .map(([x, y]) => ({ x, y }));
        prompt.geometry = geometryFromPolygon(nextPoints);
        prompt.updated_at = nowIso();
        state.selectedVertex = null;
        renderPromptPanel();
        renderReviewPanel();
        renderCanvas();
        markDirty();
        showToast("Vertex deleted");
    }

    function pointerDownOnCanvas(event) {
        if (event.button !== 0) {
            return;
        }
        const mediaPoint = canvasToMedia(canvasPointFromEvent(event));
        if (!mediaPoint) {
            return;
        }
        if (state.tool === "select") {
            const vertexHit = polygonVertexHit(mediaPoint);
            if (vertexHit) {
                applyPromptSelection(vertexHit.prompt);
                state.selectedVertex = {
                    promptId: vertexHit.prompt.prompt_id,
                    vertexIndex: vertexHit.vertexIndex,
                };
                state.vertexDrag = {
                    promptId: vertexHit.prompt.prompt_id,
                    vertexIndex: vertexHit.vertexIndex,
                    moved: false,
                };
                els.drawCanvas.setPointerCapture(event.pointerId);
                updatePolygonControls();
                renderCanvas();
                return;
            }
            const hit = hitTestPrompt(mediaPoint);
            state.selectedVertex = null;
            applyPromptSelection(hit);
            updatePolygonControls();
            renderCanvas();
            return;
        }
        if (!currentEntity()) {
            showToast("Register and select an entity first");
            openEntityDialog();
            return;
        }
        if (state.tool === "box") {
            state.boxDraft = { start: mediaPoint, end: mediaPoint };
            els.drawCanvas.setPointerCapture(event.pointerId);
            renderCanvas();
        } else if (state.tool === "polygon") {
            if (state.polygonDraft.length >= 3
                && mediaPointDistanceInCanvas(mediaPoint, state.polygonDraft[0]) <= 12) {
                finishPolygon();
                return;
            }
            state.polygonDraft.push(mediaPoint);
            state.polygonHover = mediaPoint;
            updatePolygonControls();
            renderCanvas();
        }
    }

    function pointerMoveOnCanvas(event) {
        const mediaPoint = canvasToMedia(canvasPointFromEvent(event));
        if (!mediaPoint) {
            return;
        }
        if (state.vertexDrag) {
            const prompt = currentVideo()?.visual_prompts.find((item) => item.prompt_id === state.vertexDrag.promptId);
            const points = prompt?.geometry?.polygon_xy_px;
            if (prompt && Array.isArray(points)) {
                const nextPoints = points.map(([x, y], index) =>
                    index === state.vertexDrag.vertexIndex ? mediaPoint : { x, y }
                );
                prompt.geometry = geometryFromPolygon(nextPoints);
                state.vertexDrag.moved = true;
                renderCanvas();
            }
            return;
        }
        if (state.tool === "box" && state.boxDraft) {
            state.boxDraft.end = mediaPoint;
            renderCanvas();
        } else if (state.tool === "polygon") {
            state.polygonHover = mediaPoint;
            renderCanvas();
        }
    }

    function pointerUpOnCanvas(event) {
        if (state.vertexDrag) {
            if (els.drawCanvas.hasPointerCapture(event.pointerId)) {
                els.drawCanvas.releasePointerCapture(event.pointerId);
            }
            const prompt = currentVideo()?.visual_prompts.find((item) => item.prompt_id === state.vertexDrag.promptId);
            if (prompt && state.vertexDrag.moved) {
                prompt.updated_at = nowIso();
                renderPromptPanel();
                renderReviewPanel();
                markDirty();
            }
            state.vertexDrag = null;
            renderCanvas();
            return;
        }
        if (state.tool !== "box" || !state.boxDraft) {
            return;
        }
        if (els.drawCanvas.hasPointerCapture(event.pointerId)) {
            els.drawCanvas.releasePointerCapture(event.pointerId);
        }
        const { start, end } = state.boxDraft;
        state.boxDraft = null;
        const bbox = [
            Math.min(start.x, end.x),
            Math.min(start.y, end.y),
            Math.max(start.x, end.x),
            Math.max(start.y, end.y),
        ];
        if (bbox[2] - bbox[0] < 3 || bbox[3] - bbox[1] < 3) {
            showToast("The bounding box is too small");
            renderCanvas();
            return;
        }
        saveVisualPrompt(geometryFromBBox(bbox));
    }

    function pointInPolygon(point, polygon) {
        let inside = false;
        for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
            const xi = polygon[i][0];
            const yi = polygon[i][1];
            const xj = polygon[j][0];
            const yj = polygon[j][1];
            const intersect = ((yi > point.y) !== (yj > point.y))
                && (point.x < ((xj - xi) * (point.y - yi)) / ((yj - yi) || Number.EPSILON) + xi);
            if (intersect) {
                inside = !inside;
            }
        }
        return inside;
    }

    function hitTestPrompt(point) {
        const prompts = currentVideo()?.visual_prompts.filter(promptIsAtCurrentFrame) || [];
        for (const prompt of prompts.slice().reverse()) {
            const geometry = prompt.geometry;
            if (geometry.kind === "polygon" && Array.isArray(geometry.polygon_xy_px)) {
                if (pointInPolygon(point, geometry.polygon_xy_px)) {
                    return prompt;
                }
            } else if (Array.isArray(geometry.bbox_xyxy_px)) {
                const [x1, y1, x2, y2] = geometry.bbox_xyxy_px;
                if (point.x >= x1 && point.x <= x2 && point.y >= y1 && point.y <= y2) {
                    return prompt;
                }
            }
        }
        return null;
    }

    function setInspectorPanel(panelName) {
        state.activePanel = panelName;
        document.querySelectorAll(".inspectorTab").forEach((tab) => {
            tab.classList.toggle("active", tab.dataset.panel === panelName);
            tab.setAttribute("aria-selected", tab.dataset.panel === panelName ? "true" : "false");
        });
        document.querySelectorAll("[data-panel-content]").forEach((panel) => {
            panel.classList.toggle("active", panel.dataset.panelContent === panelName);
        });
        if (panelName === "review") {
            renderReviewPanel();
        }
    }

    function validateVideo(video = currentVideo()) {
        const issues = [];
        if (!video) {
            return issues;
        }
        const duration = finiteNumber(video.media.duration_s, 0);
        const entityIds = new Set(video.entities.map((entity) => entity.entity_id));
        const promptIds = new Set(video.visual_prompts.map((prompt) => prompt.prompt_id));

        video.segments.forEach((segment, index) => {
            const name = `Segment ${index + 1}`;
            const start = finiteNumber(segment.temporal.start_s);
            const end = finiteNumber(segment.temporal.end_s);
            if (!segment.label.text?.trim()) {
                issues.push({ severity: "error", title: `${name} is missing a label`, detail: "The event must have an identifiable action or state name." });
            }
            if (start === null || end === null || start < 0 || end <= start || (duration > 0 && end > duration + 0.15)) {
                issues.push({ severity: "error", title: `${name} has an invalid time range`, detail: "Start and end times must be within the video, and the end must be later than the start." });
            }
            if (!segment.question.text?.trim()) {
                issues.push({ severity: "warning", title: `${name} has no question`, detail: "The current event does not have a benchmark question." });
            }
            if (segment.action_options.filter((option) => option.text?.trim()).length < 2) {
                issues.push({ severity: "warning", title: `${name} has too few action options`, detail: "Provide at least two valid action options." });
            }
            if (segment.answer_options.filter((option) => option.text?.trim()).length < 2) {
                issues.push({ severity: "warning", title: `${name} has too few answer options`, detail: "Provide at least two valid answer options." });
            }
            const hasCorrectAction = segment.gold.action_option_ids.some((optionId) =>
                segment.action_options.some((option) => option.option_id === optionId && option.text?.trim()),
            );
            const hasCorrectAnswer = segment.gold.answer_option_ids.some((optionId) =>
                segment.answer_options.some((option) => option.option_id === optionId && option.text?.trim()),
            );
            if (!hasCorrectAction) {
                issues.push({ severity: "error", title: `${name} has no correct action`, detail: "Select a correct action option before marking the video complete." });
            }
            if (!hasCorrectAnswer) {
                issues.push({ severity: "error", title: `${name} has no correct answer`, detail: "Select a correct answer option before marking the video complete." });
            }
            if (!segment.gold.entity_ids.length) {
                issues.push({ severity: "error", title: `${name} has no correct entity`, detail: "Each event must have at least one correct entity; multiple selections are allowed." });
            }
            validateWindow(segment.windows.waiting, "Waiting", name, start, end, issues, video.media);
            validateWindow(segment.windows.asking, "Asking", name, start, end, issues, video.media);
            const waitingEnd = finiteNumber(segment.windows.waiting?.end_s);
            const askingStart = finiteNumber(segment.windows.asking?.start_s);
            if (waitingEnd !== null && askingStart !== null && waitingEnd > askingStart + 0.001) {
                issues.push({
                    severity: "error",
                    title: `${name} has overlapping Waiting and Asking windows`,
                    detail: `Waiting ends at ${formatTime(waitingEnd, true)}, while Asking starts at ${formatTime(askingStart, true)}.`,
                });
            }
            if (!segment.visual_prompt_ids.some((id) => promptIds.has(id))) {
                issues.push({ severity: "warning", title: `${name} has no keyframe prompts`, detail: "The current event has no linked box or polygon visual prompts." });
            }
            segment.entity_ids.forEach((id) => {
                if (!entityIds.has(id)) {
                    issues.push({ severity: "error", title: `${name} references a missing entity`, detail: id });
                }
            });
            segment.gold.entity_ids.forEach((id) => {
                if (!entityIds.has(id)) {
                    issues.push({ severity: "error", title: `${name} references a missing correct entity`, detail: id });
                }
            });
        });

        video.visual_prompts.forEach((prompt) => {
            const entity = video.entities.find((item) => item.entity_id === prompt.entity_id);
            const segment = video.segments.find((item) => item.segment_id === prompt.segment_id);
            if (!entity || !segment) {
                issues.push({ severity: "error", title: "Invalid keyframe prompt reference", detail: prompt.prompt_id });
                return;
            }
            if (prompt.pts_time_s < segment.temporal.start_s - 0.1 || prompt.pts_time_s > segment.temporal.end_s + 0.1) {
                issues.push({ severity: "warning", title: `Keyframe for "${entity.display_name}" is outside the event range`, detail: `${formatTime(prompt.pts_time_s, true)} · ${segment.label.text}` });
            }
            if (!geometryIsValid(prompt.geometry, prompt.frame_size)) {
                issues.push({ severity: "error", title: "Invalid keyframe coordinates", detail: prompt.prompt_id });
            }
        });

        const orderedSegments = video.segments
            .map((segment, index) => ({
                index,
                start: finiteNumber(segment.temporal.start_s),
                end: finiteNumber(segment.temporal.end_s),
            }))
            .filter((segment) => segment.start !== null && segment.end !== null)
            .sort((first, second) => first.start - second.start);
        if (orderedSegments.length && orderedSegments[0].start > 0.001) {
            issues.push({
                severity: "warning",
                title: "Unassigned interval at the beginning of the video",
                detail: `0:00.000 - ${formatTime(orderedSegments[0].start, true)}`,
            });
        }
        for (let index = 1; index < orderedSegments.length; index += 1) {
            const previous = orderedSegments[index - 1];
            const current = orderedSegments[index];
            const delta = current.start - previous.end;
            if (delta < -0.001) {
                issues.push({
                    severity: "error",
                    title: `Segment ${previous.index + 1} overlaps Segment ${current.index + 1}`,
                    detail: `Overlap: ${Math.abs(delta).toFixed(3)} seconds.`,
                });
            } else if (delta > 0.001) {
                issues.push({
                    severity: "warning",
                    title: `Gap between Segment ${previous.index + 1} and Segment ${current.index + 1}`,
                    detail: `${formatTime(previous.end, true)} - ${formatTime(current.start, true)}`,
                });
            }
        }

        if (!issues.length) {
            issues.push({ severity: "pass", title: "Current video passed validation", detail: "No structural or reference issues were found." });
        }
        return issues;
    }

    function validateWindow(windowValue, label, segmentName, segmentStart, segmentEnd, issues, media) {
        const start = finiteNumber(windowValue?.start_s);
        const end = finiteNumber(windowValue?.end_s);
        const startFrame = finiteNumber(windowValue?.start_frame);
        const endFrame = finiteNumber(windowValue?.end_frame);
        if (start === null && end === null && startFrame === null && endFrame === null) {
            return;
        }
        if (start === null || end === null || startFrame === null || endFrame === null || end <= start) {
            issues.push({ severity: "error", title: `${segmentName} has an invalid ${label} window`, detail: "Time and frame ranges must be complete, and the end must be later than the start." });
            return;
        }
        if (!Number.isInteger(startFrame) || !Number.isInteger(endFrame) || startFrame < 0 || endFrame <= startFrame) {
            issues.push({ severity: "error", title: `${segmentName} has an invalid ${label} frame range`, detail: "Frame numbers must be non-negative integers, and End Frame must be greater than Start Frame." });
        }
        const expectedStartFrame = secondsToFrame(start, media);
        const expectedEndFrame = secondsToFrame(end, media);
        if (Math.abs(startFrame - expectedStartFrame) > 1 || Math.abs(endFrame - expectedEndFrame) > 1) {
            issues.push({ severity: "error", title: `${segmentName} has inconsistent ${label} times and frame numbers`, detail: `Expected approximately Frame ${expectedStartFrame} - ${expectedEndFrame}.` });
        }
        if (segmentStart !== null && segmentEnd !== null && (start < segmentStart - 0.1 || end > segmentEnd + 0.1)) {
            issues.push({ severity: "error", title: `${segmentName} has a ${label} window outside the event range`, detail: `${formatTime(start, true)} - ${formatTime(end, true)}` });
        }
    }

    function geometryIsValid(geometry, frameSize) {
        const bbox = geometry?.bbox_xyxy_px;
        const width = finiteNumber(frameSize?.width, 0);
        const height = finiteNumber(frameSize?.height, 0);
        if (!Array.isArray(bbox) || bbox.length !== 4 || !width || !height) {
            return false;
        }
        const [x1, y1, x2, y2] = bbox.map(Number);
        if (![x1, y1, x2, y2].every(Number.isFinite) || x1 < 0 || y1 < 0 || x2 <= x1 || y2 <= y1 || x2 > width + 0.5 || y2 > height + 0.5) {
            return false;
        }
        if (geometry.kind === "polygon") {
            return Array.isArray(geometry.polygon_xy_px) && geometry.polygon_xy_px.length >= 3;
        }
        return geometry.kind === "bbox";
    }

    function renderReviewPanel() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        const issues = validateVideo(video);
        const errors = issues.filter((item) => item.severity === "error").length;
        const warnings = issues.filter((item) => item.severity === "warning").length;
        const coveredSegments = video.segments.filter((segment) => segment.visual_prompt_ids.length).length;
        els.reviewSummary.innerHTML = `
            <div class="reviewMetric"><strong>${errors}</strong><span>Errors</span></div>
            <div class="reviewMetric"><strong>${warnings}</strong><span>Warnings</span></div>
            <div class="reviewMetric"><strong>${coveredSegments}/${video.segments.length}</strong><span>Visual Evidence Coverage</span></div>
        `;
        els.validationList.innerHTML = issues.map((item) => `
            <div class="validationItem ${item.severity}">
                <strong>${item.severity === "error" ? "×" : item.severity === "warning" ? "!" : "✓"}</strong>
                <div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail)}</span></div>
            </div>
        `).join("");
    }

    function toggleComplete() {
        const video = currentVideo();
        if (!video) {
            return;
        }
        if (video.status === "complete") {
            video.status = "draft";
            renderCurrentVideo();
            renderVideoList();
            markDirty({ annotationChange: false });
            showToast("Restored to In Progress");
            return;
        }
        const errors = validateVideo(video).filter((item) => item.severity === "error");
        if (errors.length) {
            setInspectorPanel("review");
            showToast(`${errors.length} errors still need attention`, 4000);
            return;
        }
        video.status = "complete";
        video.completed_at = nowIso();
        renderCurrentVideo();
        renderVideoList();
        markDirty({ annotationChange: false });
        showToast("Current video marked complete");
    }

    function downloadJson(filename, data) {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    function exportDataset() {
        if (!state.dataset) {
            return;
        }
        state.dataset.updated_at = nowIso();
        downloadJson("video_uncertainty_annotations.json", state.dataset);
        showToast("Complete annotation dataset exported");
    }

    async function importDataset(file) {
        try {
            const imported = JSON.parse(await file.text());
            if (!imported || typeof imported !== "object" || !imported.videos) {
                throw new Error("The file is not a valid annotation dataset");
            }
            const knownIds = new Set(state.manifest.map((item) => item.id));
            const importedIds = Object.keys(imported.videos);
            if (!importedIds.some((id) => knownIds.has(id))) {
                throw new Error("The imported file contains none of the current tracked video IDs");
            }
            if (!window.confirm("Importing will replace the annotation state on this page. Continue?")) {
                return;
            }
            state.dataset = normalizeGoldEntitySelections({
                ...state.dataset,
                ...imported,
                videos: { ...state.dataset.videos, ...imported.videos },
            });
            const requestedVideo = state.dataset.ui_state?.last_video_id;
            state.activeVideoId = knownIds.has(requestedVideo)
                ? requestedVideo
                : state.manifest[0].id;
            state.activeSegmentId = validActiveSegmentId(currentVideo(), currentVideo().ui_state?.active_segment_id);
            state.activeEntityId = currentVideo().entities[0]?.entity_id || null;
            state.selectedPromptId = null;
            state.selectedVertex = null;
            applyVideoFilter(false);
            renderDatasetSummary();
            renderVideoList();
            renderCurrentVideo({ loadMedia: true, seekToSegment: true });
            markDirty({ annotationChange: false });
            await saveDataset(true);
            showToast("Annotation dataset imported");
        } catch (error) {
            showToast(`Import failed: ${error.message}`, 5000);
        } finally {
            els.importFile.value = "";
        }
    }

    function showToast(message, duration = 2400) {
        els.toast.textContent = message;
        els.toast.classList.add("show");
        clearTimeout(state.toastTimer);
        state.toastTimer = setTimeout(() => els.toast.classList.remove("show"), duration);
    }

    function updateSegmentTextField(field, value) {
        const segment = currentSegment();
        if (!segment) {
            return;
        }
        if (field === "label") {
            segment.label.text = value;
        } else if (field === "question") {
            segment.question.text = value;
        }
        segment.updated_at = nowIso();
        markDirty();
    }

    function updateSegmentTimeField(field, value) {
        const segment = currentSegment();
        if (!segment) {
            return;
        }
        const number = finiteNumber(value, null);
        const directFields = { segmentStart: "start_s", segmentEnd: "end_s" };
        if (directFields[field]) {
            segment.temporal[directFields[field]] = number;
        } else {
            const match = field.match(/^(waiting|asking)(Start|End)(Frame)?$/);
            if (match) {
                const windowValue = segment.windows[match[1]];
                const endpoint = match[2] === "Start" ? "start" : "end";
                const isFrame = Boolean(match[3]);
                if (isFrame) {
                    const frame = number === null ? null : Math.max(0, Math.round(number));
                    windowValue[`${endpoint}_frame`] = frame;
                    windowValue[`${endpoint}_s`] = frameToSeconds(frame, currentVideo()?.media);
                    setInputValue(els[`${match[1]}${match[2]}`], windowValue[`${endpoint}_s`]);
                    setInputValue(els[field], frame);
                } else {
                    windowValue[`${endpoint}_s`] = number;
                    windowValue[`${endpoint}_frame`] = secondsToFrame(number, currentVideo()?.media);
                    setInputValue(els[`${match[1]}${match[2]}Frame`], windowValue[`${endpoint}_frame`]);
                }
            }
        }
        segment.updated_at = nowIso();
        renderTimeline();
        renderReviewPanel();
        markDirty();
    }

    function bindEvents() {
        els.videoList.addEventListener("click", (event) => {
            const button = event.target.closest("[data-video-id]");
            if (button) {
                switchVideo(button.dataset.videoId);
            }
        });
        els.videoSearch.addEventListener("input", (event) => {
            state.videoSearch = event.target.value;
            applyVideoFilter();
        });
        document.querySelectorAll(".statusFilter").forEach((button) => {
            button.addEventListener("click", () => {
                state.videoFilter = button.dataset.filter;
                document.querySelectorAll(".statusFilter").forEach((item) => item.classList.toggle("active", item === button));
                applyVideoFilter();
            });
        });
        els.previousVideoButton.addEventListener("click", () => moveVideo(-1));
        els.nextVideoButton.addEventListener("click", () => moveVideo(1));
        els.completeButton.addEventListener("click", toggleComplete);

        els.videoPlayer.addEventListener("loadedmetadata", handleLoadedMetadata);
        els.videoPlayer.addEventListener("error", handleVideoError);
        els.videoPlayer.addEventListener("timeupdate", updateDynamicVideoUI);
        els.videoPlayer.addEventListener("seeked", updateDynamicVideoUI);
        els.videoPlayer.addEventListener("play", updateTransport);
        els.videoPlayer.addEventListener("pause", updateTransport);
        els.videoPlayer.addEventListener("ended", updateTransport);
        els.playButton.addEventListener("click", togglePlayback);
        els.previousFrameButton.addEventListener("click", () => stepFrame(-1));
        els.nextFrameButton.addEventListener("click", () => stepFrame(1));
        els.fineScrubber.addEventListener("input", (event) => seekVideo(event.target.value));
        els.playbackRate.addEventListener("change", (event) => { els.videoPlayer.playbackRate = Number(event.target.value); });

        els.timeline.addEventListener("pointerdown", (event) => {
            if (event.target.closest("[data-segment-id]")) {
                return;
            }
            state.pointerScrubbingTimeline = true;
            els.timeline.setPointerCapture(event.pointerId);
            seekFromTimelineEvent(event);
        });
        els.timeline.addEventListener("pointermove", (event) => {
            if (state.pointerScrubbingTimeline) {
                seekFromTimelineEvent(event);
            }
        });
        els.timeline.addEventListener("pointerup", (event) => {
            state.pointerScrubbingTimeline = false;
            if (els.timeline.hasPointerCapture(event.pointerId)) {
                els.timeline.releasePointerCapture(event.pointerId);
            }
        });
        els.segmentTrack.addEventListener("click", (event) => {
            const button = event.target.closest("[data-segment-id]");
            if (button) {
                selectSegment(button.dataset.segmentId, true);
            }
        });
        els.segmentLabels.addEventListener("click", (event) => {
            const button = event.target.closest("[data-segment-id]");
            if (button) {
                selectSegment(button.dataset.segmentId, true);
            }
        });
        els.zoomOutButton.addEventListener("click", () => setTimelineZoom(state.timelineZoom / 2));
        els.zoomInButton.addEventListener("click", () => setTimelineZoom(state.timelineZoom * 2));

        document.querySelectorAll(".toolButton[data-tool]").forEach((button) => {
            button.addEventListener("click", () => setTool(button.dataset.tool));
        });
        els.undoPolygonPointButton.addEventListener("click", undoPolygonPoint);
        els.deletePolygonPointButton.addEventListener("click", deleteSelectedPolygonPoint);
        els.finishPolygonButton.addEventListener("click", finishPolygon);
        els.cancelDrawingButton.addEventListener("click", () => cancelDrawing(true));
        els.drawCanvas.addEventListener("pointerdown", pointerDownOnCanvas);
        els.drawCanvas.addEventListener("pointermove", pointerMoveOnCanvas);
        els.drawCanvas.addEventListener("pointerup", pointerUpOnCanvas);
        els.drawCanvas.addEventListener("pointercancel", pointerUpOnCanvas);
        els.drawCanvas.addEventListener("pointerleave", () => {
            if (state.tool === "polygon" && !state.vertexDrag) {
                state.polygonHover = null;
                renderCanvas();
            }
        });
        els.drawCanvas.addEventListener("contextmenu", (event) => {
            const mediaPoint = canvasToMedia(canvasPointFromEvent(event));
            const hit = mediaPoint ? polygonVertexHit(mediaPoint) : null;
            if (!hit) {
                return;
            }
            event.preventDefault();
            applyPromptSelection(hit.prompt);
            state.selectedVertex = { promptId: hit.prompt.prompt_id, vertexIndex: hit.vertexIndex };
            deleteSelectedPolygonPoint();
        });
        els.drawCanvas.addEventListener("dblclick", (event) => {
            if (state.tool === "polygon" && state.polygonDraft.length >= 3) {
                event.preventDefault();
                finishPolygon();
            }
        });

        els.activeEntitySelect.addEventListener("change", (event) => selectEntity(event.target.value));
        els.quickAddEntityButton.addEventListener("click", () => openEntityDialog());
        els.addEntityButton.addEventListener("click", () => openEntityDialog());
        els.entityList.addEventListener("click", (event) => {
            const useButton = event.target.closest("[data-use-entity]");
            const editButton = event.target.closest("[data-edit-entity]");
            const deleteButton = event.target.closest("[data-delete-entity]");
            if (useButton) {
                selectEntity(useButton.dataset.useEntity);
            } else if (editButton) {
                openEntityDialog(editButton.dataset.editEntity);
            } else if (deleteButton) {
                deleteEntity(deleteButton.dataset.deleteEntity);
            }
        });
        els.entityForm.addEventListener("submit", (event) => {
            event.preventDefault();
            saveEntityFromDialog();
        });
        els.closeEntityDialogButton.addEventListener("click", () => els.entityDialog.close());
        els.cancelEntityButton.addEventListener("click", () => els.entityDialog.close());

        document.querySelectorAll(".inspectorTab").forEach((tab) => {
            tab.addEventListener("click", () => setInspectorPanel(tab.dataset.panel));
        });
        els.segmentSelect.addEventListener("change", (event) => selectSegment(event.target.value, true));
        els.addSegmentButton.addEventListener("click", addSegment);
        els.deleteSegmentButton.addEventListener("click", deleteSegment);
        els.segmentLabel.addEventListener("input", (event) => {
            updateSegmentTextField("label", event.target.value);
            const option = els.segmentSelect.selectedOptions[0];
            if (option) {
                const index = currentVideo().segments.findIndex((segment) => segment.segment_id === state.activeSegmentId);
                option.textContent = `${index + 1}. ${event.target.value || "Unnamed Event"}`;
            }
            renderTimeline();
            renderVideoList();
        });
        els.questionText.addEventListener("input", (event) => updateSegmentTextField("question", event.target.value));
        els.correctEntityList.addEventListener("change", (event) => {
            if (!event.target.matches("input[type=checkbox]")) {
                return;
            }
            const segment = currentSegment();
            segment.gold.entity_ids = Array.from(
                els.correctEntityList.querySelectorAll("input[type=checkbox]:checked"),
                (input) => input.value,
            );
            renderReviewPanel();
            markDirty();
        });
        [
            [els.segmentStart, "segmentStart"], [els.segmentEnd, "segmentEnd"],
            [els.waitingStart, "waitingStart"], [els.waitingEnd, "waitingEnd"],
            [els.waitingStartFrame, "waitingStartFrame"], [els.waitingEndFrame, "waitingEndFrame"],
            [els.askingStart, "askingStart"], [els.askingEnd, "askingEnd"],
            [els.askingStartFrame, "askingStartFrame"], [els.askingEndFrame, "askingEndFrame"],
        ].forEach(([element, field]) => element.addEventListener("change", (event) => updateSegmentTimeField(field, event.target.value)));
        document.querySelectorAll("[data-use-current]").forEach((button) => {
            button.addEventListener("click", () => {
                const field = button.dataset.useCurrent;
                const input = els[field];
                input.value = field.endsWith("Frame")
                    ? (currentFrameInfo().frame_index ?? currentFrameInfo().frame_index_estimate)
                    : rounded(els.videoPlayer.currentTime, 3);
                updateSegmentTimeField(field, input.value);
            });
        });

        els.addActionOptionButton.addEventListener("click", () => addOption("action"));
        els.addAnswerOptionButton.addEventListener("click", () => addOption("answer"));
        [els.actionOptionList, els.answerOptionList].forEach((list) => {
            list.addEventListener("input", (event) => {
                const item = event.target.closest("[data-option-id]");
                if (!item || !event.target.matches("textarea")) {
                    return;
                }
                const segment = currentSegment();
                const options = item.dataset.optionKind === "action" ? segment.action_options : segment.answer_options;
                const option = options.find((entry) => entry.option_id === item.dataset.optionId);
                if (option) {
                    option.text = event.target.value;
                    markDirty();
                }
            });
            list.addEventListener("change", (event) => {
                if (!event.target.matches("input[type=radio]")) {
                    return;
                }
                const item = event.target.closest("[data-option-id]");
                const segment = currentSegment();
                if (item.dataset.optionKind === "action") {
                    segment.gold.action_option_ids = [item.dataset.optionId];
                } else {
                    segment.gold.answer_option_ids = [item.dataset.optionId];
                }
                renderReviewPanel();
                markDirty();
            });
            list.addEventListener("click", (event) => {
                const button = event.target.closest("[data-remove-option]");
                if (!button) {
                    return;
                }
                const segment = currentSegment();
                const kind = button.dataset.optionKind;
                const id = button.dataset.removeOption;
                if (kind === "action") {
                    segment.action_options = segment.action_options.filter((option) => option.option_id !== id);
                    segment.gold.action_option_ids = segment.gold.action_option_ids.filter((optionId) => optionId !== id);
                } else {
                    segment.answer_options = segment.answer_options.filter((option) => option.option_id !== id);
                    segment.gold.answer_option_ids = segment.gold.answer_option_ids.filter((optionId) => optionId !== id);
                }
                renderOptions(kind);
                renderReviewPanel();
                markDirty();
            });
        });

        els.promptEntityFilter.addEventListener("change", renderPromptPanel);
        els.promptSegmentFilter.addEventListener("change", renderPromptPanel);
        els.promptList.addEventListener("click", (event) => {
            const button = event.target.closest("[data-prompt-id]");
            if (button) {
                selectPrompt(button.dataset.promptId, true);
            }
        });
        els.deletePromptButton.addEventListener("click", () => deletePrompt());
        els.runValidationButton.addEventListener("click", () => {
            renderReviewPanel();
            showToast("Integrity check updated");
        });

        els.importButton.addEventListener("click", () => els.importFile.click());
        els.importFile.addEventListener("change", (event) => {
            const file = event.target.files?.[0];
            if (file) {
                importDataset(file);
            }
        });
        els.exportButton.addEventListener("click", exportDataset);
        els.saveButton.addEventListener("click", () => saveDataset(false));

        window.addEventListener("keydown", (event) => {
            const tag = event.target.tagName;
            const editing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target.isContentEditable;
            if (editing) {
                if (event.key === "Escape" && els.entityDialog.open) {
                    els.entityDialog.close();
                }
                return;
            }
            if (event.code === "Space") {
                event.preventDefault();
                togglePlayback();
            } else if (event.key === "ArrowLeft") {
                event.preventDefault();
                stepFrame(-1);
            } else if (event.key === "ArrowRight") {
                event.preventDefault();
                stepFrame(1);
            } else if (event.key === "Escape") {
                cancelDrawing();
            } else if ((event.key === "Delete" || event.key === "Backspace") && state.selectedVertex) {
                event.preventDefault();
                deleteSelectedPolygonPoint();
            } else if (event.key === "Backspace" && state.polygonDraft.length) {
                event.preventDefault();
                undoPolygonPoint();
            } else if (event.key === "Enter" && state.polygonDraft.length >= 3) {
                event.preventDefault();
                finishPolygon();
            } else if (event.key === "Delete" && state.selectedPromptId) {
                deletePrompt();
            }
        });

        window.addEventListener("beforeunload", (event) => {
            if (state.dirty) {
                event.preventDefault();
                event.returnValue = "";
            }
        });

        const resizeObserver = new ResizeObserver(resizeCanvas);
        resizeObserver.observe(els.videoStage);
    }

    bindEvents();
    setTool("select");
    initialize();
})();
