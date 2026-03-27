/// <reference types="chrome" />

// Background Service Worker for Landmap Scout

console.log("Landmap Scout: Service Worker Initialized");

// State Interfaces
interface TileData {
    url: string;
    timestamp: number;
    imageData?: string; // Base64 encoded image
    bbox?: number[]; // [minLon, minLat, maxLon, maxLat]
    width?: number;
    height?: number;
}

interface RecordingState {
    isRecording: boolean;
    sessionName: string;
    tiles: TileData[];
}

// Initial State
const initialState: RecordingState = {
    isRecording: false,
    sessionName: "Default Session",
    tiles: []
};

// Constants
const TILE_SIZE = 256;
const MAX_RETRIES = 5; // Increased retries
const RETRY_DELAY = 1000;
const MIN_IMAGE_SIZE_BYTES = 100; // Lowered to 100 bytes - transparent 256x256 tiles can be very small

// Track BBOXes being downloaded to prevent duplicate downloads
const downloadingBboxes = new Set<string>();
// Track already successfully processed BBOXes for this browser session to prevent re-downloading
const processedBboxes = new Set<string>();

// Storage Mutex/Queue to prevent race conditions
let tileQueue: TileData[] = [];
let isProcessingQueue = false;

async function processQueue() {
    if (isProcessingQueue || tileQueue.length === 0) return;
    isProcessingQueue = true;

    try {
        const tilesToPush = [...tileQueue];
        tileQueue = [];

        const result = await chrome.storage.local.get(['tiles']);
        const currentTiles = (result.tiles as TileData[]) || [];

        // Final deduplication before saving by bbox if available, else by URL
        const existingKeys = new Set(currentTiles.map(t => t.bbox?.join(',') || t.url));
        const newUniqueTiles = tilesToPush.filter(t => {
            const key = t.bbox?.join(',') || t.url;
            return !existingKeys.has(key);
        });

        if (newUniqueTiles.length > 0) {
            const updatedTiles = [...currentTiles, ...newUniqueTiles];
            await chrome.storage.local.set({ tiles: updatedTiles });
            updateBadge(updatedTiles.length);
            console.log(`[Scout] Saved ${newUniqueTiles.length} new tiles. Total: ${updatedTiles.length}`);

            // Mark as processed permanently for this browser session
            newUniqueTiles.forEach(t => {
                if (t.bbox) processedBboxes.add(t.bbox.join(','));
            });
        }
    } catch (error) {
        console.error("[Scout] Failed to process tile queue:", error);
    } finally {
        isProcessingQueue = false;
        if (tileQueue.length > 0) {
            setTimeout(processQueue, 100);
        }
    }
}

function queueTile(tile: TileData) {
    tileQueue.push(tile);
    processQueue();
}

// Initialize Storage on Install
chrome.runtime.onInstalled.addListener(() => {
    chrome.storage.local.get(['isRecording', 'tiles'], (result) => {
        if (result.isRecording === undefined) {
            chrome.storage.local.set(initialState);
        }
        if (result.tiles) {
            (result.tiles as TileData[]).forEach(t => {
                if (t.bbox) processedBboxes.add(t.bbox.join(','));
            });
        }
    });
});

// Update badge count
function updateBadge(count: number) {
    chrome.action.setBadgeText({ text: count.toString() });
    chrome.action.setBadgeBackgroundColor({ color: "#22c55e" });
}

// Message Listener (Communication with Popup)
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "START_RECORDING") {
        const sessionName = message.sessionName || `Session-${Date.now()}`;
        chrome.storage.local.set({
            isRecording: true,
            sessionName: sessionName,
            tiles: []
        }, () => {
            console.log(`[Scout] Recording Started: ${sessionName}`);
            downloadingBboxes.clear();
            processedBboxes.clear();
            updateBadge(0);
            sendResponse({ success: true });
        });
        return true;
    }

    if (message.type === "STOP_RECORDING") {
        chrome.storage.local.set({ isRecording: false }, () => {
            console.log("[Scout] Recording Stopped");
            chrome.action.setBadgeText({ text: "OFF" });
            sendResponse({ success: true });
        });
        return true;
    }

    if (message.type === "CLEAR_SESSION") {
        chrome.storage.local.set({ tiles: [] }, () => {
            downloadingBboxes.clear();
            processedBboxes.clear();
            updateBadge(0);
            sendResponse({ success: true });
        });
        return true;
    }
});

// Helper to parse URL parameters
function parseUrlParams(url: string): Partial<TileData> {
    try {
        const urlObj = new URL(url);
        const bboxParam = urlObj.searchParams.get('bbox') || urlObj.searchParams.get('BBOX');
        const widthParam = urlObj.searchParams.get('width') || urlObj.searchParams.get('WIDTH');
        const heightParam = urlObj.searchParams.get('height') || urlObj.searchParams.get('HEIGHT');

        let bbox: number[] | undefined;
        if (bboxParam) {
            const parts = bboxParam.split(',').map(Number);
            if (parts.length === 4 && parts.every(n => !isNaN(n))) {
                bbox = parts;
            }
        }

        return {
            bbox,
            width: widthParam ? parseInt(widthParam) : undefined,
            height: heightParam ? parseInt(heightParam) : undefined
        };
    } catch (e) {
        console.warn("[Scout] Error parsing URL params:", url, e);
        return {};
    }
}

// Helper to normalize URL for constant tile size
function getNormalizedUrl(url: string): string {
    try {
        const urlObj = new URL(url);
        urlObj.searchParams.set('WIDTH', TILE_SIZE.toString());
        urlObj.searchParams.set('HEIGHT', TILE_SIZE.toString());
        urlObj.searchParams.set('width', TILE_SIZE.toString());
        urlObj.searchParams.set('height', TILE_SIZE.toString());
        return urlObj.toString();
    } catch (e) {
        return url;
    }
}

// Utility for delay
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Helper to download image and convert to Base64
async function downloadImage(url: string, retries = MAX_RETRIES, retryDelay = RETRY_DELAY): Promise<string | undefined> {
    for (let i = 0; i < retries; i++) {
        try {
            if (i > 0) {
                const backoff = retryDelay * Math.pow(2, i - 1);
                console.log(`[Scout] Retry ${i}/${retries} after ${backoff}ms for: ${url}`);
                await delay(backoff);
            }

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 15000); // 15s timeout

            const response = await fetch(url, {
                signal: controller.signal,
                cache: 'no-store' // Force fresh download
            });
            clearTimeout(timeoutId);

            if (!response.ok) {
                console.warn(`[Scout] Download failed with HTTP ${response.status} for ${url}`);
                if (response.status >= 500) continue; // Retry on server error
                return undefined; // Don't retry on client error like 404
            }

            const blob = await response.blob();
            const contentType = blob.type;

            // Robustness: save only after completely download (verified by length and type)
            if (!contentType.startsWith('image/')) {
                console.warn(`[Scout] Response is not an image (Content-Type: ${contentType}):`, url);
                continue;
            }

            // identify the issue: thin/transparent tiles can be very small, but 0 is definitely wrong
            if (blob.size < MIN_IMAGE_SIZE_BYTES) {
                console.log(`[Scout] Image too small (${blob.size} bytes), likely incomplete or blank. Attempt ${i + 1}/${retries}.`, url);
                continue;
            }

            console.log(`[Scout] Successfully downloaded image (${blob.size} bytes, ${contentType})`);

            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => {
                    if (typeof reader.result === 'string') {
                        resolve(reader.result);
                    } else {
                        reject(new Error("Failed to convert to Base64"));
                    }
                };
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
        } catch (error) {
            console.error(`[Scout] Error downloading image (attempt ${i + 1}):`, error);
        }
    }
    return undefined;
}

// Network Interceptor
chrome.webRequest.onBeforeRequest.addListener(
    (details) => {
        // IMPORTANT: Prevent infinite loop!
        // Skip requests initiated by the extension itself (tabId is -1)
        if (details.tabId === -1) return;

        if (details.method === "GET" && details.url.includes("geoserver/LANDSMAPS/wms")) {
            chrome.storage.local.get(['isRecording'], async (result) => {
                if (result.isRecording) {
                    const params = parseUrlParams(details.url);
                    const bboxKey = params.bbox?.join(',');

                    if (!bboxKey) return;

                    // Deduplication check based on BBOX
                    if (processedBboxes.has(bboxKey) || downloadingBboxes.has(bboxKey)) {
                        return;
                    }

                    downloadingBboxes.add(bboxKey);

                    // Normalize URL to target size (256x256)
                    const normalizedUrl = getNormalizedUrl(details.url);
                    console.log(`[Scout] Intercepted WMS request. BBOX: ${bboxKey}. URL: ${normalizedUrl}`);

                    // Use robust downloader with retries
                    try {
                        const imageData = await downloadImage(normalizedUrl);

                        if (imageData) {
                            const newTile: TileData = {
                                url: normalizedUrl,
                                timestamp: Date.now(),
                                imageData,
                                ...params,
                                width: TILE_SIZE,
                                height: TILE_SIZE
                            };
                            queueTile(newTile);
                            console.log(`[Scout] Queued tile for BBOX: ${bboxKey}`);
                        } else {
                            console.error(`[Scout] FAILED to capture tile after ${MAX_RETRIES} retries for BBOX: ${bboxKey}`);
                        }
                    } catch (err) {
                        console.error(`[Scout] Uncaught error capturing BBOX ${bboxKey}:`, err);
                    }

                    downloadingBboxes.delete(bboxKey);
                }
            });
        }
        return undefined;
    },
    { urls: ["*://landsmaps.dol.go.th/*"] }
);
