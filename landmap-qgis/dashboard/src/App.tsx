import { useState, useEffect } from 'react';
import { MapContainer, TileLayer, ImageOverlay, Rectangle, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import JSZip from 'jszip';
import { saveAs } from 'file-saver';
import { generateQLR } from './qgisHelper';
import './App.css';

// Types
interface TileData {
  url: string;
  timestamp: number;
  bbox?: number[]; // [minLon, minLat, maxLon, maxLat]
  imageData?: string; // Base64 encoded image
  width?: number;
  height?: number;
}

interface ProcessingStats {
  total: number;
  processed: number;
  failed: number;
  status: 'idle' | 'processing' | 'zipping' | 'completed' | 'failed' | 'error';
}

// Utils
const parseUrlParams = (url: string) => {
  try {
    const urlObj = new URL(url);
    // Handle both lowercase and uppercase BBOX param
    const bboxParam = urlObj.searchParams.get('bbox') || urlObj.searchParams.get('BBOX');
    const widthParam = urlObj.searchParams.get('width') || urlObj.searchParams.get('WIDTH');
    const heightParam = urlObj.searchParams.get('height') || urlObj.searchParams.get('HEIGHT');

    const bbox = bboxParam ? bboxParam.split(',').map(Number) : undefined;
    const width = Number(widthParam);
    const height = Number(heightParam);
    return { bbox, width, height };
  } catch (e) {
    console.warn("Error parsing URL:", url);
    return { bbox: undefined, width: 0, height: 0 };
  }
};

// Component to fly to bounds
function MapFluTo({ bbox }: { bbox: number[] | null }) {
  const map = useMap();
  useEffect(() => {
    if (bbox && bbox.length === 4) {
      // Leaflet uses [lat, lon], bbox is [minLon, minLat, maxLon, maxLat]
      // Bounds: [[minLat, minLon], [maxLat, maxLon]]
      const [minLon, minLat, maxLon, maxLat] = bbox;
      map.fitBounds([[minLat, minLon], [maxLat, maxLon]], { padding: [50, 50] });
    }
  }, [bbox, map]);
  return null;
}

// Custom component for manual zoom control
function ZoomControl({ bbox }: { bbox: number[] | null }) {
  const map = useMap();

  const handleZoom = () => {
    console.log("Manual Zoom Clicked. BBOX:", bbox);
    if (bbox && bbox.length === 4) {
      const [minLon, minLat, maxLon, maxLat] = bbox;
      map.fitBounds([[minLat, minLon], [maxLat, maxLon]], { padding: [50, 50], maxZoom: 18 });
    }
  };

  if (!bbox) return null;

  return (
    <div className="leaflet-bottom leaflet-left" style={{ pointerEvents: 'auto', marginBottom: '20px', marginLeft: '10px' }}>
      <button
        onClick={handleZoom}
        style={{
          backgroundColor: 'white',
          border: '2px solid rgba(0,0,0,0.2)',
          borderRadius: '4px',
          padding: '5px 10px',
          cursor: 'pointer',
          fontWeight: 'bold',
          color: '#333',
          boxShadow: '0 1px 5px rgba(0,0,0,0.4)'
        }}
      >
        🔍 Zoom to Data
      </button>
    </div>
  );
}

function App() {
  const [data, setData] = useState<TileData[]>([]);
  const [sessionName, setSessionName] = useState<string>('');
  const [overallBbox, setOverallBbox] = useState<number[] | null>(null);
  const [stats, setStats] = useState<ProcessingStats>({ total: 0, processed: 0, failed: 0, status: 'idle' });
  const [availableSessions, setAvailableSessions] = useState<string[]>([]);
  const [showInstructions, setShowInstructions] = useState(false);

  // Fetch available sessions from public/areas on mount
  useEffect(() => {
    fetch('/areas/sessions.json')
      .then(res => res.json())
      .then(data => setAvailableSessions(data))
      .catch(() => console.log('No sessions.json found in public/areas'));
  }, []);

  const loadFromPublic = async (folderName: string) => {
    try {
        setStats(prev => ({ ...prev, status: 'processing', processed: 0, total: 0 }));
        const res = await fetch(`/areas/${folderName}/mission.json`);
        if (!res.ok) throw new Error("Failed to load mission.json");

        const json = await res.json();
        const tiles = json.data || [];
        setSessionName(json.session || folderName);

        const enrichedData = await Promise.all(tiles.map(async (tile: any) => {
            if (tile.fileName) {
                try {
                    const imgRes = await fetch(`/areas/${folderName}/${tile.fileName}`);
                    if (!imgRes.ok) return tile;
                    const blob = await imgRes.blob();

                    return new Promise((resolve) => {
                        const reader = new FileReader();
                        reader.onloadend = () => {
                            resolve({
                                ...tile,
                                imageData: reader.result as string
                            });
                        };
                        reader.readAsDataURL(blob);
                    });
                } catch (e) {
                    console.error("Failed to load image", tile.fileName, e);
                    return tile;
                }
            }
            return tile;
        }));

        loadSessionData({ ...json, data: enrichedData });
    } catch (e) {
        console.error(e);
        alert("Error loading session: " + e);
        setStats(prev => ({ ...prev, status: 'idle' }));
    }
  };

  const loadSessionData = (json: any) => {
        const tiles = json.data || [];
        setSessionName(json.session || 'Unnamed');

        let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;

        const validTiles = tiles.map((t: any) => {
          const params = parseUrlParams(t.url);
          // Prefer bbox from metadata if available (from extension), else parse from URL
          const bbox = t.bbox || params.bbox;

          if (bbox && bbox.length === 4) {
             const [x1, y1, x2, y2] = bbox;

             // Thailand Bounds Sanity Check (approximate)
             // Lon: 90-110, Lat: 0-30
             if (x1 < 90 || x2 > 110 || y1 < 0 || y2 > 30) {
                console.warn("Ignoring out-of-bounds tile:", bbox, t.url);
                return { ...t, bbox: undefined }; // Mark as invalid bbox
             }

             // Update global bounds
             if (!isNaN(x1)) minLon = Math.min(minLon, x1);
             if (!isNaN(y1)) minLat = Math.min(minLat, y1);
             if (!isNaN(x2)) maxLon = Math.max(maxLon, x2);
             if (!isNaN(y2)) maxLat = Math.max(maxLat, y2);
             return { ...t, bbox: bbox };
          }
          return t;
        }).filter((t: any) => t.bbox);

        console.log(`Parsed ${validTiles.length} valid tiles from ${tiles.length} raw entries.`);

        setData(validTiles);
          if (minLon !== Infinity && maxLon !== -Infinity) {
          const computedBbox = [minLon, minLat, maxLon, maxLat];
          console.log("Calculated Overall BBOX:", computedBbox);
          setOverallBbox(computedBbox);
        } else {
            console.warn("Could not calculate overall bbox", { minLon, minLat, maxLon, maxLat });
            setOverallBbox(null);
        }
        setStats({ total: validTiles.length, processed: 0, failed: 0, status: 'idle' });
  };

  // Helper to convert full URL to proxy URL
  // e.g., https://landsmaps.dol.go.th/geoserver/... -> /geoserver/...
  const getProxyUrl = (originalUrl: string) => {
    try {
      const urlObj = new URL(originalUrl);
      return urlObj.pathname + urlObj.search;
    } catch {
      return originalUrl;
    }
  };

  const processAndDownload = async () => {
    if (data.length === 0) return;
    setStats({ ...stats, status: 'processing', processed: 0, failed: 0 });

    const zip = new JSZip();
    const folder = zip.folder(sessionName || "landmap-data");
    const CHUNK_SIZE = 10; // Can be higher since we're not making network requests

    // Helper to convert Base64 to Blob
    const base64ToBlob = (base64: string): Blob => {
      // Remove data:image/png;base64, prefix if present
      const base64Data = base64.split(',')[1] || base64;
      const byteCharacters = atob(base64Data);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      return new Blob([byteArray], { type: 'image/png' });
    };

    for (let i = 0; i < data.length; i += CHUNK_SIZE) {
      const chunk = data.slice(i, i + CHUNK_SIZE);
      await Promise.all(chunk.map(async (tile, idx) => {
        try {
          const filename = `tile_${i + idx}`;

          // Check if we have Base64 image data
          if (!tile.imageData) {
            console.warn(`[HQ] Tile ${i + idx} missing imageData (Base64). Skipping.`, tile.url);
            setStats(prev => ({ ...prev, failed: prev.failed + 1 }));
            return;
          }

          // Convert Base64 to Blob and add to ZIP
          const blob = base64ToBlob(tile.imageData);
          folder?.file(`${filename}.png`, blob);

          console.log(`[HQ] Processed tile ${i + idx}, size: ${(blob.size / 1024).toFixed(2)}KB`);

          // Generate World File (.pgw) if bbox is available
          if (tile.bbox) {
            const [minLon, minLat, maxLon, maxLat] = tile.bbox;
            const width = tile.width || 256;
            const height = tile.height || 256;

            const pixelSizeX = (maxLon - minLon) / width;
            const pixelSizeY = (minLat - maxLat) / height; // Negative for standard maps

            // X/Y coordinate of the center of the upper-left pixel
            const topLeftX = minLon + (pixelSizeX / 2);
            const topLeftY = maxLat + (pixelSizeY / 2);

            // World File (.pgw) format:
            // 1. Pixel size in X direction
            // 2. Rotation about Y axis (0)
            // 3. Rotation about X axis (0)
            // 4. Pixel size in Y direction (negative)
            // 5. X coordinate of center of upper-left pixel
            // 6. Y coordinate of center of upper-left pixel
            const pgw = [
                pixelSizeX.toFixed(12),
                "0.0000000000",
                "0.0000000000",
                pixelSizeY.toFixed(12),
                topLeftX.toFixed(12),
                topLeftY.toFixed(12)
            ].join("\n");

            folder?.file(`${filename}.pgw`, pgw);
          } else {
            console.warn(`[HQ] Tile ${i + idx} missing bbox. World file not generated.`);
          }

          setStats(prev => ({ ...prev, processed: prev.processed + 1 }));
        } catch (e) {
          console.error("[HQ] Failed to process tile", tile.url, e);
          setStats(prev => ({ ...prev, failed: prev.failed + 1 }));
        }
      }));
    }

    // Generate QGIS Layer Definition (.qlr)
    const validTiles = data.filter(t => t.bbox);
    if (validTiles.length > 0) {
        const qlrContent = generateQLR(data, sessionName);
        if (qlrContent) {
            folder?.file("landmap.qlr", qlrContent);
        }
    }

    setStats(prev => ({ ...prev, status: 'zipping' }));
    const content = await zip.generateAsync({ type: "blob" });
    saveAs(content, `${sessionName}-gis-data.zip`);
    setStats(prev => ({ ...prev, status: 'completed' }));
  };

  return (
    <div className="app-container">
      <aside className="sidebar">
        <div className="logo">
           🏢 The HQ <span style={{fontSize: '0.8rem', opacity: 0.7}}>beta</span>
        </div>

        <div className="sidebar-header" style={{ marginBottom: '20px' }}>
             <p style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
               Place your unzipped mission folders in <code>public/areas/</code> to see them here.
             </p>
        </div>

        {availableSessions.length > 0 && (
            <div className="public-sessions" style={{
                margin: '20px 0',
                padding: '15px',
                background: '#1e293b',
                borderRadius: '8px',
                border: '1px solid #334155'
            }}>
                <label style={{fontSize: '0.75rem', color: '#38bdf8', marginBottom: '8px', display: 'block', fontWeight: 'bold', letterSpacing: '0.05em'}}>
                    AVAILABLE SESSIONS
                </label>
                <select
                    onChange={(e) => e.target.value && loadFromPublic(e.target.value)}
                    style={{
                        width: '100%',
                        padding: '10px',
                        background: '#0f172a',
                        color: 'white',
                        border: '1px solid #475569',
                        borderRadius: '6px',
                        fontSize: '0.9rem',
                        cursor: 'pointer'
                    }}
                    defaultValue=""
                >
                    <option value="" disabled>Select a session...</option>
                    {availableSessions.map(os => (
                        <option key={os} value={os}>{os}</option>
                    ))}
                </select>
                <div style={{fontSize: '0.7rem', color: '#64748b', marginTop: '8px'}}>
                    * Unzip your exports into public/areas/ to see them here.
                </div>
            </div>
        )}

        {data.length > 0 && (
          <div className="mission-control">
             <div className="stats-card">
               <div className="stat-item">
                 <span>Mission:</span>
                 <strong>{sessionName}</strong>
               </div>
               <div className="stat-item">
                 <span>Tiles:</span>
                 <strong>{data.length}</strong>
               </div>
               <div className="stat-item">
                 <span>Status:</span>
                 <strong style={{
                     color: stats.status === 'completed' ? '#4ade80' :
                            stats.status === 'failed' ? '#ef4444' : '#94a3b8'
                 }}>
                   {stats.status.toUpperCase()}
                 </strong>
               </div>
               {/* Bounds Debug Info */}
               <div className="stat-item" style={{flexDirection: 'column', gap: '5px'}}>
                  <span>Coverage:</span>
                  <small style={{fontFamily: 'monospace', fontSize: '0.7rem', color: '#94a3b8'}}>
                    {overallBbox ? overallBbox.map(n => n.toFixed(3)).join(', ') : 'Calculating...'}
                  </small>
               </div>

               {stats.failed > 0 && (
                   <div className="stat-item" style={{color: '#ef4444'}}>
                       <span>Failed:</span>
                       <strong>{stats.failed}</strong>
                   </div>
               )}
             </div>

             <button
               className="action-btn"
               onClick={processAndDownload}
               disabled={stats.status === 'processing' || stats.status === 'zipping'}
             >
                {stats.status === 'processing' ? 'Processing...' : '⚡ Process & Download'}
             </button>

             <button
                className="help-link-btn"
                onClick={() => setShowInstructions(true)}
                style={{
                  marginTop: '10px',
                  background: 'none',
                  border: 'none',
                  color: '#38bdf8',
                  fontSize: '0.8rem',
                  textDecoration: 'underline',
                  cursor: 'pointer',
                  width: '100%',
                  textAlign: 'center'
                }}
             >
                How to open in QGIS?
             </button>

             {stats.status === 'processing' && (
               <div className="progress-bar">
                 <div
                   className="progress-fill"
                   style={{width: `${(stats.processed / stats.total) * 100}%`}}
                 ></div>
               </div>
             )}
          </div>
        )}
      </aside>

      <main className="map-area">
        <MapContainer center={[13.7563, 100.5018]} zoom={10} scrollWheelZoom={true}>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <MapFluTo bbox={overallBbox} />
          <ZoomControl bbox={overallBbox} />

          {overallBbox && (
             <>
               <div style={{ position: 'absolute', top: 10, right: 10, background: 'white', padding: 5, zIndex: 1000, borderRadius: 4, fontSize: '12px', color: 'black', fontWeight: 'bold' }}>
                  Coverage View: {data.length} Tiles
               </div>
               {/* Debugging Bounding Box */}
               <Rectangle
                  bounds={[[overallBbox[1], overallBbox[0]], [overallBbox[3], overallBbox[2]]]}
                  pathOptions={{ color: 'red', fill: false, weight: 1, dashArray: '5, 5' }}
               />
             </>
          )}

          {/* Render ALL Tiles */}
          {data.map((tile, idx) => {
             if (!tile.bbox) return null;
             return (
              <ImageOverlay
                key={idx}
                url={tile.imageData || getProxyUrl(tile.url)}
                bounds={[[tile.bbox[1], tile.bbox[0]], [tile.bbox[3], tile.bbox[2]]]}
                opacity={0.9}
                className="map-tile-image"
                crossOrigin="anonymous"
              />
             );
          })}
        </MapContainer>

        {showInstructions && (
          <div className="instructions-overlay" onClick={() => setShowInstructions(false)}>
            <div className="instructions-card" onClick={e => e.stopPropagation()}>
              <button className="close-btn" onClick={() => setShowInstructions(false)}>×</button>
              <h2>🗺️ Importing into QGIS</h2>

              <div className="instruction-step">
                <h3>1. Download & Extract</h3>
                <p>Click "Process & Download" to get a ZIP file. Extract it to a folder. You'll see <code>.png</code> images and <code>.pgw</code> world files.</p>
              </div>

              <div className="instruction-step">
                <h3>2. Build Virtual Raster (Recommended)</h3>
                <p>Instead of dragging 1000+ files, use this "clean" method:</p>
                <ul>
                  <li>Open QGIS</li>
                  <li>Go to <strong>Raster</strong> → <strong>Miscellaneous</strong> → <strong>Build Virtual Raster (VRT)...</strong></li>
                  <li><strong>Input layers:</strong> Click <code>...</code> → <strong>Add Directory...</strong> → Select your extracted folder</li>
                  <li><strong>Resolution:</strong> Set to "Highest"</li>
                  <li><strong>Projection:</strong> Select <code>EPSG:4326</code> (WGS 84)</li>
                  <li>Click <strong>Run</strong></li>
                </ul>
              </div>

              <div className="instruction-step">
                <h3>3. Tip: Export to GeoTIFF</h3>
                <p>Once you have the Virtual Raster, right-click the layer → <strong>Export</strong> → <strong>Save As...</strong> to save it as a single high-quality GeoTIFF file.</p>
              </div>

              <div style={{marginTop: '20px', fontSize: '0.8rem', color: '#94a3b8', borderTop: '1px solid #334155', paddingTop: '15px'}}>
                Note: The <code>.pgw</code> files allow QGIS to position your tiles correctly without modifying the original images.
              </div>
            </div>
          </div>
        )}

        {stats.status === 'zipping' && (
           <div className="loading-overlay">
             📦 Compressing Assets...
           </div>
        )}
      </main>
    </div>
  );
}

export default App;
