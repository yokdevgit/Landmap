/// <reference types="chrome" />
import { useState, useEffect } from 'react';
import JSZip from 'jszip';
import './App.css';

interface TileData {
    url: string;
    timestamp: number;
    imageData?: string;
    bbox?: number[];
    width?: number;
    height?: number;
}

interface LocalState {
  isRecording: boolean;
  sessionName: string;
  tiles: TileData[];
}

function App() {
  const [state, setState] = useState<LocalState>({
    isRecording: false,
    sessionName: "",
    tiles: []
  });

  // Load initial state on mount
  useEffect(() => {
    chrome.storage.local.get(['isRecording', 'sessionName', 'tiles'], (result) => {
      setState({
        isRecording: result.isRecording === true,
        sessionName: (result.sessionName as string) || "",
        tiles: (result.tiles as TileData[]) || []
      });
    });
  }, []);

  // Poll for updates (keep isRecording and tiles in sync)
  useEffect(() => {
    const poll = () => {
      refreshState();
    };
    const interval = setInterval(poll, 1000);
    return () => clearInterval(interval);
  }, []);

  const refreshState = () => {
    chrome.storage.local.get(['isRecording', 'sessionName', 'tiles'], (result) => {
      const nextIsRecording = result.isRecording === true;
      const remoteSessionName = (result.sessionName as string) || "";
      const nextTiles = (result.tiles as TileData[]) || [];

      setState(prevState => {
        // Critical Logic:
        // 1. If recording, the Background is the source of truth (sync sessionName).
        // 2. If NOT recording, the Popup Input is the source of truth (keep persistence.sessionName).
        //    This prevents the polling loop from overwriting user typing.
        return {
          isRecording: nextIsRecording,
          sessionName: nextIsRecording ? remoteSessionName : prevState.sessionName,
          tiles: nextTiles
        };
      });
    });
  };

  const handleStart = () => {
    if (!state.sessionName.trim()) {
      alert("Please enter a session name");
      return;
    }
    // Save the session name to storage before starting, so it persists
    chrome.storage.local.set({ sessionName: state.sessionName }, () => {
      chrome.runtime.sendMessage({ type: "START_RECORDING", sessionName: state.sessionName }, refreshState);
    });
  };

  const handleStop = () => {
    chrome.runtime.sendMessage({ type: "STOP_RECORDING" }, refreshState);
  };

  const handleClear = () => {
    if (confirm("Are you sure you want to clear captured data?")) {
      chrome.runtime.sendMessage({ type: "CLEAR_SESSION" }, refreshState);
    }
  };

  const handleExport = async () => {
    if (state.tiles.length === 0) return;

    try {
        const zip = new JSZip();
        // Create images folder
        const imgFolder = zip.folder("images");

        // Prepare simplified data for mission.json (linking to image files)
        const tilesForExport = await Promise.all(state.tiles.map(async (tile, index) => {
            const fileName = `tile_${index}.png`;

            if (tile.imageData && imgFolder) {
                // Remove prefix data:image/png;base64,
                const base64Data = tile.imageData.split(',')[1];
                imgFolder.file(fileName, base64Data, {base64: true});
            }

            // Return tile data WITHOUT the massive base64 string
            // but WITH a reference to the file
            return {
                url: tile.url,
                timestamp: tile.timestamp,
                bbox: tile.bbox,
                width: tile.width,
                height: tile.height,
                fileName: `images/${fileName}`
            };
        }));

        const jsonContent = JSON.stringify({
          session: state.sessionName,
          recordedAt: new Date().toISOString(),
          totalTiles: state.tiles.length,
          data: tilesForExport
        }, null, 2);

        zip.file("mission.json", jsonContent);

        // Generate ZIP
        const content = await zip.generateAsync({type:"blob"});

        // Use FileReader to get base64 data for chrome.downloads
        const reader = new FileReader();
        reader.onload = function() {
            if (reader.result) {
                const url = reader.result as string;
                // Sanitize filename but allow Thai characters (Thai Unicode range: \u0E00-\u0E7F)
                const safeName = state.sessionName.replace(/[^a-zA-Z0-9\u0E00-\u0E7F\-_ ]/g, '_').trim() || "session";

                chrome.downloads.download({
                    url: url,
                    filename: `landmap-session-${safeName}.zip`,
                    saveAs: true // Prompt user to save to ensure they know where it goes
                });
            }
        };
        reader.readAsDataURL(content);
    } catch (e) {
        console.error("Export failed", e);
        alert("Export failed: " + e);
    }
  };

  return (
    <div className="container">
      <header className="header">
        <div className="icon">🛰️</div>
        <h1>The Scout</h1>
      </header>

      <div className={`status-card ${state.isRecording ? 'active' : ''}`}>
        <div className="status-indicator">
          <span className="dot"></span>
          {state.isRecording ? "RECORDING ACTIVE" : "READY"}
        </div>
        <div className="counter">
          <span className="count">{state.tiles.length}</span>
          <span className="label">TILES CAPTURED</span>
        </div>
      </div>

      <div className="controls">
        <div className="input-group">
          <label>Mission (Session Name)</label>
          <input
            type="text"
            placeholder="e.g. Plot A"
            value={state.sessionName}
            onChange={(e) => setState({ ...state, sessionName: e.target.value })}
            disabled={state.isRecording}
          />
        </div>

        <div className="actions">
          {!state.isRecording ? (
            <button className="btn btn-primary" onClick={handleStart}>
              ▶ START RECORDING
            </button>
          ) : (
            <button className="btn btn-danger" onClick={handleStop}>
              ⏹ STOP RECORDING
            </button>
          )}
        </div>

        <div className="secondary-actions">
           <button className="btn btn-secondary" onClick={handleExport} disabled={state.tiles.length === 0}>
             ⬇ Export JSON
           </button>
           <button className="btn btn-text" onClick={handleClear} disabled={state.isRecording || state.tiles.length === 0}>
             Clear Data
           </button>
        </div>
      </div>

      <footer className="footer">
        Verified for: landsmaps.dol.go.th
      </footer>
    </div>
  );
}

export default App;
