import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { injectAnalyticsMarkup } from './lib/analytics'
import './index.css'

injectAnalyticsMarkup()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
