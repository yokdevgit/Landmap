# IMPLEMENTATION PLAN: The HQ (Dashboard & Processor)

## 1. Project Setup
- [ ] Initialize Vite project (React + TypeScript) using **Bun**.
- [ ] Setup TailwindCSS (Wait, user instructions say "Avoid TailwindCSS unless explicitly requested". I will use **Vanilla CSS / CSS Modules** for a premium, custom look, or styled-components).
- [ ] Install analytical & GIS dependencies: `leaflet`, `react-leaflet`, `jszip`, `file-saver`, `proj4`.

## 2. Core Components

### A. Mission Control (Dashboard UI)
- **Upload Zone**: Drag & drop area for the JSON file (Mission Report).
- **Map View**: Interactive map (Leaflet) to visualize the captured locations before processing. This validates that the data is correct.

### B. The Processor (Logic)
- **CORS Handler**: Anticipating CORS issues when fetching images from a browser.
    - *Strategy A*: Try direct fetch.
    - *Strategy B*: If A fails, provide a simple "Bun Proxy Script" or instructions to run chrome with disabled security (not recommended but an option), OR use a cors-anywhere proxy.
    - *Refined Strategy*: Since we are using Bun, we can create a tiny proxy server in the same repo if needed. For now, we will try client-side first.
- **Image Downloader**: Batch download with concurrency limit (to be polite to the server).
- **Georeference Engine**:
    - Parse `BBOX` from URL.
    - Convert if necessary (WMS usually uses EPSG:4326 or 3857, we need to ensure it matches the World File format).
    - Generate `.pgw` (World File) content:
        ```text
        pixel_x_size
        rotation_y
        rotation_x
        pixel_y_size (negative)
        top_left_x
        top_left_y
        ```
- **Packager**: Bundle everything (Images + World Files + QGIS Project) into a single ZIP file.

## 3. Implementation Steps
1.  **Init**: `bun create vite dashboard`.
2.  **Dependencies**: `bun add leaflet react-leaflet jszip file-saver proj4`.
3.  **UI**: Build the layout (Header, Map, Sidebar).
4.  **Logic**: Implement the Processor Hook.
5.  **Integration**: Connect UI to Logic.
