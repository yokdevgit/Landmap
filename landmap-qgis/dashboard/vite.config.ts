import { defineConfig, type ViteDevServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readdirSync, writeFileSync, existsSync } from 'fs'
import { join } from 'path'

// Auto-sync plugin: Automatically generates sessions.json from public/areas
function autoSyncSessions() {
  const syncSessions = () => {
    const areasDir = join(process.cwd(), 'public', 'areas')
    if (!existsSync(areasDir)) {
      console.log('[Auto-Sync] public/areas directory not found')
      return
    }

    try {
      const entries = readdirSync(areasDir, { withFileTypes: true })
      const sessions = entries
        .filter(e => e.isDirectory())
        .map(e => e.name)
        .sort()

      const outputPath = join(areasDir, 'sessions.json')
      writeFileSync(outputPath, JSON.stringify(sessions, null, 2))
      console.log(`[Auto-Sync] ✅ Synced ${sessions.length} sessions to sessions.json`)
    } catch (error) {
      console.error('[Auto-Sync] Error syncing sessions:', error)
    }
  }

  return {
    name: 'auto-sync-sessions',
    buildStart() {
      // Sync on server start
      syncSessions()
    },
    configureServer(server: ViteDevServer) {
      // Watch public/areas directory for changes
      const areasDir = join(process.cwd(), 'public', 'areas')
      if (existsSync(areasDir)) {
        server.watcher.add(areasDir)
        server.watcher.on('addDir', (path: string) => {
          if (path.startsWith(areasDir) && path !== areasDir) {
            console.log('[Auto-Sync] New directory detected:', path)
            syncSessions()
          }
        })
        server.watcher.on('unlinkDir', (path: string) => {
          if (path.startsWith(areasDir)) {
            console.log('[Auto-Sync] Directory removed:', path)
            syncSessions()
          }
        })
      }
    }
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), autoSyncSessions()],
  server: {
    proxy: {
      '/geoserver': {
        target: 'https://landsmaps.dol.go.th',
        changeOrigin: true,
        secure: false,
        headers: {
          'Referer': 'https://landsmaps.dol.go.th/',
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
      }
    }
  }
})
