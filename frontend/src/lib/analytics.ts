// Google Analytics 4. The measurement ID is public — it ships in the page
// source of every GA-tagged site — so a committed .env value is safe here.
const MEASUREMENT_ID: string = import.meta.env.VITE_GA_MEASUREMENT_ID ?? ''

declare global {
  interface Window {
    dataLayer?: unknown[]
    gtag?: (...args: unknown[]) => void
  }
}

export function initAnalytics(): void {
  if (!MEASUREMENT_ID || window.gtag) return

  const script = document.createElement('script')
  script.async = true
  script.src = `https://www.googletagmanager.com/gtag/js?id=${MEASUREMENT_ID}`
  document.head.appendChild(script)

  window.dataLayer = []
  window.gtag = (...args: unknown[]) => {
    window.dataLayer!.push(args)
  }
  window.gtag('js', new Date())
  window.gtag('config', MEASUREMENT_ID)
}
