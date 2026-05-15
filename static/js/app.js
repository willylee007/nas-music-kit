const { createApp } = Vue;

const IS_VIP = document.getElementById("app")?.dataset.vip === "true";
const PROGRESS_CIRCLE_LENGTH = 100.53;

const OPTION_CACHE_KEYS = {
    searchType: "nas-music-kit:search-type",
    searchQuery: "nas-music-kit:search-query",
    source: "nas-music-kit:source",
    quality: "nas-music-kit:quality",
    downloadSubdir: "nas-music-kit:download-subdir-mode",
    theme: "nas-music-kit:theme",
    batchDownloadLyric: "nas-music-kit:batch-download-lyric",
};

const ICON_PATHS = {
    "arrow-up": '<path d="m5 12 7-7 7 7"></path><path d="M12 19V5"></path>',
    "battery-charging":
        '<path d="M14 7h-4v10"></path><path d="M14 17h-4"></path><path d="M16 7h1a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-1"></path><path d="M7 7H6a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h1"></path><path d="M11 7 8 12h4l-3 5"></path><path d="M22 11v2"></path>',
    check: '<path d="M20 6 9 17l-5-5"></path>',
    download:
        '<path d="M12 15V3"></path><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><path d="m7 10 5 5 5-5"></path>',
    "file-search-corner":
        '<path d="M14 2v4a2 2 0 0 0 2 2h4"></path><path d="M15 14a3 3 0 1 0-6 0 3 3 0 0 0 6 0Z"></path><path d="m14 15.5 2 2"></path><path d="M20 11.5V8l-6-6H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h5.5"></path><path d="M18 22h4v-4"></path>',
    loader:
        '<path d="M12 2v4"></path><path d="m16.2 7.8 2.9-2.9"></path><path d="M18 12h4"></path><path d="m16.2 16.2 2.9 2.9"></path><path d="M12 18v4"></path><path d="m4.9 19.1 2.9-2.9"></path><path d="M2 12h4"></path><path d="m4.9 4.9 2.9 2.9"></path>',
    "mic-vocal":
        '<path d="m11 7.601-5.994 8.19a1 1 0 0 0 .1 1.298l.817.817a1 1 0 0 0 1.314.087L15.09 12"></path><path d="M16.5 21.174C15.5 20.5 14.372 20 13 20c-2.058 0-3.928 2.356-6 2"></path><path d="M19 7.5c0 .8-.7 1.5-1.5 1.5S16 8.3 16 7.5 16.7 6 17.5 6 19 6.7 19 7.5"></path><path d="M20.4 14.5c.9-1.2 1.4-2.7 1.4-4.3 0-4-3.2-7.2-7.2-7.2-1.6 0-3.1.5-4.3 1.4"></path><path d="M15.2 14.2c-.4.1-.8.2-1.2.2-2.4 0-4.4-2-4.4-4.4 0-.4.1-.8.2-1.2"></path>',
    moon: '<path d="M12 3a6 6 0 0 0 9 7.1A9 9 0 1 1 12 3Z"></path>',
    pause: '<rect x="14" y="4" width="4" height="16" rx="1"></rect><rect x="6" y="4" width="4" height="16" rx="1"></rect>',
    play: '<polygon points="6 3 20 12 6 21 6 3"></polygon>',
    sun: '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path>',
    workflow:
        '<rect width="8" height="8" x="3" y="3" rx="2"></rect><path d="M7 11v4a2 2 0 0 0 2 2h4"></path><rect width="8" height="8" x="13" y="13" rx="2"></rect>',
    x: '<path d="M18 6 6 18"></path><path d="m6 6 12 12"></path>',
};

createApp({
    delimiters: ["[[", "]]"],

    data() {
        return {
            searchType: "song",
            query: "",
            source: "netease",
            quality: "999",
            downloadSubdir: "none",
            batchDownloadLyric: false,
            loading: false,
            searched: false,
            currentPage: 1,
            currentQuery: "",
            results: [],
            selectedTrackKeys: new Set(),
            batchDownloading: false,
            currentPlayingKey: null,
            audioPaused: true,
            hasNextPage: false,
            activeModal: null,
            theme: document.documentElement.dataset.theme === "day" ? "day" : "night",
            toastMessage: "",
            toastType: "info",
            toastVisible: false,
            toastTimer: null,
        };
    },

    computed: {
        searchPlaceholder() {
            return this.searchType === "album" ? "请输入专辑名称" : "请输入歌手、歌名";
        },
        selectedCount() {
            return this.selectedTrackKeys.size;
        },
        showPagination() {
            return this.results.length > 0;
        },
        toastClass() {
            return this.toastVisible ? `show ${this.toastType}` : "hidden";
        },
        themeToggleTitle() {
            return this.theme === "day" ? "切换到夜间模式" : "切换到日间模式";
        },
    },

    mounted() {
        this.restoreOptions();

        const audio = this.$refs.audioEl;
        audio.addEventListener("timeupdate", this.updateAudioProgress);
        audio.addEventListener("ended", this.handleAudioEnded);
        audio.addEventListener("pause", () => {
            this.audioPaused = true;
        });
        audio.addEventListener("play", () => {
            this.audioPaused = false;
        });

        if ("serviceWorker" in navigator) {
            navigator.serviceWorker.register("/sw.js").then(() => {
                console.log("PWA: Ready for Installation (No-cache mode)");
            });
        }
    },

    methods: {
        iconSvg(name, size = 24, className = "", fillCurrent = false) {
            const path = ICON_PATHS[name] || "";
            const classAttr = className ? ` class="${className}"` : "";
            const fill = fillCurrent ? "currentColor" : "none";

            return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="${fill}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"${classAttr}>${path}</svg>`;
        },

        restoreOptions() {
            this.searchType = this.storedSelectValue(OPTION_CACHE_KEYS.searchType, ["song", "album"], "song");
            this.query = localStorage.getItem(OPTION_CACHE_KEYS.searchQuery) || "";
            this.source = localStorage.getItem(OPTION_CACHE_KEYS.source) || "netease";
            this.quality = localStorage.getItem(OPTION_CACHE_KEYS.quality) || "999";
            this.downloadSubdir = this.storedSelectValue(
                OPTION_CACHE_KEYS.downloadSubdir,
                ["none", "artist", "album"],
                "none",
            );
            this.theme = this.storedSelectValue(OPTION_CACHE_KEYS.theme, ["night", "day"], this.theme);
            this.batchDownloadLyric = localStorage.getItem(OPTION_CACHE_KEYS.batchDownloadLyric) === "true";
            this.applyTheme();
            this.$nextTick(this.ensureSelectValues);
        },

        ensureSelectValues() {
            const sourceOptions = Array.from(this.$refs.sourceSelect?.options || []).map((option) => option.value);
            const qualityOptions = Array.from(this.$refs.qualitySelect?.options || []).map((option) => option.value);

            if (!sourceOptions.includes(this.source)) this.source = sourceOptions[0] || "netease";
            if (!qualityOptions.includes(this.quality)) this.quality = qualityOptions[0] || "999";
        },

        storedSelectValue(key, allowed, fallback) {
            const value = localStorage.getItem(key);
            return allowed.includes(value) ? value : fallback;
        },

        reloadPage() {
            location.reload();
        },

        openModal(name) {
            this.activeModal = name;
        },

        closeModal() {
            this.activeModal = null;
        },

        toggleTheme() {
            this.theme = this.theme === "day" ? "night" : "day";
            localStorage.setItem(OPTION_CACHE_KEYS.theme, this.theme);
            this.applyTheme();
        },

        applyTheme() {
            document.documentElement.dataset.theme = this.theme;
            document.documentElement.style.colorScheme = this.theme === "day" ? "light" : "dark";

            const themeColor = document.querySelector('meta[name="theme-color"]');
            if (themeColor) themeColor.setAttribute("content", this.theme === "day" ? "#f6f1e8" : "#1d232a");
        },

        persistSearchType() {
            localStorage.setItem(OPTION_CACHE_KEYS.searchType, this.searchType);
        },

        persistQuery() {
            localStorage.setItem(OPTION_CACHE_KEYS.searchQuery, this.query.trim());
        },

        persistOption(name) {
            const optionMap = {
                source: [OPTION_CACHE_KEYS.source, this.source],
                quality: [OPTION_CACHE_KEYS.quality, this.quality],
                downloadSubdir: [OPTION_CACHE_KEYS.downloadSubdir, this.downloadSubdir],
            };
            const option = optionMap[name];
            if (option) localStorage.setItem(option[0], option[1]);
        },

        persistBatchDownloadLyric() {
            localStorage.setItem(OPTION_CACHE_KEYS.batchDownloadLyric, String(this.batchDownloadLyric));
        },

        async performSearch(overrideQuery = null, page = 1, overrideSource = null) {
            const query = (overrideQuery === null ? this.query : overrideQuery).trim();
            if (!query) return;

            if (query.startsWith("http") || query.includes("163cn.tv") || query.includes("y.qq.com")) {
                await this.performWorkflowDownload(query);
                return;
            }

            this.currentPage = query !== this.currentQuery ? 1 : page;
            this.currentQuery = query;
            if (overrideQuery !== null) this.setSearchQuery(overrideQuery);

            const source = overrideSource || this.source;
            const searchType = overrideSource ? "song" : this.searchType;

            this.stopCurrent();
            this.selectedTrackKeys.clear();
            this.results = [];
            this.hasNextPage = false;
            this.loading = true;
            this.searched = false;

            try {
                const res = await fetch(
                    `/api/search?source=${source}&name=${encodeURIComponent(query)}&pages=${this.currentPage}&search_type=${searchType}${IS_VIP ? "&vip=1" : ""}`,
                );
                const data = await res.json();

                if (data.error) {
                    this.showToast(data.error, "error");
                } else if (Array.isArray(data)) {
                    this.results = data.map((item) => this.normalizeTrack(item));
                    this.hasNextPage = data.length >= 20;
                } else {
                    this.showToast("❌ 返回数据格式不正确", "error");
                }
            } catch (err) {
                this.showToast("❌ 请求失败，请检查网络", "error");
            } finally {
                this.loading = false;
                this.searched = true;
            }
        },

        async performWorkflowDownload(query) {
            this.showToast("检测到分享链接，正在尝试快速解析下载...");
            try {
                const res = await fetch(
                    `/api/workflow?text=${encodeURIComponent(query)}&br=${this.quality}&vip=${IS_VIP ? "1" : "0"}`,
                );
                const data = await res.json();

                if (data.status === "success") {
                    this.showToast(`🚀 下载成功：${data.filename}`, "success");
                    this.setSearchQuery("");
                } else {
                    this.showToast(`❌ 解析失败：${data.error || "未知错误"}`, "error");
                }
            } catch (e) {
                this.showToast("❌ 请求失败，请检查网络", "error");
            }
        },

        changePage(delta) {
            const nextPage = this.currentPage + delta;
            if (nextPage >= 1) this.performSearch(this.currentQuery, nextPage);
        },

        setSearchType(value) {
            this.searchType = value;
            this.persistSearchType();
        },

        setSearchQuery(value) {
            this.query = value;
            this.persistQuery();
        },

        searchKeyword(keyword) {
            this.setSearchType("song");
            this.performSearch(keyword, 1);
        },

        searchAlbum(albumName) {
            this.setSearchType("album");
            this.performSearch(albumName, 1);
        },

        normalizeTrack(item) {
            const artist = Array.isArray(item.artist) ? item.artist.join(", ") : item.artist || "Unknown";
            const source = item.source || this.source;
            const id = String(item.id || "");

            return {
                id,
                source,
                name: item.name || "",
                artist,
                album: item.album || "",
                pic_id: String(item.pic_id || item.id || ""),
                key: `${source}:${id}`,
                info: null,
                infoLoading: false,
                previewLoading: false,
                previewProgress: 0,
                lyricState: "idle",
                downloadState: "idle",
                downloadProgress: 0,
            };
        },

        cardId(track) {
            return `card-${track.source}-${track.id}`.replace(/[^a-zA-Z0-9_-]/g, "-");
        },

        coverUrl(track) {
            return `/api/cover?source=${encodeURIComponent(track.source)}&id=${encodeURIComponent(track.pic_id)}${IS_VIP ? "&vip=1" : ""}`;
        },

        hideBrokenImage(event) {
            event.target.style.opacity = 0;
        },

        async fetchTrackInfo(track) {
            if (track.info || track.infoLoading) return;

            track.infoLoading = true;
            try {
                const res = await fetch(
                    `/api/info?source=${track.source}&id=${track.id}&br=${this.quality}&vip=${IS_VIP ? "1" : "0"}`,
                );
                const data = await res.json();

                if (data.level || data.size) {
                    track.info = { level: data.level || "", size: data.size || "" };
                } else {
                    this.showToast("未能获取到详细信息", "info");
                }
            } catch (e) {
                this.showToast("获取信息失败", "error");
            } finally {
                track.infoLoading = false;
            }
        },

        isSelected(track) {
            return this.selectedTrackKeys.has(track.key);
        },

        toggleTrackSelection(track, checked) {
            if (checked) this.selectedTrackKeys.add(track.key);
            else this.selectedTrackKeys.delete(track.key);
        },

        toggleTrackSelectionFromCard(event, track) {
            if (event.target.closest("button, input, select, textarea, a, .clickable")) return;
            this.toggleTrackSelection(track, !this.isSelected(track));
        },

        selectAllResults() {
            this.results.forEach((track) => this.selectedTrackKeys.add(track.key));
        },

        clearSelection() {
            this.selectedTrackKeys.clear();
        },

        getSelectedTracks() {
            return this.results.filter((track) => this.isSelected(track));
        },

        isCurrentTrack(track) {
            return this.currentPlayingKey === track.key;
        },

        async togglePreview(track) {
            const audio = this.$refs.audioEl;

            if (this.isCurrentTrack(track)) {
                if (audio.paused) await audio.play();
                else audio.pause();
                return;
            }

            this.stopCurrent();
            this.currentPlayingKey = track.key;
            track.previewLoading = true;

            try {
                const res = await fetch(
                    `/api/preview?source=${track.source}&id=${track.id}&br=128${IS_VIP ? "&vip=1" : ""}`,
                );
                const data = await res.json();

                if (!res.ok || !data.url) {
                    this.showToast(data.error || "❌ 试听链接获取失败", "error");
                    this.stopCurrent();
                    return;
                }

                audio.src = data.url;
                audio.load();
                await audio.play();
            } catch (err) {
                this.showToast("❌ 试听请求失败", "error");
                this.stopCurrent();
            } finally {
                track.previewLoading = false;
            }
        },

        stopCurrent() {
            const audio = this.$refs.audioEl;
            if (audio) {
                audio.pause();
                audio.src = "";
            }

            const current = this.results.find((track) => track.key === this.currentPlayingKey);
            if (current) current.previewProgress = 0;

            this.currentPlayingKey = null;
            this.audioPaused = true;
        },

        updateAudioProgress() {
            const audio = this.$refs.audioEl;
            if (!this.currentPlayingKey || !audio.duration) return;

            const current = this.results.find((track) => track.key === this.currentPlayingKey);
            if (current) current.previewProgress = (audio.currentTime / audio.duration) * 100;
        },

        handleAudioEnded() {
            const current = this.results.find((track) => track.key === this.currentPlayingKey);
            if (current) current.previewProgress = 100;
            this.audioPaused = true;
        },

        safeSubdirName(value) {
            return String(value || "")
                .replace(/[\\/:*?"<>|]/g, "")
                .trim();
        },

        getDownloadSubdir(track = null) {
            if (this.downloadSubdir === "artist") return this.safeSubdirName(track && track.artist);
            if (this.downloadSubdir === "album") return this.safeSubdirName(track && track.album);
            return "";
        },

        async downloadLyricOnly(track) {
            track.lyricState = "downloading";
            try {
                const res = await fetch("/api/lyric", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        source: track.source,
                        id: track.id,
                        name: track.name,
                        artist: track.artist,
                        vip: IS_VIP,
                        subdir: this.getDownloadSubdir(track),
                    }),
                });
                const data = await res.json();

                if (res.ok) {
                    this.showToast(`✅ 歌词已保存: ${data.filename}`, "success");
                    track.lyricState = "done";
                } else {
                    this.showToast(data.error || "❌ 歌词下载失败", "error");
                    track.lyricState = "idle";
                }
            } catch {
                this.showToast("❌ 网络请求错误", "error");
                track.lyricState = "idle";
            }
        },

        async downloadTrack(track, showSuccessToast = true, includeLyric = false) {
            track.downloadState = "downloading";
            track.downloadProgress = 0;

            try {
                const startRes = await fetch("/api/download/start", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        source: track.source,
                        id: track.id,
                        name: track.name,
                        artist: track.artist,
                        album: track.album,
                        pic_id: track.pic_id,
                        br: this.quality,
                        vip: IS_VIP,
                        subdir: this.getDownloadSubdir(track),
                        lyric: includeLyric,
                    }),
                });
                const startData = await startRes.json();
                if (!startRes.ok) throw new Error(startData.error || "下载失败");

                while (true) {
                    await new Promise((resolve) => setTimeout(resolve, 100));

                    const progressRes = await fetch(`/api/download/progress/${startData.job_id}`);
                    const progressData = await progressRes.json();
                    if (!progressRes.ok) throw new Error(progressData.error || "下载失败");

                    track.downloadProgress = this.normalizedProgress({
                        downloadProgress: progressData.progress,
                    });

                    if (progressData.status === "success") {
                        if (showSuccessToast) this.showToast(`✅ 下载成功: ${progressData.filename}`, "success");
                        track.downloadState = "done";
                        track.downloadProgress = 100;
                        return { ok: true, data: progressData.result || progressData };
                    }

                    if (progressData.status === "error") {
                        throw new Error(progressData.error || "下载失败");
                    }
                }
            } catch (err) {
                console.error(err);
                if (showSuccessToast) this.showToast(err.message || "❌ 网络请求错误", "error");
                track.downloadState = "failed";
                return { ok: false, error: err.message || "网络请求错误" };
            }
        },

        async downloadSelectedTracks() {
            const tracks = this.getSelectedTracks();
            if (tracks.length === 0 || this.batchDownloading) return;

            this.batchDownloading = true;

            let success = 0;
            let failed = 0;

            for (const track of tracks) {
                const result = await this.downloadTrack(track, false, this.batchDownloadLyric);
                if (result.ok) success += 1;
                else failed += 1;
            }

            this.batchDownloading = false;
            this.showToast(`批量下载完成：成功 ${success} 首，失败 ${failed} 首`, failed ? "error" : "success");
        },

        normalizedProgress(track) {
            return Math.max(0, Math.min(100, Math.round(track.downloadProgress || 0)));
        },

        progressDash(track) {
            const value = this.normalizedProgress(track);
            const visibleLength = value === 0 ? 0 : (value / 100) * PROGRESS_CIRCLE_LENGTH;
            return `${visibleLength} ${PROGRESS_CIRCLE_LENGTH}`;
        },

        downloadProgressLabel(track) {
            return `下载进度 ${this.normalizedProgress(track)}%`;
        },

        showToast(message, type = "info") {
            if (this.toastTimer) clearTimeout(this.toastTimer);

            this.toastMessage = message;
            this.toastType = type;
            this.toastVisible = true;
            this.toastTimer = setTimeout(() => {
                this.toastVisible = false;
            }, 5000);
        },

        scrollToTop() {
            window.scrollTo({ top: 0, behavior: "smooth" });
        },
    },
}).mount("#app");
