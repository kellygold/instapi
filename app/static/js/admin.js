        // State
        let lastPhotoCount = 0;
        let isDownloading = false;
        let needsSync = false;
        let currentDisplayMode = 'hdmi';

        // Confirm modal (replaces browser confirm())
        function showConfirm(message, isDanger = true) {
            return new Promise(resolve => {
                const overlay = document.getElementById('confirmModal');
                document.getElementById('confirmMessage').textContent = message;
                const okBtn = document.getElementById('confirmOk');
                okBtn.className = 'modal-btn ' + (isDanger ? 'confirm-danger' : 'confirm-action');
                okBtn.textContent = isDanger ? 'Delete' : 'Confirm';
                overlay.classList.add('show');

                const cleanup = (result) => {
                    overlay.classList.remove('show');
                    okBtn.replaceWith(okBtn.cloneNode(true));
                    document.getElementById('confirmCancel').replaceWith(
                        document.getElementById('confirmCancel').cloneNode(true)
                    );
                    resolve(result);
                };

                document.getElementById('confirmOk').onclick = () => cleanup(true);
                document.getElementById('confirmCancel').onclick = () => cleanup(false);
                overlay.onclick = (e) => { if (e.target === overlay) cleanup(false); };
            });
        }

        // Toast notification
        function showToast(message, isError = false) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast ' + (isError ? 'error' : 'success') + ' show';
            setTimeout(() => { toast.classList.remove('show'); }, 3000);
        }

        // Update disk space bar from storage data
        function updateDiskBar(storage) {
            const fill = document.getElementById('diskBarFill');
            const label = document.getElementById('diskBarLabel');
            const warning = document.getElementById('diskBarWarning');
            if (!fill) return;

            const usedPct = storage.total_gb > 0
                ? Math.round((storage.used_gb / storage.total_gb) * 100)
                : 0;
            fill.style.width = usedPct + '%';
            label.textContent = `${storage.used_gb} GB / ${storage.total_gb} GB (${storage.free_gb} GB free)`;

            // Color coding
            const freeMB = storage.free_gb * 1024;
            fill.classList.remove('warning', 'critical');
            warning.classList.remove('critical');
            warning.style.display = 'none';

            if (freeMB < 100) {
                fill.classList.add('critical');
                warning.classList.add('critical');
                warning.style.display = '';
                warning.textContent = 'Uploads disabled - disk nearly full';
            } else if (freeMB < 500) {
                fill.classList.add('warning');
                warning.style.display = '';
                warning.textContent = 'Low disk space - consider removing some photos';
            }
        }

        // Load system info
        async function loadSystemInfo() {
            try {
                const resp = await fetch('/admin/system_info');
                if (resp.status === 401) { window.location.href = '/admin/login'; return; }
                const data = await resp.json();
                document.getElementById('photoCount').textContent = data.photo_count;
                document.getElementById('storageUsed').textContent = data.storage.photos_mb + ' MB';
                document.getElementById('storageFree').textContent = data.storage.free_gb + ' GB';
                document.getElementById('uptime').textContent = data.uptime;
                document.getElementById('ipAddress').textContent = data.ip_address;
                currentDisplayMode = data.display_mode || 'hdmi';
                // Disk warning
                const freeEl = document.getElementById('storageFree');
                if (data.storage.free_gb < 1) {
                    freeEl.style.color = '#f87171';
                } else if (data.storage.free_gb < 2) {
                    freeEl.style.color = '#fb923c';
                } else {
                    freeEl.style.color = '#2dd4bf';
                }
                // Update disk bar
                updateDiskBar(data.storage);
            } catch (e) {
                console.error('Failed to load system info:', e);
            }
        }

        let allPhotos = [];
        let activeFilter = 'all';

        function filterPhotos(uploader) {
            activeFilter = uploader;
            renderPhotos();
        }

        // Load photos grid
        async function loadPhotos() {
            try {
                const resp = await fetch('/admin/photos');
                allPhotos = await resp.json();
                renderPhotos();
            } catch (e) {
                console.error('Failed to load photos:', e);
            }
        }

        function renderPhotos() {
            const grid = document.getElementById('photoGrid');
            const syncRole = window.INSTAPI_CONFIG.syncRole;
            const myLabel = window.INSTAPI_CONFIG.syncLabel;
            const filtered = activeFilter === 'all'
                ? allPhotos
                : allPhotos.filter(p => p.uploaded_by === activeFilter);

            // Build filter chips
            const myKey = syncRole === 'child' ? myLabel : 'admin';
            const uploaders = [...new Set(allPhotos.map(p => p.uploaded_by).filter(u => u && u !== 'unknown'))]
                .sort((a, b) => a === myKey ? -1 : b === myKey ? 1 : a.localeCompare(b));
            const filtersEl = document.getElementById('photoFilters');
            if (uploaders.length > 1) {
                filtersEl.style.display = '';
                const displayName = (u) => {
                    if (u === myKey) return 'My Photos';
                    if (u === 'admin') return 'Admin';
                    return u;
                };
                filtersEl.innerHTML = `<button class="size-btn ${activeFilter === 'all' ? 'active' : ''}" onclick="filterPhotos('all')" style="font-size:0.75em;">All</button> ` +
                    uploaders.map(u => `<button class="size-btn ${activeFilter === u ? 'active' : ''}" onclick="filterPhotos('${u.replace(/'/g, "\\'")}')" style="font-size:0.75em;">${displayName(u)}</button>`).join(' ');
            } else {
                filtersEl.style.display = 'none';
            }

            document.getElementById('gridPhotoCount').textContent =
                filtered.length > 0 ? `${filtered.length} of ${allPhotos.length} photos` : (allPhotos.length > 0 ? `${allPhotos.length} photos` : '');

            if (filtered.length === 0 && allPhotos.length === 0) {
                grid.classList.add('empty');
                grid.innerHTML = '<div class="no-photos">No photos yet.<br>Tap "Select Photos" below to get started!</div>';
                return;
            } else if (filtered.length === 0) {
                grid.classList.add('empty');
                grid.innerHTML = '<div class="no-photos">No photos from this uploader.</div>';
                return;
            }

            grid.classList.remove('empty');
            grid.innerHTML = filtered.map(photo => {
                // Children can only delete their own photos; master/admin can delete any
                const canDelete = syncRole !== 'child' || photo.uploaded_by === myLabel;
                return `
                    <div class="photo-thumb" onclick="showLightbox('${photo.path}')">
                        <img data-src="${photo.thumb}" alt="${photo.name}"
                             src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
                             onerror="this.style.opacity='0.3'; setTimeout(() => { this.src=this.dataset.src+'?r='+Date.now(); this.classList.add('loaded'); }, 2000);">
                        ${canDelete ? `<button class="delete-btn" onclick="event.stopPropagation(); deletePhoto('${photo.path}')" title="Delete">×</button>` : ''}
                    </div>`;
            }).join('');

            // Lazy load thumbnails with IntersectionObserver
            const lazyImages = grid.querySelectorAll('img[data-src]');
            const imgObserver = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const img = entry.target;
                        img.src = img.dataset.src;
                        img.onload = () => img.classList.add('loaded');
                        imgObserver.unobserve(img);
                    }
                });
            }, { rootMargin: '100px' });
            lazyImages.forEach(img => imgObserver.observe(img));
        }

        // Load slideshow settings
        async function loadSettings() {
            try {
                const resp = await fetch('/admin/settings');
                const settings = await resp.json();
                document.getElementById('slideDuration').value = settings.slide_duration;
                document.getElementById('transition').value = settings.transition;
                document.getElementById('shuffle').checked = settings.shuffle;
                document.getElementById('kenBurns').checked = settings.ken_burns;
            } catch (e) {
                console.error('Failed to load settings:', e);
            }
        }

        // Save setting
        async function saveSetting(key, value) {
            try {
                const resp = await fetch('/admin/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ [key]: value })
                });
                const data = await resp.json();
                if (data.success) {
                    showToast('Setting saved');
                } else {
                    showToast(data.error || 'Failed to save', true);
                }
            } catch (e) {
                showToast('Failed to save setting', true);
            }
        }

        // Download status polling (only runs when needed)
        let pollTimer = null;

        async function checkDownloadStatus() {
            try {
                const resp = await fetch('/admin/download_status');
                const status = await resp.json();

                const progressEl = document.getElementById('downloadProgress');
                if (status.downloading) {
                    progressEl.style.display = 'block';
                    const pct = status.download_total > 0
                        ? Math.round((status.download_completed / status.download_total) * 100)
                        : 0;
                    document.getElementById('progressFill').style.width = pct + '%';
                    document.getElementById('progressText').textContent =
                        `Downloading photo ${status.download_completed + 1} of ${status.download_total}...`;
                    isDownloading = true;
                    // Keep polling while downloading
                    pollTimer = setTimeout(checkDownloadStatus, 2000);
                } else {
                    progressEl.style.display = 'none';
                    if (isDownloading) {
                        isDownloading = false;
                        showToast(`${status.photo_count} photos ready!`);
                        loadPhotos();
                        loadSystemInfo();
                    }
                    // Refresh grid if photo count changed
                    if (status.photo_count !== lastPhotoCount) {
                        lastPhotoCount = status.photo_count;
                        loadPhotos();
                        loadSystemInfo();
                    }
                    // Not downloading — stop polling
                    pollTimer = null;
                }
            } catch (e) {
                // Server might be restarting — stop polling
                pollTimer = null;
            }
        }

        function startPolling() {
            if (!pollTimer) {
                pollTimer = setTimeout(checkDownloadStatus, 500);
            }
        }

        // Delete single photo
        async function deletePhoto(path) {
            if (!await showConfirm('Delete this photo?')) return;
            try {
                const resp = await fetch('/admin/delete_photo', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path })
                });
                const data = await resp.json();
                if (data.success) {
                    showToast('Photo deleted');
                    loadPhotos();
                    loadSystemInfo();
                    if (currentDisplayMode === 'usb') {
                        needsSync = true;
                        document.getElementById('syncAction').style.display = '';
                    }
                } else {
                    showToast(data.error || 'Failed to delete', true);
                }
            } catch (e) {
                showToast('Failed to delete photo', true);
            }
        }

        // Sync photos to USB frame
        async function syncFrame() {
            const btn = document.getElementById('syncBtn');
            btn.textContent = 'Syncing...';
            btn.disabled = true;
            try {
                const resp = await fetch('/admin/sync_usb', { method: 'POST' });
                const data = await resp.json();
                if (data.success) {
                    showToast('Frame synced!');
                    needsSync = false;
                    document.getElementById('syncAction').style.display = 'none';
                } else {
                    showToast(data.error || 'Sync failed', true);
                }
            } catch (e) {
                showToast('Sync failed', true);
            }
            btn.textContent = 'Sync Now';
            btn.disabled = false;
        }

        // ========== Family Sync ==========

        function showChildSetup() {
            document.getElementById('syncUnconfigured').style.display = 'none';
            document.getElementById('syncChildSetup').style.display = '';
        }

        function cancelChildSetup() {
            document.getElementById('syncChildSetup').style.display = 'none';
            document.getElementById('syncUnconfigured').style.display = '';
        }

        function showSyncView(role) {
            ['syncUnconfigured', 'syncChildSetup', 'syncMaster', 'syncChild'].forEach(
                id => document.getElementById(id).style.display = 'none'
            );
            if (role === 'master') {
                document.getElementById('syncMaster').style.display = '';
                loadChildFrames();
            } else if (role === 'child') {
                document.getElementById('syncChild').style.display = '';
                loadSyncStatus();
            } else {
                document.getElementById('syncUnconfigured').style.display = '';
            }
        }

        async function configureSyncRole(role) {
            try {
                const resp = await fetch('/admin/sync_config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({sync_role: role})
                });
                const data = await resp.json();
                if (data.success) {
                    showSyncView(role);
                } else {
                    showToast(data.error || 'Failed', true);
                }
            } catch (e) {
                showToast('Failed to configure sync', true);
            }
        }

        async function saveChildConfig() {
            const masterUrl = document.getElementById('masterUrlInput').value.trim();
            const syncToken = document.getElementById('syncTokenInput').value.trim();
            if (!masterUrl || !syncToken) {
                showToast('Master URL and token required', true);
                return;
            }
            try {
                const resp = await fetch('/admin/sync_config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({sync_role: 'child', master_url: masterUrl, sync_token: syncToken})
                });
                const data = await resp.json();
                if (data.success) {
                    showToast('Sync configured! Starting first sync...');
                    showSyncView('child');
                    document.getElementById('syncMasterUrl').textContent = masterUrl;
                } else {
                    showToast(data.error || 'Failed', true);
                }
            } catch (e) {
                showToast('Failed to save config', true);
            }
        }

        function renderSyncStatus(data) {
            const card = document.getElementById('syncStatusCard');
            const dot = document.getElementById('syncDot');
            const connText = document.getElementById('syncConnectionText');
            const details = document.getElementById('syncStatusDetails');
            if (!card || !dot) return;
            card.style.display = '';

            const isError = data.last_sync_result === 'error';
            const isSyncing = data.sync_in_progress;

            // Connection dot
            dot.className = 'sync-dot ' + (isSyncing ? 'syncing' : isError ? 'error' : 'connected');
            connText.textContent = isSyncing ? 'Syncing...' : isError ? 'Master unreachable' : 'Connected to master';

            // Details
            let lines = [];
            if (data.synced_photo_count !== undefined) {
                lines.push(`${data.synced_photo_count} photos synced`);
            }
            if (data.last_sync) {
                const ago = timeAgo(data.last_sync);
                lines.push(`Last sync: ${ago}`);
                // Stale warning if > 2 hours
                const diffMs = Date.now() - new Date(data.last_sync).getTime();
                if (diffMs > 2 * 3600 * 1000 && !isSyncing) {
                    lines.push('<span class="stale-warning">May be stale</span>');
                }
            }
            // Next sync estimate
            if (data.sync_interval && data.last_sync && !isSyncing) {
                const elapsed = (Date.now() - new Date(data.last_sync).getTime()) / 1000;
                const remaining = Math.max(0, data.sync_interval - elapsed);
                if (remaining > 0) {
                    const mins = Math.ceil(remaining / 60);
                    lines.push(`Next sync in ~${mins} min`);
                }
            }
            if (isError && data.sync_error) {
                lines.push(`<span class="stale-warning">${data.sync_error}</span>`);
            }
            details.innerHTML = lines.join('<br>');
        }

        async function loadSyncStatus() {
            try {
                const resp = await fetch('/admin/sync_status');
                if (resp.status === 401) { window.location.href = '/admin/login'; return; }
                const data = await resp.json();

                // Update child view
                if (data.sync_role === 'child') {
                    document.getElementById('syncMasterUrl').textContent = data.master_url || '';
                    const icon = document.getElementById('syncStatusIcon');
                    const status = document.getElementById('syncStatusText');
                    const detail = document.getElementById('syncDetailText');

                    if (data.sync_in_progress) {
                        if (data.sync_phase === 'updating_frame') {
                            icon.textContent = '🔄';
                            status.textContent = 'Updating frame...';
                            detail.textContent = 'Refreshing USB drive';
                        } else if (data.sync_total > 0) {
                            icon.textContent = '⏳';
                            status.textContent = `Downloading ${data.sync_completed} of ${data.sync_total}`;
                            detail.textContent = '';
                        } else {
                            icon.textContent = '⏳';
                            status.textContent = 'Checking for changes...';
                            detail.textContent = '';
                        }
                    } else if (data.sync_error) {
                        icon.textContent = '⚠️';
                        status.textContent = 'Error';
                        detail.textContent = data.sync_error;
                        icon.classList.add('orange');
                    } else if (data.last_sync) {
                        icon.textContent = '✅';
                        status.textContent = `${data.synced_photo_count} photos synced`;
                        const ago = timeAgo(data.last_sync);
                        detail.textContent = `Last sync: ${ago}`;
                    } else {
                        icon.textContent = '🔄';
                        status.textContent = 'Waiting for first sync';
                        detail.textContent = '';
                    }

                    // Set interval selector
                    const sel = document.getElementById('syncIntervalSelect');
                    if (sel) sel.value = String(data.sync_interval || 1800);

                    // Render sync status card
                    renderSyncStatus(data);
                }
            } catch (e) {
                console.error('Failed to load sync status:', e);
                const status = document.getElementById('syncStatusText');
                const detail = document.getElementById('syncDetailText');
                if (status) {
                    status.textContent = 'Could not reach frame';
                    detail.textContent = 'Will retry...';
                }
            }
        }

        // Auto-poll sync status for child frames (immediate + every 10s)
        if (window.INSTAPI_CONFIG.syncRole === "child") {
        loadSyncStatus();
        setInterval(loadSyncStatus, 10000);
        }

        function timeAgo(isoStr) {
            const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
            if (diff < 60) return 'just now';
            if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
            if (diff < 86400) return Math.floor(diff / 3600) + ' hr ago';
            return Math.floor(diff / 86400) + ' days ago';
        }

        async function triggerSyncNow() {
            const btn = document.getElementById('syncNowBtn');
            btn.textContent = 'Syncing...';
            btn.disabled = true;
            try {
                const resp = await fetch('/admin/sync_now', {method: 'POST'});
                const data = await resp.json();
                if (data.success) {
                    showToast('Sync started');
                    // Poll for completion
                    const poll = setInterval(async () => {
                        await loadSyncStatus();
                        const sr = await fetch('/admin/sync_status');
                        const sd = await sr.json();
                        if (!sd.sync_in_progress) {
                            clearInterval(poll);
                            btn.textContent = 'Sync Now';
                            btn.disabled = false;
                            loadPhotos();
                            loadSystemInfo();
                        }
                    }, 2000);
                } else {
                    showToast(data.error || 'Sync failed', true);
                    btn.textContent = 'Sync Now';
                    btn.disabled = false;
                }
            } catch (e) {
                showToast('Sync failed', true);
                btn.textContent = 'Sync Now';
                btn.disabled = false;
            }
        }

        async function updateSyncInterval(value) {
            try {
                await fetch('/admin/sync_config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({sync_role: 'child', sync_interval: parseInt(value)})
                });
                showToast('Interval updated');
            } catch (e) {}
        }

        async function loadChildFrames() {
            try {
                const resp = await fetch('/admin/sync_children');
                const children = await resp.json();
                const list = document.getElementById('childFrameList');
                if (!list) return;
                if (children.length === 0) {
                    list.innerHTML = '<div class="action-item"><div class="action-content"><h3>No child frames</h3><p>Add a child frame to start sharing photos</p></div></div>';
                    return;
                }
                list.innerHTML = children.map(c => `
                    <div class="action-item">
                        <div class="action-icon blue">📺</div>
                        <div class="action-content">
                            <h3>${c.label}</h3>
                            <p style="font-size:0.7em; word-break:break-all; color:#999;">${c.token}</p>
                        </div>
                        <div style="display:flex; gap:4px;">
                            <button class="action-btn secondary" onclick="copySyncConfig('${c.token}')">Copy</button>
                            <button class="action-btn secondary" onclick="removeChildFrame('${c.token}')" style="color:#ef4444;">✕</button>
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                console.error('Failed to load children:', e);
            }
        }

        async function addChildFrame() {
            const label = prompt('Frame name (e.g. "Gramma"):');
            if (!label) return;
            try {
                const resp = await fetch('/admin/sync_add_child', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({label: label})
                });
                const data = await resp.json();
                if (data.success) {
                    showToast(`Added ${data.child.label}`);
                    loadChildFrames();
                } else {
                    showToast(data.error || 'Failed', true);
                }
            } catch (e) {
                showToast('Failed to add frame', true);
            }
        }

        async function removeChildFrame(token) {
            if (!confirm('Remove this child frame?')) return;
            try {
                await fetch('/admin/sync_remove_child', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token: token})
                });
                loadChildFrames();
            } catch (e) {
                showToast('Failed to remove', true);
            }
        }

        async function copySyncConfig(token) {
            const config = JSON.stringify({
                master_url: window.location.origin,
                sync_token: token
            }, null, 2);
            try {
                await navigator.clipboard.writeText(config);
                showToast('Config copied to clipboard');
            } catch (e) {
                showToast('Could not copy', true);
            }
        }

        // Reset to setup
        async function resetToSetup() {
            if (!await showConfirm('Factory reset? This will clear all photos and return the frame to setup.')) return;
            try {
                const resp = await fetch('/admin/reset', { method: 'POST' });
                const data = await resp.json();
                if (data.success) {
                    showToast('Frame has been reset');
                    loadPhotos();
                    loadSystemInfo();
                } else {
                    showToast(data.error || 'Reset failed', true);
                }
            } catch (e) {
                showToast('Reset failed', true);
            }
        }

        // Switch mode
        async function switchMode() {
            const currentMode = window.INSTAPI_CONFIG.displayMode;
            const newMode = currentMode === 'hdmi' ? 'usb' : 'hdmi';
            if (!await showConfirm(`Switch to ${newMode.toUpperCase()} mode? The app will restart.`, false)) return;

            try {
                const resp = await fetch('/admin/switch_mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: newMode })
                });
                const data = await resp.json();
                if (data.success) {
                    showToast(data.message);
                    setTimeout(() => location.reload(), 2000);
                } else {
                    showToast(data.error || 'Switch failed', true);
                }
            } catch (e) {
                showToast('Switch failed', true);
            }
        }

        // Update & Restart (combined)
        async function updateAndRestart() {
            if (!await showConfirm('Update and restart InstaPi?', false)) return;
            showToast('Updating...');
            try {
                const resp = await fetch('/admin/update_and_restart', { method: 'POST' });
                const data = await resp.json();
                if (data.success) {
                    showToast('Updated! Restarting...');
                    setTimeout(() => location.reload(), 4000);
                } else {
                    showToast(data.error || 'Update failed', true);
                }
            } catch (e) {
                // Server probably restarted mid-request
                showToast('Restarting...');
                setTimeout(() => location.reload(), 4000);
            }
        }

        // Grid size toggle
        function setGridSize(size) {
            const grid = document.getElementById('photoGrid');
            grid.classList.toggle('compact', size === 'compact');
            document.getElementById('sizeCompact').classList.toggle('active', size === 'compact');
            document.getElementById('sizeNormal').classList.toggle('active', size === 'normal');
        }

        // Initialize + start polling
        // Share link
        const uploadToken = window.INSTAPI_CONFIG.uploadToken;

        function initShareLink() {
            const el = document.getElementById('shareUrl');
            if (!el) return;
            const url = window.location.origin + '/upload?t=' + uploadToken;
            el.textContent = url;
        }

        async function copyShareLink() {
            const url = window.location.origin + '/upload?t=' + uploadToken;
            try {
                await navigator.clipboard.writeText(url);
                const btn = document.getElementById('copyBtn');
                btn.textContent = 'Copied!';
                setTimeout(() => { btn.textContent = 'Copy Link'; }, 2000);
            } catch (e) {
                showToast('Could not copy link', true);
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            // Initialize disk bar from server-rendered data
            if (window.INSTAPI_CONFIG.storage) {
                updateDiskBar(window.INSTAPI_CONFIG.storage);
            }
            loadSystemInfo();
            loadPhotos();
            loadSettings();
            initShareLink();
            loadSyncStatus();
            loadChildFrames();
            lastPhotoCount = parseInt(document.getElementById('photoCount').textContent) || 0;

            // Default to compact grid on mobile
            if (window.innerWidth <= 600) {
                setGridSize('compact');
            }

        });

        // Lightbox
        function showLightbox(src) {
            const overlay = document.getElementById('lightbox');
            const img = document.getElementById('lightboxImg');
            img.src = src;
            overlay.classList.add('show');
        }

        function closeLightbox() {
            const overlay = document.getElementById('lightbox');
            overlay.classList.remove('show');
            document.getElementById('lightboxImg').src = '';
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeLightbox();
        });

        // Refresh when tab regains focus (e.g., after picking photos in another tab)
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                loadPhotos();
                loadSystemInfo();
                loadSyncStatus();
                startPolling();
            }
        });
