import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import { AuthGate } from './components/auth/AuthGate'
import './index.css'
import { initTheme } from './hooks/useTheme'
// Bundled, so the app needs no network: the code editor and the fonts
import './utils/monaco'
import '@fontsource/inter/300.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'

initTheme()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><AuthGate><App /></AuthGate></React.StrictMode>,
)
