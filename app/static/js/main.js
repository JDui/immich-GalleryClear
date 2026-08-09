const config = window.appConfig || {};
const gridEl = document.getElementById("grid");
const toastContainer = document.getElementById("toast-container");
const debugPanel = document.getElementById("debug-panel");
const btnDebug = document.getElementById("btn-debug");
const btnDebugClose = document.getElementById("btn-debug-close");
const btnDebugRefresh = document.getElementById("btn-debug-refresh");
const gridPreset = document.getElementById("grid-preset");
const gridCountInput = document.getElementById("grid-count");
const gridApplyBtn = document.getElementById("grid-apply");
const gridCustom = document.getElementById("grid-custom");
const themeSelect = document.getElementById("theme-select");
const filterSelect = document.getElementById("filter-mode");

const blurBackdrop = document.getElementById("blur-backdrop");
const blurLayerA = blurBackdrop ? blurBackdrop.querySelector(".layer-a") : null;
const blurLayerB = blurBackdrop ? blurBackdrop.querySelector(".layer-b") : null;

const dbgRange = document.getElementById("dbg-range");
const btnNext = document.getElementById("btn-next");
const btnPrev = document.getElementById("btn-prev");

let assets = [];
let markState = {}; // assetId -> 'delete' | 'album' | 'none'
let currentCount =
  Number(config.gridCount) ||
  (config.gridRows && config.gridCols ? config.gridRows * config.gridCols : 6);
let currentColumns = 0;
let currentFilter = config.filterMode || "exclude-prescreen";
let currentTheme = config.theme || "rainbow";
let blurActive = 0;
let blurCurrentUrl = null;
let blurTimer = null;
let isPreviousView = false;
let prevAvailable = false;
const wipeDirections = ["left", "right", "up", "down"];

function pickWipeDirection() {
  return wipeDirections[Math.floor(Math.random() * wipeDirections.length)];
}

function clearWipeClasses(card) {
  card.classList.remove(
    "wipe-in",
    "wipe-out",
    "wipe-left",
    "wipe-right",
    "wipe-up",
    "wipe-down",
    "shatter-out",
    "delete-out",
    "fav-out"
  );
  const layer = card.querySelector(".shatter-layer");
  if (layer) layer.remove();
}

function applyWipeIn(card) {
  clearWipeClasses(card);
  const dir = pickWipeDirection();
  card.classList.add("wipe-in", `wipe-${dir}`);
}

function applyWipeOut(card) {
  clearWipeClasses(card);
  const dir = pickWipeDirection();
  card.classList.add("wipe-out", `wipe-${dir}`);
}

function applyShatterOut(card) {
  clearWipeClasses(card);
  card.classList.remove("zoomed-150", "zoomed-200", "zoomed-300");
  card.dataset.scale = "1";
  card.classList.add("delete-out");
}

function applyFavOut(card) {
  clearWipeClasses(card);
  card.classList.remove("zoomed-150", "zoomed-200", "zoomed-300");
  card.dataset.scale = "1";
  card.classList.add("fav-out");
}

function createShatterLayer(card) {
  const existing = card.querySelector(".shatter-layer");
  if (existing) existing.remove();
  const img = card.querySelector("img");
  if (!img) return;
  const rect = card.getBoundingClientRect();
  const width = rect.width || img.clientWidth || 0;
  const height = rect.height || img.clientHeight || 0;
  if (!width || !height) return;
  const layer = document.createElement("div");
  layer.className = "shatter-layer";
  const targetSize = 20;
  const cols = Math.min(24, Math.max(12, Math.round(width / targetSize)));
  const rows = Math.min(18, Math.max(10, Math.round(height / targetSize)));
  const shardWidth = width / cols;
  const shardHeight = height / rows;
  const bgUrl = img.currentSrc || img.src;
  layer.style.left = `${rect.left}px`;
  layer.style.top = `${rect.top}px`;
  layer.style.width = `${width}px`;
  layer.style.height = `${height}px`;
  const fragment = document.createDocumentFragment();
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const shard = document.createElement("div");
      shard.className = "shard";
      const baseLeft = c * shardWidth;
      const baseTop = r * shardHeight;
      const sizeScale = 0.45 + Math.random() * 0.35;
      const shardW = shardWidth * sizeScale;
      const shardH = shardHeight * sizeScale;
      const jitterX = Math.random() * (shardWidth - shardW);
      const jitterY = Math.random() * (shardHeight - shardH);
      const left = baseLeft + jitterX;
      const top = baseTop + jitterY;
      const tx = (Math.random() - 0.5) * width * 0.28;
      const ty = height * (0.55 + Math.random() * 0.75);
      const rot = (Math.random() - 0.5) * 90;
      const scaleEnd = (0.15 + Math.random() * 0.25).toFixed(2);
      const baseDelay = (r / rows) * 0.22;
      shard.style.left = `${left}px`;
      shard.style.top = `${top}px`;
      shard.style.width = `${shardW}px`;
      shard.style.height = `${shardH}px`;
      shard.style.backgroundImage = `url('${bgUrl}')`;
      shard.style.backgroundSize = `${width}px ${height}px`;
      shard.style.backgroundPosition = `-${left}px -${top}px`;
      shard.style.setProperty("--tx", `${tx}px`);
      shard.style.setProperty("--ty", `${ty}px`);
      shard.style.setProperty("--rot", `${rot}deg`);
      shard.style.setProperty("--scale-end", scaleEnd);
      shard.style.animationDelay = `${baseDelay + Math.random() * 0.2}s`;
      fragment.appendChild(shard);
    }
  }
  layer.appendChild(fragment);
  card.appendChild(layer);
  setTimeout(() => {
    if (layer.isConnected) layer.remove();
  }, 1500);
}


async function wipeOutGrid(mode = "normal") {
  const cards = [...gridEl.querySelectorAll(".card")];
  if (!cards.length) return;
  let delay = 260;
  cards.forEach((card) => {
    if (mode === "special") {
      const state = markState[card.dataset.id] || "none";
      if (state === "delete") {
        applyShatterOut(card);
        delay = Math.max(delay, 520);
        return;
      }
      if (state === "album") {
        applyFavOut(card);
        delay = Math.max(delay, 520);
        return;
      }
    }
    applyWipeOut(card);
  });
  await sleep(delay);
}

function filterModeLabel(mode) {
  if (mode === "exclude-prescreen") return "预筛相册除外";
  if (mode === "exclude-albums") return "所有相册照片除外";
  return "所有图片";
}

const themeOptions = ["rainbow", "gradient", "blur"];

function setCustomCountVisible(show) {
  if (!gridCustom) return;
  gridCustom.classList.toggle("hidden", !show);
}

function syncGridPresetUI() {
  if (!gridPreset) return;
  const isCustom = gridPreset.value === "custom";
  setCustomCountVisible(isCustom);
}

function updateBlurBackground(list = assets) {
  if (blurTimer) {
    clearTimeout(blurTimer);
    blurTimer = null;
  }
  if (currentTheme !== "blur") {
    blurCurrentUrl = null;
    if (blurLayerA) blurLayerA.classList.remove("active");
    if (blurLayerB) blurLayerB.classList.remove("active");
    return;
  }
  const first = (list || []).find((item) => item && item.id) || null;
  if (!first) {
    blurCurrentUrl = null;
    if (blurLayerA) blurLayerA.classList.remove("active");
    if (blurLayerB) blurLayerB.classList.remove("active");
    return;
  }
  const nextUrl = `/thumb/${first.id}`;
  if (nextUrl === blurCurrentUrl) return;
  const nextLayer = blurActive === 0 ? blurLayerB : blurLayerA;
  const prevLayer = blurActive === 0 ? blurLayerA : blurLayerB;
  if (!nextLayer || !prevLayer) return;

  const img = new Image();
  img.onload = () => {
    nextLayer.style.backgroundImage = `url('${nextUrl}')`;
    nextLayer.classList.add("active");
    prevLayer.classList.remove("active");
    blurActive = blurActive === 0 ? 1 : 0;
    blurCurrentUrl = nextUrl;
  };
  img.onerror = () => {
    blurCurrentUrl = null;
  };
  img.src = nextUrl;
}

function applyTheme(theme) {
  const next = themeOptions.includes(theme) ? theme : "rainbow";
  themeOptions.forEach((t) => document.body.classList.remove(`theme-${t}`));
  document.body.classList.add(`theme-${next}`);
  currentTheme = next;
  if (themeSelect) themeSelect.value = next;
  updateBlurBackground();
  renderDebugConfig();
}


function computeGridColumns(count) {
  const gridWidth = gridEl.clientWidth || (gridEl.parentElement ? gridEl.parentElement.clientWidth : 0) || window.innerWidth;
  const gap = parseFloat(getComputedStyle(gridEl).gap) || 10;
  const viewportWidth = window.visualViewport?.width || window.innerWidth;
  const viewportHeight = window.visualViewport?.height || window.innerHeight || 1;
  const aspect = viewportWidth / Math.max(1, viewportHeight);
  let minCardWidth = 300;
  if (viewportWidth <= 480) {
    // Narrow phones work best as a compact two-column review surface.
    minCardWidth = 132;
  } else if (viewportWidth <= 900 && aspect >= 0.95 && aspect <= 1.5) {
    // Foldable and tablet-like square screens can comfortably show three columns.
    minCardWidth = 170;
  } else if (viewportWidth <= 900) {
    minCardWidth = 210;
  } else if (aspect >= 4 / 3) {
    minCardWidth = 250;
  } else if (aspect >= 1.1) {
    minCardWidth = 280;
  }
  const maxColsByWidth = Math.max(1, Math.floor((gridWidth + gap) / (minCardWidth + gap)));
  let maxColsByCount = 3;
  if (count < 10) maxColsByCount = 3;
  else if (count < 25) maxColsByCount = 4;
  else if (count < 36) maxColsByCount = 5;
  else maxColsByCount = 6;
  return Math.max(1, Math.min(count, maxColsByWidth, maxColsByCount));
}

function applyOrphanCentering(count, cols) {
  const cards = [...gridEl.querySelectorAll(".card")];
  const total = Math.min(count, cards.length);
  cards.forEach((card) => {
    card.style.gridColumn = "";
    card.style.justifySelf = "";
    card.style.width = "";
  });
  if (cols <= 1 || total <= cols) return;
  if (total % cols !== 1) return;
  const last = cards[cards.length - 1];
  if (!last) return;
  const gap = parseFloat(getComputedStyle(gridEl).gap) || 0;
  const gridWidth = gridEl.clientWidth || 0;
  if (!gridWidth) return;
  const colWidth = (gridWidth - gap * (cols - 1)) / cols;
  last.style.gridColumn = "1 / -1";
  last.style.justifySelf = "center";
  last.style.width = `${colWidth}px`;
}

function applyGridLayout() {
  const cols = computeGridColumns(currentCount);
  currentColumns = cols;
  gridEl.style.setProperty("--grid-cols", String(cols));
  applyOrphanCentering(currentCount, cols);
}

function toast(message, type = "success", timeout = 2400) {
  const div = document.createElement("div");
  div.className = `toast ${type}`;
  div.textContent = message;
  toastContainer.appendChild(div);
  setTimeout(() => {
    div.style.opacity = "0";
    div.style.transform = "translateX(20px)";
  }, timeout - 400);
  setTimeout(() => div.remove(), timeout);
}

function updateStatus(text) {
  const el = document.getElementById("status-text");
  if (el) el.textContent = text;
}


function showMissingPlaceholder(card, label = "资源不可用") {
  if (card.querySelector(".missing-placeholder")) return;
  const sands = card.querySelectorAll(".sand-placeholder, .gif-placeholder");
  sands.forEach((node) => node.remove());
  const placeholder = document.createElement("div");
  placeholder.className = "missing-placeholder";
  placeholder.textContent = label;
  card.appendChild(placeholder);
  const img = card.querySelector("img");
  if (img) {
    img.style.opacity = "0";
  }
}

function createSandPlaceholder(text = "加载中") {
  const placeholder = document.createElement("div");
  placeholder.className = "sand-placeholder";
  const label = document.createElement("span");
  label.className = "sand-text";
  label.textContent = text;
  placeholder.appendChild(label);
  return placeholder;
}

function fadeOutSand(placeholder) {
  if (!placeholder || !placeholder.isConnected) return;
  placeholder.classList.remove("visible");
  placeholder.classList.add("fade-out");
  setTimeout(() => {
    if (placeholder.isConnected) {
      placeholder.remove();
    }
  }, 900);
}

function applyMarkVisual(card, badge, state) {
  card.classList.remove("selected-delete", "selected-fav");
  badge.classList.add("hidden");
  if (state === "delete") {
    card.classList.add("selected-delete");
    badge.textContent = "待删除";
    badge.classList.remove("hidden");
  } else if (state === "album") {
    card.classList.add("selected-fav");
    badge.textContent = "收藏到相册";
    badge.classList.remove("hidden");
  }
}

function buildSnapshotAssets() {
  return (assets || []).map((item) => ({
    id: item.id,
    fileName: item.fileName,
    createdAt: item.createdAt,
    type: item.type,
    mimeType: item.mimeType,
    isGif: Boolean(item.isGif),
    mark: markState[item.id] || "none",
  }));
}

async function rememberCurrentRound() {
  if (!assets.length) return;
  try {
    await fetchJSON("/api/remember-round", {
      method: "POST",
      body: JSON.stringify({
        count: currentCount,
        filter_mode: currentFilter,
        theme: currentTheme,
        theme: currentTheme,
        assets: buildSnapshotAssets(),
      }),
    });
  } catch (err) {
    console.warn("remember round failed", err);
  }
}

async function rememberForwardRound() {
  if (!assets.length) return;
  try {
    await fetchJSON("/api/remember-forward", {
      method: "POST",
      body: JSON.stringify({
        count: currentCount,
        filter_mode: currentFilter,
        theme: currentTheme,
        assets: buildSnapshotAssets(),
      }),
    });
  } catch (err) {
    console.warn("remember forward failed", err);
  }
}

function applyRoundSettings(data) {
  if (data?.round_count) {
    currentCount = Number(data.round_count) || currentCount;
    applyGridLayout();
    if (gridCountInput) gridCountInput.value = currentCount;
    const presetValue = String(currentCount);
    if ([...gridPreset.options].some((o) => o.value === presetValue)) {
      gridPreset.value = presetValue;
    } else {
      gridPreset.value = "custom";
    }
    syncGridPresetUI();
  }
  if (data?.round_filter) {
    currentFilter = data.round_filter;
    filterSelect.value = currentFilter;
  }
}

function updatePrevButtonState(data) {
  if (!btnPrev) return;
  isPreviousView = Boolean(data?.is_previous);
  prevAvailable = Boolean(data?.previous_available);
  const canPrev = prevAvailable && !isPreviousView;
  btnPrev.disabled = !canPrev;
  btnPrev.classList.toggle("prev-enabled", canPrev);
}

function beginLoading(btn) {
  if (!btn) return;
  btn.disabled = true;
  btn.classList.add("loading", "rainbow-on", "ring-anim");
  const ringDuration = btn.classList.contains("prev-button") ? 375 : 1500;
  setTimeout(() => btn.classList.remove("ring-anim"), ringDuration);
}

function endLoading(btn, startTime, ripplePromise, finalDisabled = false) {
  if (!btn) return;
  const elapsed = Date.now() - startTime;
  const minWait = Math.max(0, 300 - elapsed);
  const waitScroll = waitForScrollTop(2000);
  Promise.all([waitScroll, sleep(minWait), ripplePromise]).finally(() => {
    document.body.classList.remove("frame-active");
    btn.classList.add("loading-glow-fade");
    setTimeout(() => {
      btn.disabled = finalDisabled;
      btn.classList.remove("loading", "loading-glow-fade", "rainbow-on");
    }, 450);
  });
}

function renderGrid(list) {
  gridEl.innerHTML = "";
  markState = {};
  updateBlurBackground(list);
  const loadPromises = [];

  list.forEach((item) => {
    const initialMark = item.mark || "none";
    markState[item.id] = initialMark;
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.id = item.id;

    const img = document.createElement("img");
    const isGif =
      Boolean(item.isGif) ||
      ((item.mimeType || "").toLowerCase() === "image/gif") ||
      ((item.fileName || "").toLowerCase().endsWith(".gif"));
    img.alt = item.fileName || item.id;
    img.decoding = "async";

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = item.fileName || item.id;

    const badge = document.createElement("div");
    badge.className = "badge hidden";
    card.appendChild(badge);

    const sand = createSandPlaceholder(isGif ? "GIF 加载中" : "加载中");
    card.appendChild(sand);
    requestAnimationFrame(() => {
      if (sand.isConnected) {
        sand.classList.add("visible");
      }
    });

    if (item.missing) {
      showMissingPlaceholder(card);
      card.appendChild(meta);
      applyMarkVisual(card, badge, initialMark);
      gridEl.appendChild(card);
      return;
    }

    if (isGif) {
      card.classList.add("gif-loading");
      const placeholder = sand;
      img.loading = "eager";
      const loadPromise = new Promise((resolve) => {
        const done = () => resolve(true);
        const showImage = () => {
          if (!card.isConnected) return done();
          card.classList.remove("gif-loading");
          fadeOutSand(placeholder);
          applyWipeIn(card);
          card.classList.add("img-loaded");
          if (meta.isConnected) {
            card.insertBefore(img, meta);
          } else {
            card.appendChild(img);
          }
          done();
        };
        const fallbackToThumb = () => {
          if (!card.isConnected) return done();
          placeholder.textContent = "GIF 原图失败，尝试缩略图";
          img.addEventListener(
            "load",
            () => {
              showImage();
            },
            { once: true }
          );
          img.addEventListener(
            "error",
            () => {
              if (!card.isConnected) return done();
              showMissingPlaceholder(card);
              card.classList.remove("gif-loading");
              done();
            },
            { once: true }
          );
          img.src = `/thumb/${item.id}`;
        };
        img.addEventListener(
          "load",
          () => {
            showImage();
          },
          { once: true }
        );
        img.addEventListener(
          "error",
          () => {
            fallbackToThumb();
          },
          { once: true }
        );
        img.src = `/media/${item.id}`;
      });
      loadPromises.push(loadPromise);
    } else {
      img.loading = "eager";
      const loadPromise = new Promise((resolve) => {
        const done = () => resolve(true);
        img.addEventListener(
          "load",
          () => {
            if (!card.isConnected) return done();
            fadeOutSand(sand);
            applyWipeIn(card);
            card.classList.add("img-loaded");
            if (meta.isConnected) {
              card.insertBefore(img, meta);
            } else {
              card.appendChild(img);
            }
            done();
          },
          { once: true }
        );
        img.addEventListener(
          "error",
          () => {
            sand.remove();
            showMissingPlaceholder(card);
            done();
          },
          { once: true }
        );
        img.src = `/thumb/${item.id}`;
      });
      loadPromises.push(loadPromise);
    }

    card.appendChild(meta);
    applyMarkVisual(card, badge, initialMark);

    card.addEventListener("click", () => {
      const current = markState[item.id];
      let next = "delete";
      if (current === "delete") next = "album";
      else if (current === "album") next = "none";
      markState[item.id] = next;
      applyMarkVisual(card, badge, next);
    });

    card.addEventListener("wheel", (e) => {
      e.preventDefault();
      const rect = card.getBoundingClientRect();
      const ox = rect.width ? ((e.clientX - rect.left) / rect.width) * 100 : 50;
      const oy = rect.height ? ((e.clientY - rect.top) / rect.height) * 100 : 50;
      const currentScale =
        card.dataset.scale === "3" ? 3 :
        card.dataset.scale === "2" ? 2 :
        card.dataset.scale === "1.5" ? 1.5 : 1;
      if (currentScale === 1) {
        const origin = `${ox}% ${oy}%`;
        card.dataset.originX = ox;
        card.dataset.originY = oy;
        card.style.transformOrigin = origin;
        img.style.transformOrigin = origin;
      } else if (card.dataset.originX && card.dataset.originY) {
        const origin = `${card.dataset.originX}% ${card.dataset.originY}%`;
        card.style.transformOrigin = origin;
        img.style.transformOrigin = origin;
      } else {
        card.style.transformOrigin = "50% 50%";
        img.style.transformOrigin = "50% 50%";
      }

      if (e.deltaY < 0) {
        if (currentScale === 1) {
          card.dataset.scale = "1.5";
          card.classList.add("zoomed-150");
          card.classList.remove("zoomed-200", "zoomed-300");
        } else if (currentScale === 1.5) {
          card.dataset.scale = "2";
          card.classList.add("zoomed-200");
          card.classList.remove("zoomed-150", "zoomed-300");
        } else {
          card.dataset.scale = "3";
          card.classList.add("zoomed-300");
          card.classList.remove("zoomed-150", "zoomed-200");
        }
      } else {
        if (currentScale === 3) {
          card.dataset.scale = "2";
          card.classList.add("zoomed-200");
          card.classList.remove("zoomed-150", "zoomed-300");
        } else if (currentScale === 2) {
          card.dataset.scale = "1.5";
          card.classList.add("zoomed-150");
          card.classList.remove("zoomed-200", "zoomed-300");
        } else {
          const origin =
            card.dataset.originX && card.dataset.originY
              ? `${card.dataset.originX}% ${card.dataset.originY}%`
              : "50% 50%";
          card.style.transformOrigin = origin;
          img.style.transformOrigin = origin;
          card.dataset.scale = "1";
          card.classList.remove("zoomed-150", "zoomed-200", "zoomed-300");

          if (card._resetOriginHandler) {
            card.removeEventListener("transitionend", card._resetOriginHandler);
          }
          const handler = () => {
            card.style.transformOrigin = "50% 50%";
            img.style.transformOrigin = "50% 50%";
            delete card.dataset.originX;
            delete card.dataset.originY;
            card.removeEventListener("transitionend", handler);
            delete card._resetOriginHandler;
          };
          card._resetOriginHandler = handler;
          card.addEventListener("transitionend", handler);
          setTimeout(handler, 360);
        }
      }
    });

    gridEl.appendChild(card);
  });

  requestAnimationFrame(() => {
    applyGridLayout();
  });
  return Promise.allSettled(loadPromises);
}

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

async function saveUiSettings() {
  try {
    await fetchJSON("/api/ui-settings", {
      method: "POST",
      body: JSON.stringify({
        count: currentCount,
        filter_mode: currentFilter,
        theme: currentTheme,
      }),
    });
  } catch (err) {
    console.warn("save ui settings failed", err);
  }
}

async function loadRandom() {
  updateStatus("加载中...");
  const wipePromise = wipeOutGrid();
  try {
    const data = await fetchJSON(
      `/api/random?count=${currentCount}&filter_mode=${currentFilter}`
    );
    applyRoundSettings(data);
    assets = data.assets || [];
    await wipePromise;
    renderGrid(assets);
    updatePrevButtonState(data);
    const info = [
      `返回 ${data.total_returned}/${data.requested}`,
      `已看记录：${data.seen_count}`,
      `删除累计：${data.deleted_total || 0}，收藏累计：${data.favorited_total || 0}`,
      `数量：${currentCount}`,
      `过滤：${filterModeLabel(currentFilter)}`,
    ];
    if (data.pages_used) {
      info.push(`页码：${(data.pages_used || []).join(",")} / ${data.total_pages_considered || "?"}`);
    }
    if (data.message) info.push(data.message);
    updateStatus(info.join(" · "));
    if (data.used_seen_fallback) {
      toast("未见图片不足，包含部分已看图片", "error", 3000);
    }
    renderDebugRange(data);
  } catch (err) {
    updateStatus("加载失败");
    toast(`加载失败：${err.message}`, "error");
  }
}

async function refreshRound() {
  updateStatus("放弃本轮并重新抽取...");
  const wipePromise = wipeOutGrid();
  try {
    const data = await fetchJSON(
      `/api/refresh?count=${currentCount}&filter_mode=${currentFilter}`,
      { method: "POST" }
    );
    applyRoundSettings(data);
    assets = data.assets || [];
    await wipePromise;
    renderGrid(assets);
    updatePrevButtonState(data);
    const info = [
      `返回 ${data.total_returned}/${data.requested}`,
      `已看记录：${data.seen_count}`,
      `删除累计：${data.deleted_total || 0}，收藏累计：${data.favorited_total || 0}`,
      `数量：${currentCount}`,
      `过滤：${filterModeLabel(currentFilter)}`,
    ];
    if (data.pages_used) {
      info.push(`页码：${(data.pages_used || []).join(",")} / ${data.total_pages_considered || "?"}`);
    }
    if (data.message) info.push(data.message);
    updateStatus(info.join(" · "));
    renderDebugRange(data);
  } catch (err) {
    updateStatus("刷新失败");
    toast(`刷新失败：${err.message}`, "error");
  }
}

async function resetSeen() {
  if (!confirm("确定要清空已看记录吗？")) return;
  try {
    await fetchJSON("/api/reset-seen", { method: "POST" });
    toast("已重置已看记录");
    await loadRandom();
  } catch (err) {
    toast(`重置失败：${err.message}`, "error");
  }
}

async function nextStep() {
  const deleteIds = [];
  const albumIds = [];
  Object.entries(markState).forEach(([id, state]) => {
    if (state === "delete") deleteIds.push(id);
    if (state === "album") albumIds.push(id);
  });
  const snapshotAssets = buildSnapshotAssets();
  const wipePromise = wipeOutGrid("special");
  const ripplePromise = triggerRippleSequence(btnNext);
  document.body.classList.add("frame-active");
  beginLoading(btnNext);
  scrollToTopAccelerate(900);
  const startTime = Date.now();
  try {
    const data = await fetchJSON("/api/next", {
      method: "POST",
      body: JSON.stringify({
        delete_ids: deleteIds,
        album_ids: albumIds,
        count: currentCount,
        filter_mode: currentFilter,
        theme: currentTheme,
        current_count: currentCount,
        current_filter: currentFilter,
        current_assets: snapshotAssets,
      }),
    });
    applyRoundSettings(data);
    assets = data.assets || [];
    await wipePromise;
    renderGrid(assets);
    updatePrevButtonState(data);
    const info = [
      `返回 ${data.total_returned}/${data.requested}`,
      `已看记录：${data.seen_count}`,
      `删除累计：${data.deleted_total || 0}，收藏累计：${data.favorited_total || 0}`,
      `过滤：${filterModeLabel(currentFilter)}`,
    ];
    if (data.pages_used) {
      info.push(`页码：${(data.pages_used || []).join(",")} / ${data.total_pages_considered || "?"}`);
    }
    if (data.message) info.push(data.message);
    updateStatus(info.join(" · "));
    if (data.failed_delete?.length) toast(`删除失败 ${data.failed_delete.length} 张`, "error");
    if (data.failed_album?.length) toast(`加入相册失败 ${data.failed_album.length} 张`, "error");
  } catch (err) {
    toast(`操作失败：${err.message}`, "error");
  } finally {
    endLoading(btnNext, startTime, ripplePromise, false);
  }
}

async function loadPrevious() {
  if (!btnPrev || btnPrev.disabled) return;
  await rememberForwardRound();
  updateStatus("加载上一轮...");
  const wipePromise = wipeOutGrid();
  const ripplePromise = triggerRippleSequence(btnPrev);
  document.body.classList.add("frame-active");
  beginLoading(btnPrev);
  scrollToTopAccelerate(900);
  const startTime = Date.now();
  let finalDisabled = false;
  try {
    const data = await fetchJSON("/api/previous");
    applyRoundSettings(data);
    assets = data.assets || [];
    await wipePromise;
    renderGrid(assets);
    updatePrevButtonState(data);
    finalDisabled = btnPrev.disabled;
    const info = [
      `返回 ${data.total_returned}/${data.requested}`,
      `已看记录：${data.seen_count}`,
      `删除累计：${data.deleted_total || 0}，收藏累计：${data.favorited_total || 0}`,
      `过滤：${filterModeLabel(currentFilter)}`,
    ];
    if (data.message) info.push(data.message);
    updateStatus(info.join(" · "));
    renderDebugRange(data);
  } catch (err) {
    toast(`返回上一轮失败：${err.message}`, "error");
  } finally {
    endLoading(btnPrev, startTime, ripplePromise, finalDisabled);
  }
}

function triggerRippleOnce(sourceEl, duration = 1500, delay = 0) {
  return Promise.resolve();
}

function triggerRippleSequence(sourceEl) {
  return Promise.resolve();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function scrollToTopAccelerate(duration = 900) {
  const start = window.scrollY || 0;
  if (start <= 0) return;
  const startTime = performance.now();
  const easeIn = (t) => t * t * t;
  function step(now) {
    const elapsed = (now - startTime) / duration;
    const t = Math.min(1, Math.max(0, elapsed));
    const eased = easeIn(t);
    window.scrollTo(0, Math.round(start * (1 - eased)));
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function waitForScrollTop(timeout = 1500) {
  return new Promise((resolve) => {
    const start = performance.now();
    function check() {
      if (window.scrollY <= 2) return resolve();
      if (performance.now() - start > timeout) return resolve();
      requestAnimationFrame(check);
    }
    check();
  });
}

function renderDebugConfig() {
  document.getElementById("dbg-base-url").textContent = config.immichBaseUrl || "未设置";
  document.getElementById("dbg-api-key").textContent = config.apiKeyConfigured ? "已配置" : "未配置";
  document.getElementById("dbg-grid").textContent = String(currentCount);
  const themeEl = document.getElementById("dbg-theme");
  if (themeEl) themeEl.textContent = currentTheme;
  document.getElementById("dbg-db-path").textContent = config.dbPath || "-";
}

async function loadDebugStatus() {
  const target = document.getElementById("dbg-health");
  try {
    const data = await fetchJSON("/api/debug/status");
    if (data.ok) {
      target.innerHTML = `<span class="log-status-ok">连接正常</span> (HTTP ${data.http_status || "-"})`;
    } else {
      target.innerHTML = `<span class="log-status-bad">连接异常</span> (HTTP ${data.http_status || "?"}) ${data.error || ""}`;
    }
  } catch (err) {
    target.innerHTML = `<span class="log-status-bad">请求失败</span> ${err.message}`;
  }
}

function formatLogLine(log) {
  const parts = [
    log.timestamp,
    log.method,
    log.path,
    log.status != null ? `HTTP ${log.status}` : "-",
  ];
  if (log.error) parts.push(log.error);
  if (log.correlation_id) parts.push(`cid=${log.correlation_id}`);
  const base = parts.join(" | ");
  if (log.raw_error) {
    return `${base}\n  ↳ ${JSON.stringify(log.raw_error).slice(0, 180)}`;
  }
  return base;
}

async function loadDebugLogs() {
  const target = document.getElementById("dbg-logs");
  try {
    const data = await fetchJSON("/api/debug/logs");
    if (!data.logs || !data.logs.length) {
      target.textContent = "暂无日志";
      return;
    }
    target.innerHTML = "";
    data.logs.forEach((log) => {
      const div = document.createElement("div");
      div.className = "log-line";
      div.innerHTML = formatLogLine(log).replace(/\n/g, "<br>");
      target.appendChild(div);
    });
  } catch (err) {
    target.textContent = `获取失败：${err.message}`;
  }
}

async function loadDebugDB() {
  const target = document.getElementById("dbg-db-info");
  try {
    const data = await fetchJSON("/api/debug/db");
    const parts = [
      `配置路径: ${data.db_path}`,
      `实际路径: ${data.active_db_path || data.db_path}`,
      `存在(实际): ${data.exists_active ? "是" : "否"}`,
      `存在(配置): ${data.exists_config ? "是" : "否"}`,
    ];
    if (data.size_bytes != null) parts.push(`大小: ${data.size_bytes} bytes`);
    if (data.mtime) parts.push(`修改时间: ${data.mtime}`);
    if (data.error) parts.push(`错误: ${data.error}`);
    target.textContent = parts.join(" | ");
  } catch (err) {
    target.textContent = `获取失败：${err.message}`;
  }
}

async function fixDebugDB() {
  const target = document.getElementById("dbg-db-info");
  target.textContent = "修复中...";
  try {
    const data = await fetchJSON("/api/debug/fix-db", { method: "POST" });
    if (data.success) {
      toast("数据库已修复/初始化", "success");
      loadDebugDB();
    } else {
      toast(`修复失败：${data.error || "未知错误"}`, "error");
      target.textContent = `修复失败：${data.error || "未知错误"}`;
    }
  } catch (err) {
    target.textContent = `修复失败：${err.message}`;
    toast(`修复失败：${err.message}`, "error");
  }
}

function toggleDebugPanel(show) {
  if (show === undefined) {
    debugPanel.classList.toggle("visible");
  } else if (show) {
    debugPanel.classList.add("visible");
  } else {
    debugPanel.classList.remove("visible");
  }
  if (debugPanel.classList.contains("hidden")) {
    debugPanel.classList.remove("hidden");
  }
  if (debugPanel.classList.contains("visible")) {
    renderDebugConfig();
    loadDebugStatus();
    loadDebugLogs();
    loadDebugDB();
  }
}

document.getElementById("btn-refresh").addEventListener("click", refreshRound);
document.getElementById("btn-reset").addEventListener("click", resetSeen);
btnDebug.addEventListener("click", () => toggleDebugPanel(true));
btnDebugClose.addEventListener("click", () => toggleDebugPanel(false));
btnDebugRefresh.addEventListener("click", () => {
  loadDebugStatus();
  loadDebugLogs();
  loadDebugDB();
});
document.getElementById("btn-db-fix").addEventListener("click", fixDebugDB);
document.getElementById("btn-next").addEventListener("click", nextStep);
if (btnPrev) {
  btnPrev.addEventListener("click", loadPrevious);
}

function revealPrevButton() {
  document.body.classList.add("prev-visible");
}

window.addEventListener("scroll", () => {
  if (window.scrollY > 4) {
    revealPrevButton();
  }
});
window.addEventListener("wheel", revealPrevButton, { passive: true });
window.addEventListener("touchmove", revealPrevButton, { passive: true });

function applyGrid(count) {
  currentCount = count;
  applyGridLayout();
  toast(`已设置数量 ${count}，将在下一轮生效`);
  saveUiSettings();
}

gridPreset.addEventListener("change", () => {
  const val = gridPreset.value;
  syncGridPresetUI();
  if (val === "custom") {
    return;
  }
  const count = parseInt(val, 10);
  if (!count) return;
  applyGrid(count);
});

gridApplyBtn.addEventListener("click", () => {
  const count = parseInt((gridCountInput && gridCountInput.value) || "0", 10);
  if (!count || count <= 0) {
    toast("请输入有效的数量", "error");
    return;
  }
  applyGrid(count);
});

filterSelect.addEventListener("change", () => {
  currentFilter = filterSelect.value;
  toast(`已切换过滤：${filterModeLabel(currentFilter)}，将在下一轮生效`);
  saveUiSettings();
});

if (themeSelect) {
  themeSelect.addEventListener("change", () => {
    applyTheme(themeSelect.value);
    updateBlurBackground();
    saveUiSettings();
  });
}

function renderDebugRange(data) {
  if (!dbgRange) return;
  const pages = data.pages_used ? data.pages_used.join(",") : "-";
  const total = data.total_pages_considered || "?";
  dbgRange.textContent = `页码: ${pages} / ${total}`;
}

applyGridLayout();
if (gridCountInput) gridCountInput.value = currentCount;
const presetValue = String(currentCount);
if ([...gridPreset.options].some((o) => o.value === presetValue)) {
  gridPreset.value = presetValue;
} else {
  gridPreset.value = "custom";
}
syncGridPresetUI();
filterSelect.value = currentFilter;
applyTheme(currentTheme);
renderDebugConfig();
window.addEventListener("resize", () => {
  applyGridLayout();
});
loadRandom();
