# IMPLEMENATION PLAN: The Scout (Chrome Extension)

## 1. Project Setup
- [ ] Initialize Vite project (React + TypeScript).
- [ ] Setup `manifest.json` for Chrome Extension V3.
- [ ] Configure `vite.config.ts` for multiple entry points (Popup, Background).
- [ ] Setup TailwindCSS (optional, but good for "Rich Aesthetics"). *Self-correction: System prompt says avoid Tailwind unless requested. I will use Vanilla CSS or styled-components/modules for a premium look as per "Web Application Development" guidelines.*

## 2. Architecture Components

### A. Background Service Worker (`background.ts`)
- **Responsibility**: Listener & Data Manager.
- **Key Features**:
    - `chrome.webRequest.onBeforeRequest` or `chrome.declarativeNetRequest` (webRequest is more flexible for inspection, though dnr is V3 standard for blocking. Since we are just *recording*, `webRequest` with `blocking: false` or just inspecting is fine, OR we might need to use `chrome.debugger` if we need the response body, but here we just need URL params for the WMS. The user wants to capture URLs like in `example.txt`. WMS works via GET params. So capturing URL is enough).
    - Session State: Active/Inactive.
    - Storage: `chrome.storage.local`.

### B. Popup UI (`popup.tsx`)
- **Responsibility**: User Interface.
- **Key Features**:
    - Dashboard showing current session stats.
    - "Start/Stop Recording" toggle.
    - "Export JSON" button.
    - "Clear/Reset" button.
    - Visual feedback (Animated icons when recording).

## 3. Data Structure (JSON Export)
```json
{
  "session_name": "Project Alpha",
  "created_at": "2024-12-20T10:00:00Z",
  "total_tiles": 150,
  "tiles": [
    {
      "url": "https://landsmaps.dol.go.th/...",
      "bbox": "100.1,13.5,100.2,13.6",
      "timestamp": 1703066400000
    }
  ]
}
```

## 4. Implementation Steps
1.  **Scaffold**: Create the Vite App.
2.  **Manifest**: Define permissions (`storage`, `webRequest`, `activeTab` - strictly scoped to `*://landsmaps.dol.go.th/*`).
3.  **Background Script**: Implement the "Interceptor".
4.  **Popup UI**: Build the "Control Center".
5.  **Build Script**: Ensure `npm run build` produces a valid `dist` folder loadable by Chrome.
